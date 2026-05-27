"""统一 Agent LLM 节点：原生工具调用 + 最终回复生成。"""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.graph.llm_provider import get_deepseek_llm
from app.graph.models.graph_state import GraphState
from app.graph.tools import get_tools
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── 提示词模板 ────────────────────────────────────────────────

_AGENT_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "你是智能客服系统的统一 Agent 节点。\n"
     "你的任务是基于对话历史中的全部消息（用户消息、审核反馈、工具结果等），"
     "判断是否需要调用工具；如果需要，请直接发起工具调用；"
     "如果不需要，请直接生成可发送给用户的最终回复。\n"
     "【消息类型说明】\n"
     "- human：用户消息\n"
     "- ai(name=\"planner\")：你之前生成的回复或工具调用请求\n"
     "- ai(name=\"reviewer\")：审核节点的反馈意见，参考其建议修正回复\n"
     "- tool：工具执行返回的结果\n"
     "【规则】\n"
     "1) 信息不足且可通过工具补充时，优先调用最匹配的工具\n"
     "2) 参数不充分且无法安全调用工具时，直接向用户追问，不要伪造参数\n"
     "3) 当对话历史中出现审核反馈消息（reviewer 类型）时，参考反馈意见修正回复\n"
     "4) 当审核反馈建议转人工时，给出转人工回复\n"
     "5) 若无需继续调用工具，直接输出面向用户的自然语言回复，不要解释推理过程\n"
     "6) 若用户有转人工诉求，可直接输出转人工回复，不必强制调工具\n"
     "7) 当调用 search_faq 获取检索结果（kb_context）后，先评估质量再决定下一步：\n"
     "   a. 结果是否与用户问题直接相关\n"
     "   b. 信息是否足够回答用户问题\n"
     "   c. 若相关性差或信息不足，尝试用不同的关键词或 search_type 重新调用 search_faq\n"
     "      - 概念性问题（\"怎么退款\"\"退货流程是什么\"）→ search_type=\"semantic\"\n"
     "      - 精确匹配（订单号、错误码、具体术语）→ search_type=\"keyword\"\n"
     "      - 不确定时保持默认 → search_type=\"hybrid\"\n"
     "8) 同一轮对话中 search_faq 最多调用 2 次；若仍不满足，如实告知用户\"未找到相关信息\"，不要编造\n"
     ),
    MessagesPlaceholder(variable_name="messages"),
])

_AGENT_CHAIN = _AGENT_PROMPT | get_deepseek_llm().bind_tools(get_tools())


# ── 节点入口 ────────────────────────────────────────────────

def node_planner(state: GraphState) -> GraphState:
    """统一 LLM 节点：取全部消息 → 调用 LLM → 结果以 AIMessage(name="planner") 写回 messages。

    节点不自行重试 — 异常直接抛出，由 LangGraph add_node 的 retry_policy 统一接管。
    """
    messages = state.get("messages", []) or []
    logger.info("agent_llm_node 开始调用 | msg_count=%s", len(messages))

    result = _AGENT_CHAIN.invoke({"messages": messages})
    ai_message = result if isinstance(result, AIMessage) else AIMessage(content=str(result))
    ai_message.name = "planner"

    tool_calls = getattr(ai_message, "tool_calls", None) or []
    draft_response = "" if tool_calls else str(ai_message.content or "").strip()

    if tool_calls:
        tc_names = [tc.get("name", "?") for tc in tool_calls]
        logger.info("agent_llm_node 工具调用 | %s", tc_names)
    else:
        logger.info("agent_llm_node 草稿回复 | %s", draft_response[:120])

    logger.info(
        "agent_llm_node 调用完毕 | tool_calls=%s has_draft=%s",
        len(tool_calls), bool(draft_response),
    )
    return {
        "messages": [ai_message],
        "draft_response": draft_response,
    }

if __name__ == "__main__":
    from langchain_core.messages import HumanMessage

    def _run_test(label: str, state: GraphState) -> None:
        print(f"{'=' * 60}")
        print(f"【{label}】")
        print(f"{'=' * 60}")
        print(f"[输入] messages: {[(m.type, m.content[:120]) for m in state['messages']]}")
        result = node_planner(state)
        out_msgs = result.get("messages", [])
        draft = result.get("draft_response", "")
        for m in out_msgs:
            tc = getattr(m, "tool_calls", None) or []
            print(f"[输出] type={m.type} name={getattr(m, 'name', '-')} "
                  f"tool_calls={len(tc)} draft_response={draft[:120]!r}")
        print()

    # 测试1: 简单问候，不需要工具
    _run_test("测试1: 简单问候", {
        "messages": [HumanMessage(content="你好，请问你是谁？")],
    })

    # 测试2: FAQ 知识查询，应触发 search_faq 工具
    _run_test("测试2: FAQ查询", {
        "messages": [HumanMessage(content="如何重置账户密码？")],
    })

    # 测试3: 创建工单，应触发 create_ticket 工具
    _run_test("测试3: 创建工单", {
        "messages": [HumanMessage(content="我的订单超时未到，帮我创建一个投诉工单")],
    })

    # 测试4: 带审核反馈的修正场景
    _run_test("测试4: 审核反馈修正", {
        "messages": [
            HumanMessage(content="退款需要多久到账？"),
            AIMessage(content="退款一般3-5个工作日到账。", name="planner"),
            AIMessage(content="回复过于简略，请补充退款失败时的处理说明。", name="reviewer"),
        ],
    })