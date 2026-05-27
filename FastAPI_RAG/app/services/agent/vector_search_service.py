"""向量检索服务：混合检索（Dense + Sparse）+ RRF 融合 + Cross-encoder 重排序。"""

from __future__ import annotations

from typing import Any

from langchain_openai import OpenAIEmbeddings
from qdrant_client import QdrantClient
from qdrant_client import models as qdrant_models

from app.config.agent_config import agent_config
from app.config.qdrant_config import QdrantSettings
from app.utils.logger import get_logger

logger = get_logger(__name__)


class VectorSearchService:
    """混合检索服务：Dense 语义 + Sparse BM25 → RRF 融合 → FlashRank 重排序。"""

    def __init__(self) -> None:
        rag = agent_config.rag
        hybrid = agent_config.hybrid_search
        qdrant_settings = QdrantSettings()

        logger.info(
            "初始化向量检索服务: top_k=%d score_threshold=%.2f rerank=%s",
            rag.rag_top_k,
            rag.rag_score_threshold,
            hybrid.rerank_enabled,
        )
        # check_embedding_ctx_length=False: 禁用 tiktoken 分词编码，直接发送原始字符串
        # DashScope 兼容接口仅接受字符串输入，不支持 token ID 数组
        self._embeddings = OpenAIEmbeddings(
            model=rag.embedding_model,
            api_key=rag.embedding_api_key,
            base_url=rag.embedding_base_url,
            check_embedding_ctx_length=False,
        )
        self._qdrant_client = QdrantClient(
            url=qdrant_settings.resolved_url,
            api_key=qdrant_settings.api_key or None,
            timeout=qdrant_settings.timeout_seconds,
        )
        self._sparse_model = None   # 延迟加载 fastembed SparseTextEmbedding

    # ── 延迟初始化 ──────────────────────────────────────────────

    def _init_sparse_model(self) -> None:
        """延迟初始化 BM25 稀疏向量模型，下载失败时直接抛出异常。"""
        if self._sparse_model is not None:
            return
        from fastembed import SparseTextEmbedding

        from app.services.agent import _MODELS_DIR

        logger.info("初始化 BM25 稀疏向量模型 (fastembed Qdrant/BM25)")
        self._sparse_model = SparseTextEmbedding(model_name="Qdrant/BM25", cache_dir=_MODELS_DIR)

    # ── 公共接口 ────────────────────────────────────────────────

    def search(self, query: str, search_type: str = "hybrid") -> list[dict[str, Any]]:
        """
        【业务功能】对用户 query 进行向量检索，支持三种检索策略

        检索流程：
          - hybrid: Dense 语义 + Sparse BM25 → RRF 融合 → Cross-encoder 重排序 → score 阈值过滤
          - semantic: Dense 语义检索 → score 阈值过滤
          - keyword: Sparse BM25 关键词检索 → score 阈值过滤

        Args:
            query: 用户问题文本
            search_type: 检索策略，可选 "hybrid" / "semantic" / "keyword"

        Returns:
            chunk 列表，每条含 chunk_id/content/file_name/file_hash/chunk_index/score
            不相关的结果会被阈值过滤掉，可能返回空列表
        """
        query = query.strip()
        if not query:
            return []
        if search_type == "semantic":
            return self._search_semantic(query)
        if search_type == "keyword":
            return self._search_keyword(query)
        return self._search_hybrid(query)

    # ── 检索实现 ────────────────────────────────────────────────

    def _search_hybrid(self, query: str) -> list[dict[str, Any]]:
        """Dense + Sparse 混合检索 → RRF 融合 → Cross-encoder 重排序 → score 阈值过滤。"""
        rag = agent_config.rag
        hybrid = agent_config.hybrid_search

        dense_vector = self._embeddings.embed_query(query)

        self._init_sparse_model()
        sparse_query = self._embed_sparse_query(query)

        # 并行 prefetch + RRF 融合
        response = self._qdrant_client.query_points(
            collection_name=rag.qdrant_collection,
            prefetch=[
                qdrant_models.Prefetch(
                    query=dense_vector,
                    using="dense",
                    limit=hybrid.prefetch_limit,
                ),
                qdrant_models.Prefetch(
                    query=qdrant_models.NearestQuery(nearest=sparse_query),
                    using="sparse",
                    limit=hybrid.prefetch_limit,
                ),
            ],
            query=qdrant_models.FusionQuery(fusion=qdrant_models.Fusion.RRF),
            limit=hybrid.candidate_limit,
            with_payload=True,
        )

        candidates = [self._point_to_dict(p, p.score) for p in response.points]

        if not candidates:
            logger.info("混合检索完成: query_len=%d 命中=0", len(query))
            return []

        # Cross-encoder 重排序
        if hybrid.rerank_enabled and len(candidates) > 1:
            candidates = self._apply_rerank(query, candidates)

        # score 阈值过滤 + 截断到 top-K
        passed = [c for c in candidates if c.get("score", 0) >= rag.rag_score_threshold]
        discarded = len(candidates) - len(passed)
        results = passed[: rag.rag_top_k]

        logger.info(
            "混合检索完成: query_len=%d 候选=%d 通过阈值=%d 丢弃=%d 最终=%d",
            len(query), len(candidates), len(passed), discarded, len(results),
        )
        return results

    def _search_semantic(self, query: str) -> list[dict[str, Any]]:
        """纯 Dense 语义检索，不走 BM25 和 RRF 融合。"""
        rag = agent_config.rag

        dense_vector = self._embeddings.embed_query(query)
        response = self._qdrant_client.query_points(
            collection_name=rag.qdrant_collection,
            query=dense_vector,
            using="dense",
            limit=rag.rag_top_k,
            with_payload=True,
        )
        results = [self._point_to_dict(p, p.score) for p in response.points]
        passed = [r for r in results if r.get("score", 0) >= rag.rag_score_threshold]

        logger.info(
            "语义检索完成: query_len=%d 命中=%d 通过阈值=%d",
            len(query), len(results), len(passed),
        )
        return passed

    def _search_keyword(self, query: str) -> list[dict[str, Any]]:
        """纯 Sparse BM25 关键词检索，不走 Dense 和 RRF 融合。"""
        rag = agent_config.rag

        self._init_sparse_model()
        sparse_query = self._embed_sparse_query(query)
        response = self._qdrant_client.query_points(
            collection_name=rag.qdrant_collection,
            query=qdrant_models.NearestQuery(nearest=sparse_query),
            using="sparse",
            limit=rag.rag_top_k,
            with_payload=True,
        )
        results = [self._point_to_dict(p, p.score) for p in response.points]
        passed = [r for r in results if r.get("score", 0) >= rag.rag_score_threshold]

        logger.info(
            "关键词检索完成: query_len=%d 命中=%d 通过阈值=%d",
            len(query), len(results), len(passed),
        )
        return passed

    # ── 内部辅助 ────────────────────────────────────────────────

    def _embed_sparse_query(self, query: str) -> qdrant_models.SparseVector:
        """将 query 转为 BM25 稀疏向量（供 Qdrant query_points 使用）。"""
        sparse_result = list(self._sparse_model.embed([query]))[0]
        return qdrant_models.SparseVector(
            values=sparse_result.values.tolist(),
            indices=sparse_result.indices.tolist(),
        )

    @staticmethod
    def _point_to_dict(point, score: float) -> dict[str, Any]:
        """将 Qdrant ScoredPoint 转为统一的结果 dict 格式。"""
        meta = (point.payload or {}).get("metadata", {})
        return {
            "chunk_id": f"{meta.get('file_hash', '')}::{meta.get('chunk_index', '')}",
            "content": point.payload.get("page_content", "") if point.payload else "",
            "file_name": meta.get("file_name", ""),
            "file_hash": meta.get("file_hash", ""),
            "chunk_index": meta.get("chunk_index", -1),
            "score": round(float(score), 4),
        }

    def _init_reranker(self) -> None:
        """延迟初始化 flashrank cross-encoder 重排序模型。"""
        if hasattr(self, '_reranker') and self._reranker is not None:
            return
        from flashrank import Ranker

        from app.services.agent import _MODELS_DIR

        logger.info("初始化 flashrank 重排序模型 (ms-marco-TinyBERT-L-2-v2)")
        self._reranker = Ranker(cache_dir=_MODELS_DIR)

    def _apply_rerank(self, query: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """对候选集进行 cross-encoder 重排序，失败时回退到 RRF 原始排序。"""
        try:
            self._init_reranker()
            from flashrank import RerankRequest

            passages = [{"text": c["content"]} for c in candidates]
            result = self._reranker.rerank(RerankRequest(query=query, passages=passages))
            # result is sorted by score descending; each item has {"text": ..., "score": ...}
            score_map = {r["text"]: r["score"] for r in result}
            for c in candidates:
                c["rerank_score"] = round(float(score_map.get(c["content"], 0.0)), 4)
            candidates.sort(key=lambda x: x.get("rerank_score", 0), reverse=True)
            logger.info("Cross-encoder 重排序完成: candidates=%d", len(candidates))
        except Exception:
            logger.warning("重排序失败，回退到 RRF 原始排序", exc_info=True)
        return candidates


_vector_search_service: VectorSearchService | None = None


def get_vector_search_service() -> VectorSearchService:
    """
    【业务功能】获取向量检索服务全局单例

    业务规则：全应用共享同一实例，避免重复初始化 Embedding 模型与 Qdrant 连接

    Returns:
        VectorSearchService 实例
    """
    global _vector_search_service
    if _vector_search_service is None:
        _vector_search_service = VectorSearchService()
    return _vector_search_service
