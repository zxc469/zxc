"""会话管理相关的数据模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


# ==================== 会话相关模型 ====================

class SessionCreateInput(BaseModel):
    """创建会话的输入数据。"""
    user_id: int
    source: str = Field(default="user_initiated", description="会话来源：user_initiated/ai_transfer/admin_assign")
    priority: int = Field(default=0, ge=0, le=1, description="优先级：0-普通 1-紧急")


class SessionUpdateInput(BaseModel):
    """更新会话状态的输入数据。"""
    status: Optional[str] = None
    agent_id: Optional[int] = None
    handled_by: Optional[str] = None
    assigned_at: Optional[datetime] = None
    active_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    closed_by: Optional[str] = None
    close_reason: Optional[str] = None
    rating: Optional[int] = Field(None, ge=1, le=5)
    rating_comment: Optional[str] = None


class SessionRecord(BaseModel):
    """会话记录（数据库返回）。"""
    id: int
    session_no: str
    user_id: int
    agent_id: Optional[int] = None
    status: str
    handled_by: str = "ai"
    source: str
    priority: int
    created_at: datetime
    assigned_at: Optional[datetime] = None
    active_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    closed_by: Optional[str] = None
    close_reason: Optional[str] = None
    rating: Optional[int] = None
    rating_comment: Optional[str] = None
    updated_at: datetime
    # 计算字段（非数据库列，由 service 层富化填充）
    last_message: Optional[str] = None
    last_message_time: Optional[datetime] = None
    unread_count: Optional[int] = None

    class Config:
        model_config = ConfigDict(from_attributes=True)


# ==================== 消息相关模型 ====================

class MessageCreateInput(BaseModel):
    """创建消息的输入数据。"""
    session_id: int
    sender_type: str = Field(description="发送者类型：user/agent/ai/system")
    sender_id: Optional[int] = None
    message_type: str = Field(default="text", description="消息类型：text/image/file/system_event")
    content: str
    metadata: Optional[dict] = None


class MessageRecord(BaseModel):
    """消息记录（数据库返回）。"""
    id: int
    session_id: int
    sender_type: str
    sender_id: Optional[int] = None
    message_type: str
    content: str
    is_read: bool
    read_at: Optional[datetime] = None
    created_at: datetime
    metadata: Optional[dict] = None

    class Config:
        model_config = ConfigDict(from_attributes=True)


# ==================== 客服配额相关模型 ====================

class AgentQuotaInput(BaseModel):
    """客服配额输入数据。"""
    agent_id: int
    max_sessions: int = Field(default=5, gt=0)


class AgentQuotaRecord(BaseModel):
    """客服配额记录。"""
    agent_id: int
    current_sessions: int
    max_sessions: int
    last_updated: datetime

    class Config:
        model_config = ConfigDict(from_attributes=True)
