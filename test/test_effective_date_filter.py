"""M3 法条时效性的单元测试（mock MilvusClient，不连真实库）。"""

from __future__ import annotations

import unittest
from datetime import date
from typing import Any

from langchain_core.documents import Document

from public_kb.config import Settings
from public_kb.contracts import build_effective_expr
from public_kb.retrieval.milvus_search import (
    hybrid_search_with_full_fields,
    search_with_full_fields,
)
from public_kb.retrieval.retriever import HybridRetriever
from public_kb.services.milvus_store import MilvusStoreManager


# ── build_effective_expr 纯函数 ────────────────────────────

class EffectiveExprTests(unittest.TestCase):
    def test_expr_filters_by_date(self) -> None:
        expr = build_effective_expr(date(2024, 1, 1))
        self.assertEqual(
            expr,
            'effective_date is null or effective_date <= "2024-01-01"',
        )

    def test_expr_today_boundary(self) -> None:
        # 施行日期 = 今天 → 命中（<= 包含当天）
        expr = build_effective_expr(date(2026, 8, 30))
        self.assertIn('"2026-08-30"', expr)

    def test_expr_keeps_null_dates(self) -> None:
        # 旧数据无 effective_date → 不被过滤
        expr = build_effective_expr(date(2024, 6, 15))
        self.assertIn("effective_date is null", expr)


# ── 检索过滤表达式透传 ────────────────────────────────────

class FakeCollection:
    """记录 search / hybrid_search 调用参数的可控 fake。"""

    def __init__(self) -> None:
        self.search_calls: list[dict] = []
        self.hybrid_calls: list[dict] = []

    def search(self, name: str, **kwargs: Any):
        self.search_calls.append(kwargs)
        return [[]]

    def hybrid_search(self, name: str, **kwargs: Any):
        self.hybrid_calls.append(kwargs)
        return [[]]


class SearchExprPassthroughTests(unittest.TestCase):
    def test_search_without_expr_has_no_filter(self) -> None:
        client = FakeCollection()
        search_with_full_fields(
            client,
            _settings(),
            data=[[0.1, 0.2, 0.3]],
            anns_field="vector",
            search_params={},
            limit=5,
        )
        self.assertNotIn("filter", client.search_calls[0])

    def test_search_with_expr_passes_filter(self) -> None:
        client = FakeCollection()
        expr = build_effective_expr(date(2024, 1, 1))
        search_with_full_fields(
            client,
            _settings(),
            data=[[0.1, 0.2, 0.3]],
            anns_field="vector",
            search_params={},
            limit=5,
            expr=expr,
        )
        self.assertEqual(client.search_calls[0].get("filter"), expr)

    def test_hybrid_without_expr_has_no_filter(self) -> None:
        client = FakeCollection()
        hybrid_search_with_full_fields(
            client,
            _settings(),
            reqs=[],
            ranker=None,
            limit=5,
        )
        self.assertNotIn("filter", client.hybrid_calls[0])

    def test_hybrid_with_expr_passes_filter(self) -> None:
        client = FakeCollection()
        expr = build_effective_expr(date(2024, 1, 1))
        hybrid_search_with_full_fields(
            client,
            _settings(),
            reqs=[],
            ranker=None,
            limit=5,
            expr=expr,
        )
        self.assertEqual(client.hybrid_calls[0].get("filter"), expr)


# ── 入库侧 effective_date/status 元数据 ────────────────────

class EffectiveMetadataTests(unittest.TestCase):
    def test_build_records_writes_effective_fields(self) -> None:
        manager = MilvusStoreManager(
            _settings(),
            _FakeEmbeddings(),
            client=_FakeStoreClient(),
        )
        doc = Document(
            page_content="第一条 内容",
            metadata={
                "doc_name": "测试法",
                "chapter": "第一章",
                "chunk_index": 0,
                "effective_date": "2021-10-01",
                "status": "现行",
            },
        )
        records = manager._build_records([doc], [[0.1, 0.2, 0.3]])
        self.assertEqual(records[0]["effective_date"], "2021-10-01")
        self.assertEqual(records[0]["status"], "现行")

    def test_build_records_effective_defaults_empty(self) -> None:
        manager = MilvusStoreManager(
            _settings(),
            _FakeEmbeddings(),
            client=_FakeStoreClient(),
        )
        doc = Document(
            page_content="第一条 内容",
            metadata={"doc_name": "测试法", "chapter": "第一章", "chunk_index": 0},
        )
        records = manager._build_records([doc], [[0.1, 0.2, 0.3]])
        self.assertEqual(records[0]["effective_date"], "")
        self.assertEqual(records[0]["status"], "")


# ── HybridRetriever._effective_expr 开关 ───────────────────

class RetrieverEffectiveSwitchTests(unittest.TestCase):
    def test_switch_off_returns_none(self) -> None:
        retriever = HybridRetriever(
            vector_store=object(),
            collection=None,
            embeddings=None,
            settings=_settings(enable_effective_filter=False),
            reranker=object(),
        )
        self.assertIsNone(retriever._effective_expr())

    def test_switch_on_returns_expr(self) -> None:
        retriever = HybridRetriever(
            vector_store=object(),
            collection=None,
            embeddings=None,
            settings=_settings(enable_effective_filter=True),
            reranker=object(),
        )
        expr = retriever._effective_expr()
        self.assertIsNotNone(expr)
        self.assertIn("effective_date is null", expr)


# ── 辅助 ──────────────────────────────────────────────────

class _FakeEmbeddings:
    def __init__(self, dim: int = 3) -> None:
        self.dim = dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * self.dim for _ in range(len(texts))]


class _FakeStoreClient:
    """milvus_store._build_records 测试所需的最小 fake。"""

    def create_schema(self, **kwargs: Any):
        return self

    def add_field(self, **kwargs: Any) -> None:
        pass


def _settings(**overrides: Any) -> Settings:
    values = {
        "milvus_uri": "http://offline.invalid:19530",
        "collection_name": "public_kb_hybrid_poc_effective",
        "embedding_dim": 3,
        "enable_bm25": True,
    }
    values.update(overrides)
    return Settings(**values)


if __name__ == "__main__":
    unittest.main()
