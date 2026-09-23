"""Transport-level budget, SQL ownership and saver lifecycle tests (no external services)."""
import asyncio
import json
import threading
import time
from unittest.mock import MagicMock

import httpx
import pytest
from langchain_core.messages import HumanMessage

from public_kb.llm_factory import BudgetedChatOpenAI
from public_kb.config import Settings
from agent.execution.context import RunContext, RunStopped, use_run, tool_window


@pytest.mark.parametrize("status", [200, 500])
def test_actual_sdk_attempts_are_counted_and_sdk_retries_disabled(status):
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(status, json={
            "id": "offline", "object": "chat.completion", "created": 0, "model": "offline",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        })
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            model = BudgetedChatOpenAI(model="offline", api_key="offline", base_url="http://offline.invalid/v1",
                                       http_async_client=client, max_retries=2)
            ctx = RunContext(Settings(), "hi", "test")
            with use_run(ctx):
                if status == 200:
                    assert (await model.ainvoke([HumanMessage(content="hi")])).content == "ok"
                else:
                    with pytest.raises(Exception):
                        await model.ainvoke([HumanMessage(content="hi")])
            assert ctx.model_calls == 1 and len(calls) == 1
            assert model.max_retries == 2 and model.root_async_client.max_retries == 2
            assert calls[0].get("max_completion_tokens", calls[0].get("max_tokens")) == 4096
    asyncio.run(run())


def test_expired_worker_lease_is_not_revived_by_finalization_phase():
    from agent.runtime import run_blocking
    released, entered = threading.Event(), threading.Event()
    outcome = []
    ctx = RunContext(Settings(), "test", "session")
    def worker():
        entered.set()
        released.wait(1)
        try:
            ctx.check()
        except RunStopped as exc:
            outcome.append(str(exc))
    async def run():
        with use_run(ctx):
            with tool_window(.02):
                task = asyncio.create_task(run_blocking(worker))
                while not entered.is_set():
                    await asyncio.sleep(.001)
                await asyncio.sleep(.03)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            ctx.set_phase("finalize", 15)
            assert ctx.remaining() > 0 and ctx.inflight == 1
            released.set()
            while ctx.inflight:
                await asyncio.sleep(.001)
    asyncio.run(run())
    assert outcome == ["deadline_exceeded"]


def test_exact_sql_uses_parameter_binding_and_holds_connection_until_finished(monkeypatch):
    from agent.tools import strict_sql
    calls = []
    class Cursor:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def execute(self, sql, params=()):
            calls.append((sql, params))
        def fetchall(self):
            if calls[-1][0].startswith("SHOW"):
                return [{"Field": f} for f in strict_sql.PUBLIC_FIELDS["company_penalty"]]
            return [{"company_name": "测试有限公司", "penalty_result": "罚款"}]
    conn = MagicMock()
    conn.cursor.return_value = Cursor()
    pool = MagicMock()
    pool.connection.return_value = conn
    monkeypatch.setattr(strict_sql, "get_pool", lambda: pool)
    ctx = RunContext(Settings(), "test", "session")
    with use_run(ctx):
        result = strict_sql.query("query_company_penalty", {"company_name": "测试有限公司"})
    sql, params = next(x for x in calls if x[0].startswith("SELECT"))
    assert "测试有限公司" not in sql and params[0] == "测试有限公司"
    assert "`company_name` = %s" in sql and "LIMIT %s" in sql
    assert result["ok"] and result["metadata"]["exact_scope"]
    conn.rollback.assert_called_once()
    conn.close.assert_called_once()


def test_sql_error_is_not_a_successful_empty_set(monkeypatch):
    from agent.tools import strict_sql
    conn = MagicMock()
    conn.cursor.side_effect = OSError("private connection details")
    pool = MagicMock()
    pool.connection.return_value = conn
    monkeypatch.setattr(strict_sql, "get_pool", lambda: pool)
    ctx = RunContext(Settings(), "test", "session")
    with use_run(ctx), pytest.raises(OSError):
        strict_sql.query("query_company_penalty", {"company_name": "测试有限公司"})
    conn.close.assert_called_once()


def test_pool_enforces_readonly_and_is_bounded(monkeypatch):
    from agent.tools import strict_sql
    factory = MagicMock()
    monkeypatch.setattr(strict_sql, "_pool", None)
    monkeypatch.setattr(strict_sql, "PooledDB", factory)
    with use_run(RunContext(Settings(), "test", "session")):
        strict_sql.get_pool()
    kwargs = factory.call_args.kwargs
    assert kwargs["blocking"] is False and kwargs["maxconnections"] > 0
    assert "SET SESSION TRANSACTION READ ONLY" in kwargs["setsession"]


def test_sqlite_async_lifecycle_and_restart(tmp_path):
    pytest.importorskip("langgraph.checkpoint.sqlite.aio")
    from agent.graph import AgentGraph
    from test_react_execution import ScriptedModel, fake_tool, proposal
    path = str(tmp_path / "checkpoints.db")
    settings = Settings(agent_execution_mode="hybrid", agent_react_rollout_percent=100,
                        checkpointer_backend="sqlite", checkpointer_sqlite_path=path)
    async def run():
        first = AgentGraph(llm=ScriptedModel(proposal=proposal("static")), settings=settings,
                           async_enabled=True, tools=[fake_tool([])])
        await first.ainvoke("查询测试有限公司处罚", "one")
        await first.aclose()
        second = AgentGraph(llm=ScriptedModel(proposal=proposal("static")), settings=settings,
                            async_enabled=True, tools=[fake_tool([])])
        state = await second.aget_state("one")
        assert len(state["messages"]) == 2
        assert state["business_result"]["execution_status"] == "complete"
        assert not await second.aget_state("other")
        await second.aclose()
    asyncio.run(run())


def test_same_thread_concurrent_requests_rejected():
    from agent.graph import AgentGraph
    from test_react_execution import ScriptedModel, fake_tool, proposal
    settings = Settings(agent_execution_mode="hybrid", agent_react_rollout_percent=100)
    agent = AgentGraph(llm=ScriptedModel(proposal=proposal("static")), settings=settings,
                       async_enabled=True, tools=[fake_tool([], wait=.05)])
    async def run():
        first = asyncio.create_task(agent.ainvoke("查询测试有限公司处罚", "one"))
        await asyncio.sleep(.01)
        with pytest.raises(RuntimeError, match="session_busy"):
            await agent.ainvoke("查询测试有限公司处罚", "one")
        await first
        assert not agent._busy
    asyncio.run(run())


@pytest.mark.parametrize("base_url", ["http://offline.invalid/v1", "https://api.deepseek.com"])
def test_real_sdk_router_and_official_loop_schemas_fit_context_budget(base_url):
    from agent.execution.service import run_request
    from test_react_execution import fake_tool, proposal
    calls, payloads = [], []
    def handler(request):
        payload = json.loads(request.content)
        payloads.append(payload)
        if base_url == "https://api.deepseek.com":
            assert payload["thinking"] == {"type": "disabled"}
            assert "response_format" not in payload
        names = {t["function"]["name"] for t in payload.get("tools", [])}
        if "ExecutionDecision" not in names:
            assert payload["parallel_tool_calls"] is False
        if "ExecutionDecision" in names:
            name, args = "ExecutionDecision", proposal()
        elif not any(m["role"] == "tool" for m in payload["messages"]):
            name, args = "query_company_penalty", {"task_id": "t1", "company_name": "测试有限公司"}
        else:
            data = json.loads(next(m["content"] for m in reversed(payload["messages"]) if m["role"] == "tool"))
            name, args = "AnswerCandidate", {"task_results": [{"task_id": "t1", "blocks": [
                {"kind": "record_fact", "record_ref": data["data"]["records"][0]["record_ref"],
                 "fields": ["company_name", "penalty_result"]}]}]}
        message = {"role": "assistant", "content": None, "tool_calls": [{"id": f"c{len(payloads)}",
                   "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}
        return httpx.Response(200, json={"id": "offline", "object": "chat.completion", "created": 0,
            "model": "offline", "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls"}]})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            model = BudgetedChatOpenAI(model="offline", api_key="offline", base_url=base_url,
                                       http_async_client=client, max_retries=2)
            ctx = RunContext(Settings(), "查测试有限公司的处罚", "sdk-test")
            result = await run_request(ctx, model, [HumanMessage(content=ctx.question)], [fake_tool(calls)])
            assert result["execution_status"] == "complete", result
            assert ctx.model_calls == 3 and len(payloads) == 3 and len(calls) == 1
            assert model.extra_body is None
    asyncio.run(run())
