"""
Graph Agent：LangGraph 多节点协同编排，作为图执行的业务入口。

GraphAgent.run() 接收 session_id + user_message，组装最小 GraphState，
执行图编排后直接返回 result_state 字典。
会话历史由 LangGraph checkpointer（add_messages reducer）自动维护。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from langchain.agents.middleware.summarization import SummarizationMiddleware
from langchain_core.messages import HumanMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES, RemoveMessage

from app.config.agent_config import agent_config
from app.graph.builders.customer_service_graph_builder import build_customer_service_graph
from app.graph.llm_provider import get_deepseek_llm
from app.graph.models.execution_state import ExecutionStateSnapshot
from app.graph.models.protocol_models import ConversationContext
from app.graph.trace_execution import set_trace_session_id
from app.utils.logger import get_logger

logger = get_logger(__name__)

# ── 中文客服专用摘要 Prompt ──────────────────────────────────

CUSTOMER_SERVICE_SUMMARY_PROMPT = """你是一个客服对话摘要助手。请用中文对以下用户与客服的对话历史进行摘要。

要求：
1. 保留用户的核心诉求和问题（如退货、换货、投诉、咨询等）
2. 保留已提供的关键信息（订单号、手机号、地址等）
3. 保留已执行的操作（创建工单、查询订单、转人工等）
4. 保留未解决的问题（如果有）
5. 摘要长度控制在 300 字以内，简洁直白

对话历史：
{messages}

摘要："""


# ── DeepSeek 中文 Token 计数 ─────────────────────────────────

def count_tokens_deepseek(messages: list) -> int:
    """DeepSeek 中文 chars_per_token ≈ 2.0，使用字符数 / 2.0 估算 token 数。"""
    total_chars = sum(len(str(getattr(msg, "content", "") or "")) for msg in messages)
    return int(total_chars / 2.0)


# ── 摘要累积常量 ─────────────────────────────────────────────

_MAX_SUMMARY_MESSAGES = 3


def _is_summary_message(msg: object) -> bool:
    """判断是否为 SummarizationMiddleware 生成的摘要消息。"""
    if not isinstance(msg, HumanMessage):
        return False
    kwargs = getattr(msg, "additional_kwargs", {}) or {}
    return kwargs.get("lc_source") == "summarization"


def _get_summary_text(msg: HumanMessage) -> str:
    """从摘要消息中提取纯摘要文本（去掉前缀）。"""
    content = str(getattr(msg, "content", ""))
    prefix = "Here is a summary of the conversation to date:\n\n"
    if content.startswith(prefix):
        return content[len(prefix):]
    return content


class GraphAgent:
    """Graph 编排 Agent — 项目的核心 Agent 业务入口。"""

    def __init__(self, graph: Any | None = None) -> None:
        self._graph = graph or build_customer_service_graph()
        self._middleware = self._build_middleware()
        if self._middleware is not None:
            self._validate_summarization_config()

    # ── 中间件构建 ───────────────────────────────────────────

    def _build_middleware(self) -> SummarizationMiddleware | None:
        """构建 SummarizationMiddleware，trigger=None 时关闭瘦身。"""
        trigger = agent_config.graph.summarization_trigger
        keep = agent_config.graph.summarization_keep
        if trigger is None:
            logger.info("[summarization] 摘要中间件已关闭 | trigger=None")
            return None
        return SummarizationMiddleware(
            model=get_deepseek_llm(),
            trigger=trigger,
            keep=keep,
            summary_prompt=CUSTOMER_SERVICE_SUMMARY_PROMPT,
            trim_tokens_to_summarize=agent_config.graph.trim_tokens_to_summarize,
            token_counter=count_tokens_deepseek,
        )

    def _validate_summarization_config(self) -> None:
        """校验摘要配置：keep 必须小于 trigger，消息阈值 >= 10。

        Raises:
            ValueError: 配置不合理时抛出
        """
        trigger = agent_config.graph.summarization_trigger
        keep = agent_config.graph.summarization_keep

        triggers: list[tuple[str, int]] = (
            trigger if isinstance(trigger, list) else [trigger]
        )

        keep_type, keep_value = keep

        for trig_type, trig_value in triggers:
            if trig_type == keep_type and keep_value >= trig_value:
                raise ValueError(
                    f"摘要配置错误: keep=({keep_type!r}, {keep_value}) 必须小于 "
                    f"trigger=({trig_type!r}, {trig_value})，否则每轮都会触发摘要"
                )
            if trig_type == "messages" and trig_value < 10:
                raise ValueError(
                    f"摘要配置错误: trigger 消息数阈值 ({trig_value}) 不能小于 10"
                )

    # ── 主入口 ────────────────────────────────────────────────

    async def run(self, session_id: str, user_message: str = "") -> dict[str, Any]:
        """执行一次完整的图编排，返回 result_state 字典。

        多轮对话由 LangGraph checkpointer 通过 thread_id 自动维护消息历史，
        在调用图执行前通过 SummarizationMiddleware 对历史消息做瘦身检查。

        Args:
            session_id: 会话 ID，作为 LangGraph thread_id 使用
            user_message: 当前轮用户输入文本

        Returns:
            图执行后的 result_state 字典，包含 messages、final_answer、
            draft_response、need_human、has_error 等字段
        """
        # 统一 thread_id 类型，避免 int/str 不一致
        thread_id = str(session_id)
        config = {"configurable": {"thread_id": thread_id}}

        # 设置追踪会话 ID
        set_trace_session_id(thread_id)

        logger.info(
            "graph_agent 开始执行 | session_id=%s user_message=%s",
            thread_id,
            user_message[:50],
        )

        # 初始化执行状态
        execution_state = ExecutionStateSnapshot()
        execution_state.start()

        # 基础 state 字段
        base_state = {
            "review_cycle_count": agent_config.graph.init_review_cycle_count,
            "max_review_cycles": agent_config.graph.max_review_cycles,
            "has_error": False,
            "error_msg": "",
            "need_human": False,
            "final_answer": "",
            "draft_response": "",
            "execution_state": execution_state,
        }

        # 消息瘦身 pre-process
        input_messages, conversation_ctx = await self._preprocess_messages(
            thread_id, user_message, config
        )
        if conversation_ctx is not None:
            base_state["conversation_context"] = conversation_ctx

        state = {**base_state, "messages": input_messages}
        result_state = await self._graph.ainvoke(state, config)

        # 终态标记
        es = result_state.get("execution_state")
        if es is not None:
            if result_state.get("has_error"):
                es.mark_error(str(result_state.get("error_msg", "") or "graph execution error"))
            else:
                es.mark_completed()

        logger.info(
            "graph_agent 执行完毕 | has_error=%s has_answer=%s exec_status=%s",
            result_state.get("has_error"),
            bool(result_state.get("final_answer") or result_state.get("draft_response")),
            getattr(result_state.get("execution_state"), "exec_status", "unknown"),
        )
        return result_state

    # ── 流式执行 ────────────────────────────────────────────

    async def run_stream(
        self, session_id: str, user_message: str = ""
    ) -> AsyncIterator[dict[str, Any]]:
        """以 SSE 流式方式执行图编排，逐 token 产出事件。

        使用 astream_events v2 捕获 on_chat_model_stream 事件，
        仅在 agent_llm_node 内产出时推送 token 事件。
        工具调用与执行通过 on_tool_start/on_tool_end 推送。

        Args:
            session_id: 会话 ID，作为 LangGraph thread_id 使用
            user_message: 当前轮用户输入文本

        Yields:
            {"event": "message_start", "data": {}}
            {"event": "token", "data": {"token": str, "seq": int}}
            {"event": "tool_call", "data": {"tool_name": str, "status": "running"|"done"}}
            {"event": "done", "data": {"answer": str, "need_human": bool}}
            {"event": "error", "data": {"message": str}}
        """
        thread_id = str(session_id)
        config = {"configurable": {"thread_id": thread_id}}
        set_trace_session_id(thread_id)

        logger.info(
            "graph_agent 开始流式执行 | session_id=%s user_message=%s",
            thread_id,
            user_message[:50],
        )

        execution_state = ExecutionStateSnapshot()
        execution_state.start()

        base_state = {
            "review_cycle_count": agent_config.graph.init_review_cycle_count,
            "max_review_cycles": agent_config.graph.max_review_cycles,
            "has_error": False,
            "error_msg": "",
            "need_human": False,
            "final_answer": "",
            "draft_response": "",
            "execution_state": execution_state,
        }

        input_messages, conversation_ctx = await self._preprocess_messages(
            thread_id, user_message, config
        )
        if conversation_ctx is not None:
            base_state["conversation_context"] = conversation_ctx

        state = {**base_state, "messages": input_messages}

        in_agent_node = False
        token_seq = 0

        try:
            async for event in self._graph.astream_events(state, config, version="v2"):
                kind = event["event"]
                name = event.get("name", "")

                if kind == "on_chain_start" and name == "agent_llm_node":
                    in_agent_node = True
                    token_seq = 0
                    yield {"event": "message_start", "data": {}}

                elif kind == "on_chain_end" and name == "agent_llm_node":
                    in_agent_node = False

                elif kind == "on_chat_model_stream" and in_agent_node:
                    chunk = event["data"]["chunk"]
                    content = getattr(chunk, "content", "") or ""
                    if content and isinstance(content, str):
                        token_seq += 1
                        yield {
                            "event": "token",
                            "data": {"token": content, "seq": token_seq},
                        }

                elif kind == "on_tool_start":
                    tool_name = name or "unknown"
                    yield {
                        "event": "tool_call",
                        "data": {"tool_name": tool_name, "status": "running"},
                    }

                elif kind == "on_tool_end":
                    tool_name = name or "unknown"
                    yield {
                        "event": "tool_call",
                        "data": {"tool_name": tool_name, "status": "done"},
                    }

            # 从 checkpointer 读取终态
            final_state = await self._graph.aget_state(config)
            final_values = final_state.values if final_state and final_state.values else {}

            answer = str(
                final_values.get("final_answer")
                or final_values.get("draft_response")
                or "请稍后重试"
            )
            need_human = bool(final_values.get("need_human", False))

            es = final_values.get("execution_state")
            if es is not None:
                if final_values.get("has_error"):
                    es.mark_error(str(final_values.get("error_msg", "")))
                else:
                    es.mark_completed()

            logger.info(
                "graph_agent 流式执行完毕 | answer_len=%s need_human=%s",
                len(answer), need_human,
            )

            yield {
                "event": "done",
                "data": {
                    "answer": answer,
                    "need_human": need_human,
                },
            }

        except Exception as exc:
            logger.exception("graph_agent 流式执行异常 | session_id=%s", thread_id)
            yield {
                "event": "error",
                "data": {"message": str(exc)},
            }
            yield {
                "event": "done",
                "data": {
                    "answer": "服务内部错误，请稍后重试。",
                    "need_human": False,
                },
            }

    # ── 消息瘦身预处理 ────────────────────────────────────────

    async def _preprocess_messages(
        self, thread_id: str, user_message: str, config: dict
    ) -> tuple[list, ConversationContext | None]:
        """获取历史消息 + 中间件瘦身检查，返回 (消息列表, conversation_context)。

        若中间件未配置或不触发，返回仅含本轮 HumanMessage 的列表，
        由 checkpointer 的 add_messages reducer 自动与历史合并。
        """
        current_msg = HumanMessage(content=user_message)

        if self._middleware is None:
            return [current_msg], None

        try:
            # 获取 checkpointer 中的历史消息
            persisted = await self._graph.aget_state(config)
            historical_msgs: list = []
            if persisted.values and "messages" in persisted.values:
                historical_msgs = list(persisted.values["messages"])

            # 组装完整消息列表供中间件检查
            full_messages = historical_msgs + [current_msg]
            full_state: dict[str, Any] = {"messages": full_messages}

            # 中间件检查并执行瘦身
            runtime: dict[str, Any] = {"context": {}}
            mw_result = await self._middleware.abefore_model(full_state, runtime)

            if mw_result is None:
                logger.info(
                    "[summarization] 跳过摘要 | msg_count=%s 未达阈值",
                    len(full_messages),
                )
                return [current_msg], None

            # 中间件返回了新消息列表（含 RemoveMessage + 摘要 + 保留段）
            new_messages = mw_result.get("messages", [])
            summary_msgs = [m for m in new_messages if _is_summary_message(m)]
            preserved_count = sum(
                1 for m in new_messages
                if not isinstance(m, RemoveMessage) and not _is_summary_message(m)
            )

            summary_text = _get_summary_text(summary_msgs[-1]) if summary_msgs else ""
            summarized_count = len(full_messages) - preserved_count - len(summary_msgs)

            logger.info(
                "[summarization] 触发摘要 | total_msg_count=%s preserved=%s summary_count=%s",
                len(full_messages), preserved_count, len(summary_msgs),
            )

            # 摘要累积处理：>= 3 条摘要时合并为 1 条
            if len(summary_msgs) >= _MAX_SUMMARY_MESSAGES:
                new_messages = self._merge_summaries(new_messages, summary_msgs)

            ctx = ConversationContext(
                conversation_summary=summary_text,
                summary_message_count=summarized_count,
            )
            return new_messages, ctx

        except Exception as exc:
            logger.error("[summarization] 摘要失败 | error=%s fallback=trim_only", exc)
            fallback_msgs = await self._fallback_trim(thread_id, current_msg, config)
            return fallback_msgs, None

    # ── 摘要累积合并 ──────────────────────────────────────────

    def _merge_summaries(
        self, messages: list, summary_msgs: list[HumanMessage]
    ) -> list:
        """将多条摘要合并为一条，最终只保留 1 条摘要 HumanMessage。"""
        merged_text = "\n".join(_get_summary_text(m) for m in summary_msgs)
        merged_summary = HumanMessage(
            content=f"Here is a summary of the conversation to date:\n\n{merged_text}",
            additional_kwargs={"lc_source": "summarization"},
        )

        result = [merged_summary]
        for m in messages:
            if m in summary_msgs:
                continue
            if isinstance(m, RemoveMessage):
                result.insert(0, m)
            else:
                result.append(m)

        logger.info(
            "[summarization] 合并摘要 | before=%s after=1",
            len(summary_msgs),
        )
        return result

    # ── 摘要失败降级 ──────────────────────────────────────────

    async def _fallback_trim(
        self, thread_id: str, current_msg: HumanMessage, config: dict
    ) -> tuple[list, None]:
        """摘要生成失败时降级：保留最近 keep 条消息，不做摘要。"""
        keep_count = agent_config.graph.summarization_keep[1]
        try:
            persisted = await self._graph.aget_state(config)
            historical_msgs: list = []
            if persisted.values and "messages" in persisted.values:
                historical_msgs = list(persisted.values["messages"])

            all_messages = historical_msgs + [current_msg]
            kept = all_messages[-keep_count:] if len(all_messages) > keep_count else all_messages

            fallback_note = HumanMessage(
                content=f"[摘要生成失败，保留最近 {keep_count} 条对话]"
            )
            return [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                fallback_note,
                *kept,
            ], None
        except Exception as inner:
            logger.error("[summarization] 降级也失败 | error=%s", inner)
            return [current_msg], None


# ── 单例工厂 ─────────────────────────────────────────────────

_agent: GraphAgent | None = None


def build_graph_agent() -> GraphAgent:
    """获取全局单例 GraphAgent。"""
    global _agent
    if _agent is None:
        _agent = GraphAgent()
    return _agent


if __name__ == "__main__":
    import asyncio
    import traceback

    async def _test():
        agent = build_graph_agent()

        cases = [
            {"session_id": "test-001", "user_message": "你好"},
            {"session_id": "test-001", "user_message": "怎么退货"},
            {"session_id": "test-002", "user_message": "我的订单在哪里"},
        ]

        for i, case in enumerate(cases):
            print(f"\n{'='*60}")
            print(f"--- case {i + 1} ---")
            print(f"INPUT : {case}")
            print(f"{'='*60}")
            try:
                result = await agent.run(**case)
                answer = str(result.get("final_answer") or result.get("draft_response") or "")
                is_dict = isinstance(result, dict)
                has_msgs = "messages" in result
                has_answer = bool(answer)
                print(f"OUTPUT: has_error={result.get('has_error')}, "
                      f"need_human={result.get('need_human')}, "
                      f"answer={answer}")
                print(f"  [{'OK' if is_dict else 'FAIL'}] 返回 dict: {is_dict}")
                print(f"  [{'OK' if has_msgs else 'FAIL'}] 含 messages: {has_msgs}")
                print(f"  [{'OK' if has_answer else 'FAIL'}] 含回答内容: {has_answer}")
            except Exception:
                print(f"  [FAIL] 执行异常:")
                traceback.print_exc()

        print("--- 测试结束 ---")

    asyncio.run(_test())
