"""
Agent 对话业务服务层。

负责单轮对话的完整业务流程编排：用户消息入库 → Agent 执行 → AI 回复入库。
图执行结果的提取与适配直接在此完成，不做额外拆层。
支持同步 HTTP 和 SSE 流式两种响应模式。
"""

import json
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import ToolMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.data_access_service.session_dao import save_user_message, save_ai_message, update_session
from app.db.session_models import SessionUpdateInput
from app.graph.graph_runtime_agent import build_graph_agent
from app.schemas.chat_models import ChatRequest, ChatResponse
from app.utils.logger import get_logger

logger = get_logger(__name__)


async def run_chat(
    request: ChatRequest,
    db: AsyncSession,
    *,
    notify_ws: bool = False,
) -> ChatResponse:
    """
    执行单轮 Agent 对话：用户消息入库 → Agent 编排 → AI 回复入库 → 返回响应。

    Args:
        request: 前端请求体，包含 session_id、消息内容、发送者 ID
        db: FastAPI 依赖注入的数据库会话
        notify_ws: 是否通过 WebSocket 推送 AI 回复给用户端

    Returns:
        ChatResponse，包含 Agent 生成的 answer、可选的 ticket_id 和转人工标志
    """
    user_message = await save_user_message(
        session_id=request.session_id,
        content=request.content,
        sender_id=request.sender_id,
        db=db,
    )

    result_state = await build_graph_agent().run(
        session_id=str(request.session_id),
        user_message=request.content,
    )

    answer = str(
        result_state.get("final_answer")
        or result_state.get("draft_response")
        or "请稍后重试"
    )

    need_human = bool(result_state.get("need_human", False))

    if need_human:
        await update_session(
            request.session_id,
            SessionUpdateInput(status="waiting", handled_by="agent"),
            db,
        )
        logger.info("会话 %s AI 判定需转人工，状态已置为 waiting", request.session_id)
        if notify_ws:
            from app.services.ws_service import notify_handled_by_changed
            await notify_handled_by_changed(str(request.session_id), "agent")

    ai_message = await save_ai_message(
        session_id=request.session_id,
        content=answer,
        db=db,
    )

    if notify_ws:
        from app.services.ws_service import notify_new_message

        await notify_new_message(
            session_id=str(request.session_id),
            sender_type="user",
            message_id=user_message.id,
            content=request.content,
            timestamp=user_message.created_at,
        )

        await notify_new_message(
            session_id=str(request.session_id),
            sender_type="ai",
            message_id=ai_message.id,
            content=answer,
            timestamp=ai_message.created_at,
        )

    return ChatResponse(
        answer=answer,
        ticket_id=_extract_ticket_id(result_state.get("messages", [])),
        should_handoff_to_human=need_human,
    )


async def run_chat_stream(
    request: ChatRequest,
    db: AsyncSession,
) -> AsyncIterator[dict[str, Any]]:
    """以 SSE 流式方式执行单轮 Agent 对话。

    先保存用户消息，再通过 GraphAgent.run_stream() 逐 token 产出事件，
    流结束后保存 AI 回复到 DB 并推送 WS 通知。

    Args:
        request: 前端请求体
        db: 数据库会话

    Yields:
        SSE 事件字典：token / tool_call / done / error
    """
    user_message = await save_user_message(
        session_id=request.session_id,
        content=request.content,
        sender_id=request.sender_id,
        db=db,
    )

    answer = ""
    need_human = False

    async for event in build_graph_agent().run_stream(
        session_id=str(request.session_id),
        user_message=request.content,
    ):
        yield event

        if event["event"] == "done":
            answer = str(event["data"].get("answer", "") or "请稍后重试")
            need_human = bool(event["data"].get("need_human", False))

    if need_human:
        await update_session(
            request.session_id,
            SessionUpdateInput(status="waiting", handled_by="agent"),
            db,
        )
        logger.info("会话 %s AI 判定需转人工，状态已置为 waiting", request.session_id)

    ai_message = await save_ai_message(
        session_id=request.session_id,
        content=answer,
        db=db,
    )

    try:
        from app.services.ws_service import notify_new_message

        await notify_new_message(
            session_id=str(request.session_id),
            sender_type="user",
            message_id=user_message.id,
            content=request.content,
            timestamp=user_message.created_at,
        )
        await notify_new_message(
            session_id=str(request.session_id),
            sender_type="ai",
            message_id=ai_message.id,
            content=answer,
            timestamp=ai_message.created_at,
        )
    except Exception as exc:
        logger.warning("WebSocket 推送失败: %s", exc)


def _extract_ticket_id(messages: Any) -> str | None:
    """从 ToolMessage 中提取 create_ticket 生成的工单号。"""
    if not isinstance(messages, list):
        return None
    for item in messages:
        if not isinstance(item, ToolMessage):
            continue
        if str(item.name or "").strip() != "create_ticket":
            continue
        payload = _parse_tool_content(item.content)
        if isinstance(payload, dict):
            tid = str(payload.get("ticket_id", "")).strip()
            if tid:
                return tid
    return None


def _parse_tool_content(content: Any) -> Any:
    if not isinstance(content, str):
        return content
    text = content.strip()
    if not text:
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
