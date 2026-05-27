"""LangGraph 组装入口（流程拓扑 + checkpointer 工厂）。"""

from typing import Any

from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode
from langgraph.types import RetryPolicy

from app.config.agent_config import agent_config
from app.graph.models.graph_state import GraphState
from app.graph.nodes.agent_llm_node import node_planner
from app.graph.nodes.reviewer_node import node_reviewer
from app.graph.nodes.rule_preprocessor_node import node_rule_preprocessor
from app.graph.nodes.terminal_response_node import node_degraded_response, node_final_response
from app.graph.tools import get_tools
from app.graph.trace_execution import record_error_in_state, trace_execution
from app.graph.routing.graph_route_decider import (
    NEXT_AGENT_LLM,
    NEXT_DEGRADE,
    NEXT_FINAL,
    NEXT_REVIEWER,
    NEXT_TOOLS,
    route_after_agent_llm,
    route_after_rule,
    route_after_reviewer,
    route_after_tools,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

_RETRY_POLICY = RetryPolicy(max_attempts=3)


def _make_error_handler(node_name: str):
    """构建节点错误处理器：重试耗尽后写入 has_error 标记和执行状态，由路由导向降级节点。"""
    def _on_error(state: GraphState) -> GraphState:
        logger.warning("节点重试耗尽，进入降级链路 | node=%s", node_name)
        error_msg = str(state.get("error_msg", "") or f"节点 {node_name} 重试耗尽")
        return record_error_in_state(state, node_name, error_msg)
    return _on_error


def build_graph_checkpointer() -> Any | None:
    """创建 LangGraph 内存 checkpointer。"""
    if not agent_config.graph.enable_checkpointer:
        return None
    try:
        from langgraph.checkpoint.memory import InMemorySaver
        from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError("缺少 `langgraph.checkpoint.memory.InMemorySaver` 依赖，无法启用 checkpointer。") from exc

    serde = JsonPlusSerializer(
        allowed_msgpack_modules=[
            ("app.graph.models.protocol_models", "RuleDecisionType"),
            ("app.graph.models.protocol_models", "RuleDecision"),
            ("app.graph.models.execution_state", "NodeExecutionRecord"),
            ("app.graph.models.execution_state", "ExecutionStateSnapshot"),
            ("app.graph.models.protocol_models", "ConversationContext"),
        ],
    )
    return InMemorySaver(serde=serde)


def build_customer_service_graph(checkpointer: object | None = None):
    """构建客服图正式主流程。

    当前拓扑：
    ┌─────────────────┐    规则命中    ┌───────────────┐
    │ rule_preprocessor│──────────────→│ final_response │
    └────────┬────────┘               └───────────────┘
             │ PASS_TO_LLM
    ┌────────▼────────┐               ┌───────────────┐
    │ agent_llm_node  │──handoff_human→│ final_response │
    │  (统一 LLM)     │               └───────────────┘
    └──┬──────────┬───┘
       │call_tools│answer_direct/ask_clarify
       ▼          ▼
    ┌──────────┐  ┌───────────────────┐
    │  tool    │  │   reviewer_node   │
    │ executor │  │   (纯审核)         │
    └────┬─────┘  └──┬─────────────┬──┘
        │           │             │
        │           │approve      │revise/handoff_human
         ▼           ▼             ▼
    ┌──────────┐  ┌───────────────┐ ┌───────────────┐
    │ agent_llm│  │ final_response │ │ agent_llm_node│
    └──────────┘  └───────────────┘ └───────────────┘
                                     
    ┌───────────────────┐     approve     ┌───────────────┐
    │   reviewer_node   │────────────────→│ final_response │
    │   (纯审核)         │                └───────────────┘
    └──┬────────────────┘
       │
       └──revise → agent_llm_node
    """
    graph = StateGraph(GraphState)

    graph.add_node("rule_preprocessor", trace_execution("rule_preprocessor")(node_rule_preprocessor))
    graph.add_node("agent_llm_node", trace_execution("agent_llm_node")(node_planner),
                   retry_policy=_RETRY_POLICY,
                   error_handler=_make_error_handler("agent_llm_node"))

    graph.add_node("tool_executor", ToolNode(get_tools()))

    graph.add_node("reviewer_node", trace_execution("reviewer_node")(node_reviewer),
                   retry_policy=_RETRY_POLICY,
                   error_handler=_make_error_handler("reviewer_node"))

    graph.add_node("final_response", trace_execution("final_response")(node_final_response))
    graph.add_node("degraded_response", trace_execution("degraded_response")(node_degraded_response))

    graph.set_entry_point("rule_preprocessor")

    graph.add_conditional_edges(
        "rule_preprocessor",
        route_after_rule,
        {
            NEXT_FINAL: "final_response",
            NEXT_AGENT_LLM: "agent_llm_node",
        },
    )

    graph.add_conditional_edges(
        "agent_llm_node",
        route_after_agent_llm,
        {
            NEXT_TOOLS: "tool_executor",
            NEXT_REVIEWER: "reviewer_node",
            NEXT_DEGRADE: "degraded_response",
        },
    )

    graph.add_conditional_edges(
        "tool_executor",
        route_after_tools,
        {
            NEXT_AGENT_LLM: "agent_llm_node",
            NEXT_DEGRADE: "degraded_response",
        },
    )

    graph.add_conditional_edges(
        "reviewer_node",
        route_after_reviewer,
        {
            NEXT_FINAL: "final_response",
            NEXT_AGENT_LLM: "agent_llm_node",
            NEXT_DEGRADE: "degraded_response",
        },
    )

    graph.add_edge("final_response", END)
    graph.add_edge("degraded_response", END)

    return graph.compile(checkpointer=checkpointer or build_graph_checkpointer())


if __name__ == "__main__":
    app = build_customer_service_graph()
    graph_obj = app.get_graph()
    mermaid_code = graph_obj.draw_mermaid()
    print(mermaid_code)
