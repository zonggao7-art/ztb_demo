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


def test_prepare_handoff_cache_skips_reparse(tmp_path, monkeypatch):
    """文件级缓存：二次运行命中 assembled.md，不再调用 parser.parse。"""
    import pymupdf

    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    doc = pymupdf.open()
    try:
        doc.new_page(width=400, height=600)
        doc.save(str(pdf_dir / "book.pdf"))
    finally:
        doc.close()

    calls: list = []

    class _FakeParser:
        def parse(self, pdf_path):  # noqa: D102
            calls.append(Path(pdf_path))
            return _MD

    monkeypatch.setattr(
        "public_kb.ingestion.parser_factory.build_pdf_parser",
        lambda settings: _FakeParser(),
    )
    out = tmp_path / "out"
    s = _settings()
    s.enable_pdf_structure = False

    prepare_handoff(pdf_dir, s, out_dir=out)
    assert len(calls) == 1
    md = (out / "book.assembled.md").read_text(encoding="utf-8")
    assert md == _MD

    # 二次运行：缓存命中，不再解析；jsonl 重新由缓存 md 切片
    prepare_handoff(pdf_dir, s, out_dir=out)
    assert len(calls) == 1  # 未再调用 parser.parse
    jsonl_uids = {d.metadata["chunk_uid"]
                  for d in load_documents_jsonl(out / "book.documents.jsonl")}
    assert jsonl_uids

    # force=True 强制重解析
    prepare_handoff(pdf_dir, s, out_dir=out, force=True)
    assert len(calls) == 2


def test_prepare_handoff_skips_timeout_book_and_continues(tmp_path, monkeypatch):
    """单本超时 → 记录失败并继续下一本，不卡死整体。"""
    import pymupdf

    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    for name in ("slow.pdf", "fast.pdf"):
        doc = pymupdf.open()
        try:
            doc.new_page(width=400, height=600)
            doc.save(str(pdf_dir / name))
        finally:
            doc.close()

    import time

    class _SlowFirstParser:
        def parse(self, pdf_path):  # noqa: D102
            if "slow" in Path(pdf_path).name:
                time.sleep(5)  # 超过 1s 超时
            return _MD

    monkeypatch.setattr(
        "public_kb.ingestion.parser_factory.build_pdf_parser",
        lambda settings: _SlowFirstParser(),
    )
    s = _settings()
    s.enable_pdf_structure = False
    s.pdf_tiered_book_timeout_sec = 1

    summary = prepare_handoff(pdf_dir, s, out_dir=tmp_path / "out")
    by_name = {e["pdf"]: e for e in summary}
    assert "error" in by_name["slow.pdf"] and "跳过" in by_name["slow.pdf"]["error"]
    assert "error" not in by_name["fast.pdf"]  # 超时书不阻断下一本
    assert (tmp_path / "out" / "fast.assembled.md").exists()


def test_prepare_handoff_skips_dev_pdfs(tmp_path, monkeypatch):
    """prepare_handoff 跳过下划线前缀开发产物，正常解析其余 PDF。"""
    import pymupdf

    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    for name in ("book.pdf", "_smoke.pdf"):
        doc = pymupdf.open()
        try:
            doc.new_page(width=400, height=600)
            doc.save(str(pdf_dir / name))
        finally:
            doc.close()

    class _FakeParser:
        def parse(self, pdf_path):  # noqa: D102
            return _MD

    monkeypatch.setattr(
        "public_kb.ingestion.parser_factory.build_pdf_parser",
        lambda settings: _FakeParser(),
    )
    out = tmp_path / "out"
    s = _settings()
    s.enable_pdf_structure = False  # 简化：直接走 chunker
    summary = prepare_handoff(pdf_dir, s, out_dir=out)
    assert [e["pdf"] for e in summary] == ["book.pdf"]
    assert (out / "book.assembled.md").exists()
    assert (out / "book.documents.jsonl").exists()
    assert not (out / "_smoke.assembled.md").exists()
