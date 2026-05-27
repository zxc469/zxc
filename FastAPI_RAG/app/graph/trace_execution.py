"""节点执行追踪装饰器 — 自动记录每个节点的 entry / duration / status。"""

from __future__ import annotations

import contextvars
import functools
import inspect
import time
from collections.abc import Callable
from typing import Any

from app.graph.models.execution_state import ExecutionStateSnapshot
from app.graph.models.graph_state import GraphState
from app.utils.logger import get_logger

logger = get_logger(__name__)

# 当前会话 ID，由 GraphAgent.run() 设置
_current_session_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "trace_session_id", default=""
)

# 模块级重试计数器: (session_id, node_name) -> failure_count
# 仅在当前进程生命周期内有效，与 InMemorySaver 生命周期一致
_retry_counters: dict[tuple[str, str], int] = {}


def set_trace_session_id(session_id: str) -> None:
    """设置当前追踪会话 ID，由 GraphAgent.run() 调用。"""
    _current_session_id.set(session_id)


def _get_retry_count(session_id: str, node_name: str) -> int:
    """获取当前节点在当前会话中的累计失败次数。"""
    return _retry_counters.get((session_id, node_name), 0)


def _increment_retry_count(session_id: str, node_name: str) -> int:
    """递增并返回当前节点的失败次数。"""
    key = (session_id, node_name)
    current = _retry_counters.get(key, 0) + 1
    _retry_counters[key] = current
    return current


def _reset_retry_count(session_id: str, node_name: str) -> None:
    """重置节点的重试计数（成功后清理）。"""
    _retry_counters.pop((session_id, node_name), None)


def _ensure_execution_state(state: GraphState) -> ExecutionStateSnapshot:
    """获取或初始化 execution_state。"""
    es = state.get("execution_state")
    if es is None:
        es = ExecutionStateSnapshot()
    return es


def trace_execution(node_name: str):
    """节点执行追踪装饰器。

    自动记录节点 entry / duration / status 到 GraphState.execution_state.node_timeline。
    与 LangGraph RetryPolicy 配合：装饰器记录每次成功执行，通过模块级计数器跟踪重试次数。

    Args:
        node_name: 节点名称，如 "agent_llm_node"
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def async_wrapper(state: GraphState) -> GraphState:
            es = _ensure_execution_state(state)
            session_id = _current_session_id.get()

            retry_count = _get_retry_count(session_id, node_name)
            entered_at = es.enter_node(node_name)

            logger.info(
                "[trace_execution] 进入节点 | node=%s session=%s retry=%s",
                node_name, session_id, retry_count,
            )

            try:
                result = await func(state)
            except Exception:
                _increment_retry_count(session_id, node_name)
                logger.warning(
                    "[trace_execution] 节点异常 | node=%s session=%s attempt=%s",
                    node_name, session_id, _get_retry_count(session_id, node_name),
                )
                raise

            _reset_retry_count(session_id, node_name)
            es.record_success(node_name, entered_at, retry_count)

            logger.info(
                "[trace_execution] 节点完成 | node=%s duration_ms=%.1f retry=%s",
                node_name,
                next(
                    (r.duration_ms for r in reversed(es.node_timeline) if r.node_name == node_name),
                    0,
                ),
                retry_count,
            )

            # Merge execution_state back into result
            result_state: GraphState = {**result, "execution_state": es}
            return result_state

        @functools.wraps(func)
        def sync_wrapper(state: GraphState) -> GraphState:
            es = _ensure_execution_state(state)
            session_id = _current_session_id.get()

            retry_count = _get_retry_count(session_id, node_name)
            entered_at = es.enter_node(node_name)

            logger.info(
                "[trace_execution] 进入节点 | node=%s session=%s retry=%s",
                node_name, session_id, retry_count,
            )

            try:
                result = func(state)
            except Exception:
                _increment_retry_count(session_id, node_name)
                logger.warning(
                    "[trace_execution] 节点异常 | node=%s session=%s attempt=%s",
                    node_name, session_id, _get_retry_count(session_id, node_name),
                )
                raise

            _reset_retry_count(session_id, node_name)
            es.record_success(node_name, entered_at, retry_count)

            logger.info(
                "[trace_execution] 节点完成 | node=%s duration_ms=%.1f retry=%s",
                node_name,
                next(
                    (r.duration_ms for r in reversed(es.node_timeline) if r.node_name == node_name),
                    0,
                ),
                retry_count,
            )

            result_state: GraphState = {**result, "execution_state": es}
            return result_state

        if inspect.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


def record_error_in_state(
    state: GraphState,
    node_name: str,
    error_message: str = "",
) -> GraphState:
    """在 _make_error_handler 中调用：记录最终失败（所有重试耗尽）。

    Args:
        state: 当前图状态
        node_name: 失败的节点名
        error_message: 错误描述

    Returns:
        更新后的图状态（含 execution_state 错误记录）
    """
    session_id = _current_session_id.get()
    retry_count = _get_retry_count(session_id, node_name)
    _reset_retry_count(session_id, node_name)

    es = _ensure_execution_state(state)
    entered_at = time.time()
    es.record_error(node_name, entered_at, error_message, retry_count)
    es.mark_error(error_message)

    logger.error(
        "[trace_execution] 节点重试耗尽 | node=%s session=%s retry=%s error=%s",
        node_name, session_id, retry_count, error_message[:100],
    )

    return {**state, "execution_state": es, "has_error": True, "current_failed_node": node_name}
