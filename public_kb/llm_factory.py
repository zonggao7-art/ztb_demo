"""
LLM 工厂 — 统一的 ChatOpenAI 构造入口。

agent 图构建（graph.build_graph）、询价节点（price_inquiry._build_llm）、
知识库问答链（rag_engine._create_llm）共用此工厂，
保证 model / api_key / temperature / timeout / max_retries / base_url 口径一致。
"""

from __future__ import annotations

from typing import Optional

from langchain_openai import ChatOpenAI

from .config import Settings


def _normalize_base_url(base_url: str) -> str:
    """规范化 base_url — OpenAI 客户端会在 base_url 后自动追加 /chat/completions。

    配置里若直接给出完整请求端点（如 OpenRouter 的
    https://openrouter.ai/api/v1/chat/completions），需剥掉该后缀，
    否则会拼出 .../chat/completions/chat/completions 导致 404。
    """
    url = base_url.rstrip("/")
    suffix = "/chat/completions"
    if url.endswith(suffix):
        url = url[: -len(suffix)]
    return url


def create_llm(settings: Settings, *, temperature: Optional[float] = None) -> ChatOpenAI:
    """根据 Settings 创建 ChatOpenAI 实例。

    Args:
        settings: 全局配置。
        temperature: 覆盖 settings.llm_temperature（路由/意图解析等
            需要确定性输出的场景传 0.0）。

    Returns:
        配置完成的 ChatOpenAI 实例。
    """
    kwargs: dict = {
        "model": settings.llm_model,
        "api_key": settings.llm_api_key,
        "temperature": settings.llm_temperature if temperature is None else temperature,
        "timeout": settings.llm_timeout,
        "max_retries": settings.llm_max_retries,
    }
    if settings.llm_base_url:
        kwargs["base_url"] = _normalize_base_url(settings.llm_base_url)
    return ChatOpenAI(**kwargs)
