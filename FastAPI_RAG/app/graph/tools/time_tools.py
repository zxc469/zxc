"""
基于 MCP 协议的持久会话时间工具。
Agent 调用时通过 McpSessionManager 复用已有连接，不再每次起停进程。
"""

from __future__ import annotations

from langchain_core.tools import tool
from mcp_servers.mcp_client import McpSessionManager


def _mcp() -> McpSessionManager:
    return McpSessionManager.get()


@tool("mcp_get_current_time")
async def mcp_get_current_time(timezone_name: str = "Asia/Shanghai") -> str:
    """获取指定时区的当前时间（通过 MCP 协议调用）。"""
    return await _mcp().call_tool("get_current_time", {"timezone_name": timezone_name})


@tool("mcp_get_utc_timestamp")
async def mcp_get_utc_timestamp() -> str:
    """获取当前 UTC 时间戳，包含 ISO、Unix 秒、Unix 毫秒格式（通过 MCP 协议调用）。"""
    return await _mcp().call_tool("get_utc_timestamp", {})
