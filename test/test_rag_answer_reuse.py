"""完整 RAG 工具接回 hybrid：不重写答案，不绕过引用检查。"""
import asyncio
import json
from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.execution.context import RunContext, RunStopped, use_run
from agent.execution.contracts import ExecutionDecision
from agent.execution.evidence import EvidenceLedger
from agent.execution.executor import execute_tool
from agent.execution.output import conservative_candidate, render
from agent.execution.rag_result import validate_rag_result
from agent.execution.service import run_request
from agent.tools.knowledge import register_knowledge_tools
from agent.tools.registry import ToolRegistry
from public_kb.config import Settings
from test_react_execution import ScriptedModel


QUESTION = "测试法规问题"
ANSWER = "按资料说明，需要满足指定条件。【来源1】"
REFUSAL = "抱歉，公共知识库中暂无相关内容，无法提供可靠回答。"


def rag_result(answer=ANSWER, key="a", count=1):
    return {"answer": answer, "sources": [], "citations": [
        {"context_index": i, "chunk_id": f"{key}{i}", "chunk_uid": f"uid-{key}{i}",
         "doc_name": f"文档{key}", "chapter": "第一章", "text": "完整原文不应复制给用户。" * 100}
        for i in range(1, count + 1)],
        "citation_validation": {"all_passed": True, "is_refusal": False}}


def proposal(mode="static", questions=(QUESTION,)):
    return {"execution_mode": mode, "static_branch": "knowledge_qa" if mode == "static" else None,
            "reason_code": "EXISTING_BRANCH_COVERS" if mode == "static" else "MULTI_TARGET",
            "tasks": [{"task_id": f"t{i}", "capability": "public_kb_qa", "input_refs": [
                {"field": "question", "source_id": "u0", "value": q}]} for i, q in enumerate(questions, 1)],
            "suggested_tools": ["knowledge_qa"]}


def ctx_for(mode="static", questions=(QUESTION,), **settings):
    ctx = RunContext(Settings(**settings), "；".join(questions), "test")
    ctx.ledger = EvidenceLedger()
    ctx.decision = ExecutionDecision.model_validate(proposal(mode, questions))
    return ctx


def tools_with_rag(monkeypatch, result=None, delay=0, error=None):
    calls = []

    class FakeRAG:
        async def aquery(self, question):
            calls.append(question)
            if delay:
                await asyncio.sleep(delay)
            if error:
                raise error
            return deepcopy(result if result is not None else rag_result())

        async def retrieve_async(self, *args, **kwargs):
            raise AssertionError("hybrid 不应再选择纯检索工具")

    monkeypatch.setattr("agent.tools.knowledge._get_rag", lambda: FakeRAG())
    registry = ToolRegistry()
    register_knowledge_tools(registry)
    return [registry.get("knowledge_qa"), registry.get("search_public_kb")], calls


@pytest.mark.parametrize("mode", ["static", "react"])
def test_complete_tool_reused_without_outer_rewrite(monkeypatch, mode):
    tools, calls = tools_with_rag(monkeypatch)
    responses = [] if mode == "static" else [
        AIMessage(content="外层草稿不能发布", tool_calls=[{"name": "knowledge_qa", "id": "kb",
            "args": {"task_id": "t1", "question": QUESTION}}]),
        AIMessage(content="", tool_calls=[{"name": "AnswerCandidate", "id": "done", "args": {
            "task_results": [{"task_id": "t1", "blocks": [{"kind": "rag_answer"}]}]}}])]
    model = ScriptedModel(proposal=proposal(mode), responses=responses)
    ctx = ctx_for(mode)
    result = asyncio.run(run_request(ctx, model, [HumanMessage(content=QUESTION)], tools))
    assert result["execution_status"] == "complete", result
    assert calls == [QUESTION]
    assert model.calls == (0 if mode == "static" else 2)
    assert ANSWER in result["answer"]
    assert "原文" not in result["answer"] and "外层草稿" not in result["answer"]
    assert result["data"]["citations"][0]["text"] == rag_result()["citations"][0]["text"]
    assert result["data"]["citation_display"] == "compact"
    assert result["data"]["citation_validation"]["all_passed"]


def test_unused_context_stays_in_sources_without_forcing_it_into_answer():
    ctx = ctx_for()
    ctx.ledger.ingest("t1", "knowledge_qa", {"ok": True, "data": rag_result(count=2)}, 128)
    result = render(ctx, conservative_candidate(ctx))
    assert "【来源1】" in result["answer"] and "【来源2】" not in result["answer"]
    assert len(result["data"]["citations"]) == 2
    assert result["data"]["citation_validation"]["uncited_chunks"] == [2]


def test_multiple_tasks_remap_local_citations_once_and_keep_snapshots():
    ctx = ctx_for(questions=("问题甲", "问题乙"))
    data_a = rag_result("甲的回答。【来源1】", key="a")
    data_b = rag_result("乙的回答。【来源1】【来源 2】", key="b", count=2)
    ctx.ledger.ingest("t1", "knowledge_qa", {"ok": True, "data": data_a}, 4000)
    ctx.ledger.ingest("t2", "knowledge_qa", {"ok": True, "data": data_b}, 4000)
    data_b["answer"] = "外部篡改"
    result = render(ctx, conservative_candidate(ctx))
    assert "甲的回答。【来源1】" in result["answer"]
    assert "乙的回答。【来源2】【来源3】" in result["answer"]
    assert [c["chunk_id"] for c in result["data"]["citations"]] == ["a1", "b1", "b2"]
    assert ctx.ledger.rag_answers["t2"]["answer"].endswith("【来源1】【来源 2】")


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(answer="无引用回答"),
    lambda d: d.update(answer="伪造引用【来源9】"),
    lambda d: d.update(answer=""),
    lambda d: d.update(citations=[]),
    lambda d: d["citations"][0].update(chunk_id=None),
    lambda d: d["citations"][0].update(chunk_uid=""),
    lambda d: d["citations"][0].update(context_index=2),
    lambda d: d["citations"][0].update(text=""),
    lambda d: d.update(is_refusal=True),
])
def test_invalid_answers_are_blocked_despite_tool_success_report(monkeypatch, mutation):
    data = rag_result()
    mutation(data)
    # 模拟完整工具返回；报告声称 all_passed 不能替代重新检查。
    with pytest.raises(RunStopped):
        validate_rag_result(data)
    tools, _ = tools_with_rag(monkeypatch, data)
    if data.get("is_refusal"):
        data["citation_validation"]["is_refusal"] = True
    ctx = ctx_for()
    result = asyncio.run(run_request(ctx, ScriptedModel(proposal=proposal()),
        [HumanMessage(content=QUESTION)], tools))
    assert result["execution_status"] != "complete"
    assert ctx.ledger.rag_answers == {} and ctx.cache == {}


def test_no_results_is_a_refusal_not_a_service_failure(monkeypatch):
    data = {"answer": REFUSAL, "citations": [],
            "citation_validation": {"is_refusal": True, "all_passed": True}}
    tools, _ = tools_with_rag(monkeypatch, data)
    ctx = ctx_for()
    result = asyncio.run(run_request(ctx, ScriptedModel(proposal=proposal()),
        [HumanMessage(content=QUESTION)], tools))
    assert REFUSAL in result["answer"]
    assert "数据服务未成功返回" not in result["answer"]
    assert result["execution_status"] == "partial" and ctx.receipts[0]["ok"]


def test_successful_rag_call_is_cached(monkeypatch):
    tools, calls = tools_with_rag(monkeypatch)
    ctx = ctx_for()

    async def run():
        with use_run(ctx):
            for i in range(2):
                message = await execute_tool(ctx, {"name": "knowledge_qa", "id": str(i),
                    "args": {"task_id": "t1", "question": QUESTION}}, {t.name: t for t in tools})
                assert json.loads(message.content)["data"]["rag_answer"]["available"]
    asyncio.run(run())
    assert calls == [QUESTION] and ctx.tool_executions == 1 and ctx.tool_attempts == 2


def test_outer_model_cannot_replace_or_borrow_task_answer():
    ctx = ctx_for(questions=("问题甲", "问题乙"))
    ctx.ledger.ingest("t1", "knowledge_qa", {"ok": True, "data": rag_result()}, 4000)
    candidate = conservative_candidate(ctx).model_dump()
    candidate["task_results"][1]["blocks"] = [{"kind": "rag_answer"}]
    with pytest.raises(RunStopped, match="unknown_rag_answer"):
        render(ctx, candidate)
    candidate = conservative_candidate(ctx).model_dump()
    candidate["task_results"][0]["blocks"] = [{"kind": "limitation", "reason": "unsupported"}]
    with pytest.raises(RunStopped, match="rag_answer_required"):
        render(ctx, candidate)


def test_long_answer_is_not_cut_and_cli_hides_only_source_text(capsys):
    from agent.__main__ import _render_business_data
    long_answer = "必要条件。" * 60 + "【来源1】"
    ctx = ctx_for()
    ctx.ledger.ingest("t1", "knowledge_qa", {"ok": True, "data": rag_result(long_answer)}, 128)
    result = render(ctx, conservative_candidate(ctx))
    assert long_answer in result["answer"]
    _render_business_data(result["data"])
    printed = capsys.readouterr().out
    assert "文档a" in printed and "原文:" not in printed


def test_timeout_does_not_publish_incomplete_answer_and_next_request_recovers(monkeypatch):
    async def run():
        tools, _ = tools_with_rag(monkeypatch, delay=.1)
        ctx = ctx_for(agent_tool_timeout_s=.01)
        failed = await run_request(ctx, ScriptedModel(proposal=proposal()), [HumanMessage(content=QUESTION)], tools)
        assert failed["execution_status"] != "complete" and not ctx.ledger.rag_answers
        tools, _ = tools_with_rag(monkeypatch)
        recovered = await run_request(ctx_for(), ScriptedModel(proposal=proposal()), [HumanMessage(content=QUESTION)], tools)
        assert recovered["execution_status"] == "complete"
    asyncio.run(run())


def test_cancellation_and_concurrent_requests_keep_answers_isolated(monkeypatch):
    async def run():
        tools, _ = tools_with_rag(monkeypatch, delay=.2)
        ctx = ctx_for()
        task = asyncio.create_task(run_request(ctx, ScriptedModel(proposal=proposal()),
            [HumanMessage(content=QUESTION)], tools))
        await asyncio.sleep(.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert ctx.cancelled and not ctx.ledger.rag_answers
        tools, _ = tools_with_rag(monkeypatch)
        contexts = [ctx_for(), ctx_for()]
        results = await asyncio.gather(*(run_request(c, ScriptedModel(proposal=proposal()),
            [HumanMessage(content=QUESTION)], tools) for c in contexts))
        assert all(r["execution_status"] == "complete" for r in results)
        contexts[0].ledger.rag_answers["t1"]["answer"] = "本请求变化"
        assert contexts[1].ledger.rag_answers["t1"]["answer"] == ANSWER
    asyncio.run(run())
