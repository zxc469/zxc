"""Agent/RAG 专项服务：向量入库、混合检索、重排序。"""

from pathlib import Path

# fastembed 模型缓存目录（项目根目录 models/）
_MODELS_DIR = str(Path(__file__).resolve().parents[3] / "models")

from app.services.agent.vector_service import (
    VectorIngestService,
    get_vector_ingest_service,
)
from app.services.agent.vector_search_service import (
    VectorSearchService,
    get_vector_search_service,
)

__all__ = [
    "VectorIngestService",
    "VectorSearchService",
    "get_vector_ingest_service",
    "get_vector_search_service",
]
