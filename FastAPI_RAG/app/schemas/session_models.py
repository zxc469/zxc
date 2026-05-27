"""会话管理相关的请求/响应模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ==================== 会话相关模型 ====================

class SessionListItem(BaseModel):
    """会话列表项（用于列表展示）。"""
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
    rating: Optional[int] = None
    # 前端计算字段
    user_name: Optional[str] = None
    agent_name: Optional[str] = None
    last_message: Optional[str] = None
    last_message_time: Optional[datetime] = None
    unread_count: Optional[int] = None


class SessionDetail(BaseModel):
    """会话详情（点击后加载）。"""
    id: int # 会话ID
    session_no: str # 会话编号
    user_id: int # 用户ID
    agent_id: Optional[int] = None # 客服ID
    status: str # 会话状态
    handled_by: str = "ai" # 当前处理方: ai/agent
    source: str # 会话来源
    priority: int # 优先级
    created_at: datetime # 创建时间
    assigned_at: Optional[datetime] = None # 分配时间
    active_at: Optional[datetime] = None # 激活时间
    closed_at: Optional[datetime] = None # 结束时间
    rating: Optional[int] = None # 评分
    # 扩展信息
    user_info: Optional[dict] = None    # 用户信息
    agent_info: Optional[dict] = None    # 客服信息
    close_reason: Optional[str] = None    # 关闭原因
    rating_comment: Optional[str] = None    # 评分评论


class CreateSessionRequest(BaseModel):
    """创建会话请求。"""
    source: Optional[str] = Field(None, description="会话来源：user_initiated/ai_transfer/admin_assign")
    priority: Optional[int] = Field(None, ge=0, le=1, description="优先级：0-普通 1-紧急")


class CloseSessionRequest(BaseModel):
    """结束会话请求。"""
    close_reason: Optional[str] = Field(None, max_length=100, description="关闭原因")


class RateSessionRequest(BaseModel):
    """评价会话请求。"""
    rating: int = Field(ge=1, le=5, description="评分：1-5星")
    comment: Optional[str] = Field(None, max_length=500, description="评价内容")


# ==================== 消息相关模型 ====================

class MessageItem(BaseModel):
    """消息列表项。"""
    id: int
    session_id: int
    sender_type: str = Field(..., description="发送者类型：user/agent/ai")
    sender_id: Optional[int] = None
    message_type: str = Field(..., description="消息类型：text/image/file/system_event")
    content: str
    is_read: bool
    read_at: Optional[datetime] = None
    created_at: datetime
    metadata: Optional[dict] = Field(None, description="扩展数据（intent、ticket_id 等）")
    
    # Agent 相关字段（从 metadata 提取，便于前端直接使用）
    ticket_id: Optional[str] = Field(None, description="关联工单号")


class SendMessageRequest(BaseModel):
    """发送消息请求。"""
    message_type: str = Field(default="text", description="消息类型：text/image/file/system_event")
    content: str = Field(..., description="消息内容")
    metadata: Optional[dict] = Field(None, description="扩展数据")


# ==================== 响应模型 ====================

class SessionListResponse(BaseModel):
    """会话列表响应（分页）。"""
    total: int
    page: int
    page_size: int
    items: list[SessionListItem]


class MessageListResponse(BaseModel):
    """消息列表响应（分页）。"""
    total: int
    page: int
    page_size: int
    has_more: bool
    items: list[MessageItem]


# ==================== 客服端专用模型 ====================

class TransferSessionRequest(BaseModel):
    """转接会话请求。"""
    to_agent_id: int = Field(..., description="目标客服ID")
    reason: Optional[str] = Field(None, description="转接原因")


class AgentSessionStats(BaseModel):
    """客服会话统计。"""
    total_sessions: int = Field(..., description="总会话数")
    active_sessions: int = Field(..., description="进行中的会话数")
    waiting_sessions: int = Field(..., description="等待分配的会话数")
    closed_today: int = Field(default=0, description="今日关闭的会话数")
    avg_response_time: float = Field(default=0.0, description="平均响应时间（秒）")
    current_quota: int = Field(..., description="当前配额使用")
    max_quota: int = Field(..., description="最大配额")
