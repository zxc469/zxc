"""会话管理数据访问层。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import Depends
from sqlalchemy import select, update, delete, func, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.orm import (
    ChatSessionORM,
    SessionMessageORM,
    AgentSessionQuotaORM,
)
from app.db.postgres_pool import get_db
from app.db.session_models import (
    SessionCreateInput,
    SessionUpdateInput,
    SessionRecord,
    MessageCreateInput,
    MessageRecord,
    AgentQuotaRecord,
)


def _to_session_record(row: ChatSessionORM) -> SessionRecord:
    """将 ORM 对象转换为 SessionRecord。"""
    return SessionRecord.model_validate({
        "id": row.id,
        "session_no": row.session_no,
        "user_id": row.user_id,
        "agent_id": row.agent_id,
        "status": row.status,
        "handled_by": row.handled_by,
        "source": row.source,
        "priority": row.priority,
        "created_at": row.created_at,
        "assigned_at": row.assigned_at,
        "active_at": row.active_at,
        "closed_at": row.closed_at,
        "closed_by": row.closed_by,
        "close_reason": row.close_reason,
        "rating": row.rating,
        "rating_comment": row.rating_comment,
        "updated_at": row.updated_at,
    })


def _to_message_record(row: SessionMessageORM) -> MessageRecord:
    """将 ORM 对象转换为 MessageRecord。"""
    return MessageRecord.model_validate({
        "id": row.id,
        "session_id": row.session_id,
        "sender_type": row.sender_type,
        "sender_id": row.sender_id,
        "message_type": row.message_type,
        "content": row.content,
        "is_read": row.is_read,
        "read_at": row.read_at,
        "created_at": row.created_at,
        "metadata": row._metadata,
    })


# ==================== 会话操作 ====================

async def create_session(
    payload: SessionCreateInput,
    session_no: str,
    db: AsyncSession,
) -> SessionRecord:
    """
    【数据操作】创建新会话记录
    操作/查询条件：- 插入新记录到 chat_sessions 表
    参数：db: 数据库会话；payload: 会话创建输入数据；session_no: 会话编号
    返回：SessionRecord，创建后的会话记录
    异常：Exception: 写入失败，已完成 rollback 后向上抛出
    """
    try:
        row = ChatSessionORM(
            session_no=session_no,
            user_id=payload.user_id,
            source=payload.source,
            priority=payload.priority,
            status="waiting",
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        result = _to_session_record(row)
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        raise


async def create_session_with_agent(
    payload: SessionCreateInput,
    session_no: str,
    agent_id: Optional[int],
    status: str,
    assigned_at: Optional[datetime],
    db: AsyncSession,
) -> SessionRecord:
    """
    【数据操作】创建新会话记录（带客服分配）
    操作/查询条件：- 插入新记录到 chat_sessions 表
    参数：db: 数据库会话；payload: 会话创建输入数据；session_no: 会话编号；
          agent_id: 客服ID（可为空）；status: 会话状态；assigned_at: 分配时间
    返回：SessionRecord，创建后的会话记录
    异常：Exception: 写入失败，已完成 rollback 后向上抛出
    """
    try:
        row = ChatSessionORM(
            session_no=session_no,
            user_id=payload.user_id,
            agent_id=agent_id,
            source=payload.source,
            priority=payload.priority,
            status=status,
            handled_by="ai",
            assigned_at=assigned_at,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        result = _to_session_record(row)
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        raise


async def get_session_by_id(
    session_id: int,
    db: AsyncSession,
) -> Optional[SessionRecord]:
    """
    【数据操作】按 ID 查询会话记录
    操作/查询条件：- ChatSessionORM.id == session_id
    参数：db: 数据库会话；session_id: 会话 ID
    返回：SessionRecord 或 None（不存在时）
    """
    stmt = select(ChatSessionORM).where(ChatSessionORM.id == session_id)
    row = await db.scalar(stmt)
    return _to_session_record(row) if row else None


async def get_session_by_no(
    session_no: str,
    db: AsyncSession,
) -> Optional[SessionRecord]:
    """
    【数据操作】按会话编号查询会话记录
    操作/查询条件：- ChatSessionORM.session_no == session_no
    参数：db: 数据库会话；session_no: 会话编号
    返回：SessionRecord 或 None（不存在时）
    """
    stmt = select(ChatSessionORM).where(ChatSessionORM.session_no == session_no)
    row = await db.scalar(stmt)
    return _to_session_record(row) if row else None


async def list_user_sessions(
    user_id: int,
    *,
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession,
) -> list[SessionRecord]:
    """
    【数据操作】查询用户的会话列表（按创建时间倒序）
    操作/查询条件：- ChatSessionORM.user_id == user_id；可选 status 过滤
    参数：db: 数据库会话；user_id: 用户 ID；status: 会话状态过滤；limit: 查询数量上限；offset: 分页偏移
    返回：list[SessionRecord]
    """
    stmt = select(ChatSessionORM).where(ChatSessionORM.user_id == user_id)
    if status:
        stmt = stmt.where(ChatSessionORM.status == status)
    stmt = stmt.order_by(ChatSessionORM.created_at.desc()).offset(offset).limit(limit)
    rows = (await db.scalars(stmt)).all()
    return [_to_session_record(row) for row in rows]


async def list_agent_sessions(
    agent_id: int,
    *,
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession,
) -> list[SessionRecord]:
    """
    【数据操作】查询客服的会话列表（按创建时间倒序）
    操作/查询条件：- 属于该客服的会话 (agent_id == agent_id)
                  - 或等待分配池中的会话 (agent_id IS NULL AND status = 'waiting')
                  - 可选 status 过滤
    参数：db: 数据库会话；agent_id: 客服 ID；status: 会话状态过滤；limit: 查询数量上限；offset: 分页偏移
    返回：list[SessionRecord]
    """
    stmt = select(ChatSessionORM).where(
        or_(
            ChatSessionORM.agent_id == agent_id,
            and_(ChatSessionORM.agent_id.is_(None), ChatSessionORM.status == 'waiting'),
        )
    )
    if status:
        stmt = stmt.where(ChatSessionORM.status == status)
    stmt = stmt.order_by(ChatSessionORM.created_at.desc()).offset(offset).limit(limit)
    rows = (await db.scalars(stmt)).all()
    return [_to_session_record(row) for row in rows]


async def update_session(
    session_id: int,
    payload: SessionUpdateInput,
    db: AsyncSession,
) -> Optional[SessionRecord]:
    """
    【数据操作】更新会话记录
    操作/查询条件：- ChatSessionORM.id == session_id
    参数：db: 数据库会话；session_id: 会话 ID；payload: 更新数据
    返回：SessionRecord 或 None（不存在时）
    异常：Exception: 更新失败，已完成 rollback 后向上抛出
    """
    try:
        stmt = select(ChatSessionORM).where(ChatSessionORM.id == session_id)
        row = await db.scalar(stmt)
        if not row:
            return None

        update_data = payload.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(row, key, value)

        await db.flush()
        await db.refresh(row)
        result = _to_session_record(row)
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        raise


async def claim_session_for_transfer(
    session_id: int,
    db: AsyncSession,
) -> Optional[SessionRecord]:
    """
    【数据操作】原子认领会话：仅当 handled_by='ai' 时才更新为 agent+waiting。

    使用 UPDATE ... WHERE handled_by='ai' RETURNING * 消除
    get_session_detail 检查与 update_session 写入之间的 TOCTOU 窗口。
    并发请求中只有一条能成功认领，其余返回 None。

    Args:
        session_id: 会话 ID
        db: 数据库会话

    Returns:
        SessionRecord 认领成功，None 表示已被其他请求认领或会话不存在
    """
    try:
        stmt = (
            update(ChatSessionORM)
            .where(ChatSessionORM.id == session_id, ChatSessionORM.handled_by == "ai")
            .values(handled_by="agent", status="waiting", updated_at=datetime.now())
            .returning(ChatSessionORM)
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        await db.commit()
        if row is None:
            return None
        await db.refresh(row)
        return _to_session_record(row)
    except Exception:
        await db.rollback()
        raise


async def count_user_sessions(
    user_id: int,
    *,
    status: Optional[str] = None,
    db: AsyncSession,
) -> int:
    """
    【数据操作】统计用户的会话数量
    操作/查询条件：- ChatSessionORM.user_id == user_id；可选 status 过滤
    参数：db: 数据库会话；user_id: 用户 ID；status: 会话状态过滤
    返回：会话数量
    """
    stmt = select(func.count(ChatSessionORM.id)).where(ChatSessionORM.user_id == user_id)
    if status:
        stmt = stmt.where(ChatSessionORM.status == status)
    return (await db.scalar(stmt)) or 0


async def count_agent_sessions(
    agent_id: int,
    *,
    status: Optional[str] = None,
    db: AsyncSession,
) -> int:
    """
    【数据操作】统计客服的会话数量
    操作/查询条件：- 属于该客服的会话 (agent_id == agent_id)
                  - 或等待分配池中的会话 (agent_id IS NULL AND status = 'waiting')
    参数：db: 数据库会话；agent_id: 客服 ID；status: 会话状态过滤
    返回：会话数量
    """
    stmt = select(func.count(ChatSessionORM.id)).where(
        or_(
            ChatSessionORM.agent_id == agent_id,
            and_(ChatSessionORM.agent_id.is_(None), ChatSessionORM.status == 'waiting'),
        )
    )
    if status:
        stmt = stmt.where(ChatSessionORM.status == status)
    return (await db.scalar(stmt)) or 0


# ==================== 消息操作 ====================

async def create_message(
    payload: MessageCreateInput,
    db: AsyncSession,
) -> MessageRecord:
    """
    【数据操作】创建新消息记录
    操作/查询条件：- 插入新记录到 session_messages 表
    参数：db: 数据库会话；payload: 消息创建输入数据
    返回：MessageRecord，创建后的消息记录
    异常：Exception: 写入失败，已完成 rollback 后向上抛出
    """
    try:
        row = SessionMessageORM(
            session_id=payload.session_id,
            sender_type=payload.sender_type,
            sender_id=payload.sender_id,
            message_type=payload.message_type,
            content=payload.content,
            metadata=payload.metadata,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        result = _to_message_record(row)
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        raise


async def save_user_message(
    session_id: int,
    content: str,
    db: AsyncSession,
    sender_id: int | None = None,
) -> MessageRecord:
    """
    保存用户消息到 session_messages 表。

    Args:
        session_id: 会话 ID
        content: 消息文本
        db: 数据库会话
        sender_id: 发送者用户 ID，匿名用户可为 None

    Returns:
        MessageRecord，创建后的消息记录
    """
    return await create_message(
        MessageCreateInput(
            session_id=session_id,
            sender_type="user",
            sender_id=sender_id,
            message_type="text",
            content=content,
        ),
        db=db,
    )


async def save_ai_message(
    session_id: int,
    content: str,
    db: AsyncSession,
) -> MessageRecord:
    """
    保存 AI 回复到 session_messages 表。

    Args:
        session_id: 会话 ID
        content: AI 回复文本
        db: 数据库会话

    Returns:
        MessageRecord，创建后的消息记录
    """
    return await create_message(
        MessageCreateInput(
            session_id=session_id,
            sender_type="ai",
            message_type="text",
            content=content,
        ),
        db=db,
    )


async def list_session_messages(
    session_id: int,
    *,
    limit: int = 50,
    offset: int = 0,
    before_id: Optional[int] = None,
    db: AsyncSession,
) -> list[MessageRecord]:
    """
    【数据操作】查询会话的消息列表（按创建时间正序）
    操作/查询条件：- SessionMessageORM.session_id == session_id；可选 before_id 加载更早消息
    参数：db: 数据库会话；session_id: 会话 ID；limit: 查询数量上限；offset: 分页偏移；before_id: 加载此 ID 之前的消息
    返回：list[MessageRecord]
    """
    stmt = select(SessionMessageORM).where(SessionMessageORM.session_id == session_id)
    if before_id:
        stmt = stmt.where(SessionMessageORM.id < before_id)
    stmt = stmt.order_by(SessionMessageORM.created_at.asc()).offset(offset).limit(limit)
    rows = (await db.scalars(stmt)).all()
    return [_to_message_record(row) for row in rows]


async def count_session_messages(
    session_id: int,
    db: AsyncSession,
) -> int:
    """
    【数据操作】统计会话的消息数量
    操作/查询条件：- SessionMessageORM.session_id == session_id
    参数：db: 数据库会话；session_id: 会话 ID
    返回：消息数量
    """
    stmt = select(func.count(SessionMessageORM.id)).where(SessionMessageORM.session_id == session_id)
    return (await db.scalar(stmt)) or 0


async def mark_messages_read(
    session_id: int,
    db: AsyncSession,
) -> int:
    """
    【数据操作】标记会话中的所有消息为已读
    操作/查询条件：- SessionMessageORM.session_id == session_id AND is_read == False
    参数：db: 数据库会话；session_id: 会话 ID
    返回：更新的消息数量
    异常：Exception: 更新失败，已完成 rollback 后向上抛出
    """
    try:
        now = datetime.now()
        stmt = (
            update(SessionMessageORM)
            .where(SessionMessageORM.session_id == session_id)
            .where(SessionMessageORM.is_read == False)
            .values(is_read=True, read_at=now)
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount
    except Exception:
        await db.rollback()
        raise


async def get_sessions_enrichment(
    session_ids: list[int],
    db: AsyncSession,
) -> dict[int, dict]:
    """
    【数据操作】批量查询会话的未读数和最新一条消息。

    一次查询返回每个 session 的 unread_count、last_message、last_message_time，
    供会话列表接口使用。

    Args:
        session_ids: 会话 ID 列表
        db: 数据库会话

    Returns:
        {session_id: {unread_count: int, last_message: str|None, last_message_time: datetime|None}}
        空列表时返回空字典
    """
    if not session_ids:
        return {}

    # 未读计数: 按 session_id 分组统计 is_read=FALSE 的消息数
    unread_stmt = (
        select(
            SessionMessageORM.session_id,
            func.count(SessionMessageORM.id).label("unread_count"),
        )
        .where(SessionMessageORM.session_id.in_(session_ids))
        .where(SessionMessageORM.is_read == False)
        .group_by(SessionMessageORM.session_id)
    )
    unread_rows = (await db.execute(unread_stmt)).all()

    # 最新消息: 用窗口函数取每个 session 最新一条
    latest_subq = (
        select(
            SessionMessageORM.session_id,
            SessionMessageORM.content,
            SessionMessageORM.created_at,
            func.row_number()
            .over(
                partition_by=SessionMessageORM.session_id,
                order_by=SessionMessageORM.created_at.desc(),
            )
            .label("rn"),
        )
        .where(SessionMessageORM.session_id.in_(session_ids))
    ).subquery()

    latest_stmt = select(
        latest_subq.c.session_id,
        latest_subq.c.content.label("last_message"),
        latest_subq.c.created_at.label("last_message_time"),
    ).where(latest_subq.c.rn == 1)

    latest_rows = (await db.execute(latest_stmt)).all()

    # 合并结果
    result: dict[int, dict] = {
        sid: {"unread_count": 0, "last_message": None, "last_message_time": None}
        for sid in session_ids
    }
    for row in unread_rows:
        result[row.session_id]["unread_count"] = row.unread_count
    for row in latest_rows:
        result[row.session_id]["last_message"] = row.last_message
        result[row.session_id]["last_message_time"] = row.last_message_time

    return result


# ==================== 客服配额操作 ====================

async def get_agent_quota(
    agent_id: int,
    db: AsyncSession,
) -> Optional[AgentQuotaRecord]:
    """
    【数据操作】查询客服的会话配额
    操作/查询条件：- AgentSessionQuotaORM.agent_id == agent_id
    参数：db: 数据库会话；agent_id: 客服 ID
    返回：AgentQuotaRecord 或 None（不存在时）
    """
    stmt = select(AgentSessionQuotaORM).where(AgentSessionQuotaORM.agent_id == agent_id)
    row = await db.scalar(stmt)
    if not row:
        return None
    return AgentQuotaRecord.model_validate({
        "agent_id": row.agent_id,
        "current_sessions": row.current_sessions,
        "max_sessions": row.max_sessions,
        "last_updated": row.last_updated,
    })


async def get_available_agents(
    db: AsyncSession,
) -> list[AgentQuotaRecord]:
    """
    【数据操作】查询所有可用的客服（未达到最大会话数）
    操作/查询条件：- current_sessions < max_sessions，按 current_sessions 升序排序
    参数：db: 数据库会话
    返回：AgentQuotaRecord 列表，按当前会话数从小到大排序
    """
    stmt = (
        select(AgentSessionQuotaORM)
        .where(AgentSessionQuotaORM.current_sessions < AgentSessionQuotaORM.max_sessions)
        .order_by(AgentSessionQuotaORM.current_sessions.asc())
    )
    rows = await db.execute(stmt)
    return [
        AgentQuotaRecord.model_validate({
            "agent_id": row.agent_id,
            "current_sessions": row.current_sessions,
            "max_sessions": row.max_sessions,
            "last_updated": row.last_updated,
        })
        for row in rows.scalars().all()
    ]


async def create_or_update_agent_quota(
    agent_id: int,
    db: AsyncSession,
    max_sessions: int = 5,
) -> AgentQuotaRecord:
    """
    【数据操作】创建或更新客服的会话配额
    操作/查询条件：- AgentSessionQuotaORM.agent_id == agent_id
    参数：db: 数据库会话；agent_id: 客服 ID；max_sessions: 最大会话数
    返回：AgentQuotaRecord
    异常：Exception: 写入失败，已完成 rollback 后向上抛出
    """
    try:
        stmt = select(AgentSessionQuotaORM).where(AgentSessionQuotaORM.agent_id == agent_id)
        row = await db.scalar(stmt)
        if not row:
            row = AgentSessionQuotaORM(agent_id=agent_id, max_sessions=max_sessions)
            db.add(row)
        else:
            row.max_sessions = max_sessions

        await db.flush()
        await db.refresh(row)
        result = AgentQuotaRecord.model_validate({
            "agent_id": row.agent_id,
            "current_sessions": row.current_sessions,
            "max_sessions": row.max_sessions,
            "last_updated": row.last_updated,
        })
        await db.commit()
        return result
    except Exception:
        await db.rollback()
        raise


async def increment_agent_quota(
    agent_id: int,
    db: AsyncSession,
) -> bool:
    """
    【数据操作】增加客服的当前会话数（如果未超过配额）
    操作/查询条件：- AgentSessionQuotaORM.agent_id == agent_id AND current_sessions < max_sessions
    参数：db: 数据库会话；agent_id: 客服 ID
    返回：True=增加成功，False=已达配额
    异常：Exception: 更新失败，已完成 rollback 后向上抛出
    """
    try:
        stmt = (
            update(AgentSessionQuotaORM)
            .where(AgentSessionQuotaORM.agent_id == agent_id)
            .where(AgentSessionQuotaORM.current_sessions < AgentSessionQuotaORM.max_sessions)
            .values(
                current_sessions=AgentSessionQuotaORM.current_sessions + 1,
                last_updated=datetime.now(),
            )
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount > 0
    except Exception:
        await db.rollback()
        raise


async def decrement_agent_quota(
    agent_id: int,
    db: AsyncSession,
) -> bool:
    """
    【数据操作】减少客服的当前会话数
    操作/查询条件：- AgentSessionQuotaORM.agent_id == agent_id
    参数：db: 数据库会话；agent_id: 客服 ID
    返回：True=减少成功，False=记录不存在
    异常：Exception: 更新失败，已完成 rollback 后向上抛出
    """
    try:
        stmt = (
            update(AgentSessionQuotaORM)
            .where(AgentSessionQuotaORM.agent_id == agent_id)
            .where(AgentSessionQuotaORM.current_sessions > 0)
            .values(
                current_sessions=AgentSessionQuotaORM.current_sessions - 1,
                last_updated=datetime.now(),
            )
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount > 0
    except Exception:
        await db.rollback()
        raise
