"""Agent 上下文与状态管理 — 功能演示。

只通过 GraphAgent.run() 调用真实图编排来验证功能，不自行编造函数。

使用方式：
    uv run python app/demo_agent_features.py
    或在 PyCharm 中右键 → Run 'demo_agent_features'
"""

import asyncio
import logging
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 最前面，避免与 site-packages 中的 app 包冲突
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) in sys.path:
    sys.path.remove(str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT))

# ── 降低摘要触发阈值，便于测试触发 ──────────────────────
# 必须在 GraphAgent 导入之前覆盖配置，因为中间件在 __init__ 中构建。
import app.config.agent_config as _cfg
from app.config.agent_config import (
    AgentConfig,
    AgentLLMConfig,
    CheckpointConfig,
    GraphExecutionConfig,
    RAGConfig,
)
from app.config.rule_preprocessor_config import RulePreprocessorConfig

_cfg.agent_config = AgentConfig(
    llm=AgentLLMConfig(),
    rag=RAGConfig(),
    rule=RulePreprocessorConfig(),
    graph=GraphExecutionConfig(
        summarization_trigger=("messages", 12),   # 消息数 >= 12 触发摘要
        summarization_keep=("messages", 5),        # 保留最近 5 条
    ),
    checkpoint=CheckpointConfig(),
)

from app.graph.graph_runtime_agent import build_graph_agent  # noqa: E402
from app.utils.logger import setup_logging


def _setup() -> None:
    """初始化编码和日志。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    setup_logging(level=logging.INFO)


def print_sep(title: str) -> None:
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def print_execution_state(result: dict, label: str = "") -> None:
    """打印执行状态追踪结果。"""
    es = result.get("execution_state")
    if es is None:
        print("  [无 execution_state]")
        return

    prefix = f"  [{label}] " if label else "  "
    print(f"{prefix}exec_status   : {es.exec_status}")
    if es.started_at:
        print(f"{prefix}started_at    : {es.started_at:.2f}")
    print(f"{prefix}last_error    : {es.last_error!r}")
    print(f"{prefix}node_timeline : {len(es.node_timeline)} 个节点")
    for rec in es.node_timeline:
        print(
            f"{prefix}  {rec.node_name:<24s} {rec.status:<7s} "
            f"{rec.duration_ms:>8.1f}ms  retry={rec.retry_count}"
        )


def print_conversation_context(result: dict) -> None:
    """打印会话上下文（摘要结果）。"""
    ctx = result.get("conversation_context")
    if ctx and (ctx.conversation_summary or ctx.summary_message_count):
        print(f"  conversation_summary       : {ctx.conversation_summary[:200]}")
        print(f"  summary_message_count      : {ctx.summary_message_count}")
    else:
        print("  conversation_context       : (空)")


def print_message_stats(result: dict) -> None:
    """打印当前消息列表概况。"""
    msgs = result.get("messages", [])
    print(f"  消息总数: {len(msgs)}")
    if msgs:
        types: dict[str, int] = {}
        for m in msgs:
            t = type(m).__name__
            types[t] = types.get(t, 0) + 1
        for t, c in types.items():
            print(f"    {t}: {c}")


async def _safe_run(agent, session_id: str, user_message: str) -> dict | None:
    """安全执行 agent.run，捕获 LLM 格式异常。"""
    try:
        return await agent.run(session_id=session_id, user_message=user_message)
    except Exception as e:
        print(f"  [LLM 异常，该轮跳过] {type(e).__name__}")
        return None


# ── 演示 1：执行状态追踪 ──────────────────────────────────


async def demo_execution_state() -> None:
    """发送不同类型查询，展示每个节点的执行时间线和耗时。"""
    print_sep("演示 1：执行状态追踪")

    agent = build_graph_agent()
    session = "demo-exec-state"

    # 快速回复 — 规则命中
    print("\n--- 场景 A: 问候语（规则命中，短路到 final）---")
    r1 = await _safe_run(agent, session, "你好")
    if r1:
        print(f"  回复: {str(r1.get('final_answer', ''))[:80]}")
        print_execution_state(r1, "场景A")

    # 简单咨询 — 走完整 Agent 链路
    print("\n--- 场景 B: 咨询问题（完整 Agent 链路）---")
    r2 = await _safe_run(agent, session, "怎么退货")
    if r2:
        answer = str(r2.get("final_answer") or r2.get("draft_response") or "")
        print(f"  回复: {answer[:120]}")
        print_execution_state(r2, "场景B")


# ── 演示 2：消息瘦身 ──────────────────────────────────────


async def demo_message_slimming() -> None:
    """持续发送消息累积历史，触发 SummarizationMiddleware 自动瘦身。"""
    print_sep("演示 2：消息瘦身")

    agent = build_graph_agent()
    session = "demo-slimming"

    quick_msgs = [
        "你好", "早上好", "谢谢", "再见", "在吗",
        "hello", "hi", "你好啊", "感谢", "拜拜",
    ]
    print(f"\n--- 持续发送 {len(quick_msgs)} 轮消息，观察消息数累积 ---")
    for i, msg in enumerate(quick_msgs, 1):
        r = await _safe_run(agent, session, msg)
        if r:
            msg_count = len(r.get("messages", []))
            print(f"  第{i:2d}轮: [{msg}] -> 消息数={msg_count}")

    print("\n--- 消息数达标后发送一轮咨询，验证瘦身结果 ---")
    r_final = await _safe_run(agent, session, "退货需要多长时间")
    if r_final:
        answer = str(r_final.get("final_answer") or r_final.get("draft_response") or "")
        print(f"  回复: {answer[:150]}")
        print("\n--- 瘦身后状态 ---")
        print_message_stats(r_final)
        print_execution_state(r_final, "瘦身后")
        print_conversation_context(r_final)
    else:
        r_fallback = await _safe_run(agent, session, "你好")
        if r_fallback:
            print("\n--- 瘦身后状态（通过快速回复获取） ---")
            print_message_stats(r_fallback)
            print_execution_state(r_fallback, "瘦身后")
            print_conversation_context(r_fallback)


# ── 主入口 ────────────────────────────────────────────────


async def main() -> None:
    _setup()
    print("=" * 70)
    print("  Agent 上下文与状态管理 — 功能演示")
    print("  配置: 摘要触发 >= 12 条消息 | 保留最近 5 条")
    print("=" * 70)

    await demo_execution_state()
    await demo_message_slimming()

    print_sep("演示完毕")


if __name__ == "__main__":
    asyncio.run(main())
