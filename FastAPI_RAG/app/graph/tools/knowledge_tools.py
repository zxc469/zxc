from __future__ import annotations

from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState
from typing_extensions import Annotated

def _last_human_text(messages: list[BaseMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, HumanMessage):
            return str(msg.content).strip()
    return ""


@tool("search_faq")
def search_faq(
    query: str = "",
    search_type: str = "hybrid",
    messages: Annotated[list[BaseMessage], InjectedState("messages")] = None,
) -> dict[str, Any]:
    """FAQ 检索，返回相关证据片段。

    search_type 可选值：
      - "hybrid"（默认）：Dense 语义 + BM25 关键词混合检索，召回最全
      - "semantic"：仅 Dense 语义检索，适合自然语言描述的概念性问题
      - "keyword"：仅 BM25 关键词检索，适合精确术语、编号、错误码等匹配
    """
    from app.services.agent.vector_search_service import get_vector_search_service

    resolved_query = (query or "").strip()
    if not resolved_query:
        resolved_query = _last_human_text(list(messages or []))
    valid_types = {"hybrid", "semantic", "keyword"}
    resolved_type = search_type if search_type in valid_types else "hybrid"
    results = get_vector_search_service().search(resolved_query, search_type=resolved_type)
    return {"kb_context": results, "count": len(results), "search_type": resolved_type}


__all__ = ["search_faq"]
