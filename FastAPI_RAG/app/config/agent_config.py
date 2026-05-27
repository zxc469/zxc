"""
Agent 系统统一配置入口。

将原先分散在以下位置的 Agent 配置聚合到一处：
- app/config/settings.py        → OpenAI / Deepseek LLM 参数
- app/agents/intent/rule_preprocessor_config.py  → 规则引擎配置
- app/agents/graph/models/state_config.py        → Graph 身份字段
- app/agents/graph/agent.py     → max_retry_per_node 硬编码
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config.rule_preprocessor_config import RulePreprocessorConfig


class GraphExecutionConfig:
    """图执行参数。"""

    def __init__(
        self,
        init_review_cycle_count: int = 0,
        max_review_cycles: int = 2,
        enable_checkpointer: bool = True,
        # ── SummarizationMiddleware 配置 ──
        summarization_trigger: list[tuple[str, int]] | tuple[str, int] | None = None,
        summarization_keep: tuple[str, int] = ("messages", 20),
        trim_tokens_to_summarize: int = 4000,
    ) -> None:
        self.init_review_cycle_count = init_review_cycle_count
        self.max_review_cycles = max_review_cycles
        self.enable_checkpointer = enable_checkpointer
        # summarization
        self.summarization_trigger = summarization_trigger
        self.summarization_keep = summarization_keep
        self.trim_tokens_to_summarize = trim_tokens_to_summarize


# ============================================================================
# LLM 配置（OpenAI + Deepseek，原 settings.py 中已迁出）
# ============================================================================

_ENV_FILE = str(Path(__file__).resolve().parents[2] / ".env")


class AgentLLMConfig(BaseSettings):
    """Agent LLM 参数配置（从 .env 加载）。"""

    openai_api_key: str = Field(
        default="",
        description="OpenAI API 密钥（敏感，从 .env 加载）",
        validation_alias=AliasChoices("openai_api_key", "OPENAI_API_KEY"),
    )
    openai_base_url: str = "https://api.openai-proxy.org/v1"
    openai_model: str = "gpt-4o-mini"

    deepseek_api_key: str = Field(
        default="",
        description="DeepSeek API 密钥（敏感，从 .env 加载）",
        validation_alias=AliasChoices("DEEPSEEK_API_KEY", "deepseek_api_key"),
    )
    deepseek_api_endpoint: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    deepseek_model_name: str = "deepseek-v4-flash"
    deepseek_planner_max_tokens: int = 1024
    deepseek_planner_temperature: float = 0.1
    deepseek_planner_timeout: float = 60.0
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore")


# ============================================================================
# RAG / Embedding 配置
# ============================================================================


class RAGConfig(BaseSettings):
    """RAG 向量检索与 Embedding 参数配置（从 .env 加载）。"""

    rag_top_k: int = 5
    rag_score_threshold: float = 0.3
    rag_chunk_size: int = 1000
    rag_chunk_overlap: int = 200
    rag_chunk_separators: list[str] = ["\n\n", "\n", " ", ""]
    embedding_model: str = "text-embedding-v4"
    embedding_api_key: str = Field(
        default="",
        description="Embedding API 密钥（敏感，从 .env 加载）",
        validation_alias=AliasChoices("EMBEDDING_API_KEY", "embedding_api_key"),
    )
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    qdrant_collection: str = Field(
        default="faq_knowledge",
        description="Qdrant 默认集合名称",
        validation_alias=AliasChoices("QDRANT_COLLECTION", "qdrant_collection"),
    )
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore")


# ============================================================================
# 混合检索 + 重排序配置
# ============================================================================


@dataclass(frozen=True)
class HybridSearchConfig:
    """混合检索（Dense + Sparse）与 Cross-encoder 重排序配置。"""

    prefetch_limit: int = 20
    """每种检索器（dense / sparse）返回的候选数量。"""

    rrf_k: int = 60
    """RRF 融合参数 k，控制排名对最终得分的影响程度。"""

    rerank_enabled: bool = True
    """是否启用 cross-encoder 重排序（flashrank ms-marco-TinyBERT-L-2-v2）。"""

    candidate_limit: int = 30
    """RRF 融合后保留的最大候选数，送入重排序阶段。"""


# ============================================================================
# Checkpoint 持久化配置（第二阶段 AsyncPostgresSaver 使用）
# ============================================================================


class CheckpointConfig:
    """AsyncPostgresSaver 连接与行为配置（当前阶段预留，暂不使用）。"""

    def __init__(
        self,
        backend: str = "memory",  # "memory" | "postgres"
        pool_min_size: int = 2,
        pool_max_size: int = 5,
        pool_acquire_timeout: float = 10.0,
        retention_days: int = 30,
    ) -> None:
        self.backend = backend
        self.pool_min_size = pool_min_size
        self.pool_max_size = pool_max_size
        self.pool_acquire_timeout = pool_acquire_timeout
        self.retention_days = retention_days


# ============================================================================
# 顶层 Agent 配置（聚合入口）
# ============================================================================


@dataclass(frozen=True)
class AgentConfig:
    """Agent 系统全局配置（所有子配置的聚合入口）。"""

    llm: AgentLLMConfig = field(default_factory=AgentLLMConfig)
    rag: RAGConfig = field(default_factory=RAGConfig)
    hybrid_search: HybridSearchConfig = field(default_factory=HybridSearchConfig)
    rule: RulePreprocessorConfig = field(default_factory=RulePreprocessorConfig)
    graph: GraphExecutionConfig = field(
        default_factory=lambda: GraphExecutionConfig(
            summarization_trigger=[("messages", 40), ("tokens", 4000)],
        )
    )
    checkpoint: CheckpointConfig = field(default_factory=CheckpointConfig)


# 全局单例（模块级导入即用）
agent_config = AgentConfig()

# 向下兼容的别名常量
DEFAULT_RULE_PREPROCESSOR_CONFIG = agent_config.rule


if __name__ == "__main__":
    print(agent_config.llm.deepseek_api_key)
