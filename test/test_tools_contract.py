# -*- coding: utf-8 -*-
"""工具契约测试 — ToolResult 形状 / 错误兜底 / 参数校验 / 双通道截断（mock 底层，纯离线）。

契约规则（蓝图 §6.3 完成标准）：
  - 每个工具返回统一 ToolResult（ok/data/error/metadata）
  - 工具永不向调用方抛异常
  - P0-11 参数校验下沉：非法公司名/项目编号 → invalid_params
  - LLM 可见 content 与结构化 artifact 分离，content 行数+字符双重截断
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

import agent.tools.knowledge as knowledge_mod
import agent.tools.price_db as price_db_mod
from agent.tools.base import render_tool_content
from agent.tools.knowledge import (
    _knowledge_qa_impl,
    _search_public_kb_impl,
)
from agent.tools.price_db import (
    _query_bid_records_impl,
    _query_company_info_impl,
    _query_company_penalty_impl,
    _search_business_data_impl,
)


# ═════════════════════════════════════════════════════════
# 辅助
# ═════════════════════════════════════════════════════════

def _record(i: int = 1) -> dict[str, Any]:
    return {
        "company_name": "测试有限公司",
        "credit_code": f"91340000MA{i:08d}",
        "_source_table": "company_info",
    }


class _FakeRAG:
    """PublicKnowledgeRAG 替身 — 只实现工具用到的面。"""

    def __init__(self, *, chunks=None, query_result=None, retrieve_error=None):
        self.chunks = chunks if chunks is not None else []
        self.query_result = query_result or {"answer": "答案", "sources": [], "citations": []}
        self.retrieve_error = retrieve_error
        self.retrieve_calls: list[tuple] = []
        self.query_calls: list[str] = []

    def retrieve(self, question, top_k=None):
        self.retrieve_calls.append((question, top_k))
        if self.retrieve_error:
            raise self.retrieve_error
        return self.chunks[: top_k] if top_k else self.chunks

    async def retrieve_async(self, question, top_k=None):
        return self.retrieve(question, top_k)

    def query(self, question):
        self.query_calls.append(question)
        if self.retrieve_error:
            raise self.retrieve_error
        return self.query_result

    async def aquery(self, question):
        return self.query(question)


@pytest.fixture
def fake_rag(monkeypatch):
    def _install(rag: _FakeRAG):
        monkeypatch.setattr(knowledge_mod, "_get_rag", lambda: rag)
        return rag

    return _install


@pytest.fixture(autouse=True)
def _offline_enum_norm(monkeypatch):
    """枚举归一化需查库取 DISTINCT 值；离线单测中替换为透传，保持测试纯离线且快速。"""
    monkeypatch.setattr(price_db_mod, "_normalize_intent_enums", lambda intent: intent)


# ═════════════════════════════════════════════════════════
# ToolResult 契约
# ═════════════════════════════════════════════════════════

def test_penalty_happy_path_contract(monkeypatch):
    captured: dict[str, Any] = {}

    def _fake_penalty(company_name: str) -> list[dict]:
        captured["company_name"] = company_name
        return [_record(1), _record(2)]

    monkeypatch.setattr(price_db_mod, "_query_penalty_by_company_name", _fake_penalty)
    result = _query_company_penalty_impl("测试有限公司")

    assert captured["company_name"] == "测试有限公司"
    assert result["ok"] is True
    assert result["error"] is None
    assert len(result["data"]["records"]) == 2
    assert result["metadata"]["row_count"] == 2
    assert "company_penalty" in result["metadata"]["source"]


def test_penalty_empty_result_has_note(monkeypatch):
    monkeypatch.setattr(price_db_mod, "_query_penalty_by_company_name", lambda name: [])
    result = _query_company_penalty_impl("测试有限公司")
    assert result["ok"] is True
    assert result["data"]["records"] == []
    assert "note" in result["data"]


def test_penalty_invalid_company_name_is_invalid_params(monkeypatch):
    monkeypatch.setattr(price_db_mod, "_query_penalty_by_company_name", lambda name: [_record()])
    result = _query_company_penalty_impl("随便说说")
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_params"
    assert "全称" in result["error"]["message"]


def test_penalty_empty_company_name_is_invalid_params():
    result = _query_company_penalty_impl("  ")
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_params"


# ═════════════════════════════════════════════════════════
# SearchIntent 构造正确性
# ═════════════════════════════════════════════════════════

def test_company_info_builds_intent_and_slices(monkeypatch):
    captured: dict[str, Any] = {}

    def _fake_company_query(intent):
        captured["intent"] = intent
        return {"records": [_record(i) for i in range(30)], "queried_tables": ["ztb_clean.company_info"], "sql_count": 3, "total_sql_time": 0.2}

    monkeypatch.setattr(price_db_mod, "_query_company_data", _fake_company_query)
    result = _query_company_info_impl(
        "测试有限公司",
        industry="建筑",
        time_start="2020-01-01",
        time_end="2024-12-31",
        top_k=5,
    )

    intent = captured["intent"]
    assert intent.sub_route == "company_query"
    assert intent.hard_filters.company_name == "测试有限公司"
    assert intent.hard_filters.industry == "建筑"
    assert intent.hard_filters.time_range == {"start": "2020-01-01", "end": "2024-12-31"}
    assert intent.exact_tokens == ["测试有限公司"]

    assert result["ok"] is True
    assert len(result["data"]["records"]) == 5  # top_k 截断
    assert result["metadata"]["row_count"] == 30


def test_bid_records_project_number_mode(monkeypatch):
    captured: dict[str, Any] = {}

    def _fake_bidding(intent):
        captured["intent"] = intent
        return {"records": [{"project_number": "AH2024-001", "_source_table": "bid_project"}], "queried_tables": ["ztb_clean.bid_project"], "sql_count": 1, "total_sql_time": 0.1}

    monkeypatch.setattr(price_db_mod, "_query_bidding_data", _fake_bidding)
    result = _query_bid_records_impl(project_number="AH2024-001")

    intent = captured["intent"]
    assert intent.sub_route == "bidding_query"
    assert intent.query_type == "project_detail"
    assert intent.hard_filters.project_number == "AH2024-001"
    assert result["ok"] is True


def test_bid_records_company_maps_to_successful_bidder(monkeypatch):
    captured: dict[str, Any] = {}

    def _fake_bidding(intent):
        captured["intent"] = intent
        return {"records": [_record()], "queried_tables": ["ztb_clean.bid_project"], "sql_count": 1, "total_sql_time": 0.1}

    monkeypatch.setattr(price_db_mod, "_query_bidding_data", _fake_bidding)
    result = _query_bid_records_impl(company_name="测试有限公司")

    intent = captured["intent"]
    assert intent.hard_filters.successful_bidder == "测试有限公司"
    assert intent.query_type != "project_detail"
    assert result["ok"] is True


def test_bid_records_requires_subject():
    result = _query_bid_records_impl()
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_params"


def test_bid_records_invalid_project_number():
    result = _query_bid_records_impl(project_number="不是编号")
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_params"


def test_business_data_table_whitelist():
    result = _search_business_data_impl(keywords=["测试"], tables=["company_info", "evil_table"])
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_params"


def test_business_data_requires_keywords(monkeypatch):
    monkeypatch.setattr(price_db_mod, "_query_tables", lambda tables, intent: {"records": [], "queried_tables": [], "sql_count": 0, "total_sql_time": 0.0})
    result = _search_business_data_impl(keywords=["   "])
    assert result["ok"] is False
    assert result["error"]["code"] == "invalid_params"


def test_business_data_default_tables_and_keyword_truncation(monkeypatch):
    captured: dict[str, Any] = {}

    def _fake_query_tables(tables, intent):
        captured["tables"] = tables
        captured["intent"] = intent
        return {"records": [_record()], "queried_tables": [], "sql_count": 1, "total_sql_time": 0.1}

    monkeypatch.setattr(price_db_mod, "_query_tables", _fake_query_tables)
    result = _search_business_data_impl(keywords=[f"kw{i}" for i in range(9)])

    assert captured["tables"] == ["company_info", "company_penalty", "bid_project"]
    assert len(captured["intent"].semantic_keywords) == 5  # >5 截断
    assert result["ok"] is True


# ═════════════════════════════════════════════════════════
# RAG 工具
# ═════════════════════════════════════════════════════════

def _chunk(i: int) -> dict:
    return {
        "rank": i, "doc_name": "招标投标法", "chapter": "第二章",
        "chunk_index": i, "chunk_uid": f"uid-{i}", "text": f"条款{i}", "score": 0.9,
        "metadata": {},
    }


def test_search_public_kb_happy_path(fake_rag):
    rag = fake_rag(_FakeRAG(chunks=[_chunk(1), _chunk(2)]))
    result = _search_public_kb_impl("招标方式有哪些", top_k=2)

    assert rag.retrieve_calls[0][0] == "招标方式有哪些"
    assert result["ok"] is True
    assert len(result["data"]["chunks"]) == 2
    assert result["metadata"]["chunk_count"] == 2


def test_search_public_kb_empty_has_note(fake_rag):
    fake_rag(_FakeRAG(chunks=[]))
    result = _search_public_kb_impl("招标方式有哪些")
    assert result["ok"] is True
    assert "note" in result["data"]


def test_search_public_kb_kb_not_initialized(fake_rag):
    fake_rag(_FakeRAG(retrieve_error=RuntimeError("知识库尚未初始化，请先调用 init_knowledge_base() 入库。")))
    from agent.tools.knowledge import register_knowledge_tools
    from agent.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_knowledge_tools(reg)
    tool = reg.get("search_public_kb")
    content, artifact = tool.func(question="招标方式有哪些")
    assert artifact["ok"] is False
    assert artifact["error"]["code"] == "kb_not_initialized"
    assert json.loads(content)["error"]["code"] == "kb_not_initialized"


def test_knowledge_qa_returns_answer_and_citations(fake_rag):
    rag = fake_rag(_FakeRAG(query_result={"answer": "公开招标和邀请招标", "sources": [{"doc": "招标投标法"}], "citations": [{"chunk_uid": "uid-1"}]}))
    result = _knowledge_qa_impl("招标方式有哪些")

    assert result["ok"] is True
    assert result["data"]["answer"] == "公开招标和邀请招标"
    assert result["data"]["citations"][0]["chunk_uid"] == "uid-1"


# ═════════════════════════════════════════════════════════
# 异常兜底 / 双通道 / 截断
# ═════════════════════════════════════════════════════════

def test_wrapped_tool_never_raises_on_internal_error(monkeypatch):
    def _boom(*args, **kwargs):
        raise ValueError("意外错误")

    monkeypatch.setattr(price_db_mod, "_query_penalty_by_company_name", _boom)
    from agent.tools.price_db import register_price_db_tools
    from agent.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_price_db_tools(reg)
    tool = reg.get("query_company_penalty")
    content, artifact = tool.func(company_name="测试有限公司")

    assert artifact["ok"] is False
    assert artifact["error"]["code"] == "internal_error"
    assert artifact["metadata"]["tool"] == "query_company_penalty"
    assert "elapsed_s" in artifact["metadata"]
    # content 是合法 JSON 且含错误信息
    parsed = json.loads(content)
    assert parsed["ok"] is False
    assert parsed["error"]["code"] == "internal_error"


def test_async_variant_matches_sync(monkeypatch):
    monkeypatch.setattr(price_db_mod, "_query_penalty_by_company_name", lambda name: [_record()])
    from agent.tools.price_db import register_price_db_tools
    from agent.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_price_db_tools(reg)
    tool = reg.get("query_company_penalty")

    content, artifact = asyncio.run(tool.coroutine(company_name="测试有限公司"))
    assert artifact["ok"] is True
    assert len(artifact["data"]["records"]) == 1
    assert json.loads(content)["ok"] is True


def test_render_tool_content_row_truncation():
    result = {
        "ok": True,
        "data": {"records": [_record(i) for i in range(30)]},
        "error": None,
        "metadata": {"row_count": 30},
    }
    content = render_tool_content(result)
    parsed = json.loads(content)
    assert len(parsed["data"]["records"]) == 10
    assert parsed["data"]["_truncated"] == 20


def test_render_tool_content_char_truncation():
    result = {
        "ok": True,
        "data": {"records": [{"text": "x" * 100000}]},
        "error": None,
        "metadata": {},
    }
    content = render_tool_content(result, max_chars=500)
    assert len(content) <= 500 + 80  # 截断标记余量
    assert "_truncated_by_chars" in content
