from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config.settings import settings
from app.utils.logger import setup_logging, get_logger

setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def _lifespan(application: FastAPI):
    """FastAPI 启动时连接 MCP 服务，关闭时断开。

    MCP 连接失败不阻塞应用启动，时间相关工具调用时再降级处理。
    """
    from mcp_servers.mcp_client import McpSessionManager

    try:
        async with McpSessionManager.get() as mcp:
            logger.info("[mcp] 已连接到 TimeService SSE 服务")
            yield
    except Exception:
        logger.warning("[mcp] MCP TimeService 连接失败，时间工具暂不可用", exc_info=True)
        yield
    logger.info("[mcp] 已断开 TimeService SSE 连接")


def create_application() -> FastAPI:
    """创建并初始化 FastAPI 应用。"""
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=_lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origins_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    application.include_router(api_router, prefix="/api/v1")
    return application


app = create_application()
