"""MilvusStoreManager 的纯离线测试，不连接真实 Milvus。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from langchain_core.documents import Document

from public_kb.config import Settings
from public_kb.contracts import ConfigurationContractError, IngestionContractError
from public_kb.services.milvus_store import MilvusStoreManager


class FakeSchema:
    def __init__(self) -> None:
        self.fields: list[dict] = []
        self.functions: list[object] = []

    def add_field(self, **kwargs: object) -> None:
        self.fields.append(dict(kwargs))

    def add_function(self, function: object) -> None:
        self.functions.append(function)


class FakeIndexParams:
    def __init__(self) -> None:
        self.indexes: list[dict] = []

    def add_index(self, **kwargs: object) -> None:
        self.indexes.append(dict(kwargs))


class FakeMilvusClient:
    def __init__(self, *, exists: bool = False) -> None:
        self.exists = exists
        self.schema = FakeSchema()
        self.index_params = FakeIndexParams()
        self.calls: list[tuple] = []
        self.created_schema: FakeSchema | None = None
        self.created_indexes: FakeIndexParams | None = None
        self.insert_count_override: int | None = None

    def create_schema(self, **kwargs: object) -> FakeSchema:
        self.calls.append(("create_schema", kwargs))
        return self.schema

    def prepare_index_params(self) -> FakeIndexParams:
        self.calls.append(("prepare_index_params",))
        return self.index_params

    def has_collection(self, name: str) -> bool:
        self.calls.append(("has_collection", name))
        return self.exists

    def drop_collection(self, name: str) -> None:
        self.calls.append(("drop_collection", name))
        self.exists = False

    def create_collection(self, **kwargs: object) -> None:
        self.calls.append(("create_collection", kwargs["collection_name"]))
        self.created_schema = kwargs["schema"]  # type: ignore[assignment]
        self.created_indexes = kwargs["index_params"]  # type: ignore[assignment]
        self.exists = True

    def describe_collection(self, name: str) -> dict:
        self.calls.append(("describe_collection", name))
        fields = [{"name": item["field_name"]} for item in self.schema.fields]
        functions = [
            {"name": getattr(function, "name", "text_bm25_emb")}
            for function in self.schema.functions
        ]
        return {"fields": fields, "functions": functions}

    def list_indexes(self, name: str) -> list[str]:
        self.calls.append(("list_indexes", name))
        return [str(item["field_name"]) for item in self.index_params.indexes]

    def load_collection(self, name: str) -> None:
        self.calls.append(("load_collection", name))

    def insert(self, name: str, data: list[dict]) -> dict:
        self.calls.append(("insert", name, data))
        count = self.insert_count_override
        return {"insert_count": len(data) if count is None else count}

    def flush(self, name: str) -> None:
        self.calls.append(("flush", name))

    def get_collection_stats(self, name: str) -> dict:
        return {"row_count": 1}


class FakeEmbeddings:
    def __init__(self, dim: int, *, vector_count_delta: int = 0) -> None:
        self.dim = dim
        self.vector_count_delta = vector_count_delta

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        count = max(0, len(texts) + self.vector_count_delta)
        return [[0.1] * self.dim for _ in range(count)]


def _settings(**overrides: object) -> Settings:
    values = {
        "milvus_uri": "http://offline.invalid:19530",
        "collection_name": "public_kb_hybrid_poc_contract",
        "embedding_dim": 3,
        "enable_bm25": True,
    }
    values.update(overrides)
    return Settings(**values)


def _document() -> Document:
    return Document(
        page_content="第三条 招标投标活动应当遵循公开原则。",
        metadata={
            "doc_name": "中华人民共和国招标投标法",
            "chapter": "第一章 总则",
            "chunk_index": 0,
            "source_url": "https://example.invalid/law",
        },
    )


class MilvusStoreOfflineTests(unittest.TestCase):
    def test_builds_bm25_schema_and_indexes(self) -> None:
        client = FakeMilvusClient()
        manager = MilvusStoreManager(_settings(), FakeEmbeddings(3), client=client)

        schema = manager._build_schema()
        indexes = manager._build_index_params()

        fields = {item["field_name"]: item for item in schema.fields}
        self.assertEqual(set(fields), {"id", "text", "vector", "sparse_vector"})
        self.assertTrue(fields["text"]["enable_analyzer"])
        self.assertEqual(len(schema.functions), 1)
        index_by_field = {item["field_name"]: item for item in indexes.indexes}
        self.assertEqual(index_by_field["vector"]["metric_type"], "COSINE")
        self.assertEqual(index_by_field["sparse_vector"]["metric_type"], "BM25")

    def test_records_contain_dense_but_not_client_sparse_vector(self) -> None:
        manager = MilvusStoreManager(_settings(), FakeEmbeddings(3), client=FakeMilvusClient())

        records = manager._build_records([_document()], [[0.1, 0.2, 0.3]])

        self.assertEqual(records[0]["vector"], [0.1, 0.2, 0.3])
        self.assertNotIn("sparse_vector", records[0])
        self.assertTrue(records[0]["chunk_uid"].startswith("ck-"))
        self.assertEqual(records[0]["schema_version"], "public_kb_v2")
        self.assertEqual(records[0]["embedding_model"], "BAAI/bge-m3")

    def test_existing_collection_is_not_dropped_by_default(self) -> None:
        client = FakeMilvusClient(exists=True)
        manager = MilvusStoreManager(_settings(), FakeEmbeddings(3), client=client)

        with self.assertRaises(ConfigurationContractError):
            manager.initialize_collection([_document()])

        self.assertFalse(any(call[0] == "drop_collection" for call in client.calls))

    def test_recreate_rejects_non_experiment_collection(self) -> None:
        client = FakeMilvusClient(exists=True)
        manager = MilvusStoreManager(
            _settings(collection_name="public_kb"),
            FakeEmbeddings(3),
            client=client,
        )

        with self.assertRaises(ConfigurationContractError):
            manager.initialize_collection([_document()], recreate=True)

        self.assertFalse(any(call[0] == "drop_collection" for call in client.calls))

    def test_initialize_validates_schema_before_insert(self) -> None:
        client = FakeMilvusClient()
        manager = MilvusStoreManager(_settings(), FakeEmbeddings(3), client=client)
        with patch.object(manager, "_create_vector_store_wrapper", return_value=object()):
            manager.initialize_collection([_document()])

        names = [call[0] for call in client.calls]
        self.assertLess(names.index("describe_collection"), names.index("insert"))
        insert_call = next(call for call in client.calls if call[0] == "insert")
        self.assertNotIn("sparse_vector", insert_call[2][0])

    def test_embedding_count_mismatch_stops_insert(self) -> None:
        client = FakeMilvusClient()
        manager = MilvusStoreManager(
            _settings(),
            FakeEmbeddings(3, vector_count_delta=-1),
            client=client,
        )
        with patch.object(manager, "_create_vector_store_wrapper", return_value=object()):
            with self.assertRaises(IngestionContractError):
                manager.initialize_collection([_document()])

        self.assertFalse(any(call[0] == "insert" for call in client.calls))

    def test_server_insert_count_mismatch_is_rejected(self) -> None:
        client = FakeMilvusClient()
        client.insert_count_override = 0
        manager = MilvusStoreManager(_settings(), FakeEmbeddings(3), client=client)
        with patch.object(manager, "_create_vector_store_wrapper", return_value=object()):
            with self.assertRaises(IngestionContractError):
                manager.initialize_collection([_document()])

    def test_connection_args_use_explicit_uri_and_token(self) -> None:
        manager = MilvusStoreManager(
            _settings(milvus_token="secret-token", milvus_timeout=12),
            FakeEmbeddings(3),
            client=FakeMilvusClient(),
        )

        self.assertEqual(manager._connection_args, {
            "uri": "http://offline.invalid:19530",
            "timeout": 12,
            "token": "secret-token",
        })

    def test_dense_only_schema_remains_available_for_current_collection(self) -> None:
        manager = MilvusStoreManager(
            _settings(enable_bm25=False),
            FakeEmbeddings(3),
            client=FakeMilvusClient(),
        )

        schema = manager._build_schema()
        indexes = manager._build_index_params()

        self.assertEqual(
            {item["field_name"] for item in schema.fields},
            {"id", "text", "vector"},
        )
        self.assertEqual([item["field_name"] for item in indexes.indexes], ["vector"])


if __name__ == "__main__":
    unittest.main()
