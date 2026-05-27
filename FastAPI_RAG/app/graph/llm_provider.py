import asyncio

from langchain_openai import ChatOpenAI

from app.config.agent_config import agent_config

_deepseek_llm: ChatOpenAI | None = None
_openai_llm: ChatOpenAI | None = None


def _build_deepseek_llm() -> ChatOpenAI:
    cfg = agent_config.llm
    if not cfg.deepseek_api_key:
        raise ValueError("DEEPSEEK_API_KEY 未配置，无法初始化 DeepSeek LLM 客户端。")
    return ChatOpenAI(
        model=cfg.deepseek_model_name,
        api_key=cfg.deepseek_api_key,
        base_url=cfg.deepseek_api_endpoint,
        temperature=cfg.deepseek_planner_temperature,
        max_tokens=cfg.deepseek_planner_max_tokens,
        timeout=cfg.deepseek_planner_timeout,
        streaming=True,
    )


def _build_openai_llm() -> ChatOpenAI:
    cfg = agent_config.llm
    if not cfg.openai_api_key:
        raise ValueError("OPENAI_API_KEY 未配置，无法初始化 OpenAI LLM 客户端。")
    return ChatOpenAI(
        model=cfg.openai_model,
        api_key=cfg.openai_api_key,
        base_url=cfg.openai_base_url,
        temperature=0.7,
        max_tokens=1024,
        timeout=30,
        streaming=True,
    )


def get_deepseek_llm() -> ChatOpenAI:
    global _deepseek_llm
    if _deepseek_llm is None:
        _deepseek_llm = _build_deepseek_llm()
    return _deepseek_llm


def get_openai_llm() -> ChatOpenAI:
    global _openai_llm
    if _openai_llm is None:
        _openai_llm = _build_openai_llm()
    return _openai_llm


if __name__ == "__main__":

    async def aa():
        try:
            llm1 = get_openai_llm()
            llm2 = get_deepseek_llm()
            res1 = await llm1.ainvoke("你好,你是什么模型")
            res2 = await llm2.ainvoke("你好你是什么模型")
            print(res1)
            print(res2)
        except ValueError as exc:
            print(f"[跳过] {exc}")
        except Exception as exc:
            print(f"[错误] {exc}")

    asyncio.run(aa())
