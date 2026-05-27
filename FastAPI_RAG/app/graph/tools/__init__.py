from app.graph.tools.knowledge_tools import search_faq
from app.graph.tools.ticket_tools import create_ticket
from app.graph.tools.time_tools import mcp_get_current_time, mcp_get_utc_timestamp

TOOLS = [search_faq, create_ticket, mcp_get_current_time, mcp_get_utc_timestamp]


def get_tools():
    return list(TOOLS)


__all__ = [
    "TOOLS",
    "create_ticket",
    "get_tools",
    "mcp_get_current_time",
    "mcp_get_utc_timestamp",
    "search_faq",
]
