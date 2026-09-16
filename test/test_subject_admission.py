"""主体原文放行、SQL 防御及旧引导复用；不调用真实模型或数据库。"""
import asyncio
from unittest.mock import Mock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.execution.context import RunContext, RunStopped, use_run
from agent.execution.policy import validate_decision
from agent.execution.service import run_request
from agent.nodes.general_chat import GENERAL_GUIDANCE, node_general_chat
from agent.tools import get_enabled_tools, strict_sql
from public_kb.config import Settings
from test_react_execution import ScriptedModel
from test_router_known_repairs import RouterReplies, greeting


def decision(name="张三商店", capability="company_award_history", field="company_name", mode="static"):
    tool = {
        "company_award_history": "query_company_award_history",
        "project_award": "query_project_award",
        "company_registration": "query_company_registration",
        "company_business_scope": "query_company_business_scope",
        "company_penalty": "query_company_penalty",
    }[capability]
    return {"execution_mode": mode, "static_branch": "price_inquiry" if mode == "static" else None,
            "reason_code": "EXISTING_BRANCH_COVERS" if mode == "static" else "MULTI_TARGET",
            "suggested_tools": [tool], "tasks": [{"task_id": "t1", "capability": capability,
                "input_refs": [{"field": field, "source_id": "u0", "value": name}]}]}


def database(monkeypatch, rows=None, failure=None):
    """保留真实工具包装和 SQL 构建，只用替身接住数据库操作。"""
    cursor = Mock()
    cursor.__enter__ = Mock(return_value=cursor)
    cursor.__exit__ = Mock(return_value=False)
    queries = []

    def execute(sql, args=None):
        queries.append((sql, args))
        if sql.startswith("SHOW COLUMNS"):
            table = sql.split("`")[1]
            cursor.fetchall.return_value = [{"Field": k} for k in strict_sql.PUBLIC_FIELDS[table]]
        elif sql.startswith("SELECT"):
            if failure:
                raise failure
            cursor.fetchall.return_value = rows or []

    cursor.execute.side_effect = execute
    conn = Mock()
    conn.cursor.return_value = cursor
    pool = Mock()
    pool.connection.return_value = conn
    monkeypatch.setattr(strict_sql, "get_pool", lambda: pool)
    return queries, conn


@pytest.mark.parametrize("name,capability,field", [
    ("张三公司", "company_award_history", "company_name"),
    ("张三商店", "company_award_history", "company_name"),
    ("李四小铺", "company_registration", "company_name"),
    ("李四小铺", "company_business_scope", "company_name"),
    ("张三商店", "company_penalty", "company_name"),
    ("AH2024-001", "project_award", "project_number"),
    ("A", "company_award_history", "company_name"),
])
def test_real_tool_queries_subject_verbatim_and_reports_empty(monkeypatch, name, capability, field):
    queries, conn = database(monkeypatch)
    ctx = RunContext(Settings(), f"查{name}的记录", "admission")
    model = RouterReplies(decision(name, capability, field))
    result = asyncio.run(run_request(ctx, model, [HumanMessage(content=ctx.question)],
                                    get_enabled_tools(settings=ctx.settings)))
    selected = [(sql, args) for sql, args in queries if sql.startswith("SELECT")]
    assert len(selected) == 1 and selected[0][1][0] == name
    assert " = %s" in selected[0][0] and " LIMIT %s" in selected[0][0]
    assert name not in selected[0][0]
    assert ctx.tool_executions == 1 and result["execution_status"] == "complete"
    assert "暂未收录" in result["answer"] and "完整企业名称" not in result["answer"]
    conn.rollback.assert_called_once()
    conn.close.assert_called_once()


def test_react_path_also_allows_store_name(monkeypatch):
    queries, _ = database(monkeypatch)
    model = ScriptedModel(proposal=decision(mode="react"), responses=[
        AIMessage(content="", tool_calls=[{"id": "one", "name": "query_company_award_history",
                  "args": {"task_id": "t1", "company_name": "张三商店"}}]),
        AIMessage(content="", tool_calls=[{"id": "answer", "name": "AnswerCandidate",
                  "args": {"task_results": [{"task_id": "t1", "blocks": [
                      {"kind": "limitation", "reason": "no_match"}]}]}}]),
    ])
    ctx = RunContext(Settings(), "查张三商店的中标历史", "react-admission")
    result = asyncio.run(run_request(ctx, model, [HumanMessage(content=ctx.question)],
                                    get_enabled_tools(settings=ctx.settings)))
    assert result["branch"] == "react" and "暂未收录" in result["answer"]
    assert len([q for q in queries if q[0].startswith("SELECT")]) == 1


@pytest.mark.parametrize("name", ["", "   ", "商" * 81, 123])
def test_sql_rejects_invalid_parameter_shape_before_connection(monkeypatch, name):
    monkeypatch.setattr(strict_sql, "get_pool", lambda: pytest.fail("invalid input reached DB"))
    ctx = RunContext(Settings(), "query", "input")
    with use_run(ctx), pytest.raises(RunStopped, match="invalid_query_input"):
        strict_sql.query("query_company_award_history", {"company_name": name})


def test_sql_injection_like_name_is_only_a_bound_value(monkeypatch):
    queries, _ = database(monkeypatch)
    name = "张三商店' OR 1=1 --"
    ctx = RunContext(Settings(), "query", "sql-safety")
    with use_run(ctx):
        strict_sql.query("query_company_award_history", {"company_name": name})
    sql, args = next(q for q in queries if q[0].startswith("SELECT"))
    assert name not in sql and args[0] == name
    assert "`successful_bidder` = %s" in sql


@pytest.mark.parametrize("filters", [{}, {"company_name": "张三商店", "city": "合肥"},
                                     {"company_name": "张三商店", "sql": "DELETE FROM company_info"}])
def test_sql_query_scope_stays_restricted(monkeypatch, filters):
    monkeypatch.setattr(strict_sql, "get_pool", lambda: pytest.fail("invalid scope reached DB"))
    ctx = RunContext(Settings(), "query", "sql-scope")
    with use_run(ctx), pytest.raises(RunStopped, match="unsupported_filter"):
        strict_sql.query("query_company_registration", filters)


def test_database_failure_is_not_reported_as_uncollected(monkeypatch):
    _, conn = database(monkeypatch, failure=TimeoutError("private diagnostic"))
    ctx = RunContext(Settings(), "查张三商店的中标历史", "failure")
    result = asyncio.run(run_request(ctx, RouterReplies(decision()), [HumanMessage(content=ctx.question)],
                                    get_enabled_tools(settings=ctx.settings)))
    assert result["execution_status"] != "complete"
    assert "数据服务未成功返回" in result["answer"]
    assert "暂未收录" not in result["answer"] and "private diagnostic" not in result["answer"]
    conn.rollback.assert_called_once()
    conn.close.assert_called_once()


def test_missing_field_is_not_missing_company(monkeypatch):
    database(monkeypatch, rows=[{"company_name": "张三商店", "legal_person": None,
                                "registered_capital": 0}])
    ctx = RunContext(Settings(), "查张三商店的工商信息", "missing-field")
    result = asyncio.run(run_request(ctx, RouterReplies(decision(capability="company_registration")),
                                    [HumanMessage(content=ctx.question)], get_enabled_tools(settings=ctx.settings)))
    assert "法定代表人：未收录该字段" in result["answer"]
    assert "经营范围" not in result["answer"]
    assert "注册资本原始记录：0" in result["answer"]
    assert "系统暂未收录" not in result["answer"]


@pytest.mark.parametrize("question,route", [
    ("你好", greeting()), ("你能干什么", greeting()),
    ("天气怎么样", {"execution_mode": "unsupported", "reason_code": "OUT_OF_SCOPE"}),
    ("张三商店在哪里", {"execution_mode": "unsupported", "reason_code": "OUT_OF_SCOPE"}),
])
def test_guidance_reuses_legacy_text_without_tools(question, route):
    ctx = RunContext(Settings(), question, "guidance")
    result = asyncio.run(run_request(ctx, RouterReplies(route), [HumanMessage(content=question)], []))
    legacy = node_general_chat({"messages": [HumanMessage(content=question)]})
    assert result["answer"] == legacy["business_result"]["answer"] == GENERAL_GUIDANCE
    assert ctx.tool_executions == 0 and ctx.model_calls == 1


def test_unclear_purpose_asks_what_not_whether_name_is_real():
    ctx = RunContext(Settings(), "查一下张三商店", "clarify")
    route = {"execution_mode": "clarify", "reason_code": "MISSING_INPUT", "missing_fields": ["task_scope"]}
    result = asyncio.run(run_request(ctx, RouterReplies(route), [HumanMessage(content=ctx.question)], []))
    assert "要查询的事项" in result["answer"] and "完整企业名称" not in result["answer"]
    assert ctx.tool_executions == 0


def test_business_search_cannot_bypass_six_query_purposes():
    from pydantic import ValidationError

    ctx = RunContext(Settings(), "找几个公司", "purpose")
    route = decision()
    route["tasks"] = [{"task_id": "t1", "capability": "business_search", "input_refs": [
        {"field": "keywords", "source_id": "u0", "value": "公司"}]}]
    route["suggested_tools"] = ["search_business_data"]
    with pytest.raises(ValidationError):
        validate_decision(ctx, route, get_enabled_tools(settings=ctx.settings))
