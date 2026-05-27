"""Agent 聊天服务相关的请求/响应模型。"""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """
    POST /chat/send 请求体。

    前端发送用户消息时使用，session_id 标识会话，content 为消息文本。

    Args:
        session_id: 会话 ID，对应 chat_sessions 表主键
        content: 用户消息文本
        sender_id: 发送者用户 ID，可为空（匿名用户）
    """

    session_id: int
    content: str = Field(min_length=1, description="用户消息文本，不能为空")
    sender_id: int | None = Field(default=None, description="发送者用户 ID")


class ChatResponse(BaseModel):
    """
    POST /chat/send 响应体。

    Agent 执行完成后返回给前端的结构化响应。

    Args:
        answer: Agent 生成的回复文本
        ticket_id: 创建的工单号，无工单时为 None
        should_handoff_to_human: 是否需要转接人工客服
    """

    answer: str
    ticket_id: str | None = None
    should_handoff_to_human: bool = False
