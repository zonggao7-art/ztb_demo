"""聊天记录保留，但默认 hybrid 只执行本轮明确提出的任务。"""
import asyncio
import json
from copy import deepcopy

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.execution.context import RunContext, RunStopped
from agent.execution.contracts import ExecutionDecision
from agent.execution.policy import validate_decision, task_arguments
from agent.execution.service import run_request
from agent.graph import AgentGraph
from public_kb.config import Settings
from test_react_execution import fake_tool, proposal
from test_router_known_repairs import RouterReplies, greeting


COMPANY_QUERY = "查询测试有限公司的处罚"
CLARIFY = {"execution_mode": "clarify", "reason_code": "MISSING_INPUT", "missing_fields": ["company_name"]}
UNSUPPORTED = {"execution_mode": "unsupported", "reason_code": "OUT_OF_SCOPE"}


def history():
    return [HumanMessage(content=COMPANY_QUERY), AIMessage(content="上一轮处罚记录"),
        HumanMessage(content="干得好"), AIMessage(content="功能引导"),
        HumanMessage(content="谢谢"), AIMessage(content="功能引导")]


@pytest.mark.parametrize("question,decision", [
    ("干得好", greeting()), ("谢谢", greeting()), ("天气怎么样", UNSUPPORTED),
    ("继续", CLARIFY), ("这家公司还有处罚吗", CLARIFY),
    ("d:/DEMO/zhaotoubiao_demo/agent/__main__.py", UNSUPPORTED),
])
def test_only_current_question_is_sent_and_history_is_not_modified(question, decision):
    ctx = RunContext(Settings(), question, "test")
    ctx.sources["u3"] = COMPANY_QUERY  # 即使调用方意外残留，也不能送给模型。
    messages = [*history(), HumanMessage(content=question)]
    before = deepcopy(messages)
    model = RouterReplies(decision)
    calls = []
    result = asyncio.run(run_request(ctx, model, messages, [fake_tool(calls)]))
    payload = json.loads(model.prompts[0][1].content)
    assert payload["sources"] == ctx.sources == {"u0": question}
    assert COMPANY_QUERY not in str(model.prompts)
    assert messages == before and calls == []
    assert result["execution_status"] in {"complete", "clarify", "unsupported"}


@pytest.mark.parametrize("source", ["u1", "u2", "u3"])
def test_history_references_cannot_pass_policy_even_if_source_was_injected(source):
    ctx = RunContext(Settings(), "天气怎么样", "test")
    ctx.sources[source] = COMPANY_QUERY
    decision = proposal("static")
    decision["tasks"][0]["input_refs"][0]["source_id"] = source
    with pytest.raises(RunStopped, match="history_not_supported"):
        validate_decision(ctx, decision, [fake_tool([])])
    task = ExecutionDecision.model_validate(decision).tasks[0]
    with pytest.raises(RunStopped, match="history_not_supported"):
        task_arguments(ctx, task)


def test_old_company_cannot_be_relabelled_as_current_input():
    ctx = RunContext(Settings(), "天气怎么样", "test")
    ctx.sources["u0"] = COMPANY_QUERY
    with pytest.raises(RunStopped, match="unbound_input"):
        validate_decision(ctx, proposal("static"), [fake_tool([])])


def test_wrong_history_plan_can_repair_to_clarification_without_execution():
    decision = proposal("static")
    decision["tasks"][0]["input_refs"][0]["source_id"] = "u3"
    model = RouterReplies(decision, CLARIFY)
    ctx = RunContext(Settings(), "处罚呢", "test")
    calls = []
    result = asyncio.run(run_request(ctx, model, [*history(), HumanMessage(content=ctx.question)], [fake_tool(calls)]))
    assert result["execution_status"] == "clarify" and calls == []
    assert "每轮独立处理" in result["answer"]
    assert "history_not_supported" in model.prompts[1][-1].content
    assert COMPANY_QUERY not in str(model.prompts)


def test_conversation_keeps_history_but_does_not_reexecute_old_task():
    calls = []
    questions = [COMPANY_QUERY, "干得好", "谢谢", "天气怎么样", "处罚呢", COMPANY_QUERY]
    model = RouterReplies(proposal("static"), greeting(), greeting(), UNSUPPORTED, CLARIFY, proposal("static"))
    graph = AgentGraph(llm=model, settings=Settings(agent_execution_mode="hybrid", agent_react_rollout_percent=100),
                       async_enabled=True, tools=[fake_tool(calls)])

    async def run():
        results = [await graph.ainvoke(q, "single-turn") for q in questions]
        state = await graph.aget_state("single-turn")
        return results, state

    results, state = asyncio.run(run())
    assert calls == ["测试有限公司", "测试有限公司"]
    assert len(state["messages"]) == 12
    assert [m.content for m in state["messages"] if isinstance(m, HumanMessage)] == questions
    assert [json.loads(p[1].content)["sources"] for p in model.prompts] == [{"u0": q} for q in questions]
    for result in results[1:5]:
        assert "罚款" not in result["answer"]
