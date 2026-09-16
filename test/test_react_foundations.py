"""Hard boundaries: failures are not empty results; budgets propagate to workers."""
import asyncio
import json
import threading
from unittest.mock import MagicMock

import pytest


def test_penalty_connection_failure_is_not_empty(monkeypatch):
    from agent.nodes.price_inquiry import queries
    monkeypatch.setattr(queries, "_get_connection", lambda _: None)
    with pytest.raises(ConnectionError):
        queries._query_penalty_by_company_name("测试有限公司")


def test_penalty_query_failure_releases_connection(monkeypatch):
    from agent.nodes.price_inquiry import queries
    conn = MagicMock()
    conn.cursor.return_value.__enter__.return_value.execute.side_effect = OSError("secret-dsn")
    released = []
    monkeypatch.setattr(queries, "_get_connection", lambda _: conn)
    monkeypatch.setattr(queries, "_release_connection", released.append)
    with pytest.raises(ConnectionError):
        queries._query_penalty_by_company_name("测试有限公司")
    assert released == [conn]


def test_truncated_tool_result_is_valid_json():
    from agent.tools.base import make_tool_result, render_tool_content
    result = make_tool_result(data={"chunks": [{"text": "法" * 5000, "chunk_uid": "u"}]})
    content = render_tool_content(result, max_chars=500)
    assert len(content) <= 500
    assert json.loads(content)["data"]["_truncated_by_chars"]
    assert result["data"]["chunks"][0]["text"] == "法" * 5000


def test_context_propagates_and_worker_remains_inflight_on_cancel():
    from agent.execution.context import RunContext, current_run, use_run
    from agent.runtime.async_bridge import run_blocking
    from public_kb.config import Settings
    started, finish = threading.Event(), threading.Event()
    observed = []

    def worker():
        observed.append(current_run())
        started.set()
        finish.wait(2)

    async def scenario():
        ctx = RunContext(Settings(), question="测试", thread_id="t")
        with use_run(ctx):
            task = asyncio.create_task(run_blocking(worker))
            await asyncio.to_thread(started.wait, 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert ctx.inflight == 1
            finish.set()
            for _ in range(100):
                if ctx.inflight == 0:
                    break
                await asyncio.sleep(0.005)
            assert ctx.inflight == 0
        assert observed == [ctx]
        assert current_run() is None

    asyncio.run(scenario())


def test_model_budget_cannot_be_reset_by_stage():
    from agent.execution.context import RunContext, RunStopped
    from public_kb.config import Settings
    ctx = RunContext(Settings(agent_max_model_calls=2), question="测试", thread_id="t")
    ctx.reserve_model()
    ctx.set_phase("react", 65)
    ctx.reserve_model()
    with pytest.raises(RunStopped, match="model_budget"):
        ctx.reserve_model()


def test_separate_award_schemas_reject_legacy_cross_scope_args():
    from pydantic import ValidationError
    from agent.tools.schemas import QueryCompanyAwardHistoryInput, QueryProjectAwardInput
    with pytest.raises(ValidationError):
        QueryProjectAwardInput(project_number="AH2024-001", company_name="测试有限公司")
    with pytest.raises(ValidationError):
        QueryProjectAwardInput(project_number="AH2024-001", purchaser="测试学校")
    with pytest.raises(ValidationError):
        QueryCompanyAwardHistoryInput(company_name="测试有限公司", project_number="AH2024-001")
    with pytest.raises(ValidationError):
        QueryCompanyAwardHistoryInput(company_name="测试有限公司", sql="DELETE FROM x")
