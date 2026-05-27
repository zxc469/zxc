"""数据库 ORM 映射。

作用：
- 定义数据库表字段与 Python 对象字段的映射关系；
- 保持约束（status 检查、hash 索引、CHECK 约束等）与数据库一致。
涵盖表：knowledge_files / users / agents / admins / refresh_tokens / chat_sessions / session_messages / agent_session_quota。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CHAR, CheckConstraint, DateTime, Index, Integer, BigInteger, String, Text, Boolean, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """ORM 基类，所有表模型都继承它。"""


class KnowledgeFileORM(Base):
    """knowledge_files 表对应的 ORM 模型。"""

    __tablename__ = "knowledge_files"
    __table_args__ = (
        CheckConstraint("status IN ('processing', 'success', 'failed')", name="ck_knowledge_files_status"),
        Index("idx_knowledge_files_hash", "file_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_hash: Mapped[str] = mapped_column(CHAR(64), unique=True, nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="processing")
    total_chunks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class UserORM(Base):
    """users 表：普通用户主体。"""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status"),
        Index("idx_users_email", "email"),
        Index("idx_users_phone", "phone"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    nickname: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AgentORM(Base):
    """agents 表：客服人员主体。"""

    __tablename__ = "agents"
    __table_args__ = (
        CheckConstraint("status IN ('online', 'busy', 'offline', 'disabled')", name="ck_agents_status"),
        CheckConstraint("max_sessions >= 0", name="ck_agents_max_sessions"),
        Index("idx_agents_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    real_name: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    department: Mapped[str | None] = mapped_column(String(64), nullable=True)
    max_sessions: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="offline")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class AdminORM(Base):
    """admins 表：管理员主体。"""

    __tablename__ = "admins"
    __table_args__ = (
        CheckConstraint("role_level IN ('super_admin', 'admin')", name="ck_admins_role_level"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_admins_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    real_name: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str | None] = mapped_column(String(128), nullable=True)
    role_level: Mapped[str] = mapped_column(String(20), nullable=False, default="admin")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class RefreshTokenORM(Base):
    """refresh_tokens 表：刷新令牌的落库记录。"""

    __tablename__ = "refresh_tokens"
    __table_args__ = (
        CheckConstraint("principal_type IN ('user', 'agent', 'admin')", name="ck_refresh_tokens_principal_type"),
        Index("idx_refresh_tokens_principal", "principal_type", "principal_id"),
        Index("idx_refresh_tokens_expires_at", "expires_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    principal_type: Mapped[str] = mapped_column(String(10), nullable=False)
    principal_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    token_hash: Mapped[str] = mapped_column(CHAR(64), unique=True, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class ChatSessionORM(Base):
    """chat_sessions 表：用户与客服的会话记录。"""

    __tablename__ = "chat_sessions"
    __table_args__ = (
        CheckConstraint("status IN ('waiting', 'assigned', 'active', 'closed', 'transferred')", name="ck_sessions_status"),
        CheckConstraint("source IN ('user_initiated', 'ai_transfer', 'admin_assign')", name="ck_sessions_source"),
        CheckConstraint("handled_by IN ('ai', 'agent')", name="ck_sessions_handled_by"),
        CheckConstraint("priority >= 0 AND priority <= 1", name="ck_sessions_priority"),
        CheckConstraint("closed_by IS NULL OR closed_by IN ('user', 'agent', 'system')", name="ck_sessions_closed_by"),
        CheckConstraint("rating IS NULL OR (rating >= 1 AND rating <= 5)", name="ck_sessions_rating"),
        Index("idx_sessions_user_id", "user_id"),
        Index("idx_sessions_agent_id", "agent_id"),
        Index("idx_sessions_status", "status"),
        Index("idx_sessions_created_at", "created_at"),
        Index("idx_sessions_user_status", "user_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_no: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    agent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="waiting")
    handled_by: Mapped[str] = mapped_column(String(10), nullable=False, default="ai")
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="user_initiated")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_by: Mapped[str | None] = mapped_column(String(10), nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rating_comment: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class SessionMessageORM(Base):
    """session_messages 表：会话中的消息记录。"""

    __tablename__ = "session_messages"
    __table_args__ = (
        CheckConstraint("sender_type IN ('user', 'agent', 'ai', 'system')", name="ck_messages_sender_type"),
        CheckConstraint("message_type IN ('text', 'image', 'file', 'system_event')", name="ck_messages_message_type"),
        Index("idx_messages_session_id", "session_id"),
        Index("idx_messages_session_created", "session_id", "created_at"),
        Index("idx_messages_unread", "session_id", "is_read", postgresql_where="is_read = FALSE"),
        Index("idx_messages_sender", "sender_type", "sender_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sender_type: Mapped[str] = mapped_column(String(10), nullable=False)
    sender_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    message_type: Mapped[str] = mapped_column(String(20), nullable=False, default="text")
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    _metadata: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)


class AgentSessionQuotaORM(Base):
    """agent_session_quota 表：客服会话配额。"""

    __tablename__ = "agent_session_quota"
    __table_args__ = (
        CheckConstraint("current_sessions >= 0", name="ck_quota_current_sessions"),
        CheckConstraint("max_sessions > 0", name="ck_quota_max_sessions"),
        Index("idx_quota_available", "current_sessions", "max_sessions", postgresql_where="current_sessions < max_sessions"),
    )

    agent_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    current_sessions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_sessions: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
