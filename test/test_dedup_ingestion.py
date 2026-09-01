"""M2 块级去重 + 幂等导入的单元测试（mock MilvusClient，不连真实库）。"""

from __future__ import annotations

import unittest
from unittest.mock import patch
from typing import Any

from langchain_core.documents import Document

from public_kb.config import Settings
from public_kb.ingestion.pipeline import IngestionPipeline
from public_kb.ingestion.sinks.milvus_sink import MilvusSink
from public_kb.services.milvus_store import MilvusStoreManager


class FakeMilvusClient:
    """支持 query(判重) 的可控 fake。"""

    def __init__(self) -> None:
        self.exists = False
        self.stored_uids: set[str] = set()
        self.insert_row_count = 0  # 累计写入行数（stored_uids 是 set 不能计数）
        self.schema_fields: list[dict] = [
            {"name": "id", "type": 5, "is_primary": True, "auto_id": True},
            {"name": "text", "type": 10},
            {"name": "vector", "type": 101},
            {"name": "sparse_vector", "type": 104},
        ]
        self.indexes: list[str] = ["vector", "sparse_vector"]
        self.functions = ["text_bm25_emb"]
        self.calls: list[str] = []

    # ---- MilvusClient 接口 ----
    def create_schema(self, **kwargs: Any):
        self.calls.append("create_schema")
        return self

    def add_field(self, **kwargs: Any) -> None:
        self.schema_fields.append({"name": kwargs["field_name"]})

    def add_function(self, function: Any) -> None:
        self.functions.append(getattr(function, "name", "text_bm25_emb"))

    def prepare_index_params(self):
        self.calls.append("prepare_index_params")
        return self

    def add_index(self, **kwargs: Any) -> None:
        self.indexes.append(kwargs["field_name"])

    def has_collection(self, name: str) -> bool:
        self.calls.append("has_collection")
        return self.exists

    def drop_collection(self, name: str) -> None:
        self.exists = False

    def create_collection(self, **kwargs: Any) -> None:
        self.exists = True

    def describe_collection(self, name: str) -> dict:
        return {
            "fields": [{"name": f["name"]} for f in self.schema_fields],
            "functions": [{"name": f} for f in self.functions],
        }

    def list_indexes(self, name: str) -> list[str]:
        return list(self.indexes)

    def load_collection(self, name: str) -> None:
        pass

    def query(self, collection_name: str, filter: str, output_fields: list[str]):
        self.calls.append("query")
        # 解析 `chunk_uid in ['a', 'b']` 中的 uid 列表
        expr = filter
        if "in [" in expr:
            uid_list = expr.split("in [", 1)[1].rsplit("]", 1)[0]
            uids = [u.strip().strip("'\"") for u in uid_list.split(",")]
        else:
            uids = []
        return [
            {"chunk_uid": uid}
            for uid in uids if uid in self.stored_uids
        ]

    def insert(self, name: str, data: list[dict]) -> dict:
        self.calls.append("insert")
        for row in data:
            self.stored_uids.add(row["chunk_uid"])
            self.insert_row_count += 1
        return {"insert_count": len(data)}

    def flush(self, name: str) -> None:
        self.calls.append("flush")

    def get_collection_stats(self, name: str) -> dict:
        return {"row_count": len(self.stored_uids)}


class FakeEmbeddings:
    def __init__(self, dim: int = 3) -> None:
        self.dim = dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * self.dim for _ in range(len(texts))]


def _settings(**overrides: Any) -> Settings:
    values = {
        "milvus_uri": "http://offline.invalid:19530",
        "collection_name": "public_kb_hybrid_poc_dedup",
        "embedding_dim": 3,
        "enable_bm25": True,
    }
    values.update(overrides)
    return Settings(**values)


def _doc(content: str, chapter: str = "第一章 总则", doc_name: str = "测试法") -> Document:
    return Document(
        page_content=content,
        metadata={
            "doc_name": doc_name,
            "chapter": chapter,
            "chunk_index": 0,
        },
    )


def _same_content_pair() -> list[Document]:
    """内容完全相同的两份文档 → 相同 chunk_uid。"""
    return [_doc("第一条 为了规范招标投标活动，制定本法。"), _doc("第一条 为了规范招标投标活动，制定本法。")]


class DedupIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        # 拦截 _create_vector_store_wrapper 内部的真实连接（与 test_milvus_store_offline 同策略）
        self._wrapper_patcher = patch(
            "public_kb.services.milvus_store.MilvusVectorStore",
            return_value=object(),
        )
        self._wrapper_patcher.start()

    def tearDown(self) -> None:
        self._wrapper_patcher.stop()

    def _manager(self, client: FakeMilvusClient, **settings: Any) -> MilvusStoreManager:
        return MilvusStoreManager(_settings(**settings), FakeEmbeddings(), client=client)

    # ── 批内去重 ──────────────────────────────────────────

    def test_same_batch_duplicate_written_once(self) -> None:
        client = FakeMilvusClient()
        manager = self._manager(client)
        inserted = manager.initialize_collection(_same_content_pair())
        self.assertEqual(inserted, 1)
        self.assertEqual(len(client.stored_uids), 1)

    def test_same_batch_duplicate_counts_skipped(self) -> None:
        client = FakeMilvusClient()
        manager = self._manager(client)
        manager.initialize_collection(_same_content_pair())
        # 记录 initialize 时的 skip 由 batch_insert 日志体现；这里用 pipeline 层验证
        self.assertEqual(len(client.stored_uids), 1)

    # ── 增量幂等 ──────────────────────────────────────────

    def test_add_documents_idempotent_second_call_inserts_zero(self) -> None:
        client = FakeMilvusClient()
        manager = self._manager(client)
        manager.initialize_collection(_same_content_pair())
        second = manager.add_documents(_same_content_pair())
        self.assertEqual(second, 0)
        self.assertEqual(len(client.stored_uids), 1)

    def test_add_documents_new_content_appends(self) -> None:
        client = FakeMilvusClient()
        manager = self._manager(client)
        manager.initialize_collection([_doc("第一条 内容A")])
        added = manager.add_documents([_doc("第二条 内容B")])
        self.assertEqual(added, 1)
        self.assertEqual(len(client.stored_uids), 2)

    def test_query_failure_falls_back_to_write_all(self) -> None:
        client = FakeMilvusClient()

        class BrokenQueryClient(FakeMilvusClient):
            def query(self, **kwargs: Any):
                raise RuntimeError("mock 判重查询失败")

        broken = BrokenQueryClient()
        manager = self._manager(broken)
        # 首次初始化写入后，再次导入相同内容；批内无重复（仅 1 条），
        # 判重查询失败 → 按未命中处理，重复内容被再次写入（尽力而为退化为全量写）
        manager.initialize_collection([_doc("第一条 内容A")])
        added = manager.add_documents([_doc("第一条 内容A")])
        self.assertEqual(added, 1)

    # ── 开关回退 ──────────────────────────────────────────

    def test_disable_dedup_writes_all(self) -> None:
        client = FakeMilvusClient()
        manager = self._manager(client, enable_dedup=False)
        inserted = manager.initialize_collection(_same_content_pair())
        self.assertEqual(inserted, 2)
        self.assertEqual(client.insert_row_count, 2)

    def test_disable_dedup_append_writes_all(self) -> None:
        client = FakeMilvusClient()
        manager = self._manager(client, enable_dedup=False)
        manager.initialize_collection([_doc("第一条 内容A")])
        added = manager.add_documents([_doc("第一条 内容A")])
        self.assertEqual(added, 1)  # 旧行为：重复也写入
        self.assertEqual(client.insert_row_count, 2)

    # ── pipeline / sink 计数 ──────────────────────────────

    def test_pipeline_reports_skipped_duplicates(self) -> None:
        client = FakeMilvusClient()
        manager = self._manager(client)
        result = IngestionPipeline([MilvusSink(manager, mode="append")]).run(
            _InMemorySource(_same_content_pair())
        )
        self.assertEqual(result.chunk_count, 2)
        self.assertEqual(result.inserted_count, 1)
        self.assertEqual(result.skipped_duplicates, 1)
        self.assertEqual(result.status, "completed")

    def test_pipeline_without_dedup_reports_zero_skipped(self) -> None:
        client = FakeMilvusClient()
        manager = self._manager(client, enable_dedup=False)
        result = IngestionPipeline([MilvusSink(manager, mode="append")]).run(
            _InMemorySource(_same_content_pair())
        )
        self.assertEqual(result.inserted_count, 2)
        self.assertEqual(result.skipped_duplicates, 0)


class _InMemorySource:
    """最小 Source 实现，供 pipeline 测试使用。"""

    def __init__(self, documents: list[Document]) -> None:
        self.documents = documents

    def load(self):
        from public_kb.ingestion.models import SourceResult
        return SourceResult(documents=self.documents)


if __name__ == "__main__":
    unittest.main()
