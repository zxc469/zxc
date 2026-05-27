"""LangGraph 条件路由函数。"""

from langgraph.prebuilt import tools_condition

from app.graph.models.protocol_models import ReviewDecision
from app.graph.models.graph_state import GraphState
from app.utils.logger import get_logger

NEXT_FINAL = "to_final_response"
NEXT_AGENT_LLM = "to_agent_llm_node"
NEXT_TOOLS = "to_tool_executor"
NEXT_REVIEWER = "to_reviewer_node"
NEXT_DEGRADE = "to_degraded_response"

logger = get_logger(__name__)


def route_after_rule(state: GraphState) -> str:
    """
    【工具功能】规则节点后的路由决策
    支持范围：规则命中（final_answer / need_human）→ 终态；未命中 → agent_llm_node
    参数：state: 图状态
    返回：NEXT_FINAL / NEXT_AGENT_LLM
    """
    if state.get("need_human") or state.get("final_answer"):
        next_hop = NEXT_FINAL
    else:
        next_hop = NEXT_AGENT_LLM
    logger.info(
        "规则后路由: need_human=%s has_final_answer=%s -> 下一跳=%s",
        bool(state.get("need_human")),
        bool(state.get("final_answer")),
        next_hop,
    )
    return next_hop


def route_after_tools(state: GraphState) -> str:
    if bool(state.get("has_error")):
        next_hop = NEXT_DEGRADE
    else:
        next_hop = NEXT_AGENT_LLM
    logger.info(
        "工具后路由: has_error=%s messages=%s -> 下一跳=%s",
        bool(state.get("has_error")),
        len(state.get("messages", []) or []),
        next_hop,
    )
    return next_hop


def route_after_reviewer(state: GraphState) -> str:
    decision = str(state.get("review_decision", "")).strip().lower()

    if bool(state.get("has_error")):
        next_hop = NEXT_DEGRADE
    elif decision in (ReviewDecision.APPROVE.value, ReviewDecision.HANDOFF_HUMAN.value):
        next_hop = NEXT_FINAL
    elif decision == ReviewDecision.REVISE.value:
        next_hop = NEXT_AGENT_LLM
    else:
        next_hop = NEXT_AGENT_LLM

    logger.info(
        "审核后路由: decision=%s has_error=%s -> 下一跳=%s",
        decision,
        bool(state.get("has_error")),
        next_hop,
    )
    return next_hop


def route_after_agent_llm(state: GraphState) -> str:
    if bool(state.get("has_error")):
        next_hop = NEXT_DEGRADE
    elif tools_condition(state) == "tools":
        next_hop = NEXT_TOOLS
    else:
        next_hop = NEXT_REVIEWER
    logger.info(
        "agent_llm 后路由: has_error=%s -> 下一跳=%s",
        bool(state.get("has_error")),
        next_hop,
    )
    return next_hop
