"""WebSocket 服务：用户-客服实时消息推送。

支持两种连接：
1. 用户端 WebSocket：/ws/chat/{session_id}
2. 客服端 WebSocket：/ws/agent/{conversation_id}

消息流转：
用户发送消息 → HTTP POST → 数据库存储 → WebSocket 推送给客服
客服发送消息 → HTTP POST → 数据库存储 → WebSocket 推送给用户
"""

import asyncio
import json
from datetime import datetime
from typing import Optional

from fastapi import WebSocket
from fastapi.websockets import WebSocketDisconnect

from app.utils.logger import get_logger

logger = get_logger(__name__)


class SessionWsManager:
    """会话 WebSocket 连接管理器。
    
    维护两种映射：
    - session_id → user WebSocket（用户端连接）
    - session_id → agent WebSocket（客服端连接）
    """

    def __init__(self) -> None:
        # session_id → user websocket
        self._user_connections: dict[str, WebSocket] = {}
        # session_id → agent websocket
        self._agent_connections: dict[str, WebSocket] = {}

    async def connect_user(self, session_id: str, ws: WebSocket) -> None:
        """
        【业务功能】注册用户端 WebSocket 连接（WebSocket 由上层提前 accept）
        业务规则：1. 先关闭同 session_id 的旧连接 2. 替换为新连接
        参数：session_id: 会话 ID, ws: WebSocket 实例（已 accept）
        返回：None
        """
        old = self._user_connections.get(session_id)
        if old is not None:
            try:
                await old.close()
            except Exception:
                pass
        self._user_connections[session_id] = ws
        logger.info("User WS connected: session_id=%s", session_id)

    async def connect_agent(self, session_id: str, ws: WebSocket) -> None:
        """
        【业务功能】注册客服端 WebSocket 连接（WebSocket 由上层提前 accept）
        业务规则：1. 先关闭同 session_id 的旧连接 2. 替换为新连接
        参数：session_id: 会话 ID, ws: WebSocket 实例（已 accept）
        返回：None
        """
        old = self._agent_connections.get(session_id)
        if old is not None:
            try:
                await old.close()
            except Exception:
                pass
        self._agent_connections[session_id] = ws
        logger.info("Agent WS connected: session_id=%s", session_id)

    def disconnect_user(self, session_id: str) -> None:
        """注销用户端 WebSocket 连接。"""
        self._user_connections.pop(session_id, None)
        logger.info("User WS disconnected: session_id=%s", session_id)

    def disconnect_agent(self, session_id: str) -> None:
        """注销客服端 WebSocket 连接。"""
        self._agent_connections.pop(session_id, None)
        logger.info("Agent WS disconnected: session_id=%s", session_id)

    async def send_to_user(self, session_id: str, message: dict) -> bool:
        """
        【业务功能】向用户端推送消息
        参数：session_id: 会话 ID, message: 消息字典
        返回：True=发送成功，False=连接不存在
        """
        ws = self._user_connections.get(session_id)
        if ws is None:
            return False
        try:
            await ws.send_text(json.dumps(message, ensure_ascii=False))
            return True
        except Exception as e:
            logger.warning("Failed to send to user: session_id=%s, error=%s", session_id, e)
            return False

    async def send_to_agent(self, session_id: str, message: dict) -> bool:
        """
        【业务功能】向客服端推送消息
        参数：session_id: 会话 ID, message: 消息字典
        返回：True=发送成功，False=连接不存在
        """
        ws = self._agent_connections.get(session_id)
        if ws is None:
            return False
        try:
            await ws.send_text(json.dumps(message, ensure_ascii=False))
            return True
        except Exception as e:
            logger.warning("Failed to send to agent: session_id=%s, error=%s", session_id, e)
            return False

    async def broadcast_message(
        self,
        session_id: str,
        sender_type: str,
        message_data: dict,
    ) -> None:
        """
        【业务功能】广播消息到会话的双方（用户和客服）

        业务规则：
          1. 用户发送的消息 → 推送给客服
          2. 客服发送的消息 → 推送给用户
          3. AI 回复 → 推送给用户（也推送给客服，保持对话记录同步）

        降级策略：
          消息始终先落库再推送 WS。WS 推送失败（对方不在线或不在当前会话页）
          时仅记录告警日志，不影响 HTTP 响应。前端重新进入会话页时通过
          GET /sessions/{id}/messages 拉取历史消息，不会丢失。

        Args:
            session_id: 会话 ID
            sender_type: 发送者类型（user/agent/ai）
            message_data: 消息字典

        Returns:
            None
        """
        if sender_type == "user":
            if not await self.send_to_agent(session_id, message_data):
                logger.warning(
                    "WS 推送失败(用户消息→客服): session_id=%s, 客服未连接或已断开",
                    session_id,
                )
        elif sender_type == "agent":
            if not await self.send_to_user(session_id, message_data):
                logger.warning(
                    "WS 推送失败(客服消息→用户): session_id=%s, 用户未连接或已断开",
                    session_id,
                )
        elif sender_type == "ai":
            # AI 回复仅推送给客服端用于监控，用户端通过 HTTP 响应获取，避免重复
            if not await self.send_to_agent(session_id, message_data):
                logger.warning(
                    "WS 推送失败(AI回复→客服): session_id=%s, 客服未连接或已断开",
                    session_id,
                )


# 全局单例会话连接管理器
session_ws_manager = SessionWsManager()


async def handle_user_session_ws(session_id: str, ws: WebSocket) -> None:
    """
    【业务功能】处理用户端会话 WebSocket 全生命周期
    业务规则：
      1. 接受连接后推送一条 system 欢迎消息
      2. 持续监听消息帧（暂不处理用户通过 WS 发送消息，统一走 HTTP POST）
      3. 连接断开后注销连接
    参数：session_id: 会话 ID, ws: WebSocket 实例
    返回：None
    """
    await session_ws_manager.connect_user(session_id, ws)
    
    # 推送欢迎消息
    welcome = {
        "type": "system",
        "id": "0",
        "sender": "system",
        "content": "已连接到客服系统",
        "timestamp": datetime.now().isoformat(),
    }
    await session_ws_manager.send_to_user(session_id, welcome)
    
    try:
        while True:
            # 保持连接活跃，接收心跳或忽略消息
            # 用户发送消息统一走 HTTP POST /user/sessions/{id}/messages
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
                logger.debug("User WS received: session_id=%s, data=%s", session_id, data)
            except json.JSONDecodeError:
                continue

    except WebSocketDisconnect:
        logger.info("User WS disconnected: session_id=%s", session_id)
    except Exception as exc:
        logger.warning("User WS error: session_id=%s, error=%s", session_id, exc)
    finally:
        session_ws_manager.disconnect_user(session_id)


async def handle_agent_session_ws(session_id: str, ws: WebSocket) -> None:
    """
    【业务功能】处理客服端会话 WebSocket 全生命周期
    业务规则：
      1. 接受连接后推送一条 system 欢迎消息
      2. 持续监听消息帧（暂不处理客服通过 WS 发送消息，统一走 HTTP POST）
      3. 连接断开后注销连接
    参数：session_id: 会话 ID, ws: WebSocket 实例
    返回：None
    """
    await session_ws_manager.connect_agent(session_id, ws)
    
    # 推送欢迎消息
    welcome = {
        "type": "system",
        "id": "0",
        "sender": "system",
        "content": "已连接到会话",
        "timestamp": datetime.now().isoformat(),
    }
    await session_ws_manager.send_to_agent(session_id, welcome)
    
    try:
        while True:
            # 保持连接活跃
            # 客服发送消息统一走 HTTP POST /agent/sessions/{id}/messages
            raw = await ws.receive_text()
            try:
                data = json.loads(raw)
                logger.debug("Agent WS received: session_id=%s, data=%s", session_id, data)
            except json.JSONDecodeError:
                continue

    except WebSocketDisconnect:
        logger.info("Agent WS disconnected: session_id=%s", session_id)
    except Exception as exc:
        logger.warning("Agent WS error: session_id=%s, error=%s", session_id, exc)
    finally:
        session_ws_manager.disconnect_agent(session_id)


async def notify_new_message(
    session_id: str,
    sender_type: str,
    message_id: int,
    content: str,
    timestamp: datetime,
) -> None:
    """
    【业务功能】通知会话双方有新消息（在 HTTP POST 发送消息后调用）
    业务规则：
      1. 用户发送的消息 → 推送给客服
      2. 客服发送的消息 → 推送给用户
    参数：session_id: 会话 ID, sender_type: 发送者类型（user/agent）,
          message_id: 消息 ID, content: 消息内容, timestamp: 时间戳
    返回：None
    """
    message_data = {
        "type": "new_message",
        "id": str(message_id),
        "sender": sender_type,
        "content": content,
        "timestamp": timestamp.isoformat(),
    }
    
    await session_ws_manager.broadcast_message(session_id, sender_type, message_data)


async def notify_session_closed(session_id: str) -> None:
    """通知双方会话已关闭。"""
    message_data = {
        "type": "system",
        "id": "0",
        "sender": "system",
        "event": "session_closed",
        "content": "会话已结束",
        "timestamp": datetime.now().isoformat(),
    }
    await session_ws_manager.send_to_user(session_id, message_data)
    await session_ws_manager.send_to_agent(session_id, message_data)


async def notify_handled_by_changed(session_id: str, handled_by: str) -> None:
    """通知双方会话处理模式已切换（AI/人工）。"""
    message_data = {
        "type": "system",
        "id": "0",
        "sender": "system",
        "event": "handled_by_changed",
        "handled_by": handled_by,
        "content": "会话已切换为AI模式" if handled_by == "ai" else "会话已切换为人工模式",
        "timestamp": datetime.now().isoformat(),
    }
    await session_ws_manager.send_to_user(session_id, message_data)
    await session_ws_manager.send_to_agent(session_id, message_data)


# ==================== 用户全局通知通道 ====================

class UserNotificationManager:
    """用户全局通知连接管理器。

    每个用户保持一条全局 WebSocket，用于接收未读消息增量通知，
    与 session 级别的 WebSocket（/ws/chat/{session_id}）互不干扰。
    解决用户浏览会话列表页或查看其他会话时无法实时更新未读计数的问题。
    """

    def __init__(self) -> None:
        self._connections: dict[int, WebSocket] = {}

    async def connect(self, user_id: int, ws: WebSocket) -> None:
        """注册用户全局通知连接（WebSocket 由上层提前 accept）。"""
        old = self._connections.get(user_id)
        if old is not None:
            try:
                await old.close()
            except Exception:
                pass
        self._connections[user_id] = ws
        logger.info("User notification WS connected: user_id=%s", user_id)

    def disconnect(self, user_id: int) -> None:
        self._connections.pop(user_id, None)
        logger.info("User notification WS disconnected: user_id=%s", user_id)

    async def send(self, user_id: int, message: dict) -> bool:
        ws = self._connections.get(user_id)
        if ws is None:
            return False
        try:
            await ws.send_text(json.dumps(message, ensure_ascii=False))
            return True
        except Exception as e:
            logger.warning("Failed to send to user notification: user_id=%s, error=%s", user_id, e)
            return False


user_notification_manager = UserNotificationManager()


async def notify_user_global_message(
    user_id: int,
    session_id: int,
    message_id: int,
    content: str,
    timestamp: datetime,
    sender_type: str = "agent",
) -> None:
    """向指定用户的全局通知通道推送新消息事件，用于实时更新侧边栏未读计数。

    与 per-session WS 互补：per-session WS 负责当前会话的实时消息，
    全局通道负责任意会话的未读增量更新。
    """
    payload = {
        "type": "new_message",
        "session_id": session_id,
        "sender": sender_type,
        "message_id": message_id,
        "content": content,
        "timestamp": timestamp.isoformat(),
    }
    await user_notification_manager.send(user_id, payload)


# ==================== 客服全局通知通道 ====================
# 解决客服未连接具体 session 时无法收到新会话通知的问题

class AgentNotificationManager:
    """客服全局通知连接管理器。

    每个客服保持一条全局 WebSocket，用于接收新会话分配通知，
    与 session 级别的 WebSocket（/ws/agent/{session_id}）互不干扰。
    """

    def __init__(self) -> None:
        self._connections: dict[int, WebSocket] = {}

    async def connect(self, agent_id: int, ws: WebSocket) -> None:
        """注册客服全局通知连接（WebSocket 由上层提前 accept）。"""
        old = self._connections.get(agent_id)
        if old is not None:
            try:
                await old.close()
            except Exception:
                pass
        self._connections[agent_id] = ws
        logger.info("Agent notification WS connected: agent_id=%s", agent_id)

    def disconnect(self, agent_id: int) -> None:
        self._connections.pop(agent_id, None)
        logger.info("Agent notification WS disconnected: agent_id=%s", agent_id)

    async def send(self, agent_id: int, message: dict) -> bool:
        ws = self._connections.get(agent_id)
        if ws is None:
            return False
        try:
            await ws.send_text(json.dumps(message, ensure_ascii=False))
            return True
        except Exception as e:
            logger.warning("Failed to send to agent notification: agent_id=%s, error=%s", agent_id, e)
            return False

    async def broadcast(self, message: dict) -> int:
        """向所有已连接的客服广播消息，返回成功发送数。"""
        count = 0
        for agent_id in list(self._connections.keys()):
            if await self.send(agent_id, message):
                count += 1
        return count

    @property
    def connected_agent_ids(self) -> list[int]:
        return list(self._connections.keys())


agent_notification_manager = AgentNotificationManager()


async def notify_agent_new_session(
    agent_id: int,
    session_detail: dict,
    messages: list[dict],
) -> None:
    """通知指定客服有新会话分配，携带完整会话上下文。

    客服端收到后可直接插入侧边栏和消息缓存，无需额外 HTTP 请求。
    """
    message_data = {
        "type": "system",
        "id": "0",
        "sender": "system",
        "event": "new_session_assigned",
        "session": session_detail,
        "messages": messages,
        "content": f"新会话 {session_detail.get('session_no', '')} 已分配给您",
        "timestamp": datetime.now().isoformat(),
    }
    await agent_notification_manager.send(agent_id, message_data)


async def notify_waiting_session_broadcast(
    session_detail: dict,
    messages: list[dict],
) -> None:
    """通知所有在线客服有新的等待中会话，携带完整会话上下文。"""
    message_data = {
        "type": "system",
        "id": "0",
        "sender": "system",
        "event": "new_waiting_session",
        "session": session_detail,
        "messages": messages,
        "content": f"新会话 {session_detail.get('session_no', '')} 等待客服接入",
        "timestamp": datetime.now().isoformat(),
    }
    count = await agent_notification_manager.broadcast(message_data)
    if count > 0:
        logger.info(
            "Broadcast waiting session to %d agents: session_id=%s",
            count,
            session_detail.get("id"),
        )
