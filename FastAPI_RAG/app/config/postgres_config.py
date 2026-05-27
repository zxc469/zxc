"""PostgreSQL 配置读取。

作用：
- 从 `.env` 读取 PostgreSQL 连接和连接池参数；
- 给 `pool.py` 提供统一配置对象，避免散落读取环境变量。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class PostgresSettings(BaseSettings):
    """PostgreSQL 相关配置模型。"""
    # 连接字符串
    postgres_dsn: str = Field(
        default="",
        validation_alias=AliasChoices("POSTGRES_DSN"),
    )
    # 连接池最小大小
    pg_pool_min_size: int = Field(default=1, validation_alias=AliasChoices("PG_POOL_MIN_SIZE"))
    # 连接池最大大小
    pg_pool_max_size: int = Field(default=10, validation_alias=AliasChoices("PG_POOL_MAX_SIZE"))
    # 连接池获取超时时间
    pg_pool_acquire_timeout_seconds: float = Field(
        default=10.0,
        validation_alias=AliasChoices("PG_POOL_ACQUIRE_TIMEOUT_SECONDS", "PG_POOL_TIMEOUT_SECONDS"),
    )
    # 连接池空闲超时时间
    pg_pool_idle_timeout_seconds: float = Field(
        default=60.0,
        validation_alias=AliasChoices("PG_POOL_IDLE_TIMEOUT_SECONDS", "PG_POOL_IDLE_TIMEOUT"),
    )
    # 连接池连接超时时间
    pg_pool_connect_timeout_seconds: float = Field(
        default=10.0,
        validation_alias=AliasChoices("PG_POOL_CONNECT_TIMEOUT_SECONDS", "PG_POOL_CONNECT_TIMEOUT"),
    )
    

    model_config = SettingsConfigDict(env_file=str(ENV_FILE), env_file_encoding="utf-8", extra="ignore")


# 全局配置实例，模块加载时即读取 .env
postgres_settings = PostgresSettings()
