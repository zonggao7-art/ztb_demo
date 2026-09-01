# -*- coding: utf-8 -*-
"""阶段 3：SQL 语句级超时（safe_execute）与连接归还 / 单表失败隔离离线测试。

mock 同步 SQL 执行器与池连接，不依赖真实 MySQL，覆盖：
  - safe_execute 语句超时 → 抛 _SQLTimeoutError 且连接归还池
  - safe_execute 正常执行 → 返回 (rows, elapsed)，连接保持可用
  - SQL 执行错误（非超时）→ 原异常上抛、连接不归还
  - _query_table_async 中 _SQLTimeoutError → 返回空结果不扩散
  - query_tables_async 单表异常 → 其余表结果正常合并（gather_limited 隔离）
"""
from __future__ import annotations

import asyncio
import time

import pytest

import agent.nodes.price_inquiry.recall_async as recall_async_mod
from agent.nodes.price_inquiry import HardFilters, SearchIntent
from agent.nodes.price_inquiry.recall_async import (
    _SQLTimeoutError,
    _query_table_async,
    query_tables_async,
    safe_execute,
)


class _Conn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_safe_execute_timeout_closes_conn_and_raises(monkeypatch):
    """语句超时 → 抛 _SQLTimeoutError，且连接必须归还池（conn.close 被调用）。"""

    class _Slow:
        def fetch(self, conn, sql, params):
            time.sleep(2)
            return [], 0.0

    class _S:
        sql_stmt_timeout_s = 0.2

    conn = _Conn()
    monkeypatch.setattr(recall_async_mod, "_get_settings", lambda: _S())
    monkeypatch.setattr(recall_async_mod, "_execute_sql_fetch_rows", _Slow().fetch)

    async def _t():
        with pytest.raises(_SQLTimeoutError):
            await safe_execute(conn, "SELECT 1", (), "company_info", "FULLTEXT_OR")
        assert conn.closed, "超时后连接必须归还池"

    asyncio.run(_t())


def test_safe_execute_success_returns_rows_and_elapsed(monkeypatch):
    """正常执行 → 返回 (rows, elapsed)，连接保持可用不归还。"""

    class _Fast:
        def fetch(self, conn, sql, params):
            return [{"_id_": "1"}], 0.01

    class _S:
        sql_stmt_timeout_s = 5.0

    conn = _Conn()
    monkeypatch.setattr(recall_async_mod, "_get_settings", lambda: _S())
    monkeypatch.setattr(recall_async_mod, "_execute_sql_fetch_rows", _Fast().fetch)

    rows, elapsed = asyncio.run(
        safe_execute(conn, "SELECT 1", (), "company_info", "FULLTEXT_OR")
    )
    assert rows == [{"_id_": "1"}]
    assert elapsed >= 0.0
    assert conn.closed is False


def test_sql_error_propagates_without_closing_conn(monkeypatch):
    """非超时 SQL 错误 → 原异常上抛，连接保持可用（由降级链继续复用）。"""

    def boom(conn, sql, params):
        raise RuntimeError("syntax error")

    class _S:
        sql_stmt_timeout_s = 5.0

    conn = _Conn()
    monkeypatch.setattr(recall_async_mod, "_get_settings", lambda: _S())
    monkeypatch.setattr(recall_async_mod, "_execute_sql_fetch_rows", boom)

    async def _t():
        with pytest.raises(RuntimeError, match="syntax error"):
            await safe_execute(conn, "BAD SQL", (), "company_info", "FULLTEXT_OR")
        assert conn.closed is False, "非超时错误连接保持可用"

    asyncio.run(_t())


def test_query_table_async_timeout_returns_empty(monkeypatch):
    """单表检索链 _SQLTimeoutError → 该表返回空结果，不向上抛异常。"""

    class _FakeAcquire:
        def __init__(self, conn):
            self._conn = conn

        async def __aenter__(self):
            return self._conn

        async def __aexit__(self, *exc):
            return False

    conn = _Conn()

    async def semantic_timeout(conn, table, classification, intent, semantic_ids):
        raise _SQLTimeoutError("timeout")

    intent = SearchIntent(
        hard_filters=HardFilters(company_name="测试科技有限公司"), sub_route="all"
    )
    monkeypatch.setattr(recall_async_mod, "acquire", lambda: _FakeAcquire(conn))
    monkeypatch.setattr(
        recall_async_mod, "_semantic_recall_candidates", lambda intent, tables: {}
    )
    monkeypatch.setattr(recall_async_mod, "_query_semantic_rows_async", semantic_timeout)

    result = asyncio.run(_query_table_async("company_info", intent))
    assert result["table"] == "company_info"
    assert result["rows"] == []
    assert result["sql_count"] == 0


def test_query_tables_async_single_table_exception_keeps_others(monkeypatch):
    """多表并行：单表异常被隔离，其余表结果正常合并（验收②的单表维度）。"""
    intent = SearchIntent(
        hard_filters=HardFilters(company_name="测试科技有限公司"), sub_route="all"
    )
    calls: list[str] = []

    async def fake_query_table(table: str, intent):
        calls.append(table)
        if table == "company_penalty":
            raise RuntimeError("penalty table crashed")
        return {
            "table": table,
            "rows": [{"_id_": f"{table}_1", "company_name": "测试科技有限公司"}],
            "sql_count": 1,
            "total_sql_time": 0.1,
        }

    monkeypatch.setattr(recall_async_mod, "_query_table_async", fake_query_table)

    result = asyncio.run(
        query_tables_async(["company_info", "company_penalty", "bid_project"], intent)
    )
    assert set(calls) == {"company_info", "company_penalty", "bid_project"}
    assert result["total_found"] == 2
    assert len(result["records"]) == 2
    assert len(result["queried_tables"]) == 2
    assert all("company_penalty" not in t for t in result["queried_tables"]), \
        "失败表不应出现在 queried_tables"


def test_merge_and_rank_dedup_by_table_and_id(monkeypatch):
    """合并层：同 (table, _id_) 记录去重（取最大 _score_），跨表记录共存。"""
    from agent.nodes.price_inquiry.recall_async import _merge_and_rank

    intent = SearchIntent(
        hard_filters=HardFilters(company_name="测试科技有限公司"), sub_route="all"
    )
    results = [
        {"table": "company_info", "rows": [
            {"_id_": "c1", "company_name": "测试科技有限公司", "_score_": 0.9},
            {"_id_": "c1", "company_name": "测试科技有限公司", "_score_": 0.6},
        ], "sql_count": 1, "total_sql_time": 0.1},
        {"table": "bid_project", "rows": [
            {"_id_": "b1", "successful_bidder": "测试科技有限公司", "_score_": 0.8},
        ], "sql_count": 2, "total_sql_time": 0.3},
    ]

    ranked, queried_tables, sql_count, total_sql_time = _merge_and_rank(results, intent)

    assert len(ranked) == 2, "同 (table,_id_) 应去重为 1 条"
    merged = [r for r in ranked if r.get("_id_") == "c1"]
    assert merged and merged[0]["_score_"] == 0.9, "去重后应保留最大 _score_"
    assert sql_count == 3
    assert total_sql_time == pytest.approx(0.4)
    assert len(queried_tables) == 2