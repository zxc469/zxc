"""向量化入库服务：Markdown 分块 → Dense + Sparse 双向量 → Qdrant。"""

from __future__ import annotations

import asyncio
import uuid

from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    Filter,
    FieldCondition,
    MatchValue,
    PointStruct,
    SparseVectorParams,
    VectorParams,
)

from app.config.agent_config import agent_config
from app.config.qdrant_config import QdrantSettings
from app.db.qdrant_client import create_qdrant_async_client
from app.utils.logger import get_logger

logger = get_logger(__name__)

_BATCH_SIZE = 10  # DashScope 兼容接口限制每次请求不超过 10 条


class VectorIngestService:
    """将 Markdown 文本切块、生成 Dense + Sparse 双向量并写入 Qdrant。"""

    def __init__(self) -> None:
        rag = agent_config.rag
        self._qdrant_settings = QdrantSettings()

        logger.info("初始化向量入库服务")
        # check_embedding_ctx_length=False: 禁用 tiktoken 分词编码，直接发送原始字符串
        # DashScope 兼容接口仅接受字符串输入，不支持 token ID 数组
        self._embeddings = OpenAIEmbeddings(
            model=rag.embedding_model,
            api_key=rag.embedding_api_key,
            base_url=rag.embedding_base_url,
            check_embedding_ctx_length=False,
        )
        self._sparse_model = None  # 延迟加载，避免首次 import fastembed 拖慢启动

    def _init_sparse_model(self) -> None:
        """延迟初始化 BM25 稀疏向量模型，下载失败时直接抛出异常。"""
        if self._sparse_model is not None:
            return
        from fastembed import SparseTextEmbedding

        from app.services.agent import _MODELS_DIR

        logger.info("初始化 BM25 稀疏向量模型 (fastembed Qdrant/BM25)")
        self._sparse_model = SparseTextEmbedding(model_name="Qdrant/BM25", cache_dir=_MODELS_DIR)

    @staticmethod
    def _build_splitter(
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        chunk_separators: list[str] | None = None,
    ) -> RecursiveCharacterTextSplitter:
        """按请求参数构建文本切分器，未传参时回退到全局默认配置。"""
        rag = agent_config.rag
        return RecursiveCharacterTextSplitter(
            chunk_size=chunk_size if chunk_size is not None else rag.rag_chunk_size,
            chunk_overlap=chunk_overlap if chunk_overlap is not None else rag.rag_chunk_overlap,
            separators=chunk_separators if chunk_separators is not None else rag.rag_chunk_separators,
        )

    async def _ensure_collection(self, qdrant_client: AsyncQdrantClient) -> None:
        """确保 Qdrant collection 存在且配置了 named vectors (dense + sparse)。

        若 collection 不存在则自动创建；若存在但缺少 sparse_vectors 配置则记录警告，
        因为 schema 从单向量升级为 named vectors 需要手动重建 collection。

        Args:
            qdrant_client: Qdrant 异步客户端。

        Raises:
            RuntimeError: collection 创建失败。
        """
        collection_name = agent_config.rag.qdrant_collection

        collections_response = await qdrant_client.get_collections()
        existing_names = [c.name for c in collections_response.collections]

        if collection_name in existing_names:
            collection_info = await qdrant_client.get_collection(collection_name)
            params = collection_info.config.params
            has_sparse = bool(params.sparse_vectors)
            if not has_sparse:
                logger.warning(
                    "Collection %s 未配置 sparse_vectors，请手动删除后重新入库以启用混合检索",
                    collection_name,
                )
            return

        sample = await self._embeddings.aembed_query("sample")
        dimension = len(sample)

        logger.info(
            "创建 Qdrant collection: %s (dense=%dd, sparse=BM25)",
            collection_name, dimension,
        )

        await qdrant_client.create_collection(
            collection_name=collection_name,
            vectors_config={
                "dense": VectorParams(
                    size=dimension,
                    distance=Distance.COSINE,
                ),
            },
            sparse_vectors_config={
                "sparse": SparseVectorParams(),
            },
        )

    async def ingest(
        self,
        markdown: str,
        file_hash: str,
        file_name: str,
        ingest_id: str,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        chunk_separators: list[str] | None = None,
    ) -> int:
        """
        【业务功能】将 Markdown 内容分块、生成 Dense + Sparse 双向量并写入 Qdrant

        业务规则：切块结果为空时抛 ValueError；每个向量写入 ingest_id 属性，支持精准回滚

        Args:
            markdown: 已转换的 Markdown 文本
            file_hash: 文件 SHA-256 哈希
            file_name: 文件名
            ingest_id: 本次入库操作的唯一 ID
            chunk_size/chunk_overlap/chunk_separators: 可选，切分参数覆盖

        Returns:
            实际入库的 chunk 数量

        Raises:
            ValueError: 切块结果为空
        """
        splitter = self._build_splitter(chunk_size, chunk_overlap, chunk_separators)
        chunks = splitter.split_text(markdown)
        if not chunks:
            raise ValueError("文档切块结果为空，无法向量化。")

        self._init_sparse_model()

        qdrant_client = create_qdrant_async_client()
        try:
            await self._ensure_collection(qdrant_client)

            collection_name = agent_config.rag.qdrant_collection
            total = len(chunks)

            for batch_start in range(0, total, _BATCH_SIZE):
                batch_chunks = chunks[batch_start : batch_start + _BATCH_SIZE]
                batch_indices = list(range(batch_start, batch_start + len(batch_chunks)))

                # 生成 dense 向量 (异步 API)
                dense_vectors = await self._embeddings.aembed_documents(batch_chunks)

                # 生成 sparse 向量 (fastembed 为同步，在线程池执行)
                sparse_results = await asyncio.to_thread(
                    lambda b=batch_chunks: list(self._sparse_model.embed(b))
                )

                points = []
                for i, (chunk, idx) in enumerate(zip(batch_chunks, batch_indices)):
                    sparse_vec = sparse_results[i]
                    points.append(
                        PointStruct(
                            id=uuid.uuid4().hex,
                            vector={
                                "dense": dense_vectors[i],
                                "sparse": {
                                    "values": sparse_vec.values.tolist(),
                                    "indices": sparse_vec.indices.tolist(),
                                },
                            },
                            payload={
                                "page_content": chunk,
                                "metadata": {
                                    "file_hash": file_hash,
                                    "file_name": file_name,
                                    "chunk_index": idx,
                                    "ingest_id": ingest_id,
                                },
                            },
                        )
                    )

                await qdrant_client.upsert(
                    collection_name=collection_name,
                    points=points,
                )

            logger.info(
                "向量化入库完成: file_hash=%s ingest_id=%s chunks=%d",
                file_hash, ingest_id, total,
            )
            return total
        finally:
            await qdrant_client.close()

    async def get_chunks_by_file_hash(self, file_hash: str) -> list[str]:
        """
        【业务功能】按 file_hash 查询文件的所有分块，按 chunk_index 排序

        Args:
            file_hash: 文件 SHA-256 哈希

        Returns:
            有序分块文本列表
        """
        qdrant_client = create_qdrant_async_client()
        try:
            points, _ = await qdrant_client.scroll(
                collection_name=agent_config.rag.qdrant_collection,
                scroll_filter=Filter(
                    must=[FieldCondition(key="metadata.file_hash", match=MatchValue(value=file_hash))]
                ),
                limit=1000,
                with_payload=True,
                with_vectors=False,
            )
            sorted_points = sorted(
                points,
                key=lambda p: p.payload.get("metadata", {}).get("chunk_index", 0),
            )
            return [p.payload.get("page_content", "") or "" for p in sorted_points]
        finally:
            await qdrant_client.close()

    async def delete_by_ingest_id(self, ingest_id: str) -> None:
        """
        【业务功能】按 ingest_id 精准删除本次入库写入的向量，用于并发安全的补偿回滚

        业务规则：仅删除本次入库操作写入的向量，不影响并发请求已写入的其他向量；删除失败仅记录日志

        Args:
            ingest_id: 入库操作唯一 ID
        """
        qdrant_client = create_qdrant_async_client()
        try:
            await qdrant_client.delete(
                collection_name=agent_config.rag.qdrant_collection,
                points_selector=Filter(
                    must=[
                        FieldCondition(
                            key="metadata.ingest_id",
                            match=MatchValue(value=ingest_id),
                        )
                    ]
                ),
            )
            logger.info("Qdrant 精准回滚完成: ingest_id=%s", ingest_id)
        except Exception:
            logger.exception("Qdrant 精准回滚失败: ingest_id=%s", ingest_id)
        finally:
            await qdrant_client.close()

    async def delete_by_file_hash(self, file_hash: str) -> None:
        """
        【业务功能】按 file_hash 删除文件对应的全部向量，用于用户主动删除文件时同步清理 Qdrant

        业务规则：删除该文件所有分块向量；删除失败不影响 PostgreSQL 删除结果，仅记录日志

        Args:
            file_hash: 文件 SHA-256 哈希
        """
        qdrant_client = create_qdrant_async_client()
        try:
            await qdrant_client.delete(
                collection_name=agent_config.rag.qdrant_collection,
                points_selector=Filter(
                    must=[
                        FieldCondition(
                            key="metadata.file_hash",
                            match=MatchValue(value=file_hash),
                        )
                    ]
                ),
            )
            logger.info("Qdrant 文件向量删除完成: file_hash=%s", file_hash)
        except Exception:
            logger.exception("Qdrant 文件向量删除失败: file_hash=%s", file_hash)
        finally:
            await qdrant_client.close()


_vector_ingest_service: VectorIngestService | None = None


def get_vector_ingest_service() -> VectorIngestService:
    """
    【业务功能】获取向量入库服务全局单例

    业务规则：全应用共享同一实例，避免重复初始化 Embedding 模型

    Returns:
        VectorIngestService 实例
    """
    global _vector_ingest_service
    if _vector_ingest_service is None:
        _vector_ingest_service = VectorIngestService()
    return _vector_ingest_service
