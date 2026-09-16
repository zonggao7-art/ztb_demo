"""Isolate routing from execution: one tool can serve two distinct approved tasks."""
import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool

from agent.execution.context import RunContext, RunStopped
from agent.execution.contracts import ExecutionDecision
from agent.execution.output import conservative_candidate, render
from agent.execution.service import run_request
from agent.tools.schemas import QueryCompanyRegistrationInput
from public_kb.config import Settings
from test_react_execution import ScriptedModel

COMPANIES = ["合肥市百益通商贸有限公司", "合肥达斯辉机电设备有限公司"]
QUESTION = f"查询一下{COMPANIES[0]}和{COMPANIES[1]}的工商信息"


@pytest.mark.parametrize("mode", ["static", "react"])
@pytest.mark.parametrize("second_status", ["found", "empty", "failed"])
def test_same_tool_two_subjects_are_executed_and_published_separately(mode, second_status):
    decision = {"execution_mode": mode,
                "static_branch": "price_inquiry" if mode == "static" else None,
                "reason_code": "EXISTING_BRANCH_COVERS" if mode == "static" else "MULTI_TARGET",
                "suggested_tools": ["query_company_registration"], "tasks": [
                    {"task_id": f"t{i}", "capability": "company_registration", "input_refs": [
                        {"field": "company_name", "source_id": "u0", "value": name}]}
                    for i, name in enumerate(COMPANIES, 1)]}
    # The existing schema already accepts two tasks using the same capability.
    ExecutionDecision.model_validate(decision)
    ctx = RunContext(Settings(), QUESTION, "multi-subject")
    calls = []

    async def query(company_name, task_id=None, **kwargs):
        calls.append((task_id, company_name))
        status = second_status if task_id == "t2" else "found"
        result = {"ok": status != "failed", "data": {"records": (
            [{"company_name": company_name, "legal_person": f"测试法人{task_id}"}]
            if status == "found" else [])}, "metadata": {"exact_scope": True}}
        return json.dumps(result, ensure_ascii=False), result

    tool = StructuredTool.from_function(coroutine=query, name="query_company_registration", description="offline",
               args_schema=QueryCompanyRegistrationInput, response_format="content_and_artifact")

    def answer(_):
        return AIMessage(content="", tool_calls=[{"name": "AnswerCandidate", "id": "answer",
                          "args": conservative_candidate(ctx).model_dump()}])

    responses = [AIMessage(content="", tool_calls=[{"name": tool.name, "id": f"call-{i}",
                    "args": {"task_id": f"t{i}", "company_name": company}}])
                 for i, company in enumerate(COMPANIES, 1)] + [answer]
    model = ScriptedModel(proposal=decision, responses=responses if mode == "react" else [])
    result = asyncio.run(run_request(ctx, model, [HumanMessage(content=QUESTION)], [tool]))
    assert calls == [("t1", COMPANIES[0]), ("t2", COMPANIES[1])]
    assert ctx.tool_executions == 2 and ctx.tool_attempts == 2
    assert result["execution_status"] == ("partial" if second_status == "failed" else "complete")
    assert {t["task_id"] for t in result["data"]["task_results"]} == {"t1", "t2"}
    assert all(row["company_name"] == COMPANIES[int(row["task_id"][1:]) - 1]
               for row in result["data"]["records"])
    assert model.calls == (0 if mode == "static" else 3)
    candidate = conservative_candidate(ctx)
    # Even a syntactically valid answer cannot borrow company A's record for B.
    candidate.task_results[1].blocks = candidate.task_results[0].blocks
    with pytest.raises(RunStopped, match="unknown_evidence"):
        render(ctx, candidate)
