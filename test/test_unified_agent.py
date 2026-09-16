"""Offline acceptance tests for the single top-level Agent baseline."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from collections import Counter
from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool

from agent.execution.context import RunContext, use_run
from agent.execution.contracts import ExecutionDecision
from agent.execution.evidence import EvidenceLedger
from agent.execution.service import execution_path, hybrid_selected
from agent.execution.unified import (
    BASELINE_TOOL_NAMES,
    execute_unified_tool,
    filter_baseline_tools,
    prepare_baseline_tools,
    run_unified_request,
)
from agent.nodes.general_chat import GENERAL_GUIDANCE
from agent.tools import GLOBAL_TOOL_REGISTRY, register_default_tools
from agent.tools.schemas import (
    QueryCompanyAwardHistoryInput,
    QueryCompanyPenaltyInput,
    QueryProjectAwardInput,
)
from public_kb.config import Settings


def tool_call(name, args, call_id="call"):
    return {"id": call_id, "name": name, "args": args}


def finish(status="complete", missing_fields=None, call_id="finish"):
    return tool_call("FinishAction", {
        "status": status,
        "missing_fields": missing_fields or [],
    }, call_id)


class UnifiedScriptedModel(BaseChatModel):
    responses: list[Any] = []
    calls: int = 0

    @property
    def _llm_type(self):
        return "offline-unified-scripted"

    def with_structured_output(self, *args, **kwargs):
        raise AssertionError("the unified path must not invoke a top-level router")

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        value = self.responses.pop(0)
        message = value(messages) if callable(value) else value
        return ChatResult(generations=[ChatGeneration(message=message)])


def fake_penalty_tool(calls, result=None):
    async def impl(company_name, task_id=None, top_k=None):
        calls.append(company_name)
        value = deepcopy(result) if result is not None else {
            "ok": True,
            "data": {"records": [{"company_name": company_name, "penalty_result": "罚款"}]},
            "metadata": {"exact_scope": True},
            "error": None,
        }
        return json.dumps(value, ensure_ascii=False), value

    return StructuredTool.from_function(
        coroutine=impl,
        name="query_company_penalty",
        description="offline penalty",
        args_schema=QueryCompanyPenaltyInput,
        response_format="content_and_artifact",
    )


def fake_project_award_tool(calls):
    async def impl(project_number, task_id=None, top_k=None):
        calls.append(project_number)
        value = {
            "ok": True,
            "data": {"records": [{
                "project_number": project_number,
                "project_name": "测试项目",
                "successful_bidder": "测试有限公司",
            }]},
            "metadata": {"exact_scope": True},
            "error": None,
        }
        return json.dumps(value, ensure_ascii=False), value

    return StructuredTool.from_function(
        coroutine=impl,
        name="query_project_award",
        description="offline project award",
        args_schema=QueryProjectAwardInput,
        response_format="content_and_artifact",
    )


def fake_company_award_history_tool(calls, records=None):
    async def impl(company_name, task_id=None, top_k=None):
        calls.append(company_name)
        rows = deepcopy(records) if records is not None else [{
            "project_number": "AH2024-001",
            "project_name": "测试项目",
            "successful_bidder": company_name,
        }]
        value = {
            "ok": True,
            "data": {"records": rows},
            "metadata": {"exact_scope": True},
            "error": None,
        }
        return json.dumps(value, ensure_ascii=False), value

    return StructuredTool.from_function(
        coroutine=impl,
        name="query_company_award_history",
        description="offline company award history",
        args_schema=QueryCompanyAwardHistoryInput,
        response_format="content_and_artifact",
    )


def context(question, **settings):
    ctx = RunContext(Settings(agent_execution_mode="unified", **settings), question, "session",
                     architecture="unified")
    ctx.ledger = EvidenceLedger()
    return ctx


def test_default_mode_is_unified_without_hybrid_router(monkeypatch):
    monkeypatch.delenv("AGENT_EXECUTION_MODE", raising=False)
    settings = Settings()
    assert settings.agent_execution_mode == "unified"
    assert execution_path(settings, "session") == "unified"
    assert not hybrid_selected(settings, "session")


def test_baseline_exposes_six_business_tools_with_separate_contracts():
    register_default_tools()
    exposed = prepare_baseline_tools(GLOBAL_TOOL_REGISTRY.to_langchain_tools())
    assert {tool.name for tool in exposed} == {
        "knowledge_qa",
        "query_company_registration",
        "query_company_business_scope",
        "query_project_award",
        "query_company_award_history",
        "query_company_penalty",
    }
    assert "search_business_data" not in BASELINE_TOOL_NAMES
    assert "search_public_kb" not in BASELINE_TOOL_NAMES
    assert "query_company_info" not in BASELINE_TOOL_NAMES
    assert "query_bid_records" not in BASELINE_TOOL_NAMES
    schemas = {tool.name: tool.args_schema.model_json_schema()["properties"] for tool in exposed}
    assert set(schemas["query_project_award"]) == {"project_number", "top_k"}
    assert set(schemas["query_company_award_history"]) == {"company_name", "top_k"}
    assert set(schemas["query_company_registration"]) == {"company_name", "top_k"}
    assert set(schemas["query_company_business_scope"]) == {"company_name", "top_k"}

    model_contract = json.dumps(ExecutionDecision.model_json_schema(), ensure_ascii=False)
    assert "search_public_kb" not in model_contract
    assert "search_business_data" not in model_contract


def test_single_tool_then_finish_without_router_or_free_text():
    calls = []
    model = UnifiedScriptedModel(responses=[
        AIMessage(content="不得发布的草稿", tool_calls=[tool_call(
            "query_company_penalty", {"company_name": "测试有限公司"})]),
        AIMessage(content="仍不得发布", tool_calls=[finish()]),
    ])
    ctx = context("查询测试有限公司的处罚")
    result = asyncio.run(run_unified_request(ctx, model, [fake_penalty_tool(calls)]))
    assert calls == ["测试有限公司"]
    assert model.calls == 2 and ctx.model_calls == 2
    assert result["execution_status"] == "complete"
    assert "罚款" in result["answer"]
    assert "不得发布" not in result["answer"]
    assert result["execution"]["mode"] == "unified"


def test_default_agent_graph_enters_unified_loop():
    from agent.graph import AgentGraph

    calls = []
    model = UnifiedScriptedModel(responses=[
        AIMessage(content="", tool_calls=[tool_call(
            "query_company_penalty", {"company_name": "测试有限公司"})]),
        AIMessage(content="", tool_calls=[finish()]),
    ])
    agent = AgentGraph(llm=model, tools=[fake_penalty_tool(calls)],
                       settings=Settings(agent_execution_mode="unified"), async_enabled=True)
    try:
        result = asyncio.run(agent.ainvoke("查询测试有限公司的处罚", "unified-graph"))
        state = agent.get_state("unified-graph")
    finally:
        agent.close()
    assert calls == ["测试有限公司"]
    assert result["intent"] == "agent"
    assert result["business_result"]["execution"]["mode"] == "unified"
    assert state["business_result"]["execution"]["mode"] == "unified"


def test_unified_stream_has_agent_stages_and_no_router_stage():
    from agent.graph import AgentGraph
    from agent.streaming import EventType

    calls = []
    model = UnifiedScriptedModel(responses=[
        AIMessage(content="", tool_calls=[tool_call(
            "query_company_penalty", {"company_name": "测试有限公司"})]),
        AIMessage(content="", tool_calls=[finish()]),
    ])
    agent = AgentGraph(llm=model, tools=[fake_penalty_tool(calls)],
                       settings=Settings(agent_execution_mode="unified"), async_enabled=True)

    async def collect():
        return [event async for event in agent.astream(
            "查询测试有限公司的处罚", "unified-stream")]

    try:
        events = asyncio.run(collect())
    finally:
        agent.close()
    stages = [event.payload.get("stage") for event in events if event.type is EventType.STAGE]
    assert events[0].payload["mode"] == "unified"
    assert events[-1].type is EventType.FINAL
    assert "agent_start" in stages and "validation_done" in stages
    assert "router_start" not in stages and "router_done" not in stages


def test_next_tool_may_use_a_verified_prior_result():
    bid_calls, penalty_calls = [], []
    model = UnifiedScriptedModel(responses=[
        AIMessage(content="", tool_calls=[tool_call(
            "query_project_award", {"project_number": "AH2024-001"}, "bid")]),
        AIMessage(content="", tool_calls=[tool_call(
            "query_company_penalty", {"company_name": "测试有限公司"}, "penalty")]),
        AIMessage(content="", tool_calls=[finish()]),
    ])
    ctx = context("查询项目AH2024-001的中标记录，再查中标供应商的处罚")
    result = asyncio.run(run_unified_request(
        ctx, model, [fake_project_award_tool(bid_calls), fake_penalty_tool(penalty_calls)]))
    assert bid_calls == ["AH2024-001"] and penalty_calls == ["测试有限公司"]
    assert [item["tool"] for item in result["data"]["actions"]] == [
        "query_project_award", "query_company_penalty"]
    assert "测试项目" in result["answer"] and "罚款" in result["answer"]


def test_project_award_may_use_only_prior_project_number_field():
    history_calls, project_calls = [], []
    model = UnifiedScriptedModel(responses=[
        AIMessage(content="", tool_calls=[tool_call(
            "query_company_award_history", {"company_name": "测试有限公司"}, "history")]),
        AIMessage(content="", tool_calls=[tool_call(
            "query_project_award", {"project_number": "AH2024-001"}, "project")]),
        AIMessage(content="", tool_calls=[finish()]),
    ])
    ctx = context("查询测试有限公司的中标历史，再查其中项目的中标情况")
    result = asyncio.run(run_unified_request(ctx, model, [
        fake_company_award_history_tool(history_calls),
        fake_project_award_tool(project_calls),
    ]))

    assert history_calls == ["测试有限公司"] and project_calls == ["AH2024-001"]
    assert result["execution_status"] == "complete"
    assert [item["tool"] for item in result["data"]["actions"]] == [
        "query_company_award_history", "query_project_award"]

    rejected_calls = []
    rejected_ctx = context("查询测试有限公司的中标历史")
    rejected_ctx.ledger.ingest("s0", "query_company_award_history", {
        "ok": True,
        "data": {"records": [{
            "project_number": "AH2024-001",
            "project_name": "AH2024-002",
            "successful_bidder": "测试有限公司",
        }]},
        "metadata": {"exact_scope": True},
        "error": None,
    }, 4000)

    async def reject_non_project_number_field():
        with use_run(rejected_ctx):
            return await execute_unified_tool(rejected_ctx, tool_call(
                "query_project_award", {"project_number": "AH2024-002"}), {
                    "query_project_award": fake_project_award_tool(rejected_calls),
                })

    rejected = asyncio.run(reject_non_project_number_field())
    assert rejected.status == "error" and "unbound_input" in rejected.content
    assert rejected_calls == [] and rejected_ctx.tool_executions == 0


def test_policy_rejection_then_success_does_not_pollute_published_result():
    calls = []
    model = UnifiedScriptedModel(responses=[
        AIMessage(content="", tool_calls=[tool_call(
            "query_company_penalty", {"company_name": "虚构有限公司"}, "rejected")]),
        AIMessage(content="", tool_calls=[tool_call(
            "query_company_penalty", {"company_name": "测试有限公司"}, "repaired")]),
        AIMessage(content="", tool_calls=[finish()]),
    ])
    ctx = context("查询测试有限公司的处罚")
    result = asyncio.run(run_unified_request(ctx, model, [fake_penalty_tool(calls)]))

    assert calls == ["测试有限公司"]
    assert ctx.tool_attempts == 2 and ctx.tool_executions == 1
    assert result["execution_status"] == "complete"
    assert "罚款" in result["answer"]
    assert "数据服务未成功返回" not in result["answer"]
    assert result["answer"].startswith("s1 · 处罚记录")
    assert "s2 ·" not in result["answer"]
    assert [item["outcome"] for item in result["data"]["actions"]] == [
        "policy_rejection", "tool_result"]
    assert result["execution"]["attempted_tools"] == [
        "query_company_penalty", "query_company_penalty"]
    assert result["execution"]["executed_tools"] == ["query_company_penalty"]
    assert result["execution"]["tools"] == ["query_company_penalty"]
    assert result["execution"]["policy_version"] == "agent-loop-v2"


def test_policy_rejection_without_verified_result_cannot_finish_as_complete():
    calls = []
    model = UnifiedScriptedModel(responses=[
        AIMessage(content="", tool_calls=[tool_call(
            "query_company_penalty", {"company_name": "虚构有限公司"})]),
        AIMessage(content="", tool_calls=[finish()]),
    ])
    ctx = context("查询测试有限公司的处罚")
    result = asyncio.run(run_unified_request(ctx, model, [fake_penalty_tool(calls)]))

    assert calls == [] and ctx.tool_executions == 0
    assert result["execution_status"] == "partial"
    assert "没有执行数据查询" in result["answer"]
    assert result["data"]["actions"][0]["outcome"] == "policy_rejection"


def test_real_tool_failure_remains_partial_and_user_visible():
    calls = []
    failed = {
        "ok": False,
        "data": {"records": []},
        "metadata": {"exact_scope": True},
        "error": {"code": "database_unavailable"},
    }
    model = UnifiedScriptedModel(responses=[
        AIMessage(content="", tool_calls=[tool_call(
            "query_company_penalty", {"company_name": "测试有限公司"})]),
        AIMessage(content="", tool_calls=[finish()]),
    ])
    ctx = context("查询测试有限公司的处罚")
    result = asyncio.run(run_unified_request(
        ctx, model, [fake_penalty_tool(calls, result=failed)]))

    assert calls == ["测试有限公司"]
    assert result["execution_status"] == "partial"
    assert "数据服务未成功返回" in result["answer"]
    assert result["data"]["actions"][0]["outcome"] == "tool_result"
    assert result["execution"]["executed_tools"] == ["query_company_penalty"]


def test_invented_argument_is_rejected_before_io():
    calls = []
    ctx = context("查询测试有限公司的处罚")
    tools = {"query_company_penalty": fake_penalty_tool(calls)}

    async def run():
        with use_run(ctx):
            return await execute_unified_tool(ctx, tool_call(
                "query_company_penalty", {"company_name": "虚构有限公司"}), tools)

    message = asyncio.run(run())
    assert message.status == "error"
    assert "unbound_input" in message.content
    assert calls == [] and ctx.tool_executions == 0


def test_batch_is_rejected_before_any_tool_execution():
    calls = []
    model = UnifiedScriptedModel(responses=[
        AIMessage(content="", tool_calls=[
            tool_call("query_company_penalty", {"company_name": "测试有限公司"}, "one"),
            tool_call("query_company_penalty", {"company_name": "测试有限公司"}, "two"),
        ]),
        AIMessage(content="", tool_calls=[finish("partial")]),
    ])
    ctx = context("查询测试有限公司的处罚")
    result = asyncio.run(run_unified_request(ctx, model, [fake_penalty_tool(calls)]))
    assert calls == [] and ctx.tool_attempts == 2 and ctx.tool_executions == 0
    assert ctx.attempted_tools == ["query_company_penalty", "query_company_penalty"]
    assert ctx.executed_tools == []
    assert result["execution_status"] == "partial"
    assert [item["outcome"] for item in result["data"]["actions"]] == [
        "policy_rejection", "policy_rejection"]


def test_rejected_batch_does_not_consume_unified_external_io_budget():
    calls = []
    model = UnifiedScriptedModel(responses=[
        AIMessage(content="", tool_calls=[
            tool_call("query_company_penalty", {"company_name": "测试有限公司"}, "one"),
            tool_call("query_company_penalty", {"company_name": "测试有限公司"}, "two"),
            tool_call("query_company_penalty", {"company_name": "测试有限公司"}, "three"),
        ]),
        AIMessage(content="", tool_calls=[tool_call(
            "query_company_penalty", {"company_name": "测试有限公司"}, "repaired")]),
        AIMessage(content="", tool_calls=[finish()]),
    ])
    ctx = context("查询测试有限公司的处罚", agent_max_tool_calls=1)
    result = asyncio.run(run_unified_request(ctx, model, [fake_penalty_tool(calls)]))

    assert calls == ["测试有限公司"]
    assert ctx.tool_attempts == 4 and ctx.tool_executions == 1
    assert len(ctx.attempted_tools) == 4
    assert ctx.executed_tools == ["query_company_penalty"]
    assert result["execution_status"] == "complete"
    assert [item["outcome"] for item in result["data"]["actions"]] == [
        "policy_rejection", "policy_rejection", "policy_rejection", "tool_result"]
    assert "数据服务未成功返回" not in result["answer"]


def test_explicit_hybrid_and_legacy_remain_rollback_paths():
    hybrid = Settings(agent_execution_mode="hybrid", agent_react_rollout_percent=100)
    assert execution_path(hybrid, "session") == "hybrid"
    assert execution_path(Settings(agent_execution_mode="legacy"), "session") == "legacy"


def test_minimal_variable_eval_corpus_covers_six_business_tools():
    data = json.loads((Path(__file__).parent / "fixtures" / "unified_eval_cases.json").read_text(encoding="utf-8"))
    cases = data["cases"]
    assert len(cases) == 16
    assert Counter(case["category"] for case in cases) == {
        "single_tool": 6,
        "independent_multi_tool": 2,
        "result_dependent": 2,
        "clarify_or_unsupported": 4,
        "empty_or_failure": 2,
    }
    assert data["variables"]["temperature"] == 0
    assert data["variables"]["tool_backend"] == "fixed_fake"


def test_general_guidance_matches_the_six_business_line_boundary():
    for capability in (
        "法律法规问答",
        "企业工商信息",
        "企业经营范围",
        "项目中标情况",
        "企业中标历史",
        "企业违法/处罚信息",
    ):
        assert capability in GENERAL_GUIDANCE
    assert "产品中标价格" not in GENERAL_GUIDANCE
    assert "经营异常" not in GENERAL_GUIDANCE
