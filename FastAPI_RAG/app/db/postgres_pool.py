"""PostgreSQL 异步连接与会话管理。"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config.postgres_config import postgres_settings


class PostgresPoolManager:
    """管理 PostgreSQL 异步引擎和会话工厂。"""

    def __init__(self, dsn: str | None = None) -> None:
        self._dsn = (dsn or postgres_settings.postgres_dsn or "").strip()   # 数据库连接字符串
        self._engine: AsyncEngine | None = None  # 异引擎实例
        self._session_maker: async_sessionmaker[AsyncSession] | None = None  # 会话工厂

    async def init(self) -> AsyncEngine:
        """初化引擎（重调用会用已创建的引擎）"""
        if self._engine is not None:
            return self._engine
        # 查连接字符串是否为空
        if not self._dsn:
            raise ValueError("POSTGRES_DSN 未配置")
        if self._dsn.startswith("postgresql://"):
            self._dsn = self._dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
        # 创建异引擎，默认自动创建连接池
        self._engine = create_async_engine(
            self._dsn,
            pool_size=postgres_settings.pg_pool_min_size,
            max_overflow=max(0, postgres_settings.pg_pool_max_size - postgres_settings.pg_pool_min_size),
            pool_timeout=postgres_settings.pg_pool_acquire_timeout_seconds,
            echo=False,
        )
        # 创建会话工厂
        self._session_maker = async_sessionmaker(self._engine, expire_on_commit=False)
        return self._engine

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None
            self._session_maker = None

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """统一数据库会话：读写都过该会话执行"""
        await self.init()
        if self._session_maker is None:
            raise RuntimeError("Session 未初始化")
        async with self._session_maker() as session:
            yield session


postgres_pool_manager = PostgresPoolManager()


async def get_db() -> AsyncIterator[AsyncSession]:
    async with postgres_pool_manager.session() as db:
        yield db

