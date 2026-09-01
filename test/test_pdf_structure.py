# 功能：pdf_structure 模块单元测试（合成夹具，不依赖 MinerU）。
"""Unit tests for ingestion/transforms/pdf_structure.py."""

from __future__ import annotations

from public_kb.ingestion.transforms.chunker import SemanticChunker
from public_kb.ingestion.transforms.pdf_structure import (
    adapt_pdf_markdown,
    detect_reflow_suspect,
    split_table_blocks,
    strip_toc_noise,
)


# ── split_table_blocks ────────────────────────────────────

def test_table_segment_is_detected_as_atomic():
    md = (
        "正文第一段。\n"
        "| 列A | 列B |\n"
        "| --- | --- |\n"
        "| 甲 | 乙 |\n"
        "正文第二段。\n"
    )
    segments = split_table_blocks(md)
    assert any(is_table for _, is_table in segments)
    table_texts = [t for t, is_table in segments if is_table]
    assert len(table_texts) == 1
    assert table_texts[0].startswith("| 列A")


def test_single_pipe_row_is_not_a_table():
    # 单行 | 前缀（普通文本中的竖线）不足阈值，不应判为表格
    md = "这是正文 | 带竖线\n下一行普通文本\n"
    segments = split_table_blocks(md)
    assert all(not is_table for _, is_table in segments)


def test_table_requires_min_rows():
    md = "| 仅一行 |\n正文\n"
    segments = split_table_blocks(md, min_table_rows=2)
    assert all(not is_table for _, is_table in segments)
    segments1 = split_table_blocks(md, min_table_rows=1)
    assert any(is_table for _, is_table in segments1)


def test_multiple_tables_split_separately():
    md = (
        "| A1 |\n| A2 |\n"
        "间隔文本\n"
        "| B1 |\n| B2 |\n"
    )
    table_texts = [t for t, is_table in split_table_blocks(md) if is_table]
    assert len(table_texts) == 2
    assert table_texts[0].startswith("| A1")
    assert table_texts[1].startswith("| B1")


# ── strip_toc_noise ───────────────────────────────────────

def test_toc_dot_leader_line_removed():
    md = "招标投标法实施条例⋯⋯⋯⋯⋯⋯⋯⋯(82)\n正文内容\n"
    cleaned = strip_toc_noise(md)
    assert "实施条例⋯" not in cleaned
    assert "正文内容" in cleaned


def test_body_ellipsis_kept():
    md = "本条所称…由国务院规定。\n正文\n"
    cleaned = strip_toc_noise(md)
    assert "由国务院规定" in cleaned


def test_toc_long_line_kept():
    # 超过阈值的长行即使带点线+页码也不应误删
    long_line = "这是一段很长的正文" * 20 + "⋯⋯(999)"
    cleaned = strip_toc_noise(long_line)
    assert "⋯⋯(999)" in cleaned


# ── detect_reflow_suspect ─────────────────────────────────

def test_reflow_suspect_detected():
    # 真实条文号序列：双栏乱序顺读后单块堆积 12+ 个条文号 → 打标
    scrambled = "".join(f"第{i}条 " for i in range(1, 16)) + " 内容"
    assert detect_reflow_suspect(scrambled) is True


def test_reflow_suspect_absent_for_normal_text():
    assert detect_reflow_suspect("普通文本，无条文号。") is False
    assert detect_reflow_suspect("") is False


# ── adapt_pdf_markdown ────────────────────────────────────

def _chunker() -> SemanticChunker:
    return SemanticChunker(max_chars=2000, overlap_chars=100)


def test_adapt_keeps_table_as_single_atomic_document():
    md = (
        "## 第一条 示例条文。\n"
        "本条款内容。\n"
        "| 项目 | 金额 |\n"
        "| --- | --- |\n"
        "| 甲 | 100 |\n"
    )
    docs = adapt_pdf_markdown(md, "test.pdf", _chunker())
    table_docs = [d for d in docs if d.metadata.get("content_type") == "table"]
    assert len(table_docs) == 1
    # 原子块保证：整个表格作为一个 Document，未被句子拆分
    assert table_docs[0].page_content.startswith("| 项目 | 金额 |")
    assert "| 甲 | 100 |" in table_docs[0].page_content
    assert table_docs[0].metadata["chapter"] == "表格块"


def test_adapt_keeps_body_inline_after_article_heading():
    # `## 第一条 正文…` 是 cleaned_v1 / 电子书常见格式：
    # 若不拆分，正文会随标题行被 SemanticChunker 丢弃
    md = "## 第一条 为了规范招标投标活动，制定本法。\n"
    docs = adapt_pdf_markdown(md, "test.pdf", _chunker())
    joined = "\n".join(d.page_content for d in docs)
    assert "为了规范招标投标活动" in joined


def test_adapt_removes_toc_and_chunks_text():
    md = (
        "招标投标法实施条例⋯⋯⋯⋯(82)\n"
        "## 第一章 总则\n"
        "## 第一条 为了规范招标投标活动，制定本法。\n"
    )
    docs = adapt_pdf_markdown(md, "test.pdf", _chunker())
    joined = "\n".join(d.page_content for d in docs)
    assert "实施条例⋯" not in joined
    assert "为了规范招标投标活动" in joined


def test_adapt_toggles_off_preserve_behavior():
    md = "招标投标法实施条例⋯⋯⋯⋯(82)\n正文\n"
    docs_off = adapt_pdf_markdown(
        md, "test.pdf", _chunker(), enable_toc_filter=False
    )
    assert any("实施条例⋯" in d.page_content for d in docs_off)


def test_adapt_marks_reflow_suspect_blocks():
    # 真实条文号序列堆积 → 触发打标（见 detect_reflow_suspect）
    scrambled = "".join(f"第{i}条 " for i in range(1, 16)) + " 内容"
    md = f"## 第一章 总则\n{scrambled}\n"
    docs = adapt_pdf_markdown(md, "test.pdf", _chunker())
    flagged = [d for d in docs if d.metadata.get("pdf_layout_suspect")]
    assert flagged, "应至少打标一个疑似双栏乱序块"
    unflagged = adapt_pdf_markdown(
        md, "test.pdf", _chunker(), enable_reflow_flag=False
    )
    assert not any(d.metadata.get("pdf_layout_suspect") for d in unflagged)
