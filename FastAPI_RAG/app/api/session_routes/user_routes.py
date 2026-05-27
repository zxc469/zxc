"""用户端会话 API。"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_principal
from app.api.session_routes.common import (
    ensure_principal_type,
    to_message_item,
    to_message_list_response,
    to_session_detail,
    to_session_list_response,
)
from app.db.postgres_pool import get_db
from app.schemas.auth_models import PrincipalView
from app.schemas.chat_models import ChatRequest, ChatResponse
from app.schemas.common_models import ApiResponse
from app.schemas.session_models import (
    CloseSessionRequest,
    CreateSessionRequest,
    MessageItem,
    MessageListResponse,
    RateSessionRequest,
    SendMessageRequest,
    SessionDetail,
    SessionListResponse,
)
from app.services.auth_service import AuthError
from app.services.chat_service import run_chat, run_chat_stream
from app.services.session_service import session_service
from app.utils.logger import get_logger
from app.utils.rate_limiter import rate_limit_chat

logger = get_logger(__name__)

user_session_router = APIRouter(prefix="/user/sessions", tags=["user-sessions"])


async def _notify_user_message(session_id: int, message) -> None:
    try:
        from app.services.ws_service import notify_new_message

        await notify_new_message(
            session_id=str(session_id),
            sender_type="user",
            message_id=message.id,
            content=message.content,
            timestamp=message.created_at,
        )
    except Exception as exc:
        logger.warning(f"WebSocket 推送失败: {exc}")


@user_session_router.get("", response_model=ApiResponse[SessionListResponse])
async def get_user_sessions(
    principal: PrincipalView = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    status: Optional[str] = Query(None, description="会话状态过滤"),
) -> ApiResponse[SessionListResponse]:
    ensure_principal_type(principal, "user", "仅用户可访问")

    try:
        sessions, total = await session_service.get_user_sessions(
            user_id=principal.principal_id,
            db=db,
            status=status,
            page=page,
            page_size=page_size,
        )
    except AuthError as exc:
        logger.error(f"获取用户会话列表失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    return ApiResponse(data=to_session_list_response(sessions, total, page, page_size))


@user_session_router.post("", response_model=ApiResponse[SessionDetail], status_code=status.HTTP_201_CREATED)
async def create_user_session(
    body: CreateSessionRequest,
    principal: PrincipalView = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[SessionDetail]:
    ensure_principal_type(principal, "user", "仅用户可访问")

    try:
        session = await session_service.create_session(
            user_id=principal.principal_id,
            db=db,
            source=body.source or "user_initiated",
            priority=body.priority or 0,
        )
    except AuthError as exc:
        logger.error(f"创建会话失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    return ApiResponse(data=to_session_detail(session))


@user_session_router.get("/{session_id}", response_model=ApiResponse[SessionDetail])
async def get_user_session_detail(
    session_id: int,
    principal: PrincipalView = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[SessionDetail]:
    ensure_principal_type(principal, "user", "仅用户可访问")

    try:
        session = await session_service.get_session_detail(session_id, db=db)
    except AuthError as exc:
        if "不存在" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc))
        logger.error(f"获取会话详情失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    if session.user_id != principal.principal_id:
        raise HTTPException(status_code=404, detail="会话不存在")

    return ApiResponse(data=to_session_detail(session))


@user_session_router.get("/{session_id}/messages", response_model=ApiResponse[MessageListResponse])
async def get_user_session_messages(
    session_id: int,
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(50, ge=1, le=100, description="每页数量"),
    before_id: Optional[int] = Query(None, description="加载此ID之前的消息"),
    principal: PrincipalView = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[MessageListResponse]:
    ensure_principal_type(principal, "user", "仅用户可访问")

    try:
        session = await session_service.get_session_detail(session_id, db=db)
    except AuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if session.user_id != principal.principal_id:
        raise HTTPException(status_code=404, detail="会话不存在")

    try:
        messages, total = await session_service.get_session_messages(
            session_id=session_id,
            db=db,
            page=page,
            page_size=page_size,
            before_id=before_id,
        )
    except AuthError as exc:
        if "不存在" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc))
        logger.error(f"获取消息列表失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    return ApiResponse(data=to_message_list_response(messages, total, page, page_size))


@user_session_router.post("/{session_id}/messages", response_model=ApiResponse[MessageItem])
async def send_user_message(
    session_id: int,
    body: SendMessageRequest,
    principal: PrincipalView = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[MessageItem]:
    ensure_principal_type(principal, "user", "仅用户可访问")

    try:
        session = await session_service.get_session_detail(session_id, db=db)
    except AuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if session.user_id != principal.principal_id:
        raise HTTPException(status_code=404, detail="会话不存在")

    try:
        message = await session_service.send_message(
            session_id=session_id,
            db=db,
            sender_type="user",
            sender_id=principal.principal_id,
            message_type=body.message_type,
            content=body.content,
            metadata=body.metadata,
        )
    except AuthError as exc:
        if "不存在" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc))
        logger.error(f"发送消息失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    await _notify_user_message(session_id, message)

    return ApiResponse(data=to_message_item(message))


@user_session_router.post("/{session_id}/chat", response_model=ApiResponse[ChatResponse])
async def chat_with_ai(
    session_id: int,
    body: SendMessageRequest,
    principal: PrincipalView = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[ChatResponse]:
    """
    AI 智能客服对话接口：用户消息经 AI Agent 处理后返回回复。

    与 send_user_message（直接发给人工客服）不同，本接口走 LangGraph Agent 链路，
    由 AI 自动生成回复。AI 判定需转人工时（should_handoff_to_human=True），
    前端应切换到 agent 模式改用 send_user_message。

    Args:
        session_id: 会话 ID
        body: 消息内容（message_type + content）
        principal: 当前登录用户
        db: 数据库会话

    Returns:
        ChatResponse，包含 AI 回复、工单号和转人工标志

    Raises:
        HTTPException 404: 会话不存在或不属于当前用户
        HTTPException 422: 参数校验失败
        HTTPException 500: Agent 执行异常
    """
    ensure_principal_type(principal, "user", "仅用户可访问")

    try:
        session = await session_service.get_session_detail(session_id, db=db)
    except AuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if session.user_id != principal.principal_id:
        raise HTTPException(status_code=404, detail="会话不存在")

    rate_limit_chat(principal.principal_id)

    request = ChatRequest(
        session_id=session_id,
        content=body.content,
        sender_id=principal.principal_id,
    )

    try:
        result = await run_chat(request, db=db, notify_ws=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("chat_with_ai 异常: session_id=%s", session_id)
        raise HTTPException(status_code=500, detail="服务内部错误")

    return ApiResponse(data=result)


@user_session_router.post("/{session_id}/chat/stream")
async def chat_with_ai_stream(
    session_id: int,
    body: SendMessageRequest,
    principal: PrincipalView = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
):
    """AI 智能客服 SSE 流式对话接口。

    与 /chat（同步版）不同，本接口返回 SSE 流，逐 token 推送 AI 回复。
    用户消息先入库，再通过 GraphAgent.run_stream() 流式产出事件。

    SSE 事件类型：
    - message_start: Agent 节点开始生成（审核修正重新生成时前端需清空旧草稿）
    - token: AI 生成的文本片段，data 含 token + seq
    - tool_call: Agent 工具调用状态，data 含 tool_name + status(running/done)
    - done: 生成完成，data 含 answer + need_human
    - error: 异常，data 含 message

    Args:
        session_id: 会话 ID
        body: 消息内容
        principal: 当前登录用户
        db: 数据库会话

    Returns:
        StreamingResponse (text/event-stream)
    """
    ensure_principal_type(principal, "user", "仅用户可访问")

    try:
        session = await session_service.get_session_detail(session_id, db=db)
    except AuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if session.user_id != principal.principal_id:
        raise HTTPException(status_code=404, detail="会话不存在")

    rate_limit_chat(principal.principal_id)

    request = ChatRequest(
        session_id=session_id,
        content=body.content,
        sender_id=principal.principal_id,
    )

    async def _event_stream():
        """SSE 事件生成器，捕获 run_chat_stream 异常并转为 error 事件。"""
        try:
            async for event in run_chat_stream(request, db=db):
                event_name = event.get("event", "message")
                data = event.get("data", {})
                yield f"event: {event_name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        except Exception as exc:
            logger.exception("SSE 流异常: session_id=%s", session_id)
            yield f"event: error\ndata: {json.dumps({'message': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@user_session_router.put("/{session_id}/read", response_model=ApiResponse[dict])
async def mark_user_session_read(
    session_id: int,
    principal: PrincipalView = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    ensure_principal_type(principal, "user", "仅用户可访问")

    try:
        session = await session_service.get_session_detail(session_id, db=db)
    except AuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if session.user_id != principal.principal_id:
        raise HTTPException(status_code=404, detail="会话不存在")

    try:
        read_count = await session_service.mark_session_read(session_id, db=db)
    except AuthError as exc:
        if "不存在" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc))
        logger.error(f"标记已读失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    return ApiResponse(data={"read_count": read_count})


@user_session_router.post("/{session_id}/transfer", response_model=ApiResponse[dict])
async def transfer_to_agent(
    session_id: int,
    principal: PrincipalView = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    ensure_principal_type(principal, "user", "仅用户可访问")

    try:
        session = await session_service.get_session_detail(session_id, db=db)
    except AuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if session.user_id != principal.principal_id:
        raise HTTPException(status_code=404, detail="会话不存在")

    if session.handled_by == "agent":
        raise HTTPException(status_code=400, detail="当前已在人工服务中，无需重复转接")

    from app.data_access_service.session_dao import (
        claim_session_for_transfer, update_session, get_available_agents, increment_agent_quota,
    )
    from app.db.session_models import SessionUpdateInput

    # 原子认领会话（消除 TOCTOU 窗口：并发请求中仅一条能成功）
    claimed = await claim_session_for_transfer(session_id, db)
    if claimed is None:
        raise HTTPException(status_code=400, detail="当前已在人工服务中，无需重复转接")

    # 尝试自动分配一个可用客服
    available_agents = await get_available_agents(db)
    agent_id = None
    new_status = "waiting"
    assigned_at = None

    if available_agents:
        best_agent = available_agents[0]
        quota_ok = await increment_agent_quota(best_agent.agent_id, db)
        if quota_ok:
            agent_id = best_agent.agent_id
            new_status = "assigned"
            assigned_at = datetime.now()

    await update_session(session_id, SessionUpdateInput(
        status=new_status,
        agent_id=agent_id,
        assigned_at=assigned_at,
    ), db)

    # ── 获取完整会话上下文（含用户信息 + 最近消息），推送给客服 ──
    from app.data_access_service.session_dao import get_session_by_id, list_session_messages
    from app.services.ws_service import notify_handled_by_changed, notify_agent_new_session, notify_waiting_session_broadcast
    from app.api.session_routes.common import to_session_detail, to_message_item

    await notify_handled_by_changed(str(session_id), "agent")

    # 重新获取更新后的会话
    updated_session = await get_session_by_id(session_id, db)
    session_dict = to_session_detail(updated_session).model_dump(mode="json")

    # 查找用户信息
    from app.db.orm import UserORM
    from sqlalchemy import select as _select
    user_row = await db.scalar(_select(UserORM).where(UserORM.id == updated_session.user_id))
    if user_row:
        session_dict["user_info"] = {
            "id": user_row.id,
            "username": user_row.username,
            "nickname": user_row.nickname,
        }

    # 最近 30 条消息作为上下文
    msg_records, _ = await session_service.get_session_messages(
        session_id, db=db, page=1, page_size=30,
    )
    messages_data = [to_message_item(m).model_dump(mode="json") for m in msg_records]

    # 推送给客服
    if agent_id is not None:
        await notify_agent_new_session(agent_id, session_dict, messages_data)
    else:
        await notify_waiting_session_broadcast(session_dict, messages_data)

    return ApiResponse(message="已请求转人工")


@user_session_router.post("/{session_id}/close", response_model=ApiResponse[SessionDetail])
async def close_user_session(
    session_id: int,
    body: CloseSessionRequest,
    principal: PrincipalView = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[SessionDetail]:
    ensure_principal_type(principal, "user", "仅用户可访问")

    try:
        session = await session_service.get_session_detail(session_id, db=db)
    except AuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if session.user_id != principal.principal_id:
        raise HTTPException(status_code=404, detail="会话不存在")

    try:
        session = await session_service.close_session(
            session_id=session_id,
            db=db,
            closed_by="user",
            close_reason=body.close_reason,
        )
    except AuthError as exc:
        if "不存在" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc))
        logger.error(f"结束会话失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    from app.services.ws_service import notify_session_closed
    await notify_session_closed(str(session_id))

    return ApiResponse(data=to_session_detail(session))


@user_session_router.put("/{session_id}/rate", response_model=ApiResponse[SessionDetail])
async def rate_user_session(
    session_id: int,
    body: RateSessionRequest,
    principal: PrincipalView = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[SessionDetail]:
    ensure_principal_type(principal, "user", "仅用户可访问")

    try:
        session = await session_service.get_session_detail(session_id, db=db)
    except AuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if session.user_id != principal.principal_id:
        raise HTTPException(status_code=404, detail="会话不存在")

    try:
        session = await session_service.rate_session(
            session_id=session_id,
            db=db,
            rating=body.rating,
            comment=body.comment,
        )
    except AuthError as exc:
        if "不存在" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc))
        logger.error(f"评价会话失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    return ApiResponse(data=to_session_detail(session))
