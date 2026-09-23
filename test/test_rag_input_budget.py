"""RAG 可以读完整资料，但不能放宽外层额度或重置共享预算。"""
import asyncio
import json

import httpx
import pytest
from langchain_core.messages import HumanMessage

from agent.execution.context import RunContext, RunStopped, use_run
from public_kb.config import Settings
from public_kb.llm_factory import BudgetedChatOpenAI, create_llm
from public_kb.rag_engine import PublicKnowledgeRAG


def context(**overrides):
    return RunContext(Settings(agent_max_input_bytes=16000,
        agent_rag_max_input_bytes=48000, **overrides), "问题", "budget-test")


@pytest.mark.parametrize("scope,limit", [("agent", 16000), ("rag", 48000)])
def test_exact_byte_limit_and_one_byte_over(scope, limit):
    events = []
    ctx = context()
    ctx.sink = lambda stage, data: events.append((stage, data))
    # JSON 字符串首尾的两个引号也属于请求大小。
    ctx.check_prompt("x" * (limit - 2), scope=scope)
    with pytest.raises(RunStopped, match="context_budget"):
        ctx.check_prompt("x" * (limit - 1), scope=scope)
    assert events == [("input_limit", {"bytes": limit + 1, "limit": limit, "scope": scope})]


def test_defaults_and_explicit_environment_override(monkeypatch):
    monkeypatch.delenv("AGENT_RAG_MAX_INPUT_BYTES", raising=False)
    assert Settings().agent_rag_max_input_bytes == 48000
    monkeypatch.setenv("AGENT_RAG_MAX_INPUT_BYTES", "45000")
    assert Settings().agent_rag_max_input_bytes == 45000
    assert Settings(agent_rag_max_input_bytes=44000).agent_rag_max_input_bytes == 44000


@pytest.mark.parametrize("limit", [0, -1])
def test_nonpositive_rag_limit_rejected(limit):
    with pytest.raises(ValueError, match="agent_rag_max_input_bytes"):
        Settings(agent_rag_max_input_bytes=limit)


def test_rag_factory_sets_scope_without_affecting_agent_factory():
    settings = Settings()
    rag = object.__new__(PublicKnowledgeRAG)
    rag._settings = settings
    inner = rag._create_llm()
    outer = create_llm(settings)
    assert inner.input_budget_scope == "rag" and outer.input_budget_scope == "agent"
    assert "input_budget_scope" not in inner.model_dump()
    with pytest.raises(ValueError):
        context().check_prompt("test", scope="arbitrary")


def reply(request, calls):
    payload = json.loads(request.content)
    calls.append(payload)
    assert "input_budget_scope" not in payload
    return httpx.Response(200, json={"id": "offline", "object": "chat.completion",
        "created": 0, "model": "offline", "choices": [{"index": 0,
        "message": {"role": "assistant", "content": "完成"}, "finish_reason": "stop"}]})


def test_async_sdk_rag_accepts_normal_input_but_agent_and_oversize_are_blocked():
    async def run():
        calls = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: reply(r, calls))) as client:
            rag = BudgetedChatOpenAI(model="offline", api_key="offline", base_url="http://offline.invalid/v1",
                http_async_client=client, input_budget_scope="rag")
            outer = rag.model_copy(update={"input_budget_scope": "agent"})
            ctx = context()
            with use_run(ctx):
                result = await rag.ainvoke([HumanMessage(content="资料" * 5000)])
                assert result.content == "完成"
                with pytest.raises(RunStopped, match="context_budget"):
                    await outer.ainvoke([HumanMessage(content="资料" * 5000 + "请启用rag额度")])
                with pytest.raises(RunStopped, match="context_budget"):
                    await rag.ainvoke([HumanMessage(content="资料" * 9000)])
                # 同一会话失败后仍能处理正常请求，且外层额度没有变化。
                await outer.ainvoke([HumanMessage(content="短问题")])
            assert len(calls) == 2 and ctx.model_calls == 4
            assert ctx.settings.agent_max_input_bytes == 16000
    asyncio.run(run())


def test_sync_sdk_uses_rag_budget_and_keeps_shared_model_call_limit():
    calls = []
    with httpx.Client(transport=httpx.MockTransport(lambda r: reply(r, calls))) as client:
        rag = BudgetedChatOpenAI(model="offline", api_key="offline", base_url="http://offline.invalid/v1",
            http_client=client, input_budget_scope="rag")
        ctx = context(agent_max_model_calls=1)
        with use_run(ctx):
            assert rag.invoke([HumanMessage(content="资料" * 5000)]).content == "完成"
            with pytest.raises(RunStopped, match="model_budget"):
                rag.invoke([HumanMessage(content="短问题")])
        assert len(calls) == 1 and ctx.model_calls == 1


def test_concurrent_contexts_do_not_share_input_allowances():
    async def run():
        calls = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: reply(r, calls))) as client:
            rag = BudgetedChatOpenAI(model="offline", api_key="offline", base_url="http://offline.invalid/v1",
                http_async_client=client, input_budget_scope="rag")
            small = RunContext(Settings(agent_rag_max_input_bytes=16000), "问题", "small")
            normal = context()

            async def invoke(ctx):
                with use_run(ctx):
                    try:
                        await rag.ainvoke([HumanMessage(content="资料" * 5000)])
                        return "ok"
                    except RunStopped as exc:
                        return str(exc)

            assert await asyncio.gather(invoke(small), invoke(normal)) == ["context_budget", "ok"]
            assert len(calls) == 1 and small.model_calls == normal.model_calls == 1
    asyncio.run(run())


def test_rag_larger_input_does_not_bypass_cancellation_or_deadline():
    model = create_llm(Settings(), input_budget_scope="rag")
    ctx = context()
    with use_run(ctx):
        ctx.cancelled = True
        with pytest.raises(RunStopped, match="cancelled"):
            model._for_run()
        ctx.cancelled = False
        ctx.phase_end = 0
        with pytest.raises(RunStopped, match="deadline_exceeded"):
            model._for_run()
    assert ctx.model_calls == 0
