"""Offline acceptance for authorization, real official loop and publication boundaries."""
from __future__ import annotations
import asyncio
import json
from copy import deepcopy
from typing import Any
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatResult, ChatGeneration
from langchain_core.runnables import RunnableLambda
from langchain_core.tools import StructuredTool

from public_kb.config import Settings
from agent.execution.context import RunContext, RunStopped, use_run
from agent.execution.contracts import ExecutionDecision
from agent.execution.policy import validate_decision, validate_call, task_arguments
from agent.execution.evidence import EvidenceLedger
from agent.execution.output import conservative_candidate, render
from agent.execution.service import run_request, hybrid_selected
from agent.execution.executor import execute_tool
from agent.tools.schemas import QueryCompanyPenaltyInput


def proposal(mode="react", company="测试有限公司"):
    return {"execution_mode": mode, "static_branch": "price_inquiry" if mode == "static" else None,
            "tasks": [{"task_id": "t1", "capability": "company_penalty", "input_refs": [
                {"field": "company_name", "source_id": "u0", "value": company}]}],
            "reason_code": "EXISTING_BRANCH_COVERS" if mode == "static" else "MULTI_TARGET",
            "suggested_tools": ["query_company_penalty"]}


class ScriptedModel(BaseChatModel):
    proposal: dict
    responses: list[Any] = []
    calls: int = 0
    routed: int = 0

    @property
    def _llm_type(self):
        return "offline-scripted"

    def with_structured_output(self, schema, **kwargs):
        async def run(_):
            self.routed += 1
            return schema.model_validate(self.proposal)
        return RunnableLambda(run)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        if self.responses:
            value = self.responses.pop(0)
            message = value(messages) if callable(value) else value
        else:
            tool = next(m for m in reversed(messages) if isinstance(m, ToolMessage))
            data = json.loads(tool.content)
            row = data["data"]["records"][0]
            message = AIMessage(content="", tool_calls=[{"name": "AnswerCandidate", "id": "answer",
                "args": {"task_results": [{"task_id": "t1", "blocks": [{"kind": "record_fact",
                        "record_ref": row["record_ref"], "fields": ["company_name", "penalty_result"]}]}]}}])
        return ChatResult(generations=[ChatGeneration(message=message)])


def call(args=None, name="query_company_penalty", call_id="one"):
    return {"id": call_id, "name": name, "args": args or {"task_id": "t1", "company_name": "测试有限公司"}}


def fake_tool(calls, result=None, wait=0):
    async def impl(company_name, task_id=None, top_k=None):
        calls.append(company_name)
        if wait:
            await asyncio.sleep(wait)
        value = deepcopy(result) if result is not None else {
            "ok": True, "data": {"records": [{"company_name": company_name, "penalty_result": "罚款"}]},
            "metadata": {"exact_scope": True}, "error": None}
        return json.dumps(value, ensure_ascii=False), value
    return StructuredTool.from_function(coroutine=impl, name="query_company_penalty",
              description="offline", args_schema=QueryCompanyPenaltyInput, response_format="content_and_artifact")


def context(**kwargs):
    ctx = RunContext(Settings(**kwargs), "查询测试有限公司的处罚", "session")
    ctx.ledger = EvidenceLedger()
    ctx.decision = ExecutionDecision.model_validate(proposal())
    return ctx


def test_official_loop_executes_then_renders_only_verified_fact():
    calls = []
    model = ScriptedModel(proposal=proposal(), responses=[AIMessage(content="内部草稿不得外发", tool_calls=[call()])])
    ctx = context()
    result = asyncio.run(run_request(ctx, model, [HumanMessage(content=ctx.question)], [fake_tool(calls)]))
    assert calls == ["测试有限公司"]
    assert result["execution_status"] == "complete", result
    assert "罚款" in result["answer"] and "内部草稿" not in result["answer"]
    assert model.routed == 1 and model.calls == 2
    assert ctx.model_calls == 3 and ctx.tool_executions == 1


def test_static_uses_no_second_intent_model():
    calls = []
    model = ScriptedModel(proposal=proposal("static"))
    ctx = context()
    result = asyncio.run(run_request(ctx, model, [HumanMessage(content=ctx.question)], [fake_tool(calls)]))
    assert calls == ["测试有限公司"] and model.calls == 0
    assert result["execution_status"] == "complete"


def test_batch_rejected_before_any_execution():
    calls = []
    model = ScriptedModel(proposal=proposal(), responses=[AIMessage(content="", tool_calls=[call(), call(call_id="two")])])
    ctx = context()
    result = asyncio.run(run_request(ctx, model, [HumanMessage(content=ctx.question)], [fake_tool(calls)]))
    assert calls == [] and ctx.tool_attempts == 2
    assert result["execution_status"] != "complete"


@pytest.mark.parametrize("change", [
    {"company_name": "模型虚构有限公司", "task_id": "t1"},
    {"company_name": "测试有限公司", "sql": "DELETE FROM company_penalty", "task_id": "t1"},
    {"company_name": "测试有限公司"},
])
def test_tool_scope_violation_is_not_executed(change):
    ctx, calls = context(), []
    tools = {"query_company_penalty": fake_tool(calls)}
    async def run():
        with use_run(ctx):
            return await execute_tool(ctx, call(change), tools)
    result = asyncio.run(run())
    assert result.status == "error" and calls == []
    assert result.tool_call_id == "one"


def test_identical_calls_are_cached_but_attempts_are_counted():
    ctx, calls = context(), []
    tools = {"query_company_penalty": fake_tool(calls)}
    async def run():
        with use_run(ctx):
            await execute_tool(ctx, call(), tools)
            await execute_tool(ctx, call(call_id="two"), tools)
    asyncio.run(run())
    assert len(calls) == 1 and ctx.tool_attempts == 2 and ctx.tool_executions == 1


def test_router_cannot_bind_an_entity_from_assistant_or_invent_it():
    ctx = context()
    tools = [fake_tool([])]
    with pytest.raises(RunStopped, match="unbound_input"):
        validate_decision(ctx, proposal(company="虚构有限公司"), tools)
    value = proposal()
    value["tasks"][0]["input_refs"][0]["source_id"] = "assistant0"
    with pytest.raises(RunStopped):
        validate_decision(ctx, value, tools)


def test_disabled_tool_rejected_by_router():
    with pytest.raises(RunStopped, match="capability_unavailable"):
        validate_decision(context(), proposal(), [])


def test_unimplemented_date_filter_is_not_silently_dropped():
    value = proposal()
    value["tasks"][0]["input_refs"].append({"field": "time_start", "source_id": "u0", "value": "2024-01-01"})
    with pytest.raises(RunStopped, match="unsupported_filter"):
        validate_decision(context(), value, [fake_tool([])])


def test_artifact_only_evidence_is_not_usable_and_snapshot_is_immutable():
    ctx = context()
    result = {"ok": True, "data": {"records": [{"company_name": "测试有限公司", "penalty_result": "X" * 1000}]}}
    content = ctx.ledger.ingest("t1", "query_company_penalty", result, 256)
    assert json.loads(content)["ok"] and ctx.ledger.for_task("t1") == []
    result["data"]["records"].clear()
    assert len(ctx.ledger.snapshots[0]["result"]["data"]["records"]) == 1


def test_invented_record_reference_blocks_publication():
    ctx = context()
    candidate = {"task_results": [{"task_id": "t1", "blocks": [
        {"kind": "record_fact", "record_ref": "fake", "fields": ["company_name"]}]}]}
    with pytest.raises(RunStopped, match="unknown_evidence"):
        render(ctx, candidate)


def test_tool_failure_cannot_be_rendered_as_no_match():
    ctx = context()
    ctx.receipts.append({"task_id": "t1", "tool": "query_company_penalty", "ok": False, "empty": True})
    candidate = {"task_results": [{"task_id": "t1", "blocks": [{"kind": "limitation", "reason": "no_match"}]}]}
    with pytest.raises(RunStopped, match="unverified_empty"):
        render(ctx, candidate)
    result = render(ctx, conservative_candidate(ctx))
    assert "数据服务未成功返回" in result["answer"]


def test_legal_quotes_must_be_verbatim_and_r1_r7_enforced():
    ctx = context()
    chunk = {"chunk_id": "pk1", "chunk_uid": "uid1", "doc_name": "法规", "chapter": "第一条", "text": "应当遵守本条规定。"}
    ctx.ledger.ingest("t1", "search_public_kb", {"ok": True, "data": {"chunks": [chunk]}}, 4000)
    eid = ctx.ledger.for_task("t1")[0]["id"]
    candidate = {"task_results": [{"task_id": "t1", "blocks": [{"kind": "legal_excerpt", "evidence_id": eid, "quote": "可以不遵守。"}]}]}
    with pytest.raises(RunStopped, match="quote_not_in_source"):
        render(ctx, candidate)
    candidate["task_results"][0]["blocks"][0]["quote"] = chunk["text"]
    result = render(ctx, candidate)
    assert result["data"]["citation_validation"]["all_passed"]
    assert all(r["enabled"] for r in result["data"]["citation_validation"]["rules"])
    ctx.ledger.entries[eid]["payload"]["chunk_id"] = None
    with pytest.raises(RunStopped, match="citation_invalid"):
        render(ctx, candidate)


def test_rollout_is_stable_and_hybrid_is_explicit(monkeypatch):
    monkeypatch.delenv("AGENT_EXECUTION_MODE", raising=False)
    monkeypatch.delenv("AGENT_REACT_ROLLOUT_PERCENT", raising=False)
    monkeypatch.delenv("AGENT_REACT_THREAD_ALLOWLIST", raising=False)
    assert not hybrid_selected(Settings(), "session")
    assert not hybrid_selected(Settings(), "another-session")
    assert hybrid_selected(Settings(agent_execution_mode="hybrid"), "session")
    assert not hybrid_selected(Settings(agent_execution_mode="legacy"), "session")
    settings = Settings(agent_execution_mode="hybrid", agent_react_rollout_percent=0,
                        agent_react_thread_allowlist="review")
    assert hybrid_selected(settings, "review") and not hybrid_selected(settings, "other")


def test_cancel_propagates_and_does_not_retry():
    calls, ctx = [], context()
    model = ScriptedModel(proposal=proposal("static"))
    async def run():
        task = asyncio.create_task(run_request(ctx, model, [HumanMessage(content=ctx.question)], [fake_tool(calls, wait=10)]))
        async with asyncio.timeout(2):
            while not calls:
                await asyncio.sleep(.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert calls == ["测试有限公司"] and ctx.cancelled


def test_hybrid_graph_stream_and_memory_do_not_leak_internal_messages():
    from agent.graph import AgentGraph
    from agent.streaming import EventType
    calls = []
    model = ScriptedModel(proposal=proposal("static"))
    agent = AgentGraph(llm=model, settings=Settings(agent_execution_mode="hybrid", agent_react_rollout_percent=100),
                       async_enabled=True, tools=[fake_tool(calls)])
    async def run():
        events = [e async for e in agent.astream("查询测试有限公司的处罚", "private1")]
        state = await agent.aget_state("private1")
        other = await agent.aget_state("private2")
        return events, state, other
    events, state, other = asyncio.run(run())
    assert events[-1].type == EventType.FINAL, [e.model_dump() for e in events]
    assert sum(e.type in {EventType.FINAL, EventType.ERROR, EventType.CANCELLED} for e in events) == 1
    first_token = next(i for i, e in enumerate(events) if e.type == EventType.TOKEN)
    assert any(e.payload.get("stage") == "validation_done" for e in events[:first_token])
    assert [type(m) for m in state["messages"]] == [HumanMessage, AIMessage]
    assert not other


def test_completed_and_unfinished_history_are_not_binding_sources():
    from agent.execution.router import decide
    ctx = context()
    value = proposal("static")
    value["tasks"][0]["input_refs"][0]["source_id"] = "u1"
    model = ScriptedModel(proposal=value)
    messages = [HumanMessage(content="测试有限公司"), AIMessage(content="上一轮已完成"),
                HumanMessage(content="取消的另一家公司"), HumanMessage(content="查刚才那家公司的处罚")]
    ctx.question = str(messages[-1].content)
    ctx.sources["u0"] = ctx.question
    with pytest.raises(RunStopped, match="router_invalid"):
        asyncio.run(decide(ctx, model, messages, [fake_tool([])]))
    assert ctx.sources == {"u0": ctx.question}


def test_dependency_only_accepts_unique_verified_bidder():
    ctx = context()
    ctx.decision = ExecutionDecision.model_validate({
        "execution_mode": "react", "reason_code": "RESULT_DEPENDENT", "tasks": [
            {"task_id": "t1", "capability": "project_award", "input_refs": []},
            {"task_id": "t2", "capability": "company_penalty", "depends_on": ["t1"],
             "input_refs": [{"field": "company_name", "source_id": "t1.successful_bidder", "value": ""}]}]})
    result = {"ok": True, "data": {"records": [{"successful_bidder": "测试有限公司"}]}}
    ctx.ledger.ingest("t1", "query_project_award", result, 4000)
    assert task_arguments(ctx, ctx.decision.tasks[1])["company_name"] == "测试有限公司"
    result["data"]["records"] = [{"successful_bidder": "另一家有限公司"}]
    ctx.ledger.ingest("t1", "query_project_award", result, 4000)
    with pytest.raises(RunStopped, match="ambiguous_dependency"):
        task_arguments(ctx, ctx.decision.tasks[1])


def test_same_legal_chunk_deduplicated_across_calls_and_tasks():
    ledger = EvidenceLedger()
    chunk = {"chunk_id": "pk1", "chunk_uid": "uid1", "text": "原文", "doc_name": "法规", "chapter": "第一条"}
    result = {"ok": True, "data": {"chunks": [chunk]}}
    ledger.ingest("t1", "search_public_kb", result, 4000)
    ledger.ingest("t2", "search_public_kb", result, 4000)
    assert len(ledger.entries) == 1
    assert ledger.for_task("t1")[0]["id"] == ledger.for_task("t2")[0]["id"]


def test_response_subject_mismatch_is_rejected():
    calls, ctx = [], context()
    result = {"ok": True, "metadata": {"exact_scope": True},
              "data": {"records": [{"company_name": "另一家有限公司"}]}}
    tools = {"query_company_penalty": fake_tool(calls, result)}
    async def run():
        with use_run(ctx):
            return await execute_tool(ctx, call(), tools)
    message = asyncio.run(run())
    assert message.status == "error" and "result_scope_mismatch" in message.content
    assert not ctx.ledger.entries


def test_invalid_router_has_only_one_repair():
    calls, ctx = [], context()
    model = ScriptedModel(proposal=proposal(company="虚构有限公司"))
    result = asyncio.run(run_request(ctx, model, [HumanMessage(content=ctx.question)], [fake_tool(calls)]))
    assert model.routed == 2 and ctx.model_calls == 2 and calls == []
    assert result["execution_status"] == "insufficient_evidence"


def test_tool_attempt_budget_rejects_without_io():
    calls, ctx = [], context(agent_max_tool_calls=1)
    tools = {"query_company_penalty": fake_tool(calls)}
    async def run():
        with use_run(ctx):
            await execute_tool(ctx, call(), tools)
            with pytest.raises(RunStopped, match="tool_budget"):
                await execute_tool(ctx, call(call_id="two"), tools)
    asyncio.run(run())
    assert len(calls) == 1 and ctx.tool_attempts == 1


def test_unstructured_model_reply_only_allows_guarded_static_fallback():
    calls, ctx = [], context()
    model = ScriptedModel(proposal=proposal(), responses=[AIMessage(content="保证无风险！")])
    result = asyncio.run(run_request(ctx, model, [HumanMessage(content=ctx.question)], [fake_tool(calls)]))
    assert calls == ["测试有限公司"] and ctx.fallback_branch == "price_inquiry"
    assert result["execution_status"] == "partial"
    assert "保证无风险" not in result["answer"] and result["execution"]["react_fallback"]


def test_eval_corpus_is_explicitly_unreviewed():
    from pathlib import Path
    from collections import Counter
    data = json.loads((Path(__file__).parent / "fixtures/react_eval_cases.json").read_text(encoding="utf-8"))
    assert Counter(c["category"] for c in data["cases"]) == {"static": 60, "react": 60, "clarify": 40, "unsupported": 40}
    assert all("reviewed_by" in c and "reviewed_at" in c for c in data["cases"])


@pytest.mark.parametrize("value", [0, -1, True, 101])
def test_invalid_top_k_is_rejected_before_execution(value):
    from pydantic import ValidationError
    with pytest.raises((RunStopped, ValidationError)):
        validate_call(context(), "query_company_penalty",
                      {"task_id": "t1", "company_name": "测试有限公司", "top_k": value},
                      {"query_company_penalty": fake_tool([])})
