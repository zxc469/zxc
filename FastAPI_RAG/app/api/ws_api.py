from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres_pool import get_db
from app.services.ws_service import (
    handle_user_session_ws,
    handle_agent_session_ws,
    agent_notification_manager,
    user_notification_manager,
)
from app.utils.logger import get_logger
from app.utils.security import decode_access_token

logger = get_logger(__name__)

ws_router = APIRouter(prefix="/ws", tags=["websocket"])

# WebSocket 握手时使用的子协议前缀，前端通过 new WebSocket(url, ["access_token.<token>"]) 传入
_WS_TOKEN_PROTO_PREFIX = "access_token."


def _extract_ws_token(ws: WebSocket, query_token: str = "") -> str | None:
    """从 WebSocket 连接中提取 JWT token。

    优先从 Sec-WebSocket-Protocol 子协议头中提取（前端通过
    new WebSocket(url, ["access_token.<token>"]) 传入），
    回退到 URL query 参数（兼容旧客户端）。

    Args:
        ws: 未接受的 WebSocket 连接
        query_token: URL query 中传入的 token（兼容旧版）

    Returns:
        token 字符串或 None
    """
    # 优先：Sec-WebSocket-Protocol 子协议头
    subprotocol = ws.headers.get("sec-websocket-protocol", "")
    if subprotocol:
        for proto in subprotocol.split(","):
            proto = proto.strip()
            if proto.startswith(_WS_TOKEN_PROTO_PREFIX):
                token = proto[len(_WS_TOKEN_PROTO_PREFIX):]
                if token:
                    return token
    # 回退：URL query param
    if query_token:
        return query_token
    return None


def _validate_and_decode(token: str | None) -> dict | None:
    """校验 JWT token 并返回 payload，无效时返回 None。"""
    if not token:
        return None
    try:
        return decode_access_token(token)
    except Exception:
        return None


async def _ws_close(ws: WebSocket, code: int, reason: str) -> None:
    """安全关闭 WebSocket 连接（已接受或未接受均可调用）。"""
    try:
        await ws.close(code=code, reason=reason)
    except Exception:
        pass


# ====================================================================
# 用户端会话 WebSocket
# ====================================================================

@ws_router.websocket("/chat/{session_id}")
async def user_session_ws(
    session_id: int,
    ws: WebSocket,
    token: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
) -> None:
    """用户端会话 WebSocket：接收实时消息推送。

    鉴权流程：
      1. 从 Sec-WebSocket-Protocol 头或 URL query 提取 token
      2. 校验 JWT，无效则拒绝连接（4001）
      3. 校验 principal_type 为 user（4003）
      4. 校验该 user 拥有该会话（4003）
    """
    raw_token = _extract_ws_token(ws, token)
    payload = _validate_and_decode(raw_token)

    if payload is None:
        logger.warning("User WS 鉴权失败: session_id=%s", session_id)
        await _ws_close(ws, code=4001, reason="Invalid or missing token")
        return

    if payload.get("principal_type") != "user":
        logger.warning("User WS 非 user 角色: session_id=%s principal_type=%s", session_id, payload.get("principal_type"))
        await _ws_close(ws, code=4003, reason="User only")
        return

    user_id: int = payload["principal_id"]

    # 校验会话归属：仅用户本人的会话才能连接
    from app.data_access_service.session_dao import get_session_by_id
    session = await get_session_by_id(session_id, db)
    if session is None or session.user_id != user_id:
        logger.warning("User WS 会话归属校验失败: session_id=%s user_id=%s", session_id, user_id)
        await _ws_close(ws, code=4003, reason="Access denied")
        return

    logger.info("User WS handshake: session_id=%s user_id=%s", session_id, user_id)

    # 接受连接时回显匹配的子协议，否则浏览器会断开
    accepted_proto = f"{_WS_TOKEN_PROTO_PREFIX}{raw_token}" if raw_token else None
    await ws.accept(subprotocol=accepted_proto)
    await handle_user_session_ws(session_id=str(session_id), ws=ws)


# ====================================================================
# 客服端会话 WebSocket
# ====================================================================

@ws_router.websocket("/agent/{session_id}")
async def agent_session_ws(
    session_id: int,
    ws: WebSocket,
    token: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
) -> None:
    """客服端会话 WebSocket：接收实时消息推送。

    鉴权流程：
      1. 从 Sec-WebSocket-Protocol 头或 URL query 提取 token
      2. 校验 JWT，无效则拒绝连接（4001）
      3. 校验 principal_type 为 agent（4003）
      4. 校验该 agent 拥有该会话（或会话在等待池中）（4003）
    """
    raw_token = _extract_ws_token(ws, token)
    payload = _validate_and_decode(raw_token)

    if payload is None:
        logger.warning("Agent WS 鉴权失败: session_id=%s", session_id)
        await _ws_close(ws, code=4001, reason="Invalid or missing token")
        return

    if payload.get("principal_type") != "agent":
        logger.warning("Agent WS 非 agent 角色: session_id=%s principal_type=%s", session_id, payload.get("principal_type"))
        await _ws_close(ws, code=4003, reason="Agent only")
        return

    agent_id: int = payload["principal_id"]

    # 校验会话访问权：属于自己的会话 或 等待分配池中的会话允许连接
    from app.data_access_service.session_dao import get_session_by_id
    session = await get_session_by_id(session_id, db)
    if session is None:
        logger.warning("Agent WS 会话不存在: session_id=%s", session_id)
        await _ws_close(ws, code=4003, reason="Session not found")
        return

    # 允许：自己的会话 OR 等待分配池中未分配的会话
    is_own = session.agent_id == agent_id
    is_waiting_pool = session.agent_id is None and session.status == "waiting"
    if not is_own and not is_waiting_pool:
        logger.warning("Agent WS 会话归属校验失败: session_id=%s agent_id=%s session_agent_id=%s status=%s",
                       session_id, agent_id, session.agent_id, session.status)
        await _ws_close(ws, code=4003, reason="Access denied")
        return

    logger.info("Agent WS handshake: session_id=%s agent_id=%s", session_id, agent_id)

    accepted_proto = f"{_WS_TOKEN_PROTO_PREFIX}{raw_token}" if raw_token else None
    await ws.accept(subprotocol=accepted_proto)
    await handle_agent_session_ws(session_id=str(session_id), ws=ws)


# ====================================================================
# 用户全局通知 WebSocket
# ====================================================================

@ws_router.websocket("/user/notifications")
async def user_notifications_ws(
    ws: WebSocket,
    token: str = Query(default=""),
) -> None:
    """用户全局通知 WebSocket：接收跨会话的未读消息增量通知。

    连接时通过 token 解析 user_id，后续该用户所在会话有新消息时，
    服务端通过此通道推送 new_message 事件，前端据此实时更新侧边栏未读计数。
    """
    raw_token = _extract_ws_token(ws, token)
    payload = _validate_and_decode(raw_token)
    if payload is None:
        await _ws_close(ws, code=4001, reason="Invalid or missing token")
        return

    if payload.get("principal_type") != "user":
        await _ws_close(ws, code=4003, reason="User only")
        return

    user_id: int = payload["principal_id"]

    accepted_proto = f"{_WS_TOKEN_PROTO_PREFIX}{raw_token}" if raw_token else None
    await ws.accept(subprotocol=accepted_proto)
    await user_notification_manager.connect(user_id, ws)

    try:
        while True:
            raw = await ws.receive_text()
            logger.debug("User notification WS received: user_id=%s, data=%s", user_id, raw)
    except WebSocketDisconnect:
        logger.info("User notification WS disconnected: user_id=%s", user_id)
    except Exception as exc:
        logger.warning("User notification WS error: user_id=%s, error=%s", user_id, exc)
    finally:
        user_notification_manager.disconnect(user_id)


# ====================================================================
# 客服全局通知 WebSocket
# ====================================================================

@ws_router.websocket("/agent/notifications")
async def agent_notifications_ws(
    ws: WebSocket,
    token: str = Query(default=""),
) -> None:
    """客服全局通知 WebSocket：接收新会话分配通知，与服务端保持长连接。

    连接时通过 token 解析 agent_id，后续该客服被分配新会话时，
    服务端通过此通道推送 new_session_assigned 事件。
    """
    raw_token = _extract_ws_token(ws, token)
    payload = _validate_and_decode(raw_token)
    if payload is None:
        await _ws_close(ws, code=4001, reason="Invalid or missing token")
        return

    if payload.get("principal_type") != "agent":
        await _ws_close(ws, code=4003, reason="Agent only")
        return

    agent_id: int = payload["principal_id"]

    accepted_proto = f"{_WS_TOKEN_PROTO_PREFIX}{raw_token}" if raw_token else None
    await ws.accept(subprotocol=accepted_proto)
    await agent_notification_manager.connect(agent_id, ws)

    try:
        while True:
            raw = await ws.receive_text()
            logger.debug("Agent notification WS received: agent_id=%s, data=%s", agent_id, raw)
    except WebSocketDisconnect:
        logger.info("Agent notification WS disconnected: agent_id=%s", agent_id)
    except Exception as exc:
        logger.warning("Agent notification WS error: agent_id=%s, error=%s", agent_id, exc)
    finally:
        agent_notification_manager.disconnect(agent_id)
