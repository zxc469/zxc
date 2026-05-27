from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from typing_extensions import Annotated


def _build_placeholder_ticket_id() -> str:
    date_str = datetime.now().strftime("%Y%m%d")
    random_part = str(uuid4())[:6].upper()
    return f"TMP{date_str}{random_part}"


def _last_human_text(messages: list[BaseMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return str(msg.content).strip()
    return ""


@tool("create_ticket")
def create_ticket(
    user_message: str = "",
    category: str = "售后咨询",
    messages: Annotated[list[BaseMessage], InjectedState("messages")] = None,
) -> dict[str, Any]:
    """建单占位：生成占位工单号，并返回 tool_text 供最终回复拼装。"""
    resolved_user_message = (user_message or "").strip()
    if not resolved_user_message:
        resolved_user_message = _last_human_text(list(messages or []))
    resolved_category = (category or "").strip() or "售后咨询"
    ticket_id = _build_placeholder_ticket_id()
    tool_text = (
        f"已记录建单请求，当前为占位实现。"
        f"占位工单号：{ticket_id}，分类：{resolved_category}，描述：{resolved_user_message or '用户未提供具体描述'}。"
    )
    return {
        "ticket_id": ticket_id,
        "category": resolved_category,
        "user_message": resolved_user_message,
        "status": "placeholder_created",
        "placeholder": True,
        "next_action": "后续接入真实工单系统后替换当前占位实现",
        "tool_text": tool_text,
    }


__all__ = ["create_ticket"]
