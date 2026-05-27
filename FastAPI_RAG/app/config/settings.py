from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """应用配置：敏感信息从 .env 读取，非敏感信息在此硬编码。"""

    app_name: str = "FastAPI RAG Minimal Demo"
    app_version: str = "0.1.0"

    # -- JWT 鉴权 --
    jwt_secret_key: str = Field(
        default="",
        validation_alias=AliasChoices("JWT_SECRET_KEY"),
        description="JWT 签名密钥（敏感，从 .env 加载）",
    )
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14

    # -- CORS --
    cors_allow_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # -- 知识库 / 文件上传 --
    tmp_dir: str = Field(
        default="",
        validation_alias=AliasChoices("KNOWLEDGE_TMP_DIR"),
        description="临时目录",
    )
    markdown_output_dir: str = Field(
        default="",
        validation_alias=AliasChoices("KNOWLEDGE_MARKDOWN_OUTPUT_DIR"),
        description="Markdown 持久化输出目录，留空则不落盘",
    )
    markitdown_max_file_size_mb: int = 20
    markitdown_supported_mime_types: str = (
        "application/pdf,"
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
        "text/plain,text/markdown,text/csv,"
        "application/octet-stream,application/x-md"
    )

    model_config = SettingsConfigDict(env_file=str(ENV_FILE), env_file_encoding="utf-8", extra="ignore")

    @field_validator("markitdown_max_file_size_mb")
    @classmethod
    def _validate_markitdown_max_file_size_mb(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("MARKITDOWN_MAX_FILE_SIZE_MB 必须大于 0。")
        return value

    @field_validator("markitdown_supported_mime_types")
    @classmethod
    def _validate_markitdown_supported_mime_types(cls, value: str) -> str:
        items = [item.strip() for item in value.split(",") if item.strip()]
        if not items:
            raise ValueError("MARKITDOWN_SUPPORTED_MIME_TYPES 不能为空。")
        invalid = [item for item in items if "/" not in item]
        if invalid:
            raise ValueError(f"MIME 格式非法: {invalid}")
        return ",".join(items)

    @field_validator("jwt_secret_key")
    @classmethod
    def _validate_jwt_secret_key(cls, value: str) -> str:
        if not value or len(value) < 32:
            raise ValueError("JWT_SECRET_KEY 必须设置且长度不少于 32 字符。生产环境请使用 `python -c \"import secrets; print(secrets.token_urlsafe(48))\"` 生成。")
        return value

    @property
    def cors_allow_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def markitdown_supported_mime_list(self) -> list[str]:
        return [item.strip() for item in self.markitdown_supported_mime_types.split(",") if item.strip()]


settings = Settings()

if __name__ == "__main__":
    print(settings.tmp_dir)
