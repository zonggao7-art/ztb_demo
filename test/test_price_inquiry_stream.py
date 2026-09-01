from __future__ import annotations

import asyncio

import pytest

from agent.nodes.price_inquiry.recall_async import query_tables_async


def test_progress_callback_called(monkeypatch):
    class Table:
        async def __call__(self, table_name, intent):
            return {"table": table_name, "rows": [{"id": table_name}], "sql_count": 1, "total_sql_time": 0.1}

    monkeypatch.setattr(
        "agent.nodes.price_inquiry.recall_async._query_table_async",
        Table(),
    )
    monkeypatch.setattr(
        "agent.nodes.price_inquiry.recall_async.run_blocking",
        lambda func, *args, **kwargs: asyncio.sleep(0, result=([], [], 0, 0.0)),
    )
    calls = []
    asyncio.run(query_tables_async(["a"], object(), progress_callback=lambda t, r, p: calls.append((t, p))))
    assert calls == [("a", "partial"), ("a", "final")]
