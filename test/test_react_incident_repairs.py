"""Regressions for the three user-reported queries and their failure modes."""
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import HumanMessage, AIMessage

from agent.nodes.answer_templates import render_answer
from agent.nodes.price_inquiry.company_response import company_response
from agent.nodes.price_inquiry.models import SearchIntent, HardFilters
from public_kb.config import Settings

COMPANY = "合肥盈拓成电子科技有限公司"
QUESTIONS = [
    f"查一下{COMPANY}的工商信息，再告诉我招标的方式有哪些？",
    f"查一下{COMPANY}的工商信息，再差一下评审委员会的职责有哪些？",
    f"查一下{COMPANY}的工商信息，再查询一下这家公司有没有处罚记录？",
]


def intent():
    return SearchIntent(hard_filters=HardFilters(company_name=COMPANY),
                        sub_route="company_query", query_type="penalty_check",
                        need_penalty_check=True, original_question=QUESTIONS[2])


def profile():
    return {"company_name": COMPANY, "credit_code": "test-code", "legal_person": "测试法人",
            "_source_table": "company_info"}


@pytest.mark.parametrize("status", ["empty", "failed", "timeout"])
def test_profile_is_not_a_penalty_and_empty_is_not_failure(status):
    response = company_response(intent(), {"records": [profile()], "table_status": {
        "company_info": "success", "company_penalty": status}})
    biz = response["business_result"]
    assert COMPANY in biz["answer"] and "测试法人" in biz["answer"]
    assert "存在不良记录" not in biz["answer"]
    assert ("未检索到处罚记录" in biz["answer"]) == (status == "empty")
    assert biz["execution_status"] == ("complete" if status == "empty" else "partial")


def test_missing_optional_field_only_filled_for_actual_penalty():
    row = {"company_name": COMPANY, "penalty_result": "罚款", "_source_table": "company_penalty"}
    answer = render_answer("penalty_check", [row])
    assert "处罚日期：未提供" in answer and "罚款" in answer
    with pytest.raises(ValueError, match="source_mismatch"):
        render_answer("penalty_check", [profile()])
    with pytest.raises(ValueError, match="penalty_evidence"):
        render_answer("penalty_check", [{"company_name": COMPANY}])


def test_multiple_penalties_stay_separate_from_profile():
    penalties = [dict(company_name=COMPANY, penalty_result=f"罚款{i}", penalty_date=f"2025-01-0{i}",
                      _source_table="company_penalty") for i in (1, 2)]
    result = company_response(intent(), {"records": [profile(), *penalties],
        "table_status": {"company_info": "success", "company_penalty": "success"}})
    assert "存在 2 条" in result["business_result"]["answer"]
    assert "存在 3 条" not in result["business_result"]["answer"]
    assert len(result["business_result"]["data"]["records"]) == 3


def test_actual_async_node_original_crash_scenario(monkeypatch):
    from agent.nodes.price_inquiry import node_async as node
    monkeypatch.setattr(node, "_build_llm", lambda: None)
    monkeypatch.setattr(node, "_parse_unified_intent_async", AsyncMock(return_value=intent()))
    monkeypatch.setattr(node, "_normalize_intent_enums", lambda x: x)
    monkeypatch.setattr(node, "query_tables_async", AsyncMock(return_value={
        "records": [profile()], "table_status": {"company_info": "success", "company_penalty": "empty"}}))
    result = asyncio.run(node.node_price_inquiry_async({"messages": [HumanMessage(content=QUESTIONS[2])]}))
    assert "未检索到处罚记录" in result["business_result"]["answer"]


@pytest.mark.parametrize("failure", [None, ConnectionError("offline"), TimeoutError("offline")])
def test_sync_company_query_preserves_empty_and_error(monkeypatch, failure):
    from agent.nodes.price_inquiry import queries
    monkeypatch.setattr(queries, "_query_tables", lambda *a: {
        "records": [profile()], "table_status": {"company_info": "success"}})
    def penalty(_):
        if failure:
            raise failure
        return []
    monkeypatch.setattr(queries, "_query_penalty_by_company_name", penalty)
    result = queries._query_company_data(intent())
    status = "empty" if failure is None else "timeout" if isinstance(failure, TimeoutError) else "failed"
    assert result["table_status"]["company_penalty"] == status
    assert "penalty_date" not in result["records"][0]
    assert company_response(intent(), result)["business_result"]["execution_status"] == (
        "complete" if failure is None else "partial")


@pytest.mark.parametrize("failure", [None, ConnectionError("offline"), TimeoutError("offline")])
def test_async_exact_penalty_status(monkeypatch, failure):
    from agent.nodes.price_inquiry import recall_async as recall
    @asynccontextmanager
    async def acquire():
        yield object()
    monkeypatch.setattr(recall, "acquire", acquire)
    monkeypatch.setattr(recall, "_get_classification", lambda _: {"fields": ["company_name"]})
    execute = AsyncMock(side_effect=failure) if failure else AsyncMock(return_value=([], .01))
    monkeypatch.setattr(recall, "safe_execute", execute)
    result = asyncio.run(recall._query_table_async("company_penalty", intent()))
    assert result["status"] == ("empty" if failure is None else
                                "timeout" if isinstance(failure, TimeoutError) else "failed")
    sql, params = execute.call_args.args[1:3]
    assert " = %s" in sql and "LIMIT 50" in sql and params == (COMPANY,)


def test_cli_clear_keeps_explicit_hybrid_only_for_new_session(capsys):
    from agent.__main__ import _describe_session
    from agent.execution.service import hybrid_selected
    agent = SimpleNamespace(_settings=Settings(agent_execution_mode="hybrid", agent_react_rollout_percent=0),
                            _cli_execution_mode="hybrid")
    _describe_session(agent, "first")
    assert hybrid_selected(agent._settings, "first")
    _describe_session(agent, "second")
    assert hybrid_selected(agent._settings, "second")
    assert not hybrid_selected(agent._settings, "first")
    assert "hybrid" in capsys.readouterr().out


def test_rejected_batch_can_repair_once_without_executing_rejected_calls():
    from test_react_execution import ScriptedModel, proposal, context, fake_tool, call
    from agent.execution.service import run_request
    calls, ctx = [], context()
    model = ScriptedModel(proposal=proposal(), responses=[
        AIMessage(content="", tool_calls=[call(), call(call_id="two")]),
        AIMessage(content="", tool_calls=[call(call_id="single")])])
    result = asyncio.run(run_request(ctx, model, [HumanMessage(content=ctx.question)], [fake_tool(calls)]))
    assert result["execution_status"] == "complete"
    assert len(calls) == 1 and ctx.tool_attempts == 3 and ctx.model_calls == 4


def test_repeated_batches_stop_without_any_tool_execution():
    from test_react_execution import ScriptedModel, proposal, context, fake_tool, call
    from agent.execution.service import run_request
    batch = AIMessage(content="", tool_calls=[call(), call(call_id="two")])
    calls, ctx = [], context()
    model = ScriptedModel(proposal=proposal(), responses=[batch, batch])
    result = asyncio.run(run_request(ctx, model, [HumanMessage(content=ctx.question)], [fake_tool(calls)]))
    assert not calls and ctx.tool_attempts == 4
    assert result["execution"]["failure_code"] == "batch_tool_calls_rejected"


def test_short_model_excerpt_cannot_authorize_unseen_tail_but_citation_keeps_full_text():
    from test_react_execution import context
    from agent.execution.output import render
    from agent.execution.context import RunStopped
    ctx = context()
    text = "可见的法规原文。" * 200 + "不允许模型引用的尾部。"
    chunk = {"chunk_id": "pk1", "chunk_uid": "uid1", "doc_name": "法规", "chapter": "第一条", "text": text}
    content = ctx.ledger.ingest("t1", "search_public_kb", {"ok": True, "data": {"chunks": [chunk]}}, 4000)
    view = json.loads(content)["data"]["chunks"][0]
    assert len(view["text"]) <= 500 and "尾部" not in content
    assert ctx.ledger.entries[view["evidence_id"]]["payload"]["text"] == text
    block = {"kind": "legal_excerpt", "evidence_id": view["evidence_id"]}
    candidate = {"task_results": [{"task_id": "t1", "blocks": [block]}]}
    result = render(ctx, candidate)
    assert result["data"]["citations"][0]["text"] == text
    assert "尾部" not in result["answer"]
    block["quote"] = "不允许模型引用的尾部。"
    with pytest.raises(RunStopped, match="quote_not_in_source"):
        render(ctx, candidate)


@pytest.mark.parametrize("index", [0, 1, 2])
def test_original_questions_cover_both_tasks_through_verified_graph(index):
    from test_react_execution import ScriptedModel
    from langchain_core.tools import StructuredTool
    from agent.tools.schemas import QueryCompanyRegistrationInput, QueryCompanyPenaltyInput, KnowledgeQAInput
    from agent.execution.context import RunContext
    from agent.execution.service import run_request
    question = QUESTIONS[index]
    law = "招标的方式有哪些" if index == 0 else "评审委员会的职责有哪些"
    second = "public_kb_qa" if index < 2 else "company_penalty"
    second_tool = "knowledge_qa" if index < 2 else "query_company_penalty"
    second_args = {"task_id": "t2", **({"question": law} if index < 2 else {"company_name": COMPANY})}
    decision = {
        "execution_mode": "react" if index < 2 else "static",
        "static_branch": None if index < 2 else "price_inquiry",
        "reason_code": "CROSS_CAPABILITY" if index < 2 else "EXISTING_BRANCH_COVERS",
        "tasks": [
            {"task_id": "t1", "capability": "company_registration", "input_refs": [
                {"field": "company_name", "source_id": "u0", "value": COMPANY}]},
            {"task_id": "t2", "capability": second, "input_refs": [
                {"field": "question" if index < 2 else "company_name", "source_id": "u0",
                 "value": law if index < 2 else COMPANY}]}],
        "suggested_tools": ["query_company_registration", second_tool],
    }
    calls = []
    def tool(name, schema, value):
        async def execute(**kwargs):
            calls.append(name)
            return json.dumps(value, ensure_ascii=False), value
        return StructuredTool.from_function(coroutine=execute, name=name, description="offline",
                    args_schema=schema, response_format="content_and_artifact")
    info = {"ok": True, "data": {"records": [{"company_name": COMPANY, "legal_person": "测试法人"}]},
            "metadata": {"exact_scope": True}}
    other = ({"ok": True, "data": {"answer": "按照规定履行职责。【来源1】",
        "citations": [{"context_index": 1, "chunk_id": "pk", "chunk_uid": "uid",
        "doc_name": "法规", "chapter": "第一条", "text": "按照规定履行职责。"}]}} if index < 2 else
        {"ok": True, "data": {"records": []}, "metadata": {"exact_scope": True}})
    def final(messages):
        from langchain_core.messages import ToolMessage
        observations = [json.loads(m.content)["data"] for m in messages if isinstance(m, ToolMessage)]
        return AIMessage(content="", tool_calls=[{"id": "answer", "name": "AnswerCandidate",
            "args": {"task_results": [
                {"task_id": "t1", "blocks": [{"kind": "record_fact",
                 "record_ref": observations[0]["records"][0]["record_ref"], "fields": ["company_name", "legal_person"]}]},
                {"task_id": "t2", "blocks": [{"kind": "rag_answer"}]}]}}])
    responses = [] if index == 2 else [
        AIMessage(content="", tool_calls=[{"id": "info", "name": "query_company_registration",
                  "args": {"task_id": "t1", "company_name": COMPANY}}]),
        AIMessage(content="", tool_calls=[{"id": "other", "name": second_tool, "args": second_args}]), final]
    model = ScriptedModel(proposal=decision, responses=responses)
    ctx = RunContext(Settings(), question, "offline")
    result = asyncio.run(run_request(ctx, model, [HumanMessage(content=question)], [
        tool("query_company_registration", QueryCompanyRegistrationInput, info),
        tool(second_tool, KnowledgeQAInput if index < 2 else QueryCompanyPenaltyInput, other)]))
    assert result["execution_status"] == "complete", result
    assert calls == ["query_company_registration", second_tool]
    assert {t["task_id"] for t in result["data"]["task_results"]} == {"t1", "t2"}


def test_sync_sql_timeout_keeps_connection_until_worker_finishes(monkeypatch):
    from concurrent.futures import Future
    from agent.nodes.price_inquiry import recall
    future, conn, released = Future(), object(), []
    monkeypatch.setattr(recall, "_get_connection", lambda _: conn)
    monkeypatch.setattr(recall, "_get_classification", lambda _: {"fields": ["company_name"]})
    monkeypatch.setattr(recall, "_release_connection", released.append)
    def timeout(*args):
        raise recall.SQLWorkerTimeout(future)
    monkeypatch.setattr(recall, "_execute_recall_chain_for_table", timeout)
    result = recall._query_tables(["company_info"], intent())
    assert result["table_status"]["company_info"] == "timeout" and released == []
    future.set_result(None)
    assert released == [conn]


def test_cli_explicit_hybrid_uses_requested_session_without_global_rollout(monkeypatch):
    import sys
    import agent.__main__ as cli
    from agent.execution.service import hybrid_selected
    seen = {}
    class Graph:
        def __init__(self, *, async_enabled, settings):
            self._settings = settings
            seen["async"] = async_enabled
        def close(self):
            seen["closed"] = True
    def run(agent, question, *, deadline_s, thread_id):
        seen["selected"] = hybrid_selected(agent._settings, thread_id)
        seen["other"] = hybrid_selected(agent._settings, "other")
        seen["thread"] = thread_id
    monkeypatch.setattr(cli, "AgentGraph", Graph)
    monkeypatch.setattr(cli, "run_single_stream", run)
    monkeypatch.setattr(sys, "argv", ["agent", "--execution-mode", "hybrid", "--thread-id",
                                    "chosen", "--stream", "--question", QUESTIONS[0]])
    cli.main()
    assert seen == {"async": True, "selected": True, "other": False, "thread": "chosen", "closed": True}
