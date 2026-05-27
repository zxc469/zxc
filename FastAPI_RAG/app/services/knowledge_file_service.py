from __future__ import annotations

import asyncio
import hashlib
import tempfile
import uuid
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import Settings, settings
from app.data_access_service.dao import (
    delete_file_by_id,
    get_file_by_hash,
    list_files,
    save_file_meta,
)
from app.db.models import KnowledgeFileRecord, SaveFileMetaInput
from app.utils.document_processing import DocumentConverter, get_document_converter
from app.utils.logger import get_logger
from app.services.agent.vector_service import get_vector_ingest_service

logger = get_logger(__name__)


class KnowledgeFileService:
    def __init__(
        self,
        converter: DocumentConverter | None = None,
        app_settings: Settings = settings,
    ) -> None:
        self._converter = converter or get_document_converter()
        self._settings = app_settings

    async def async_convert_file(self, upload_file: UploadFile) -> tuple[str, str, int, str, str]:
        """
        【业务功能】读取上传文件、校验格式大小，并转换为 Markdown 文本
        业务规则：1. 文件大小不超过配置限制  2. MIME 类型必须在白名单内  3. .md/.txt 直接解码，其他格式通过转换器处理
        参数：upload_file: FastAPI UploadFile 对象
        返回：(file_name, file_hash, file_size, file_type, markdown) 元组
        异常：ValueError: 文件大小/类型/格式不合规
        """
        file_name = upload_file.filename or "uploaded_file"
        file_bytes = await upload_file.read()
        content_type = upload_file.content_type or ""

        # 验证文件大小与 MIME 类型
        if len(file_bytes) > self._settings.markitdown_max_file_size_mb * 1024 * 1024:
            raise ValueError("文件大小超出限制。")
        if content_type and content_type not in self._settings.markitdown_supported_mime_list:
            raise ValueError("文件类型不受支持。")

        file_hash = self._calculate_sha256(file_bytes)
        file_type = self._resolve_file_type(file_name, content_type)

        # 转换为 Markdown（.md/.txt 直接解码；其他格式写临时文件后调用转换器）
        try:
            suffix = Path(file_name).suffix.lower()
            if suffix in (".md", ".txt"):
                markdown = file_bytes.decode("utf-8", errors="replace")
            else:
                def _convert() -> str:
                    with tempfile.TemporaryDirectory(
                        prefix="knowledge_ingest_", dir=self._settings.tmp_dir or None
                    ) as tmp_dir:
                        file_path = Path(tmp_dir) / f"upload{suffix}"
                        file_path.write_bytes(file_bytes)
                        return self._converter.convert_to_markdown(str(file_path))
                markdown = await asyncio.to_thread(_convert)
        except Exception as exc:
            logger.exception("markitdown 转换失败: file_name=%s", file_name)
            raise ValueError("文件转换失败，请检查文件格式。") from exc

        return file_name, file_hash, len(file_bytes), file_type, markdown

    async def async_ingest_file(
        self,
        file_name: str,
        file_hash: str,
        file_size: int,
        file_type: str,
        markdown: str,
        db: AsyncSession,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        chunk_separators: list[str] | None = None,
    ) -> tuple[str, str, KnowledgeFileRecord]:
        """
        【业务功能】将 Markdown 内容向量化入 Qdrant，并将文件元数据写入 PostgreSQL
        业务规则：1. 同 file_hash 已存在则直接返回  2. 每次入库生成 ingest_id，Qdrant 先写，失败中止  3. PostgreSQL 写失败或并发冲突均按 ingest_id 精准回滚 Qdrant  4. 并发冲突回滚后返回已有记录
        参数：file_name/file_hash/file_size/file_type/markdown: 转换阶段产出；db: 数据库会话
              chunk_size/chunk_overlap/chunk_separators: 可选，切分参数覆盖
        返回：(code, message, KnowledgeFileRecord) 元组
        异常：ValueError: 切块为空；Exception: 向量化或数据库写入失败
        """
        existed = await get_file_by_hash(file_hash, db)
        if existed:
            return "ALREADY_EXISTS", "文件已存在，跳过入库。", existed

        # 每次入库操作生成唯一 ID，用于 Qdrant 层精准回滚（避免并发时回滚其他请求已写入的向量）
        ingest_id = str(uuid.uuid4())
        #向量化入 Qdrant（先于 PostgreSQL，失败则整个入库中止）
        total_chunks = await get_vector_ingest_service().ingest(
            markdown, file_hash, file_name, ingest_id=ingest_id,
            chunk_size=chunk_size, chunk_overlap=chunk_overlap, chunk_separators=chunk_separators,
        )

        #持久化 Markdown 文件（非关键路径，失败不中断入库）
        if markdown and self._settings.markdown_output_dir:
            try:
                output_dir = Path(self._settings.markdown_output_dir)
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / f"{file_hash}.md").write_text(markdown, encoding="utf-8")
            except Exception:
                logger.exception("Markdown 文件持久化失败，已忽略: file_hash=%s", file_hash)

        #写元数据到 PostgreSQL
        payload = SaveFileMetaInput(
            file_name=file_name,
            file_hash=file_hash,
            file_type=file_type,
            file_size=file_size,
            status="success",
            total_chunks=total_chunks,
            error_msg=None,
        )
        try:
            record = await save_file_meta(payload, db)
        except IntegrityError:
            # 并发写入触发唯一约束冲突：DAO 已完成 DB 回滚，此处仅清理 Qdrant 向量
            logger.warning("并发入库冲突，回滚本次写入的 Qdrant 向量: ingest_id=%s file_hash=%s", ingest_id, file_hash)
            await get_vector_ingest_service().delete_by_ingest_id(ingest_id)
            existed = await get_file_by_hash(file_hash, db)
            if existed:
                return "ALREADY_EXISTS", "并发入库冲突，文件已存在。", existed
            raise
        except Exception:
            # PostgreSQL 写入失败：DAO 已完成 DB 回滚，此处仅清理 Qdrant 向量
            logger.exception("PostgreSQL 元数据写入失败，尝试精准补偿回滚 Qdrant: ingest_id=%s file_hash=%s", ingest_id, file_hash)
            await get_vector_ingest_service().delete_by_ingest_id(ingest_id)
            raise

        return "SUCCESS", "文件入库成功。", record

    async def async_list_files(self, db: AsyncSession, *, limit: int = 100, offset: int = 0) -> list[KnowledgeFileRecord]:
        """
        【业务功能】分页获取知识库文件元数据列表
        业务规则：按创建时间倒序，支持分页
        参数：db: 数据库会话；limit: 每页数量；offset: 偏移量
        返回：list[KnowledgeFileRecord]
        """
        return await list_files(limit=limit, offset=offset, db=db)

    async def async_delete_file_by_id(self, file_id: int, db: AsyncSession) -> KnowledgeFileRecord | None:
        """
        【业务功能】根据文件 ID 删除知识库文件元数据记录并同步清除 Qdrant 向量
        业务规则：先删除 PostgreSQL 元数据，再按 file_hash 清除 Qdrant 向量；文件不存在时返回 None
        参数：file_id: 文件 ID；db: 数据库会话
        返回：KnowledgeFileRecord（删除的记录）或 None
        """
        record = await delete_file_by_id(file_id, db)
        if record is not None:
            await get_vector_ingest_service().delete_by_file_hash(record.file_hash)
        return record

    def _resolve_file_type(self, file_name: str, content_type: str) -> str:
        """兜底逻辑：file_name 无扩展名时回退到 MIME 类型。"""
        suffix = Path(file_name).suffix.lstrip(".").lower()
        if suffix:
            return suffix
        return content_type.split("/")[-1] if "/" in content_type else "unknown"

    def _calculate_sha256(self, file_bytes: bytes) -> str:
        """计算 SHA-256 哈希值。"""
        return hashlib.sha256(file_bytes).hexdigest()


_knowledge_file_service: KnowledgeFileService | None = None


def get_knowledge_file_service() -> KnowledgeFileService:
    """获取知识文件服务单例。"""
    global _knowledge_file_service
    if _knowledge_file_service is None:
        _knowledge_file_service = KnowledgeFileService()
    return _knowledge_file_service
