# -*- coding: utf-8 -*-
"""阶段 3：price_inquiry 异步节点（node_price_inquiry_async）离线测试。

mock 意图解析、多表并行召回入口与回答渲染（渲染引擎另有测试覆盖），
不依赖真实 LLM / MySQL / Milvus，覆盖：
  - 节点主流程：sub_route=all 三表并行、business_result / messages 结构、async 标记
  - 二级路由表传递（all → company_info / company_penalty / bid_project）
  - P0-12 修正：已提取项目编号强制修正为 project_detail + bidding_query
  - P0-11 前置校验：project_detail 缺编号 → 统一引导
  - P0-11 后置回溯：company_query 召回无匹配 → 统一引导
  - 空消息早退
  - SQL 查询阶段整体异常 → 优雅降级返回空结果（不抛异常）
"""
from __future__ import annotations

import asyncio

from langchain_core.messages import AIMessage

import agent.nodes.price_inquiry.node_async as node_async_mod
from agent.nodes.price_inquiry import HardFilters, SearchIntent


def _make_intent(question: str, sub_route: str = "all", query_type: str = "mixed",
                 company_name: str | None = None, project_number: str | None = None) -> SearchIntent:
    return SearchIntent(
        hard_filters=HardFilters(company_name=company_name, project_number=project_number),
        semantic_keywords=["测试科技"] if company_name else ["测试"],
        original_question=question,
        sub_route=sub_route,
        query_type=query_type,
    )


def _patch_render(monkeypatch, render_calls: list):
    """Mock 回答渲染入口，记录 (query_type, records_len, sub_route)。"""
    def fake_render(query_type, records, *, entity="", sub_route=""):
        render_calls.append((query_type, len(records), sub_route))
        return "（mock 渲染结果）"
    monkeypatch.setattr(node_async_mod, "render_answer", fake_render)


def test_all_route_calls_three_tables_and_returns_struct(monkeypatch):
    """主流程：all 路由必须并行三表，business_result 结构与 async 标记正确。"""
    captured: dict[str, object] = {}
    render_calls: list[tuple] = []

    async def fake_intent(question: str, llm):
        return _make_intent(question, sub_route="all", company_name="测试科技有限公司")

    async def fake_query(tables, intent):
        captured["tables"] = list(tables)
        return {
            "records": [
                {"_id_": "c1", "company_name": "测试科技有限公司", "company_level": "大型企业"},
                {"_id_": "b1", "successful_bidder": "测试科技有限公司", "project_number": "AH2024-001"},
            ],
            "total_found": 2,
            "queried_tables": ["ztb_clean.company_info", "ztb_clean.bid_project"],
            "sql_count": 5,
            "total_sql_time": 0.8,
        }

    monkeypatch.setattr(node_async_mod, "_build_llm", lambda: None)
    monkeypatch.setattr(node_async_mod, "_parse_unified_intent_async", fake_intent)
    monkeypatch.setattr(node_async_mod, "query_tables_async", fake_query)
    _patch_render(monkeypatch, render_calls)

    result = asyncio.run(node_async_mod.node_price_inquiry_async(
        {"messages": [AIMessage(content="查询测试科技有限公司的中标和工商信息")]}
    ))
    assert captured["tables"] == ["company_info", "company_penalty", "bid_project"]
    br = result["business_result"]
    assert br["branch"] == "price_inquiry"
    assert br["sub_route"] == "all"
    assert len(br["data"]["records"]) == 2
    assert br["data"]["total_found"] == 2
    assert br["data"]["meta"]["async"] is True
    assert br["data"]["meta"]["sql_count"] == 5
    assert result["messages"][0].content == br["answer"]
    assert br["answer"] == "（mock 渲染结果）"
    assert render_calls and render_calls[0][0] == "mixed"
    assert render_calls[0][1] == 2


def test_project_number_forces_project_detail(monkeypatch):
    """P0-12：LLM 未归类 project_detail 但已提取有效编号 → 强制修正并走 bid_project。"""
    captured: dict[str, object] = {}
    render_calls: list[tuple] = []

    async def fake_intent(question: str, llm):
        return _make_intent(
            question, sub_route="all", query_type="bidding_profile",
            project_number="AH2024-001",
        )

    async def fake_query(tables, intent):
        captured["tables"] = list(tables)
        captured["intent"] = intent
        return {
            "records": [
                {"_id_": "b1", "project_number": "AH2024-001", "project_name": "某信息化项目"},
            ],
            "total_found": 1,
            "queried_tables": ["ztb_clean.bid_project"],
            "sql_count": 2,
            "total_sql_time": 0.3,
        }

    monkeypatch.setattr(node_async_mod, "_build_llm", lambda: None)
    monkeypatch.setattr(node_async_mod, "_parse_unified_intent_async", fake_intent)
    monkeypatch.setattr(node_async_mod, "query_tables_async", fake_query)
    _patch_render(monkeypatch, render_calls)

    result = asyncio.run(node_async_mod.node_price_inquiry_async(
        {"messages": [AIMessage(content="查询 AH2024-001 的中标情况")]}
    ))
    br = result["business_result"]
    assert br["sub_route"] == "bidding_query"
    assert br["query_type"] == "project_detail"
    assert captured["tables"] == ["bid_project"]
    assert captured["intent"].query_type == "project_detail"
    # 回答渲染应收到修正后的 query_type（证实 P0-12 在异步链路同样生效）
    assert render_calls and render_calls[0][0] == "project_detail"


def test_project_detail_without_number_triggers_guidance(monkeypatch):
    """P0-11 前置：project_detail 未提供 project_number → 统一引导。"""

    async def fake_intent(question: str, llm):
        return _make_intent(question, sub_route="bidding_query", query_type="project_detail")

    monkeypatch.setattr(node_async_mod, "_build_llm", lambda: None)
    monkeypatch.setattr(node_async_mod, "_parse_unified_intent_async", fake_intent)

    result = asyncio.run(node_async_mod.node_price_inquiry_async(
        {"messages": [AIMessage(content="查询项目编号的中标情况")]}
    ))
    br = result["business_result"]
    assert br["query_type"] == "unified_guidance"
    assert br["data"]["intent"]["guard_reason"] == "project_detail_no_number"


def test_company_query_post_guard_no_match_triggers_guidance(monkeypatch):
    """P0-11 后置：company_query 召回记录无一匹配目标公司 → 触发引导，不输出盲目结果。"""

    async def fake_intent(question: str, llm):
        return _make_intent(
            question, sub_route="company_query", query_type="company_profile",
            company_name="测试科技有限公司",
        )

    async def fake_query(tables, intent):
        return {
            "records": [{"_id_": "c9", "company_name": "无关公司有限公司"}],
            "total_found": 1,
            "queried_tables": ["ztb_clean.company_info"],
            "sql_count": 2,
            "total_sql_time": 0.1,
        }

    monkeypatch.setattr(node_async_mod, "_build_llm", lambda: None)
    monkeypatch.setattr(node_async_mod, "_parse_unified_intent_async", fake_intent)
    monkeypatch.setattr(node_async_mod, "query_tables_async", fake_query)

    result = asyncio.run(node_async_mod.node_price_inquiry_async(
        {"messages": [AIMessage(content="查询测试科技有限公司的工商情况")]}
    ))
    br = result["business_result"]
    assert br["query_type"] == "unified_guidance"
    assert "post_recall_company_no_match" in br["data"]["intent"]["guard_reason"]


def test_empty_messages_early_return():
    """空消息早退：无需任何 mock，直接返回友好提示。"""
    result = asyncio.run(node_async_mod.node_price_inquiry_async({"messages": []}))
    assert result["business_result"]["answer"] == "抱歉，没有收到您的问题，请重新输入。"


def test_query_stage_global_exception_degrades_gracefully(monkeypatch):
    """查询阶段整体异常（非单表异常）→ 返回空结果，不向调用方抛异常。"""

    async def fake_intent(question: str, llm):
        return _make_intent(question, sub_route="all", company_name="测试科技有限公司")

    async def boom(tables, intent):
        raise RuntimeError("unexpected global failure")

    monkeypatch.setattr(node_async_mod, "_build_llm", lambda: None)
    monkeypatch.setattr(node_async_mod, "_parse_unified_intent_async", fake_intent)
    monkeypatch.setattr(node_async_mod, "query_tables_async", boom)

    result = asyncio.run(node_async_mod.node_price_inquiry_async(
        {"messages": [AIMessage(content="查询测试科技有限公司的中标和工商信息")]}
    ))
    br = result["business_result"]
    assert br["data"]["records"] == []
    assert br["data"]["tables"] == ["company_info", "company_penalty", "bid_project"]
    assert br["answer"], "降级回答不应为空"