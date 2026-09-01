"""L6 交接产物（handoff）测试：分块确定性 + JSONL 往返 + 双路径一致性。"""

from __future__ import annotations

from pathlib import Path

import pytest

from public_kb.config import Settings
from public_kb.ingestion.handoff import (
    chunk_markdown,
    dump_documents_jsonl,
    ingest_markdown_dir,
    load_documents_jsonl,
    prepare_handoff,
)

_MD = """# 第一章 总则

第一条 为了规范招标投标活动，根据《中华人民共和国招标投标法》制定本条例。

第二条 本法所称招标投标活动，是指招标投标双方在招标投标活动中实施的……

第三条 国家鼓励利用信息网络进行电子招标投标。

## 第一节 适用范围

第四条 在中华人民共和国境内进行招标投标活动，适用本法。
"""


def _settings() -> Settings:
    s = Settings()
    s.pdf_tiered_routing_enabled = False  # 分块测试不依赖解析路由
    return s


def test_chunk_markdown_deterministic():
    """同一 markdown 两次分块，块边界与元数据逐字段一致。"""
    s = _settings()
    a = chunk_markdown(_MD, "demo.pdf", s)
    b = chunk_markdown(_MD, "demo.pdf", s)
    assert len(a) == len(b) > 0
    for da, db in zip(a, b):
        assert da.page_content == db.page_content
        assert da.metadata == db.metadata


def test_chunk_uid_differs_by_doc_name():
    """chunk_uid 依赖 doc_name：同一内容不同文件名 → 不同 uid（确定性前提）。"""
    from public_kb.ingestion.handoff import _freeze_chunk_uid

    s = _settings()
    a = _freeze_chunk_uid(chunk_markdown(_MD, "demo.pdf", s))
    b = _freeze_chunk_uid(chunk_markdown(_MD, "other.pdf", s))
    uids_a = {d.metadata["chunk_uid"] for d in a}
    uids_b = {d.metadata["chunk_uid"] for d in b}
    assert uids_a and uids_a != uids_b


def test_jsonl_roundtrip(tmp_path):
    """dump → load 往返，page_content/metadata/chunk_uid 不丢失。"""
    s = _settings()
    docs = chunk_markdown(_MD, "demo.pdf", s)
    # chunk_markdown 不固化 uid，dump 前固化（prepare_handoff 里做）
    from public_kb.ingestion.handoff import _freeze_chunk_uid
    docs = _freeze_chunk_uid(docs)
    path = tmp_path / "demo.documents.jsonl"
    dump_documents_jsonl(docs, path)
    loaded = load_documents_jsonl(path)
    assert len(loaded) == len(docs)
    for d1, d2 in zip(docs, loaded):
        assert d1.page_content == d2.page_content
        assert d1.metadata == d2.metadata
        assert d2.metadata["chunk_uid"].startswith("ck-")


def test_markdown_path_matches_jsonl_path(tmp_path):
    """两条导入路径一致：assembled.md 重新分块 == 导出的 jsonl（uid 集合相同）。"""
    s = _settings()
    docs = prepare_md = chunk_markdown(_MD, "demo", s)
    from public_kb.ingestion.handoff import _freeze_chunk_uid
    docs = _freeze_chunk_uid(docs)
    jsonl_path = tmp_path / "demo.documents.jsonl"
    dump_documents_jsonl(docs, jsonl_path)
    jsonl_uids = {d.metadata["chunk_uid"] for d in load_documents_jsonl(jsonl_path)}

    md_dir = tmp_path / "md"
    md_dir.mkdir()
    (md_dir / "demo.assembled.md").write_text(_MD, encoding="utf-8")
    md_docs = chunk_markdown(_MD, "demo", s)
    from public_kb.ingestion.handoff import _freeze_chunk_uid as _fz
    md_uids = {d.metadata["chunk_uid"] for d in _fz(md_docs)}
    assert jsonl_uids == md_uids


def _make_pdf(path: Path, pages: int = 1) -> None:
    """构造一个最小空白 PDF。"""
    import pymupdf

    doc = pymupdf.open()
    try:
        for _ in range(pages):
            doc.new_page(width=400, height=600)
        doc.save(str(path))
    finally:
        doc.close()


class _FakeParser:
    """替身解析器：返回固定 markdown。"""

    def parse(self, pdf_path):  # noqa: D102
        return _MD


def _slow_worker(pdf_path, out_dir, force, settings, result_queue):
    """子进程 worker（超时测试用）：sleep 超过超时阈值。"""
    import time

    time.sleep(3)
    result_queue.put({"chunks": 0, "cached": True})


def _fast_worker(pdf_path, out_dir, force, settings, result_queue):
    """子进程 worker（成功路径测试用）：立即返回固定 entry。"""
    result_queue.put({"chunks": 7, "markdown": "m", "jsonl": "j"})


def test_prepare_handoff_cache_hit_skips_parse(tmp_path):
    """文件级缓存：assembled.md 已存在时只分块不解析（主进程，快速）。"""
    from public_kb.ingestion.handoff import _process_single_book

    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    _make_pdf(pdf_dir / "book.pdf")

    out = tmp_path / "out"
    out.mkdir()
    (out / "book.assembled.md").write_text(_MD, encoding="utf-8")

    s = _settings()
    s.enable_pdf_structure = False
    summary = prepare_handoff(pdf_dir, s, out_dir=out)
    entry = {e["pdf"]: e for e in summary}["book.pdf"]
    assert entry.get("cached") is True
    assert (out / "book.documents.jsonl").exists()
    jsonl = load_documents_jsonl(out / "book.documents.jsonl")
    assert jsonl and all(d.metadata["chunk_uid"].startswith("ck-") for d in jsonl)
    # _process_single_book 缓存路径同语义：不触碰解析器
    entry2 = _process_single_book(pdf_dir / "book.pdf", out, False, s)
    assert entry2.get("cached") is True


def test_process_single_book_parse_writes_artifacts(tmp_path, monkeypatch):
    """全量解析路径：写 assembled.md + documents.jsonl（进程内，替换解析器）。"""
    from public_kb.ingestion.handoff import _process_single_book

    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    _make_pdf(pdf_dir / "book.pdf")
    monkeypatch.setattr(
        "public_kb.ingestion.parser_factory.build_pdf_parser",
        lambda settings: _FakeParser(),
    )
    s = _settings()
    s.enable_pdf_structure = False
    out = tmp_path / "out"
    out.mkdir()
    entry = _process_single_book(pdf_dir / "book.pdf", out, False, s)
    assert entry["chunks"] > 0
    assert (out / "book.assembled.md").read_text(encoding="utf-8") == _MD
    assert (out / "book.documents.jsonl").exists()


def test_book_process_timeout_terminates(tmp_path):
    """进程级超时：超时 terminate，返回 TimeoutError 且不残留结果。"""
    from public_kb.ingestion.handoff import _run_book_process

    result, err = _run_book_process(
        tmp_path / "x.pdf", tmp_path, False, _settings(), 1,
        worker=_slow_worker,
    )
    assert result is None
    assert err is not None and "跳过" in str(err)


def test_book_process_returns_result(tmp_path):
    """进程级成功路径：子进程结果经队列回传。"""
    from public_kb.ingestion.handoff import _run_book_process

    result, err = _run_book_process(
        tmp_path / "x.pdf", tmp_path, False, _settings(), 10,
        worker=_fast_worker,
    )
    assert err is None
    assert result == {"chunks": 7, "markdown": "m", "jsonl": "j"}


def test_prepare_handoff_skips_dev_pdfs(tmp_path):
    """prepare_handoff 跳过下划线前缀开发产物；正常书走缓存不解析。"""
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    _make_pdf(pdf_dir / "book.pdf")
    _make_pdf(pdf_dir / "_smoke.pdf")

    out = tmp_path / "out"
    out.mkdir()
    (out / "book.assembled.md").write_text(_MD, encoding="utf-8")

    s = _settings()
    s.enable_pdf_structure = False
    summary = prepare_handoff(pdf_dir, s, out_dir=out)
    assert [e["pdf"] for e in summary] == ["book.pdf"]
    assert not (out / "_smoke.assembled.md").exists()
    assert (out / "book.documents.jsonl").exists()
