"""
LLM 工厂 — 统一的 ChatOpenAI 构造入口。

agent 图构建（graph.build_graph）、询价节点（price_inquiry._build_llm）、
知识库问答链（rag_engine._create_llm）共用此工厂，
保证 model / api_key / temperature / timeout / max_retries / base_url 口径一致。
"""

from __future__ import annotations

from typing import Optional, ClassVar, Literal
from urllib.parse import urlsplit

from langchain_openai import ChatOpenAI
from pydantic import Field

from .config import Settings


class BudgetedChatOpenAI(ChatOpenAI):
    """Charge actual hybrid SDK attempts, including nested RAG model calls.

    Clone client options per call: never mutate the shared legacy model/client.
    SDK retries are disabled in hybrid mode; any retry must re-enter the budget.
    """
    manages_run_budget: ClassVar[bool] = True
    # 由创建模型的程序指定用途；不通过提示词决定，也不发送给模型服务。
    input_budget_scope: Literal["agent", "rag"] = Field(default="agent", exclude=True)

    def _uses_deepseek_api(self) -> bool:
        return urlsplit(str(self.openai_api_base or "")).hostname == "api.deepseek.com"

    def _non_thinking_body(self, overrides=None) -> dict:
        return {
            **(self.extra_body or {}),
            **(overrides or {}),
            "thinking": {"type": "disabled"},
        }

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        """Keep tool selection constraints without DeepSeek thinking-mode conflicts."""
        if self._uses_deepseek_api():
            kwargs["extra_body"] = self._non_thinking_body(kwargs.get("extra_body"))
        return super().bind_tools(tools, tool_choice=tool_choice, **kwargs)

    def with_structured_output(self, schema=None, *, method=None, **kwargs):
        """Use validated tool output for DeepSeek instead of OpenAI JSON Schema mode."""
        model = self
        if self._uses_deepseek_api():
            if method in (None, "json_schema"):
                method = "function_calling"
            model = self.model_copy(update={"extra_body": self._non_thinking_body()})
        return ChatOpenAI.with_structured_output(
            model, schema, method=method or "json_schema", **kwargs,
        )

    def _for_run(self):
        from agent.execution.context import current_run
        ctx = current_run()
        if ctx is None:
            return self
        ctx.reserve_model()
        root = self.root_client.with_options(max_retries=0, timeout=ctx.remaining())
        aroot = self.root_async_client.with_options(max_retries=0, timeout=ctx.remaining())
        return self.model_copy(update={
            "root_client": root, "root_async_client": aroot,
            "client": root.chat.completions, "async_client": aroot.chat.completions,
            "max_retries": 0, "streaming": False,
            "max_tokens": ctx.settings.agent_model_max_output_tokens,
        })

    def _get_request_payload(self, *args, **kwargs):
        payload = super()._get_request_payload(*args, **kwargs)
        from agent.execution.context import current_run
        ctx = current_run()
        if ctx is not None:
            ctx.check_prompt(payload, scope=self.input_budget_scope)
        return payload

    def _generate(self, *args, **kwargs):
        return ChatOpenAI._generate(self._for_run(), *args, **kwargs)

    async def _agenerate(self, *args, **kwargs):
        return await ChatOpenAI._agenerate(self._for_run(), *args, **kwargs)

    def _stream(self, *args, **kwargs):
        yield from ChatOpenAI._stream(self._for_run(), *args, **kwargs)

    async def _astream(self, *args, **kwargs):
        async for chunk in ChatOpenAI._astream(self._for_run(), *args, **kwargs):
            yield chunk


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


def create_llm(settings: Settings, *, temperature: Optional[float] = None,
               input_budget_scope: Literal["agent", "rag"] = "agent") -> ChatOpenAI:
    """根据 Settings 创建 ChatOpenAI 实例。

    Args:
        settings: 全局配置。
        temperature: 覆盖 settings.llm_temperature（路由/意图解析等
            需要确定性输出的场景传 0.0）。
        input_budget_scope: agent 使用调度输入额度；rag 使用完整资料输入额度。

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
    return BudgetedChatOpenAI(**kwargs, input_budget_scope=input_budget_scope)
