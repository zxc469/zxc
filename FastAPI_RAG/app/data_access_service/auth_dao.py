"""鉴权模块数据访问层：users / agents / admins / refresh_tokens。

职责分工：
- 按 principal_type 统一路由到对应 ORM 表
- 写操作由本层负责 commit / rollback（遵循"DAO 层全权管理事务"决策）
- 失败时已完成 rollback 后再向上抛出
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.orm import (
    AdminORM,
    AgentORM,
    RefreshTokenORM,
    UserORM,
)

# 三角色表统一路由字典，按 principal_type 选择对应 ORM 类
_PRINCIPAL_ORM: dict[str, type] = {
    "user": UserORM,
    "agent": AgentORM,
    "admin": AdminORM,
}


def _get_orm_class(principal_type: str):
    """根据 principal_type 返回对应的 ORM 类，未知类型抛 ValueError。"""
    orm_cls = _PRINCIPAL_ORM.get(principal_type)
    if orm_cls is None:
        raise ValueError(f"未知的主体类型: {principal_type}")
    return orm_cls


# ============================================================================
# 账号注册 / 查询
# ============================================================================
async def get_principal_by_username(
    principal_type: str,
    username: str,
    db: AsyncSession,
) -> Any | None:
    """
    【数据操作】按 username 查询指定类型主体（user / agent / admin）
    查询条件：- ORM.username == username
    参数：principal_type: 主体类型；username: 登录账号；db: 数据库会话
    返回：ORM 对象 或 None（不存在时）
    """
    orm_cls = _get_orm_class(principal_type)
    return await db.scalar(select(orm_cls).where(orm_cls.username == username))


async def get_principal_by_id(
    principal_type: str,
    principal_id: int,
    db: AsyncSession,
) -> Any | None:
    """
    【数据操作】按主键 ID 查询指定类型主体
    查询条件：- ORM.id == principal_id
    参数：principal_type: 主体类型；principal_id: 主体 ID；db: 数据库会话
    返回：ORM 对象 或 None
    """
    orm_cls = _get_orm_class(principal_type)
    return await db.get(orm_cls, principal_id)


async def create_user(
    *,
    username: str,
    password_hash: str,
    nickname: str | None,
    email: str | None,
    phone: str | None,
    avatar_url: str | None,
    db: AsyncSession,
) -> UserORM:
    """
    【数据操作】插入一条 users 记录
    操作条件：- 无（外层已校验 username 唯一）
    参数：see kwargs；db: 数据库会话
    返回：UserORM，插入后的用户记录（含主键）
    异常：IntegrityError 等写入失败，已完成 rollback 后向上抛出
    """
    try:
        row = UserORM(
            username=username,
            password_hash=password_hash,
            nickname=nickname,
            email=email,
            phone=phone,
            avatar_url=avatar_url,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        await db.commit()
        return row
    except Exception:
        await db.rollback()
        raise


async def create_agent(
    *,
    username: str,
    password_hash: str,
    real_name: str,
    email: str | None,
    phone: str | None,
    department: str | None,
    max_sessions: int,
    db: AsyncSession,
) -> AgentORM:
    """
    【数据操作】插入一条 agents 记录
    参数：see kwargs；db: 数据库会话
    返回：AgentORM，插入后的客服记录
    异常：写入失败，已完成 rollback 后向上抛出
    """
    try:
        row = AgentORM(
            username=username,
            password_hash=password_hash,
            real_name=real_name,
            email=email,
            phone=phone,
            department=department,
            max_sessions=max_sessions,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        await db.commit()
        return row
    except Exception:
        await db.rollback()
        raise


async def create_admin(
    *,
    username: str,
    password_hash: str,
    real_name: str,
    email: str | None,
    role_level: str,
    db: AsyncSession,
) -> AdminORM:
    """
    【数据操作】插入一条 admins 记录
    参数：see kwargs；db: 数据库会话
    返回：AdminORM，插入后的管理员记录
    异常：写入失败，已完成 rollback 后向上抛出
    """
    try:
        row = AdminORM(
            username=username,
            password_hash=password_hash,
            real_name=real_name,
            email=email,
            role_level=role_level,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        await db.commit()
        return row
    except Exception:
        await db.rollback()
        raise


async def update_last_login(
    principal_type: str,
    principal_id: int,
    db: AsyncSession,
) -> None:
    """
    【数据操作】更新指定主体的 last_login_at 为当前时间
    查询条件：- ORM.id == principal_id
    参数：principal_type: 主体类型；principal_id: 主体 ID；db: 数据库会话
    返回：None
    异常：写入失败，已完成 rollback 后向上抛出
    """
    orm_cls = _get_orm_class(principal_type)
    try:
        stmt = update(orm_cls).where(orm_cls.id == principal_id).values(last_login_at=datetime.now(timezone.utc))
        await db.execute(stmt)
        await db.commit()
    except Exception:
        await db.rollback()
        raise


# ============================================================================
# refresh_tokens 管理
# ============================================================================
async def create_refresh_token(
    *,
    principal_type: str,
    principal_id: int,
    token_hash: str,
    expires_at: datetime,
    user_agent: str | None,
    ip_address: str | None,
    db: AsyncSession,
) -> RefreshTokenORM:
    """
    【数据操作】插入一条 refresh_tokens 记录
    参数：see kwargs；db: 数据库会话
    返回：RefreshTokenORM，插入后的令牌记录
    异常：写入失败，已完成 rollback 后向上抛出
    """
    try:
        row = RefreshTokenORM(
            principal_type=principal_type,
            principal_id=principal_id,
            token_hash=token_hash,
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        db.add(row)
        await db.flush()
        await db.refresh(row)
        await db.commit()
        return row
    except Exception:
        await db.rollback()
        raise


async def get_refresh_token_by_hash(
    token_hash: str,
    db: AsyncSession,
) -> RefreshTokenORM | None:
    """
    【数据操作】按 token_hash 查询 refresh token 记录
    查询条件：- RefreshTokenORM.token_hash == token_hash
    参数：token_hash: SHA256 摘要；db: 数据库会话
    返回：RefreshTokenORM 或 None
    """
    return await db.scalar(
        select(RefreshTokenORM).where(RefreshTokenORM.token_hash == token_hash)
    )


async def revoke_refresh_token_by_hash(
    token_hash: str,
    db: AsyncSession,
) -> int:
    """
    【数据操作】按 token_hash 吊销单个 refresh token（设置 revoked_at=now）
    查询条件：- token_hash 匹配且未吊销
    参数：token_hash: SHA256 摘要；db: 数据库会话
    返回：受影响行数（0 或 1）
    异常：写入失败，已完成 rollback 后向上抛出
    """
    try:
        stmt = (
            update(RefreshTokenORM)
            .where(
                RefreshTokenORM.token_hash == token_hash,
                RefreshTokenORM.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(timezone.utc))
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount or 0
    except Exception:
        await db.rollback()
        raise


async def revoke_all_principal_tokens(
    principal_type: str,
    principal_id: int,
    db: AsyncSession,
) -> int:
    """
    【数据操作】吊销某主体名下所有未吊销的 refresh token（强制下线）
    查询条件：- (principal_type, principal_id) 匹配 且 revoked_at 为 NULL
    参数：principal_type: 主体类型；principal_id: 主体 ID；db: 数据库会话
    返回：被吊销的记录数
    异常：写入失败，已完成 rollback 后向上抛出
    """
    try:
        stmt = (
            update(RefreshTokenORM)
            .where(
                RefreshTokenORM.principal_type == principal_type,
                RefreshTokenORM.principal_id == principal_id,
                RefreshTokenORM.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.now(timezone.utc))
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount or 0
    except Exception:
        await db.rollback()
        raise
