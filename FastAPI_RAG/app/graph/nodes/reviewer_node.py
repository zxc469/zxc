"""审核 Agent：审核生成器回复质量并输出修正决策。"""

from __future__ import annotations

from langchain_core.messages import AIMessage
from langchain_core.output_parsers import PydanticOutputParser, StrOutputParser
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from app.graph.llm_provider import get_deepseek_llm
from app.graph.models.protocol_models import ReviewDecision, ReviewResult
from app.graph.models.graph_state import GraphState
from app.utils.logger import get_logger

logger = get_logger(__name__)


# ── 提示词模板 ────────────────────────────────────────────────

_REVIEWER_PROMPT = ChatPromptTemplate.from_messages([
    ("system",
     "你是智能客服系统的审核器LM1。\n"
     "你的任务是检查生成器输出的回复是否满足用户需求，输出审核决策，不要输出解释文字。\n"
     "必须严格遵守输出格式约束。\n"
     "{format_instructions}\n"
     "【审核决策说明】\n"
     "- approve：回复准确完整，可直接输出给用户\n"
     "- revise：回复需要修正，返回统一 LLM 节点重写\n"
     "- handoff_human：问题需要人工介入处理\n"
     "【消息类型说明】\n"
     "- human：用户消息\n"
     "- ai(name=\"planner\")：生成器回复草稿或工具调用请求\n"
     "- ai(name=\"reviewer\")：你之前给出的审核反馈（历史轮次）\n"
     "- tool：工具执行返回的结果\n"
     "【常见审核文字要求】\n"
     "1) 准确性：不得编造订单状态、物流节点、优惠政策等未提供信息\n"
     "2) 完整性：要覆盖用户问题核心，避免答非所问或只答一半\n"
     "3) 一致性：与用户输入、上下文及工具结果保持一致，冲突时优先工具结果\n"
     "4) 清晰性：表达简洁、步骤清楚，避免模糊措辞和歧义\n"
     "5) 语气要求：礼貌、自然、客服口吻，不生硬、不命令式\n"
     "6) 安全合规：不输出敏感隐私，不提供违规、危险或越权指引\n"
     "7) 可执行性：涉及操作建议时，应给出用户可执行的下一步\n"
     "8) 检索支撑：若回复引用了知识库检索结果（对话历史中的 tool 消息含 kb_context），"
     "检查回复结论是否与检索结果原文一致；若矛盾或曲解原文，必须判 revise\n"
     "9) 检索遗漏：若用户问题明显需要知识库支撑（如产品规格、流程说明、政策条款等事实性查询），"
     "但对话历史中从未调用 search_faq，应判 revise 并要求先检索再回答\n"
     ),
    MessagesPlaceholder(variable_name="messages"),
])

_PARSER = PydanticOutputParser(pydantic_object=ReviewResult)
_REVIEWER_CHAIN = _REVIEWER_PROMPT | get_deepseek_llm() | StrOutputParser() | _PARSER


# ── 节点入口 ────────────────────────────────────────────────

def node_reviewer(state: GraphState) -> GraphState:
    """审核节点：全部消息 → LLM 审核 → ChatMessage(role="reviewer") 写入 messages。

    节点不自行重试 — 异常直接抛出，由 LangGraph add_node 的 retry_policy 统一接管。
    """
    review_cycle_count = int(state.get("review_cycle_count", 0))
    max_review_cycles = int(state.get("max_review_cycles", 2))

    if review_cycle_count >= max_review_cycles:
        logger.warning(
            "reviewer_node 跳过审核 | 审核循环超限 count=%s max=%s",
            review_cycle_count, max_review_cycles,
        )
        return {
            **state,
            "has_error": True,
            "current_failed_node": "reviewer_node",
            "error_msg": f"审核循环次数超限: {review_cycle_count}/{max_review_cycles}",
        }

    messages = state.get("messages", []) or []
    draft_text = str(state.get("draft_response", "")).strip()

    logger.info(
        "reviewer_node 开始审核 | msg_count=%s draft_len=%s cycle=%s",
        len(messages), len(draft_text), review_cycle_count,
    )

    review = _REVIEWER_CHAIN.invoke({
        "format_instructions": _PARSER.get_format_instructions(),
        "messages": messages,
    })

    reviewer_msg = AIMessage(
        content=f"[审核结果] 决策：{review.decision.value}\n反馈：{review.review_feedback}",
        name="reviewer",
    )

    approved_answer = draft_text if review.decision == ReviewDecision.APPROVE else ""

    result: GraphState = {
        "messages": [reviewer_msg],
        "review_decision": review.decision.value if review.decision else "",
        "review_feedback": review.review_feedback,
        "need_human": review.decision == ReviewDecision.HANDOFF_HUMAN or bool(state.get("need_human", False)),
        "draft_response": draft_text,
        "final_answer": approved_answer or str(state.get("final_answer", "")),
        "review_cycle_count": review_cycle_count + 1,
    }

    logger.info("reviewer_node 审核决策 | decision=%s feedback=%s", review.decision.value, review.review_feedback[:80])

    logger.info(
        "reviewer_node 审核完毕 | decision=%s feedback_len=%s cycle=%s",
        review.decision.value if review.decision else "",
        len(review.review_feedback or ""),
        result.get("review_cycle_count", 0),
    )
    return result
