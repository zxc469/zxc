"""Qdrant 配置模型与环境变量加载。"""

from __future__ import annotations

from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


class QdrantSettings(BaseSettings):
    """Qdrant 客户端连接配置。"""

    host: str = Field(default="localhost", validation_alias=AliasChoices("QDRANT_HOST"))
    port: int = Field(default=6333, validation_alias=AliasChoices("QDRANT_PORT"))
    grpc_port: int = Field(default=6334, validation_alias=AliasChoices("QDRANT_GRPC_PORT"))
    url: str = Field(default="", validation_alias=AliasChoices("QDRANT_URL"))
    api_key: str = Field(default="", validation_alias=AliasChoices("QDRANT_API_KEY"))
    tls: bool = Field(default=False, validation_alias=AliasChoices("QDRANT_TLS"))
    prefer_grpc: bool = Field(default=False, validation_alias=AliasChoices("QDRANT_PREFER_GRPC"))
    timeout_seconds: float = 30.0
    
    model_config = SettingsConfigDict(env_file=str(ENV_FILE), env_file_encoding="utf-8", extra="ignore")

    @property
    def resolved_url(self) -> str:
        if self.url.strip():
            return self.url.strip()
        scheme = "https" if self.tls else "http"
        return f"{scheme}://{self.host}:{self.port}"

    @field_validator("port", "grpc_port")
    @classmethod
    def _validate_ports(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("QDRANT_PORT / QDRANT_GRPC_PORT 必须大于 0。")
        return value

    @field_validator("timeout_seconds")
    @classmethod
    def _validate_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("QDRANT_TIMEOUT_SECONDS 必须大于 0。")
        return value

    @field_validator("api_key")
    @classmethod
    def _normalize_api_key(cls, value: str) -> str:
        clean = value.strip()
        if clean.startswith("#"):
            return ""
        return clean

