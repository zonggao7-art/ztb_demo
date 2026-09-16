"""回归已确认的业务边界与路由纠错，不测试新的规划策略。"""
import asyncio
import json

import httpx
import pytest
from langchain_core.messages import HumanMessage
from langchain_core.exceptions import OutputParserException
from langchain_core.runnables import RunnableLambda
from pydantic import ValidationError

from agent.execution.context import RunContext, RunStopped, use_run
from agent.execution.contracts import ExecutionDecision
from agent.execution.policy import validate_decision
from agent.execution.router import decide
from agent.execution.service import run_request
from agent.tools import get_enabled_tools
from agent.tools.schemas import QueryCompanyRegistrationInput
from public_kb.config import Settings

COMPANY = "合肥市百益通商贸有限公司"
EXTRA_FILTERS = {
    "industry": "商贸", "region": "合肥", "province": "安徽省", "city": "合肥市",
    "business_status": "存续", "time_start": "2020-01-01", "time_end": "2024-12-31",
}


def company_decision(extra=None):
    refs = [{"field": "company_name", "source_id": "u0", "value": COMPANY}]
    if extra:
        refs.append({"field": extra, "source_id": "u0", "value": EXTRA_FILTERS[extra]})
    return {"execution_mode": "static", "static_branch": "price_inquiry",
            "reason_code": "EXISTING_BRANCH_COVERS", "suggested_tools": ["query_company_registration"],
            "tasks": [{"task_id": "t1", "capability": "company_registration", "input_refs": refs}]}


def greeting(empty=False):
    return {"execution_mode": "static", "static_branch": "general_chat",
            "reason_code": "EXISTING_BRANCH_COVERS", "suggested_tools": [],
            "tasks": [] if empty else [{"task_id": "t1", "capability": "general_chat"}]}


class RouterReplies:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts = []

    def with_structured_output(self, schema, **kwargs):
        async def invoke(messages):
            self.prompts.append(list(messages))
            value = self.replies.pop(0)
            if isinstance(value, Exception):
                raise value
            return schema.model_validate(value)
        return RunnableLambda(invoke)


def context(question="你好"):
    ctx = RunContext(Settings(), question, "known-repair")
    events = []
    ctx.sink = lambda stage, payload: events.append({"stage": stage, **payload})
    return ctx, events


@pytest.mark.parametrize("field", EXTRA_FILTERS)
def test_company_tool_schema_rejects_every_extra_business_filter(field):
    with pytest.raises(ValidationError):
        QueryCompanyRegistrationInput(company_name=COMPANY, **{field: EXTRA_FILTERS[field]})


@pytest.mark.parametrize("field", EXTRA_FILTERS)
def test_company_policy_rejects_extra_filter_even_when_value_is_in_user_input(field):
    ctx, _ = context(f"查{COMPANY} {EXTRA_FILTERS[field]}")
    with pytest.raises(RunStopped, match="unsupported_filter"):
        validate_decision(ctx, company_decision(field), get_enabled_tools(settings=ctx.settings))


@pytest.mark.parametrize("field", EXTRA_FILTERS)
def test_sql_layer_rejects_extra_filter_before_connecting(monkeypatch, field):
    from agent.tools import strict_sql
    monkeypatch.setattr(strict_sql, "get_pool", lambda: pytest.fail("非法参数不能触达数据库"))
    ctx, _ = context()
    with use_run(ctx), pytest.raises(RunStopped, match="unsupported_filter"):
        strict_sql.query("query_company_registration", {"company_name": COMPANY, field: EXTRA_FILTERS[field]})


def test_greeting_empty_tasks_gets_specific_feedback_then_succeeds():
    model = RouterReplies(greeting(empty=True), greeting())
    ctx, events = context()
    result = asyncio.run(run_request(ctx, model, [HumanMessage(content="你好")], []))
    assert result["branch"] == "general_chat" and result["execution_status"] == "complete"
    rejected = next(e for e in events if e["stage"] == "router_rejected")
    assert rejected["code"] == "tasks_required"
    assert rejected["issues"][0]["path"] == "tasks"
    feedback = model.prompts[1][-1].content
    assert "tasks_required" in feedback and "general_chat" in feedback
    assert len(model.prompts) == 2 and ctx.model_calls == 2 and ctx.tool_attempts == 0


def test_repair_does_not_silently_invent_greeting_task_or_loop_forever():
    model = RouterReplies(greeting(empty=True), greeting(empty=True))
    ctx, events = context()
    result = asyncio.run(run_request(ctx, model, [HumanMessage(content="你好")], []))
    assert result["execution_status"] != "complete"
    assert [e["code"] for e in events if e["stage"] == "router_rejected"] == ["tasks_required"] * 2
    assert len(model.prompts) == 2 and ctx.tool_executions == 0


def test_validation_details_report_field_and_type_without_values_or_unknown_field_names():
    invalid = greeting()
    invalid["tasks"][0]["task_id"] = "secret-input-value"
    invalid["secret-extra-field-name"] = "secret-extra-field-value"
    model = RouterReplies(invalid, greeting())
    ctx, events = context()
    asyncio.run(run_request(ctx, model, [HumanMessage(content="你好")], []))
    feedback = model.prompts[1][-1].content
    diagnostic_text = json.dumps(events, ensure_ascii=False) + feedback
    assert "tasks.0.task_id" in diagnostic_text and "string_pattern_mismatch" in diagnostic_text
    assert "secret-input-value" not in diagnostic_text
    assert "secret-extra-field-name" not in diagnostic_text
    assert "secret-extra-field-value" not in diagnostic_text


@pytest.mark.parametrize("error,expected", [
    (httpx.ConnectError("secret-service-address"), "router_service_error"),
    (RuntimeError("secret-internal-detail"), "router_internal_error"),
])
def test_non_schema_failures_are_not_mislabeled_or_sent_for_model_repair(error, expected):
    model = RouterReplies(error)
    ctx, events = context()
    result = asyncio.run(run_request(ctx, model, [HumanMessage(content="你好")], []))
    assert result["execution"]["failure_code"] == expected
    assert len(model.prompts) == 1
    assert "secret-" not in json.dumps(events)


def test_company_extra_filter_correction_does_not_widen_allowed_schema():
    model = RouterReplies(company_decision("city"), company_decision())
    ctx, events = context(f"查{COMPANY}的工商信息")
    decision = asyncio.run(decide(ctx, model, [HumanMessage(content=ctx.question)], get_enabled_tools(settings=ctx.settings)))
    assert [r.field for r in decision.tasks[0].input_refs] == ["company_name"]
    assert events[0]["code"] == "unsupported_filter"
    assert "company_name" in model.prompts[1][-1].content


@pytest.mark.parametrize("fault,expected", [
    ("repeated_company", "duplicate_binding"),
    ("repeated_task", "invalid_task_dependencies"),
    ("branch", "static_branch_mismatch"),
])
def test_malformed_multi_company_plan_has_specific_error(fault, expected):
    from copy import deepcopy
    value = company_decision()
    if fault == "repeated_company":
        value["tasks"][0]["input_refs"].append(deepcopy(value["tasks"][0]["input_refs"][0]))
    elif fault == "repeated_task":
        value["tasks"].append(deepcopy(value["tasks"][0]))
    else:
        value["static_branch"] = None
    model = RouterReplies(value, company_decision())
    ctx, events = context(f"查{COMPANY}")
    decision = asyncio.run(decide(ctx, model, [HumanMessage(content=ctx.question)], get_enabled_tools(settings=ctx.settings)))
    assert len(decision.tasks) == 1
    assert events[0]["code"] == expected
    assert expected in model.prompts[1][-1].content


def test_parse_error_has_one_repair_without_leaking_original_model_text():
    model = RouterReplies(OutputParserException("secret-malformed-response"), greeting())
    ctx, events = context()
    result = asyncio.run(run_request(ctx, model, [HumanMessage(content="你好")], []))
    assert result["execution_status"] == "complete"
    assert next(e for e in events if e["stage"] == "router_rejected")["code"] == "router_parse_error"
    assert "secret-malformed-response" not in json.dumps(events)
    assert "secret-malformed-response" not in model.prompts[1][-1].content


def test_cli_displays_safe_error_location():
    from agent.__main__ import _render_stream_event
    from agent.streaming import EventType, make_event
    event = make_event(EventType.STAGE, "offline", {"stage": "router_rejected", "code": "tasks_required",
                       "attempt": 1, "issues": [{"path": "tasks", "type": "tasks_required"}]})
    text = _render_stream_event(event)
    assert "错误位置=tasks (tasks_required)" in text and "attempt=1" in text


def test_sql_selects_city_as_output_without_using_it_as_filter(monkeypatch):
    from unittest.mock import MagicMock
    from agent.tools import strict_sql
    row = {"company_name": COMPANY, "city": "合肥市", "industry": "商贸"}
    pool, connection = MagicMock(), MagicMock()
    pool.connection.return_value = connection
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchall.side_effect = [[{"Field": field} for field in row], [row]]
    monkeypatch.setattr(strict_sql, "get_pool", lambda: pool)
    ctx, _ = context()
    with use_run(ctx):
        result = strict_sql.query("query_company_registration", {"company_name": COMPANY})
    sql, params = cursor.execute.call_args.args
    assert "`city`" in sql.split(" WHERE ")[0]
    assert sql.split(" WHERE ")[1] == "`company_name` = %s LIMIT %s"
    assert params[0] == COMPANY
    assert result["data"]["records"][0] == row
    connection.close.assert_called_once()
