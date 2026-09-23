"""Milvus 主键化入库（chunk_uid 主键 + upsert 防重 + 交接包校验）的单元测试。

全部使用 mock MilvusClient，不需要真实 Milvus 服务。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from langchain_core.documents import Document
from pymilvus import DataType

from public_kb.book_pipeline import load_handoff_bundle, prepare_handoff
from public_kb.config import Settings
from public_kb.milvus_store import MilvusStoreManager


def _settings(**overrides) -> Settings:
    return Settings(collection_name="public_kb_ut", **overrides)


class _FakeEmbeddings:
    """embed_documents 桩：返回固定小向量，避免真实 API 调用。"""

    def embed_documents(self, texts, chunk_size=None, **kwargs):
        return [[0.1, 0.2] for _ in texts]

    def embed_query(self, text):
        return [0.1, 0.2]


def _doc(text: str, doc_name: str = "测试书", chunk_index: int = 0, **meta) -> Document:
    metadata = {"doc_name": doc_name, "chapter": "第一章", "chunk_index": chunk_index, **meta}
    return Document(page_content=text, metadata=metadata)


def _manager(client_mock) -> MilvusStoreManager:
    with mock.patch("public_kb.milvus_store.MilvusClient", return_value=client_mock), \
         mock.patch("public_kb.milvus_store.MilvusVectorStore"):
        return MilvusStoreManager(_settings(), _FakeEmbeddings())


def _client_stub(has_collection=False):
    client = mock.MagicMock()
    client.has_collection.return_value = has_collection

    schemas: list = []

    def _make_schema(*_args, **_kwargs):
        schema = mock.MagicMock()
        schema.fields = []
        schema.functions = []
        schema.add_field.side_effect = lambda **kw: schema.fields.append(kw)
        schema.add_function.side_effect = lambda fn: schema.functions.append(fn)
        schemas.append(schema)
        return schema

    client.create_schema.side_effect = _make_schema
    client.created_schemas = schemas
    client.prepare_index_params.return_value = mock.MagicMock()
    client.describe_collection.return_value = {
        "fields": [
            {"name": "chunk_uid"},
            {"name": "text"},
            {"name": "vector"},
            {"name": "sparse_vector"},
        ],
        "functions": [{"name": "text_bm25"}],
    }
    client.get_collection_stats.return_value = {"row_count": 0}
    return client


def test_schema_uses_chunk_uid_primary_key_without_auto_id():
    client = _client_stub()
    manager = _manager(client)

    manager.initialize_collection([_doc("正文内容。")])

    kwargs = client.create_schema.call_args.kwargs
    assert kwargs["auto_id"] is False
    assert kwargs["enable_dynamic_field"] is True
    schema = client.created_schemas[0]
    pk = [f for f in schema.fields if f.get("is_primary")]
    assert len(pk) == 1
    assert pk[0]["field_name"] == "chunk_uid"
    assert pk[0]["datatype"] == DataType.VARCHAR
    # 主键化后不再存在旧的自增 id 字段
    assert all(f["field_name"] != "id" for f in schema.fields)
    client.upsert.assert_called_once()


def test_missing_doc_name_is_rejected():
    client = _client_stub()
    manager = _manager(client)

    bad = Document(page_content="缺 doc_name 的数据。", metadata={"chapter": "第一章"})

    try:
        manager.upsert_documents([bad])
    except ValueError as exc:
        assert "doc_name" in str(exc)
    else:
        raise AssertionError("缺少 doc_name 的数据必须被拒绝入库")


def test_duplicate_uid_within_one_call_is_rejected():
    client = _client_stub()
    manager = _manager(client)

    docs = [
        _doc("相同内容的重复块。", chunk_index=0, chunk_uid="ck-dup"),
        _doc("相同内容的重复块。", chunk_index=1, chunk_uid="ck-dup"),
    ]

    try:
        manager.upsert_documents(docs)
    except ValueError as exc:
        assert "重复 chunk_uid" in str(exc)
    else:
        raise AssertionError("同一次调用内的重复 chunk_uid 必须被拒绝")


def test_missing_chunk_index_is_injected_and_uid_derived():
    client = _client_stub()
    manager = _manager(client)

    docs = [_doc("没有 chunk_index 的块。", chunk_index=None, doc_name="书A")]
    docs[0].metadata.pop("chunk_index")

    manager.upsert_documents(docs)

    record = client.upsert.call_args.args[1][0]
    assert record["chunk_index"] == 0
    assert record["chunk_uid"].startswith("ck-")
    assert record["doc_name"] == "书A"


def test_replace_doc_deletes_old_rows_before_upsert():
    client = _client_stub(has_collection=True)
    manager = _manager(client)

    manager.upsert_documents(
        [_doc("新版本内容。", doc_name='含"引号"的书名')],
        replace_doc='含"引号"的书名',
    )

    delete_filter = client.delete.call_args.kwargs.get("filter") or client.delete.call_args.args[-1]
    assert 'doc_name == "含\\"引号\\"的书名"' in delete_filter
    client.upsert.assert_called_once()


def test_row_count_returns_zero_when_collection_missing():
    client = _client_stub(has_collection=False)
    manager = _manager(client)
    assert manager.row_count() == 0


# ── 交接包加载与三重校验 ──


def _fixture_root(root: Path) -> Path:
    from public_kb.normalize import BOOK_PROFILES

    def write(directory: str, filename: str, items: list[dict]) -> None:
        auto = root / directory / "auto"
        auto.mkdir(parents=True)
        (auto / filename).write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")

    long1 = "有效的招标投标法律实务正文。" * 90
    long2 = "答：政府采购应当遵循公开透明、公平竞争、公正和诚实信用原则。" * 45
    long3 = "为了规范招标投标活动，保护国家利益和当事人的合法权益，制定本法。" * 40
    write(
        "招标投标法律解读与风险防范实务 (白如银)",
        "招标投标法律解读与风险防范实务_content_list.json",
        [
            {"type": "text", "text": "第一章 招标投标基础知识", "page_idx": 15},
            {"type": "text", "text": "一、基本概念", "page_idx": 15},
            {"type": "text", "text": long1, "page_idx": 16},
        ],
    )
    write(
        "政府采购、工程招标、投标与评标1200问（第3版）_刘海桑",
        "政府采购、工程招标、投标与评标1200问_content_list.json",
        [
            {"type": "text", "text": "第一章", "page_idx": 6},
            {"type": "text", "text": "政府采购", "page_idx": 6},
            {"type": "text", "text": "1.什么是政府采购？", "page_idx": 7},
            {"type": "text", "text": long2, "page_idx": 8},
        ],
    )
    write(
        "中华人民共和国招标投标法律法规全书 (OCR)",
        "中华人民共和国招标投标法律法规全书_content_list.json",
        [
            {"type": "text", "text": "一、 招标投标", "page_idx": 12},
            {"type": "text", "text": "中华人民共和国招标投标法", "page_idx": 14, "text_level": 1},
            {"type": "text", "text": "第一条【立法目的】" + long3, "page_idx": 14},
        ],
    )
    assert set(BOOK_PROFILES) == {"book1", "book2", "book3"}
    return root


def test_load_handoff_bundle_merges_and_verifies(tmp_path):
    source = _fixture_root(tmp_path / "raw_data")
    out = tmp_path / "handoff"
    manifest = prepare_handoff(source, out)

    docs = load_handoff_bundle(out)

    assert len(docs) == manifest["chunk_count"]
    uids = [d.metadata["chunk_uid"] for d in docs]
    assert len(set(uids)) == len(uids) == manifest["chunk_count"]
    assert all(d.metadata.get("doc_name") for d in docs)


def test_load_handoff_bundle_rejects_tampered_uid(tmp_path):
    source = _fixture_root(tmp_path / "raw_data")
    out = tmp_path / "handoff"
    prepare_handoff(source, out)

    target = out / "book1" / "documents.jsonl"
    rows = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows[0]["metadata"]["chunk_uid"] = "ck-tampered"
    target.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows) + "\n",
        encoding="utf-8",
    )

    try:
        load_handoff_bundle(out)
    except ValueError as exc:
        assert "manifest 不一致" in str(exc) or "拒绝入库" in str(exc)
    else:
        raise AssertionError("篡改 chunk_uid 后必须拒绝入库")


def test_load_handoff_bundle_rejects_missing_manifest(tmp_path):
    try:
        load_handoff_bundle(tmp_path)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("缺少 manifest.json 必须报错")


def test_cli_parser_exposes_ingest_handoff():
    from public_kb.__main__ import _build_parser

    parser = _build_parser()
    args = parser.parse_args(["--ingest-handoff", "DATA/repaired_knowledge"])
    assert args.ingest_handoff == "DATA/repaired_knowledge"
    assert args.replace_doc is None
    assert args.rebuild is False

    args2 = parser.parse_args([
        "--ingest-handoff", "DATA/repaired_knowledge",
        "--replace-doc", "旧书名",
    ])
    assert args2.replace_doc == "旧书名"

    args3 = parser.parse_args(["--ingest-handoff", "X", "--rebuild"])
    assert args3.rebuild is True
