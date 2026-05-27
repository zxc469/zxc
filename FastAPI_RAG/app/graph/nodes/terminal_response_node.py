"""终态 Agent：final_response / degraded_response — 追加 AIMessage 到消息历史。
"""

from __future__ import annotations

from app.graph.models.graph_state import GraphState
from app.utils.logger import get_logger

logger = get_logger(__name__)


class FinalResponseAgent:
    """完成态 Agent：输出正常终态（final_response）。"""

    def run(self, state: GraphState) -> GraphState:
        """
        【工具功能】构建正常终态输出并统一终态字段
        支持：final_answer 已存在/不存在两种场景（不存在时使用默认文案）
        参数：state: 图共享状态
        返回：包含 final_answer/messages 的终态状态
        异常：无
        """
        final_text = ("已为你转接人工客服，请稍等。"
            if state.get("need_human")
            else (
                state.get("final_answer")
                or state.get("draft_response")
                or "会话已结束，如需帮助请随时再联系。"
            )
        )
        final_text = str(final_text)
        logger.info("final_response 最终回复 | %s", final_text[:120])
        return {
            **state,
            "final_answer": final_text,
        }


class DegradedResponseAgent:
    """降级态 Agent：输出降级终态（degraded_response）。"""

    def run(self, state: GraphState) -> GraphState:
        """
        【工具功能】构建降级终态输出
        支持：节点异常、重试超限、审核循环超限后的统一兜底
        参数：state: 图共享状态
        返回：包含 has_error 标记和 final_answer 的终态状态
        异常：无
        """
        error_msg = str(state.get("error_msg", "")).strip()
        logger.warning(
            "触发降级终态 | current_failed_node=%s error_msg=%s",
            state.get("current_failed_node", "unknown"),
            error_msg,
        )

        fallback = error_msg or "当前服务繁忙，请稍后重试。"
        logger.warning("degraded_response 降级回复 | error=%s", error_msg)
        return {
            **state,
            "has_error": True,
            "final_answer": fallback,
        }


def node_final_response(state: GraphState) -> GraphState:
    """最终响应节点函数。"""
    return FinalResponseAgent().run(state)


def node_degraded_response(state: GraphState) -> GraphState:
    """降级响应节点函数。"""
    return DegradedResponseAgent().run(state)
