"""客服端会话 API。"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
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
from app.schemas.common_models import ApiResponse
from app.schemas.session_models import (
    AgentSessionStats,
    CloseSessionRequest,
    MessageItem,
    MessageListResponse,
    SendMessageRequest,
    SessionDetail,
    SessionListResponse,
    TransferSessionRequest,
)
from app.services.auth_service import AuthError
from app.services.session_service import session_service
from app.utils.logger import get_logger

logger = get_logger(__name__)

agent_session_router = APIRouter(prefix="/agent/sessions", tags=["agent-sessions"])


async def _notify_agent_message(session_id: int, message, db: AsyncSession) -> None:
    """推送客服消息通知：per-session WS + 用户全局通知通道。

    1. per-session WS：消息实时推送到当前会话双方
    2. 用户全局 WS：未读计数增量更新（用户不在该会话页时也能收到）
    """
    try:
        from app.services.ws_service import notify_new_message

        await notify_new_message(
            session_id=str(session_id),
            sender_type="agent",
            message_id=message.id,
            content=message.content,
            timestamp=message.created_at,
        )
    except Exception as exc:
        logger.warning(f"WebSocket 推送失败: {exc}")

    # 用户全局通知：实时更新侧边栏未读计数
    try:
        from app.services.ws_service import notify_user_global_message
        from app.data_access_service.session_dao import get_session_by_id

        session = await get_session_by_id(session_id, db)
        if session is not None:
            await notify_user_global_message(
                user_id=session.user_id,
                session_id=session_id,
                message_id=message.id,
                content=message.content,
                timestamp=message.created_at,
                sender_type="agent",
            )
    except Exception as exc:
        logger.warning(f"用户全局通知推送失败: {exc}")


@agent_session_router.get("/stats", response_model=ApiResponse[AgentSessionStats])
async def get_agent_session_stats(
    principal: PrincipalView = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[AgentSessionStats]:
    ensure_principal_type(principal, "agent", "仅客服可访问")

    try:
        stats = await session_service.get_agent_stats(principal.principal_id, db=db)
    except AuthError as exc:
        logger.error(f"获取客服统计失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    return ApiResponse(data=AgentSessionStats(**stats))


@agent_session_router.get("", response_model=ApiResponse[SessionListResponse])
async def get_agent_sessions(
    principal: PrincipalView = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    status: Optional[str] = Query(None, description="会话状态过滤"),
) -> ApiResponse[SessionListResponse]:
    ensure_principal_type(principal, "agent", "仅客服可访问")

    try:
        sessions, total = await session_service.get_agent_sessions(
            agent_id=principal.principal_id,
            db=db,
            status=status,
            page=page,
            page_size=page_size,
        )
    except AuthError as exc:
        logger.error(f"获取客服会话列表失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    return ApiResponse(data=to_session_list_response(sessions, total, page, page_size))


@agent_session_router.get("/{session_id}", response_model=ApiResponse[SessionDetail])
async def get_agent_session_detail(
    session_id: int,
    principal: PrincipalView = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[SessionDetail]:
    ensure_principal_type(principal, "agent", "仅客服可访问")

    try:
        session = await session_service.get_session_detail(session_id, db=db)
    except AuthError as exc:
        if "不存在" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc))
        logger.error(f"获取会话详情失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    # 允许查看：属于自己的会话 + 等待分配池中的会话
    if session.agent_id is not None and session.agent_id != principal.principal_id:
        raise HTTPException(status_code=404, detail="会话不存在")

    return ApiResponse(data=to_session_detail(session))


@agent_session_router.get("/{session_id}/messages", response_model=ApiResponse[MessageListResponse])
async def get_agent_session_messages(
    session_id: int,
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(50, ge=1, le=100, description="每页数量"),
    before_id: Optional[int] = Query(None, description="加载此ID之前的消息"),
    principal: PrincipalView = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[MessageListResponse]:
    ensure_principal_type(principal, "agent", "仅客服可访问")

    # 校验会话访问权：自己的会话 或 等待分配池中的会话
    try:
        session = await session_service.get_session_detail(session_id, db=db)
    except AuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if session.agent_id is not None and session.agent_id != principal.principal_id:
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


@agent_session_router.post("/{session_id}/messages", response_model=ApiResponse[MessageItem])
async def send_agent_message(
    session_id: int,
    body: SendMessageRequest,
    principal: PrincipalView = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[MessageItem]:
    ensure_principal_type(principal, "agent", "仅客服可访问")

    # 允许向自己的会话或等待池中会话发送消息（首次消息会自动 accept）
    try:
        session = await session_service.get_session_detail(session_id, db=db)
    except AuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if session.agent_id is not None and session.agent_id != principal.principal_id:
        raise HTTPException(status_code=404, detail="会话不存在")

    try:
        message = await session_service.send_message(
            session_id=session_id,
            db=db,
            sender_type="agent",
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

    await _notify_agent_message(session_id, message, db)

    return ApiResponse(data=to_message_item(message))


@agent_session_router.put("/{session_id}/accept", response_model=ApiResponse[dict])
async def accept_agent_session(
    session_id: int,
    principal: PrincipalView = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    ensure_principal_type(principal, "agent", "仅客服可访问")

    try:
        await session_service.accept_session(
            session_id=session_id,
            agent_id=principal.principal_id,
            db=db,
        )
    except AuthError as exc:
        if "不存在" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc))
        logger.error(f"接受会话失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    from app.services.ws_service import notify_handled_by_changed
    await notify_handled_by_changed(str(session_id), "agent")

    return ApiResponse(message="会话已接受")


@agent_session_router.put("/{session_id}/read", response_model=ApiResponse[dict])
async def mark_agent_session_read(
    session_id: int,
    principal: PrincipalView = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    ensure_principal_type(principal, "agent", "仅客服可访问")

    try:
        session = await session_service.get_session_detail(session_id, db=db)
    except AuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if session.agent_id is not None and session.agent_id != principal.principal_id:
        raise HTTPException(status_code=404, detail="会话不存在")

    try:
        read_count = await session_service.mark_session_read(session_id, db=db)
    except AuthError as exc:
        if "不存在" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc))
        logger.error(f"标记已读失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    return ApiResponse(data={"read_count": read_count})


@agent_session_router.put("/{session_id}/transfer", response_model=ApiResponse[dict])
async def transfer_agent_session(
    session_id: int,
    body: TransferSessionRequest,
    principal: PrincipalView = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    ensure_principal_type(principal, "agent", "仅客服可访问")

    try:
        session = await session_service.get_session_detail(session_id, db=db)
    except AuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if session.agent_id != principal.principal_id:
        raise HTTPException(status_code=404, detail="会话不存在")

    try:
        await session_service.transfer_session(
            session_id=session_id,
            db=db,
            from_agent_id=principal.principal_id,
            to_agent_id=body.to_agent_id,
            reason=body.reason,
        )
    except AuthError as exc:
        if "不存在" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc))
        logger.error(f"转接会话失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    from app.services.ws_service import notify_handled_by_changed
    await notify_handled_by_changed(str(session_id), "agent")

    return ApiResponse(message="会话已转接")


@agent_session_router.put("/{session_id}/transfer-to-ai", response_model=ApiResponse[dict])
async def transfer_agent_session_to_ai(
    session_id: int,
    principal: PrincipalView = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[dict]:
    ensure_principal_type(principal, "agent", "仅客服可访问")

    try:
        session = await session_service.get_session_detail(session_id, db=db)
    except AuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if session.agent_id != principal.principal_id:
        raise HTTPException(status_code=404, detail="会话不存在")

    try:
        await session_service.transfer_to_ai(
            session_id=session_id,
            db=db,
            agent_id=principal.principal_id,
        )
    except AuthError as exc:
        if "不存在" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc))
        logger.error(f"转AI失败: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

    from app.services.ws_service import notify_handled_by_changed
    await notify_handled_by_changed(str(session_id), "ai")

    return ApiResponse(message="已转AI模式")


@agent_session_router.put("/{session_id}/close", response_model=ApiResponse[SessionDetail])
async def close_agent_session(
    session_id: int,
    body: CloseSessionRequest,
    principal: PrincipalView = Depends(get_current_principal),
    db: AsyncSession = Depends(get_db),
) -> ApiResponse[SessionDetail]:
    ensure_principal_type(principal, "agent", "仅客服可访问")

    try:
        session = await session_service.get_session_detail(session_id, db=db)
    except AuthError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if session.agent_id != principal.principal_id:
        raise HTTPException(status_code=404, detail="会话不存在")

    try:
        session = await session_service.close_session(
            session_id=session_id,
            db=db,
            closed_by="agent",
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
