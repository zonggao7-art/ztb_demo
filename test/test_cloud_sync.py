"""cloud_sync 单元测试 + 本地 Milvus 集成冒烟测试。

运行方式（无需额外依赖，使用标准库 unittest）：
    python -m unittest discover -s test -v
"""

from __future__ import annotations

import tempfile
import unittest
from unittest import mock

from pymilvus import MilvusClient, DataType

from cloud_sync.config import CloudSyncConfig
from cloud_sync.connection import RedisConn, RedisProtocolError, retry_with_backoff
from cloud_sync.milvus_sync import MilvusMigrator, build_schema
from cloud_sync.verify import ConsistencyVerifier, record_fingerprint
from cloud_sync.watermark import WatermarkStore


class TestFingerprint(unittest.TestCase):
    def test_identical_records_have_same_fingerprint(self):
        a = {"b": 1, "a": [1.0, 2.0], "text": "x"}
        b = {"a": [1.0, 2.0], "text": "x", "b": 1}
        self.assertEqual(record_fingerprint(a), record_fingerprint(b))

    def test_exclude_field_is_ignored(self):
        a = {"id": 1, "text": "x", "vector": [1.0]}
        b = {"id": 999, "text": "x", "vector": [1.0]}
        self.assertEqual(
            record_fingerprint(a, exclude_field="id"),
            record_fingerprint(b, exclude_field="id"),
        )
        self.assertNotEqual(record_fingerprint(a), record_fingerprint(b))

    def test_different_content_differs(self):
        self.assertNotEqual(
            record_fingerprint({"text": "a"}), record_fingerprint({"text": "b"})
        )


class TestRetry(unittest.TestCase):
    def test_retries_then_succeeds(self):
        calls = {"n": 0}

        @retry_with_backoff(max_retries=3, backoff=0.0)
        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("boom")
            return "ok"

        self.assertEqual(flaky(), "ok")
        self.assertEqual(calls["n"], 3)

    def test_gives_up_after_max_retries(self):
        @retry_with_backoff(max_retries=2, backoff=0.0)
        def always_fail():
            raise RuntimeError("x")

        with self.assertRaises(RuntimeError):
            always_fail()


class TestWatermark(unittest.TestCase):
    def test_persist_and_reload(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        store = WatermarkStore(path)
        store.set_milvus("public_kb", last_pk=123, count=123)
        store.set_redis_count(456)

        reloaded = WatermarkStore(path)
        self.assertEqual(reloaded.get_milvus("public_kb")["last_pk"], 123)
        self.assertEqual(reloaded.get_milvus("public_kb")["count"], 123)
        self.assertEqual(reloaded.get_redis_count(), 456)


class TestBuildSchema(unittest.TestCase):
    def test_build_schema_passes_primary_and_params(self):
        client = mock.MagicMock()
        schema = mock.MagicMock()
        client.create_schema.return_value = schema

        desc = {
            "auto_id": True,
            "enable_dynamic_field": True,
            "fields": [
                {"name": "id", "type": DataType.INT64, "is_primary": True, "auto_id": True},
                {"name": "text", "type": DataType.VARCHAR, "params": {"max_length": 100}},
                {"name": "vector", "type": DataType.FLOAT_VECTOR, "params": {"dim": 8}},
            ],
        }
        build_schema(client, desc)

        client.create_schema.assert_called_once_with(
            auto_id=True, enable_dynamic_field=True
        )
        calls = [c.kwargs for c in schema.add_field.call_args_list]
        self.assertEqual(len(calls), 3)
        self.assertTrue(calls[0]["is_primary"])
        self.assertTrue(calls[0]["auto_id"])
        self.assertEqual(calls[1]["max_length"], 100)
        self.assertEqual(calls[2]["dim"], 8)


class TestRespParsing(unittest.TestCase):
    class _DummySock:
        def recv(self, n):
            raise AssertionError("缓冲区已足够，不应触发 recv")

    def _conn_with(self, raw: bytes) -> RedisConn:
        conn = RedisConn("localhost", 6379)
        conn._sock = self._DummySock()
        conn._buf = raw
        return conn

    def test_bulk_string(self):
        conn = self._conn_with(b"$5\r\nhello\r\n")
        self.assertEqual(conn._read_reply(), b"hello")

    def test_integer(self):
        conn = self._conn_with(b":42\r\n")
        self.assertEqual(conn._read_reply(), 42)

    def test_nil(self):
        conn = self._conn_with(b"$-1\r\n")
        self.assertIsNone(conn._read_reply())

    def test_array(self):
        conn = self._conn_with(b"*2\r\n:1\r\n$2\r\nok\r\n")
        self.assertEqual(conn._read_reply(), [1, b"ok"])

    def test_error(self):
        conn = self._conn_with(b"-ERR bad\r\n")
        with self.assertRaises(RedisProtocolError):
            conn._read_reply()


class TestMilvusFullSync(unittest.TestCase):
    def _make_migrator(self):
        config = CloudSyncConfig()
        config.watermark_path = tempfile.mktemp(suffix=".json")
        source = mock.MagicMock()
        target = mock.MagicMock()

        source.has_collection.return_value = True
        source.describe_collection.return_value = {
            "auto_id": True,
            "enable_dynamic_field": True,
            "fields": [
                {"name": "id", "type": DataType.INT64, "is_primary": True, "auto_id": True},
                {"name": "text", "type": DataType.VARCHAR, "params": {"max_length": 100}},
                {"name": "vector", "type": DataType.FLOAT_VECTOR, "params": {"dim": 8}},
            ],
        }
        source.list_indexes.return_value = ["vector"]
        source.describe_index.return_value = {
            "field_name": "vector",
            "index_type": "IVF_FLAT",
            "metric_type": "COSINE",
            "nlist": "128",
        }
        source.query_iterator.return_value = [
            [
                {"id": 1, "text": "a", "vector": [0.1] * 8},
                {"id": 2, "text": "b", "vector": [0.2] * 8},
            ],
            [
                {"id": 3, "text": "c", "vector": [0.3] * 8},
            ],
        ]

        target.has_collection.return_value = False
        target.create_schema.return_value = mock.MagicMock()
        target.prepare_index_params.return_value = mock.MagicMock()

        migrator = MilvusMigrator(
            config, source=source, target=target,
            collection_map={"public_kb": "public_kb_target"},
        )
        return config, migrator, source, target

    def test_full_sync_strips_auto_id_and_inserts(self):
        config, migrator, source, target = self._make_migrator()

        summary = migrator.full_sync(["public_kb"])

        self.assertEqual(summary["public_kb"], 3)
        # 目标集合使用重映射名
        target.create_collection.assert_called_once()
        self.assertEqual(target.create_collection.call_args.args[0], "public_kb_target")
        # auto_id 主键被剔除后再插入
        inserted = target.insert.call_args_list
        all_rows = [row for call in inserted for row in call.args[1]]
        self.assertEqual(len(all_rows), 3)
        for row in all_rows:
            self.assertNotIn("id", row)
        # 水位线记录最大主键
        wm = WatermarkStore(config.watermark_path).get_milvus("public_kb")
        self.assertEqual(wm["last_pk"], 3)
        self.assertEqual(wm["count"], 3)


class TestMilvusIncrementalReconcile(unittest.TestCase):
    def test_reconcile_copies_only_missing(self):
        config = CloudSyncConfig()
        config.watermark_path = tempfile.mktemp(suffix=".json")
        source = mock.MagicMock()
        target = mock.MagicMock()

        source.has_collection.return_value = True
        target.has_collection.return_value = True
        source.describe_collection.return_value = {
            "auto_id": False,
            "enable_dynamic_field": False,
            "fields": [
                {"name": "pk", "type": DataType.VARCHAR, "is_primary": True,
                 "params": {"max_length": 64}},
                {"name": "text", "type": DataType.VARCHAR, "params": {"max_length": 100}},
                {"name": "vector", "type": DataType.FLOAT_VECTOR, "params": {"dim": 8}},
            ],
        }

        def fake_collect(client, collection, _pk_field, batch_size=5000):
            if client is source:
                return {"a", "b", "c"}
            return {"a"}

        with mock.patch(
            "cloud_sync.milvus_sync.collect_primary_keys", side_effect=fake_collect
        ):
            source.query.return_value = [
                {"pk": "b", "text": "b", "vector": [0.1] * 8},
                {"pk": "c", "text": "c", "vector": [0.2] * 8},
            ]
            migrator = MilvusMigrator(config, source=source, target=target)
            inserted = migrator.incremental_sync(["price"])

        self.assertEqual(inserted["price"], 2)
        # 只按缺失主键 b,c 查询并插入
        source.query.assert_called_once()
        self.assertEqual(set(source.query.call_args.kwargs["ids"]), {"b", "c"})


class MilvusIntegrationSmokeTest(unittest.TestCase):
    """本地 Milvus 端到端冒烟测试：源集合 → 重命名目标集合 → 校验一致。"""

    URI = "http://localhost:19530"
    SRC = "_cloud_sync_smoke_src"
    DST = "_cloud_sync_smoke_dst"

    @classmethod
    def setUpClass(cls):
        try:
            client = MilvusClient(uri=cls.URI)
            client.list_collections()
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"本地 Milvus 不可用，跳过集成测试: {exc}")

        for name in (cls.SRC, cls.DST):
            if client.has_collection(name):
                client.drop_collection(name)

        schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field(field_name="pk", datatype=DataType.VARCHAR,
                         max_length=64, is_primary=True)
        schema.add_field(field_name="text", datatype=DataType.VARCHAR, max_length=256)
        schema.add_field(field_name="vector", datatype=DataType.FLOAT_VECTOR, dim=8)
        index_params = client.prepare_index_params()
        index_params.add_index(field_name="vector", index_type="IVF_FLAT",
                               metric_type="COSINE", params={"nlist": 16})
        client.create_collection(cls.SRC, schema=schema, index_params=index_params)

        rows = [
            {"pk": f"k{i}", "text": f"hello-{i}", "vector": [0.1 * i] * 8}
            for i in range(10)
        ]
        client.insert(cls.SRC, rows)
        client.flush(cls.SRC)

    @classmethod
    def tearDownClass(cls):
        try:
            client = MilvusClient(uri=cls.URI)
            for name in (cls.SRC, cls.DST):
                if client.has_collection(name):
                    client.drop_collection(name)
        except Exception:  # noqa: BLE001
            pass

    def test_full_sync_then_verify(self):
        config = CloudSyncConfig()
        config.watermark_path = tempfile.mktemp(suffix=".json")
        config.milvus_batch_size = 4  # 强制多批分页，覆盖分页逻辑

        migrator = MilvusMigrator(
            config,
            collection_map={self.SRC: self.DST},
        )
        summary = migrator.full_sync([self.SRC])
        self.assertEqual(summary[self.SRC], 10)

        verifier = ConsistencyVerifier(
            config,
            collection_map={self.SRC: self.DST},
        )
        result = verifier.verify([self.SRC])
        self.assertTrue(result[self.SRC]["passed"], result)


if __name__ == "__main__":
    unittest.main()
