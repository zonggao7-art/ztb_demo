"""T4 Markdown 装配层单元测试（不依赖 pymupdf / MinerU）。"""

from __future__ import annotations

from public_kb.ingestion.transforms.pdf_legal_page_classifier import PageRouteDecision
from public_kb.ingestion.transforms.pdf_markdown_assembler import (
    PageBlock,
    ParsedPage,
    assemble_page_blocks,
    assemble_pages,
    build_manifest,
    clean_assembled_document,
    normalize_heading_levels,
    parse_table_placeholder,
    table_placeholder,
)


def _route(tier="A", label="text", parser="fast_text", page_idx=0) -> PageRouteDecision:
    return PageRouteDecision(
        page_idx=page_idx, page_label=label, tier=tier,
        reason="test", confidence=0.9, parser=parser, features={},
    )


def _page(idx, markdown="", parser="fast_text", tier="A", label="text", blocks=()):
    return ParsedPage(
        page_idx=idx, markdown=markdown, parser=parser,
        route=_route(tier=tier, label=label, parser=parser, page_idx=idx),
        blocks=tuple(blocks),
    )


# ── 表格占位标签 ─────────────────────────────────────────

def test_table_placeholder_roundtrip():
    tag = table_placeholder(11, "t11_0", (77, 312, 535, 510))
    parsed = parse_table_placeholder(tag)
    assert parsed == {"page": 12, "id": "t11_0", "bbox": (77.0, 312.0, 535.0, 510.0)}


def test_parse_non_table_line_returns_none():
    assert parse_table_placeholder("正文内容") is None


# ── 页内块排序（图文表混排） ────────────────────────────

def test_assemble_page_blocks_orders_by_y_and_marks_table():
    text_above = PageBlock(order_key=100, kind="text", content="表格上方的文字")
    table = PageBlock(
        order_key=200, kind="table", content="| 项目 | 金额 |\n| --- | --- |\n| 甲 | 100 |",
        bbox=(77, 312, 535, 510),
    )
    text_below = PageBlock(order_key=300, kind="text", content="表格下方的文字")
    out = assemble_page_blocks(0, [text_below, table, text_above])
    assert out.index("表格上方的文字") < out.index("| 项目")
    assert out.index("| 项目") < out.index("表格下方的文字")
    assert "<!-- table: page=1, id=t0_0, bbox=" in out


def test_assemble_page_blocks_table_not_split():
    table_content = "| 项目 | 金额 |\n| --- | --- |\n| 甲 | 100 |"
    table = PageBlock(order_key=100, kind="table", content=table_content, bbox=(0, 0, 10, 10))
    out = assemble_page_blocks(0, [table])
    assert table_content in out  # 表格整体保留，未被拆散


# ── 页序缝合（穿插还原） ────────────────────────────────

def test_assemble_pages_restores_interleaved_order():
    # 前 2 页快路径、中间 1 页 MinerU、后面 1 页快路径 —— 乱序输入
    pages = [
        _page(2, "第三页 MinerU 内容", parser="mineru", tier="C", label="table_complex"),
        _page(0, "第一页文本"),
        _page(1, "第二页文本"),
        _page(3, "第四页文本"),
    ]
    out = assemble_pages(pages)
    # 顺序还原：第一页 → 第二页 → 第三页 → 第四页
    assert out.index("第一页文本") < out.index("第二页文本")
    assert out.index("第二页文本") < out.index("第三页 MinerU 内容")
    assert out.index("第三页 MinerU 内容") < out.index("第四页文本")


def test_assemble_pages_keeps_page_markers():
    pages = [_page(0, "内容A"), _page(1, "内容B")]
    out = assemble_pages(pages, page_marker=True)
    assert "<!-- page: 1 -->" in out
    assert "<!-- page: 2 -->" in out


# ── 标题级别规范化 ───────────────────────────────────────

def test_normalize_heading_levels_legal():
    md = "### 第一章 总则\n##### 第一条 条文\n正文"
    out = normalize_heading_levels(md)
    assert "## 第一章 总则" in out
    assert "#### 第一条 条文" in out


def test_normalize_heading_levels_non_legal_unchanged():
    md = "## 普通标题\n正文"
    assert normalize_heading_levels(md) == md


# ── 页眉页脚/目录去重 ────────────────────────────────────

def test_clean_document_removes_toc_and_repeated_noise_keeps_legal():
    md = (
        "招标投标法实施条例⋯⋯⋯⋯(82)\n"
        + "\n".join(["机械工业出版社"] * 6)
        + "\n## 第一章 总则\n正文内容\n"
    )
    out = clean_assembled_document(md)
    assert "实施条例⋯" not in out
    assert "机械工业出版社" not in out
    assert "第一章 总则" in out
    assert "正文内容" in out


# ── manifest ─────────────────────────────────────────────

def test_build_manifest_summary():
    pages = [
        _page(0, "文本", parser="fast_text", tier="A", label="text"),
        _page(1, "表格", parser="mineru", tier="C", label="table_complex"),
        _page(2, "文本", parser="fast_text", tier="A", label="text"),
    ]
    m = build_manifest(pages, parser_version="2.0.x")
    assert m["summary"]["total_pages"] == 3
    assert m["summary"]["tier_distribution"] == {"A": 2, "C": 1}
    assert m["summary"]["parser_distribution"] == {"fast_text": 2, "mineru": 1}
    assert m["pages"][1]["parser"] == "mineru"
    assert m["pages"][1]["reason"] == "test"
