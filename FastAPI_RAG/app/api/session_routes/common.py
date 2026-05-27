from __future__ import annotations

from fastapi import HTTPException

from app.schemas.auth_models import PrincipalView
from app.schemas.session_models import (
    MessageItem,
    MessageListResponse,
    SessionDetail,
    SessionListItem,
    SessionListResponse,
)


def ensure_principal_type(principal: PrincipalView, expected_type: str, detail: str) -> None:
    if principal.principal_type != expected_type:
        raise HTTPException(status_code=403, detail=detail)


def to_session_list_item(session) -> SessionListItem:
    return SessionListItem(
        id=session.id,
        session_no=session.session_no,
        user_id=session.user_id,
        agent_id=session.agent_id,
        status=session.status,
        handled_by=getattr(session, "handled_by", "ai"),
        source=session.source,
        priority=session.priority,
        created_at=session.created_at,
        assigned_at=session.assigned_at,
        active_at=session.active_at,
        closed_at=session.closed_at,
        rating=session.rating,
        last_message=getattr(session, "last_message", None),
        last_message_time=getattr(session, "last_message_time", None),
        unread_count=getattr(session, "unread_count", None),
    )


def to_session_detail(session) -> SessionDetail:
    return SessionDetail(
        id=session.id,
        session_no=session.session_no,
        user_id=session.user_id,
        agent_id=session.agent_id,
        status=session.status,
        handled_by=getattr(session, "handled_by", "ai"),
        source=session.source,
        priority=session.priority,
        created_at=session.created_at,
        assigned_at=session.assigned_at,
        active_at=session.active_at,
        closed_at=session.closed_at,
        rating=session.rating,
        close_reason=getattr(session, "close_reason", None),
        rating_comment=getattr(session, "rating_comment", None),
    )


def to_message_item(message) -> MessageItem:
    metadata = message.metadata or {}
    return MessageItem(
        id=message.id,
        session_id=message.session_id,
        sender_type=message.sender_type,
        sender_id=message.sender_id,
        message_type=message.message_type,
        content=message.content,
        is_read=message.is_read,
        read_at=message.read_at,
        created_at=message.created_at,
        metadata=message.metadata,
        ticket_id=metadata.get("ticket_id"),
    )


def to_session_list_response(
    sessions,
    total: int,
    page: int,
    page_size: int,
) -> SessionListResponse:
    return SessionListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[to_session_list_item(session) for session in sessions],
    )


def to_message_list_response(
    messages,
    total: int,
    page: int,
    page_size: int,
) -> MessageListResponse:
    return MessageListResponse(
        total=total,
        page=page,
        page_size=page_size,
        has_more=(page * page_size) < total,
        items=[to_message_item(message) for message in messages],
    )
