"""knowledge_files 数据访问层。"""

from __future__ import annotations

from fastapi import Depends
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import KnowledgeFileRecord, SaveFileMetaInput
from app.db.orm import KnowledgeFileORM
from app.db.postgres_pool import get_db, postgres_pool_manager

# 内部函数，将 ORM 对象转换为 KnowledgeFileRecord
def _to_record(row: KnowledgeFileORM) -> KnowledgeFileRecord:
    return KnowledgeFileRecord.model_validate(
        {
            "id": row.id,
            "file_name": row.file_name,
            "file_hash": row.file_hash,
            "file_type": row.file_type,
            "file_size": row.file_size,
            "status": row.status,
            "total_chunks": row.total_chunks,
            "error_msg": row.error_msg,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    )


async def save_file_meta(
    payload: SaveFileMetaInput,
    db: AsyncSession = Depends(get_db),
    ) -> KnowledgeFileRecord:
    """
    【数据操作】按 file_hash 存在性执行 upsert（不存在则插入，存在则更新），成功后提交事务
    操作/查询条件：- KnowledgeFileORM.file_hash == payload.file_hash
    参数：session: 数据库会话；payload: SaveFileMetaInput，文件元数据输入
    返回：KnowledgeFileRecord，持久化后的文件记录
    异常：IntegrityError: 并发冲突（唯一约束）；Exception: 其他写入失败；均已完成 rollback 后向上抛出
    """
    try:
        stmt = select(KnowledgeFileORM).where(KnowledgeFileORM.file_hash == payload.file_hash)
        row = await db.scalar(stmt)
        if row is None:
            row = KnowledgeFileORM()
            db.add(row)
        row.file_name = payload.file_name
        row.file_hash = payload.file_hash
        row.file_type = payload.file_type
        row.file_size = payload.file_size
        row.status = payload.status
        row.total_chunks = payload.total_chunks
        row.error_msg = payload.error_msg
        await db.flush()
        await db.refresh(row)
        result = _to_record(row)
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        raise


async def get_file_by_hash(
    file_hash: str,
    db: AsyncSession = Depends(get_db),
    ) -> KnowledgeFileRecord | None:
    """
    【数据操作】按 file_hash 查询单条文件元数据记录
    操作/查询条件：- KnowledgeFileORM.file_hash == file_hash
    参数：session: 数据库会话；file_hash: 文件 SHA-256 哈希
    返回：KnowledgeFileRecord 或 None（不存在时）
    """
    row = await db.scalar(select(KnowledgeFileORM).where(KnowledgeFileORM.file_hash == file_hash))
    return _to_record(row) if row else None

async def get_file_by_id(
    file_id: int,
    db: AsyncSession = Depends(get_db),
) -> KnowledgeFileRecord | None:
    """
    【数据操作】按 ID 查询单条文件元数据记录
    操作/查询条件：- KnowledgeFileORM.id == file_id
    参数：session: 数据库会话；file_id: 文件 ID
    返回：KnowledgeFileRecord 或 None（不存在时）
    """
    row = await db.scalar(select(KnowledgeFileORM).where(KnowledgeFileORM.id == file_id))
    return _to_record(row) if row else None


async def list_files(
    *,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    ) -> list[KnowledgeFileRecord]:
    """
    【数据操作】按创建时间倒序分页查询文件元数据列表
    操作/查询条件：- 全表查询，按 created_at 倒序，offset/limit 分页
    参数：session: 数据库会话；limit: 查询数量上限；offset: 分页偏移
    返回：list[KnowledgeFileRecord]
    """
    stmt = select(KnowledgeFileORM).order_by(KnowledgeFileORM.created_at.desc()).offset(offset).limit(limit)
    rows = (await db.scalars(stmt)).all()
    return [_to_record(row) for row in rows]

async def delete_file_by_id(
    file_id: int,
    db: AsyncSession = Depends(get_db),
    ) -> KnowledgeFileRecord | None:
    """
    【数据操作】按 ID 删除文件元数据记录并返回被删数据，成功后提交事务
    操作/查询条件：- KnowledgeFileORM.id == file_id
    参数：session: 数据库会话；file_id: 文件 ID
    返回：KnowledgeFileRecord（被删记录）或 None（不存在时）
    异常：Exception: 删除失败，已完成 rollback 后向上抛出
    """
    try:
        stmt = delete(KnowledgeFileORM).where(KnowledgeFileORM.id == file_id).returning(KnowledgeFileORM)
        row = (await db.scalars(stmt)).first()
        result = _to_record(row) if row else None
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        raise

