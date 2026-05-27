"""Qdrant 客户端和基础连接操作。"""

from __future__ import annotations

from typing import Any

from qdrant_client import AsyncQdrantClient

from app.config.qdrant_config import QdrantSettings


def create_qdrant_async_client(settings: QdrantSettings | None = None) -> AsyncQdrantClient:
    """创建 Qdrant 异步客户端。"""
    cfg = settings or QdrantSettings()
    return AsyncQdrantClient(
        url=cfg.resolved_url,
        api_key=cfg.api_key or None,
        timeout=cfg.timeout_seconds,
        prefer_grpc=cfg.prefer_grpc,
        grpc_port=cfg.grpc_port,
        https=cfg.tls,
    )


class QdrantConnectionService:
    """Qdrant 连接服务。"""
    def __init__(self, client: AsyncQdrantClient, settings: QdrantSettings | None = None) -> None:
        """初始化 Qdrant 连接服务。"""
        self._client = client
        self._settings = settings or QdrantSettings()

    """检查 Qdrant 健康状态。"""
    async def health_check(self) -> dict[str, Any]:
        collections = await self._client.get_collections()
        return {
            "ok": True,
            "resolved_url": self._settings.resolved_url,
            "collection_count": len(collections.collections),
        }
    """关闭 Qdrant 连接。"""
    async def close(self) -> None:
        await self._client.close()


QdrantVectorService = QdrantConnectionService


class QdrantClientFactory:
    """兼容旧导出名称。"""
    def __init__(self, settings: QdrantSettings | None = None) -> None:
        self._settings = settings

    def create_async_client(self) -> AsyncQdrantClient:
        return create_qdrant_async_client(self._settings)

