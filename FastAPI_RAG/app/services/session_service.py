"""会话管理业务服务层。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.postgres_pool import get_db
from app.db.session_models import (
    SessionCreateInput,
    SessionUpdateInput,
    SessionRecord,
    MessageCreateInput,
    MessageRecord,
)
from app.data_access_service import session_dao
from app.services.auth_service import AuthError


class SessionService:
    """会话管理业务服务。"""

    async def create_session(
        self,
        user_id: int,
        db: AsyncSession,
        source: str = "user_initiated",
        priority: int = 0,
    ) -> SessionRecord:
        """
        【业务功能】创建新会话，默认 AI 模式
        业务规则：
          1. 生成唯一的会话编号
          2. 新会话默认由 AI 处理（handled_by='ai'），不预分配客服
          3. 状态为 active，用户可直接与 AI 对话
        参数：user_id: 用户ID；source: 会话来源；priority: 优先级
        返回：SessionRecord，创建后的会话记录
        异常：AuthError: 创建失败
        """
        try:
            import random

            timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
            random_suffix = str(random.randint(1000, 9999))
            session_no = f"SESS{timestamp}{random_suffix}"

            payload = SessionCreateInput(
                user_id=user_id,
                source=source,
                priority=priority,
            )

            session = await session_dao.create_session_with_agent(
                payload=payload,
                session_no=session_no,
                agent_id=None,
                status="active",
                assigned_at=None,
                db=db,
            )
            return session
        except Exception as e:
            raise AuthError("SESSION_CREATE_FAILED", f"创建会话失败: {str(e)}")

    async def get_session_detail(
        self,
        session_id: int,
        db: AsyncSession,
    ) -> SessionRecord:
        """
        【业务功能】获取会话详情
        业务规则：
          1. 验证会话是否存在
        参数：session_id: 会话ID
        返回：SessionRecord
        异常：AuthError: 会话不存在
        """
        session = await session_dao.get_session_by_id(session_id, db)
        if not session:
            raise AuthError("SESSION_NOT_FOUND", f"会话 {session_id} 不存在")
        return session

    async def get_user_sessions(
        self,
        user_id: int,
        db: AsyncSession,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[SessionRecord], int]:
        """
        【业务功能】获取用户的会话列表
        业务规则：
          1. 按创建时间倒序排列
          2. 支持按状态过滤
          3. 支持分页
        参数：user_id: 用户ID；status: 状态过滤；page: 页码；page_size: 每页数量
        返回：(会话列表, 总数)
        """
        offset = (page - 1) * page_size
        sessions = await session_dao.list_user_sessions(
            user_id,
            status=status,
            limit=page_size,
            offset=offset,
            db=db,
        )
        total = await session_dao.count_user_sessions(user_id, status=status, db=db)

        # 批量富化未读数与最后消息
        if sessions:
            enrichment = await session_dao.get_sessions_enrichment(
                [s.id for s in sessions], db=db
            )
            for s in sessions:
                info = enrichment.get(s.id)
                if info:
                    s.last_message = info["last_message"]
                    s.last_message_time = info["last_message_time"]
                    s.unread_count = info["unread_count"]

        return sessions, total

    async def get_agent_sessions(
        self,
        agent_id: int,
        db: AsyncSession,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[SessionRecord], int]:
        """
        【业务功能】获取客服的会话列表
        业务规则：
          1. 按创建时间倒序排列
          2. 支持按状态过滤
          3. 支持分页
        参数：agent_id: 客服ID；status: 状态过滤；page: 页码；page_size: 每页数量
        返回：(会话列表, 总数)
        """
        offset = (page - 1) * page_size
        sessions = await session_dao.list_agent_sessions(
            agent_id,
            status=status,
            limit=page_size,
            offset=offset,
            db=db,
        )
        total = await session_dao.count_agent_sessions(agent_id, status=status, db=db)

        # 批量富化未读数与最后消息
        if sessions:
            enrichment = await session_dao.get_sessions_enrichment(
                [s.id for s in sessions], db=db
            )
            for s in sessions:
                info = enrichment.get(s.id)
                if info:
                    s.last_message = info["last_message"]
                    s.last_message_time = info["last_message_time"]
                    s.unread_count = info["unread_count"]

        return sessions, total

    async def send_message(
        self,
        session_id: int,
        db: AsyncSession,
        sender_type: str,
        sender_id: Optional[int],
        message_type: str,
        content: str,
        metadata: Optional[dict] = None,
    ) -> MessageRecord:
        """
        【业务功能】发送会话消息
        业务规则：
          1. 验证会话是否存在
          2. 记录消息内容和发送者信息
        参数：session_id: 会话ID；sender_type: 发送者类型；sender_id: 发送者ID；
             message_type: 消息类型；content: 消息内容；metadata: 扩展数据
        返回：MessageRecord，创建后的消息记录
        异常：AuthError: 会话不存在或发送失败
        """
        # 验证会话存在
        session = await session_dao.get_session_by_id(session_id, db)
        if not session:
            raise AuthError("SESSION_NOT_FOUND", f"会话 {session_id} 不存在")

        # agent 首次回复 waiting 会话时自动接受
        if sender_type == "agent" and session.status == "waiting" and sender_id is not None:
            await self.accept_session(session_id=session_id, agent_id=sender_id, db=db)

        payload = MessageCreateInput(
            session_id=session_id,
            sender_type=sender_type,
            sender_id=sender_id,
            message_type=message_type,
            content=content,
            metadata=metadata,
        )
        return await session_dao.create_message(payload, db)

    async def get_session_messages(
        self,
        session_id: int,
        db: AsyncSession,
        page: int = 1,
        page_size: int = 50,
        before_id: Optional[int] = None,
    ) -> tuple[list[MessageRecord], int]:
        """
        【业务功能】获取会话消息列表
        业务规则：
          1. 验证会话是否存在
          2. 按创建时间正序排列
          3. 支持分页和游标加载（before_id）
        参数：session_id: 会话ID；page: 页码；page_size: 每页数量；before_id: 游标ID
        返回：(消息列表, 总数)
        异常：AuthError: 会话不存在
        """
        # 验证会话存在
        session = await session_dao.get_session_by_id(session_id, db)
        if not session:
            raise AuthError("SESSION_NOT_FOUND", f"会话 {session_id} 不存在")

        offset = (page - 1) * page_size
        messages = await session_dao.list_session_messages(
            session_id,
            limit=page_size,
            offset=offset,
            before_id=before_id,
            db=db,
        )
        total = await session_dao.count_session_messages(session_id, db=db)
        return messages, total

    async def mark_session_read(
        self,
        session_id: int,
        db: AsyncSession,
    ) -> int:
        """
        【业务功能】标记会话消息已读
        业务规则：
          1. 验证会话是否存在
          2. 将所有未读消息标记为已读
        参数：session_id: 会话ID
        返回：已读消息数量
        异常：AuthError: 会话不存在
        """
        session = await session_dao.get_session_by_id(session_id, db)
        if not session:
            raise AuthError("SESSION_NOT_FOUND", f"会话 {session_id} 不存在")

        return await session_dao.mark_messages_read(session_id, db)

    async def accept_session(
        self,
        session_id: int,
        agent_id: int,
        db: AsyncSession,
    ) -> SessionRecord:
        """
        【业务功能】客服接受会话
        业务规则：
          1. 验证会话是否存在且状态为 waiting 或 assigned
          2. 如果是 waiting 状态，分配客服ID并增加配额
          3. 如果是 assigned 状态（已自动分配），只需更新状态为 active
          4. 记录接受时间
        参数：session_id: 会话ID；agent_id: 客服ID
        返回：SessionRecord，更新后的会话记录
        异常：AuthError: 会话不存在或状态不正确
        """
        session = await session_dao.get_session_by_id(session_id, db)
        if not session:
            raise AuthError("SESSION_NOT_FOUND", f"会话 {session_id} 不存在")

        # 允许接受 waiting 或 assigned 状态的会话
        if session.status not in ["waiting", "assigned"]:
            raise AuthError("SESSION_INVALID_STATUS", f"会话 {session_id} 状态为 {session.status}，无法接受")

        # 如果是 waiting 状态，需要分配客服并增加配额
        if session.status == "waiting":
            quota_updated = await session_dao.increment_agent_quota(agent_id, db)
            if not quota_updated:
                raise AuthError("AGENT_QUOTA_EXCEEDED", f"客服 {agent_id} 已达最大会话数")

        payload = SessionUpdateInput(
            agent_id=agent_id,  # 分配客服（如果是 waiting）
            status="active",
            handled_by="agent",
            active_at=datetime.now(),
        )
        result = await session_dao.update_session(session_id, payload, db)
        if not result:
            raise AuthError("SESSION_UPDATE_FAILED", "更新会话失败")

        return result

    async def close_session(
        self,
        session_id: int,
        db: AsyncSession,
        closed_by: str,
        close_reason: Optional[str] = None,
    ) -> SessionRecord:
        """
        【业务功能】结束会话
        业务规则：
          1. 验证会话是否存在
          2. 更新会话状态为 closed
          3. 记录关闭时间和原因
        参数：session_id: 会话ID；closed_by: 关闭者；close_reason: 关闭原因
        返回：SessionRecord，更新后的会话记录
        异常：AuthError: 会话不存在
        """
        session = await session_dao.get_session_by_id(session_id, db)
        if not session:
            raise AuthError("SESSION_NOT_FOUND", f"会话 {session_id} 不存在")

        payload = SessionUpdateInput(
            status="closed",
            closed_at=datetime.now(),
            closed_by=closed_by,
            close_reason=close_reason,
        )
        result = await session_dao.update_session(session_id, payload, db)
        if not result:
            raise AuthError("SESSION_UPDATE_FAILED", "更新会话失败")

        # 如果是客服关闭，减少配额
        if session.agent_id and closed_by == "agent":
            await session_dao.decrement_agent_quota(session.agent_id, db)

        return result

    async def rate_session(
        self,
        session_id: int,
        db: AsyncSession,
        rating: int,
        comment: Optional[str] = None,
    ) -> SessionRecord:
        """
        【业务功能】用户评价会话
        业务规则：
          1. 验证会话是否存在
          2. 会话必须已关闭才能评价
          3. 评分范围 1-5
        参数：session_id: 会话ID；rating: 评分；comment: 评价内容
        返回：SessionRecord，更新后的会话记录
        异常：AuthError: 会话不存在或未关闭
        """
        session = await session_dao.get_session_by_id(session_id, db)
        if not session:
            raise AuthError("SESSION_NOT_FOUND", f"会话 {session_id} 不存在")

        if session.status != "closed":
            raise AuthError("SESSION_NOT_CLOSED", f"会话 {session_id} 未关闭，无法评价")

        payload = SessionUpdateInput(
            rating=rating,
            rating_comment=comment,
        )
        result = await session_dao.update_session(session_id, payload, db)
        if not result:
            raise AuthError("SESSION_UPDATE_FAILED", "更新会话失败")
        return result

    async def transfer_session(
        self,
        session_id: int,
        db: AsyncSession,
        from_agent_id: int,
        to_agent_id: int,
        reason: Optional[str] = None,
    ) -> SessionRecord:
        """
        【业务功能】转接会话给其他客服
        业务规则：
          1. 验证会话是否存在
          2. 减少原客服配额，增加新客服配额
          3. 更新会话的客服ID
        参数：session_id: 会话ID；from_agent_id: 原客服ID；to_agent_id: 新客服ID；reason: 转接原因
        返回：SessionRecord，更新后的会话记录
        异常：AuthError: 会话不存在或配额不足
        """
        session = await session_dao.get_session_by_id(session_id, db)
        if not session:
            raise AuthError("SESSION_NOT_FOUND", f"会话 {session_id} 不存在")

        # 减少原客服配额
        if from_agent_id:
            await session_dao.decrement_agent_quota(from_agent_id, db)

        # 增加新客服配额
        quota_increased = await session_dao.increment_agent_quota(to_agent_id, db)
        if not quota_increased:
            raise AuthError("AGENT_QUOTA_EXCEEDED", f"客服 {to_agent_id} 已达最大会话数")

        payload = SessionUpdateInput(
            agent_id=to_agent_id,
            status="assigned",
            handled_by="agent",
        )
        result = await session_dao.update_session(session_id, payload, db)
        if not result:
            raise AuthError("SESSION_UPDATE_FAILED", "更新会话失败")
        return result

    async def transfer_to_ai(
        self,
        session_id: int,
        db: AsyncSession,
        agent_id: int,
    ) -> SessionRecord:
        """
        【业务功能】客服将会话转回 AI 模式
        业务规则：
          1. 验证会话存在且属于该客服
          2. 将 handled_by 置为 ai
          3. 释放客服配额
        参数：session_id: 会话ID；agent_id: 客服ID
        返回：SessionRecord，更新后的会话记录
        异常：AuthError: 会话不存在或状态不正确
        """
        session = await session_dao.get_session_by_id(session_id, db)
        if not session:
            raise AuthError("SESSION_NOT_FOUND", f"会话 {session_id} 不存在")
        if session.agent_id != agent_id:
            raise AuthError("SESSION_NOT_OWNED", "会话不属于当前客服")

        if session.agent_id:
            await session_dao.decrement_agent_quota(session.agent_id, db)

        payload = SessionUpdateInput(
            handled_by="ai",
            agent_id=None,
            status="active",
        )
        result = await session_dao.update_session(session_id, payload, db)
        if not result:
            raise AuthError("SESSION_UPDATE_FAILED", "更新会话失败")
        return result

    async def get_agent_stats(
        self,
        agent_id: int,
        db: AsyncSession,
    ) -> dict:
        """
        【业务功能】获取客服会话统计
        业务规则：
          1. 统计各类状态的会话数量
          2. 计算平均响应时间（如果有数据）
        参数：agent_id: 客服ID
        返回：统计数据字典
        """
        # 获取各状态会话数
        total = await session_dao.count_agent_sessions(agent_id, db=db)
        active = await session_dao.count_agent_sessions(agent_id, status="active", db=db)
        waiting = await session_dao.count_agent_sessions(agent_id, status="waiting", db=db)

        # 获取配额信息
        quota = await session_dao.get_agent_quota(agent_id, db=db)

        return {
            "total_sessions": total,
            "active_sessions": active,
            "waiting_sessions": waiting,
            "current_quota": quota.current_sessions if quota else 0,
            "max_quota": quota.max_sessions if quota else 5,
        }


# 全局单例
session_service = SessionService()
