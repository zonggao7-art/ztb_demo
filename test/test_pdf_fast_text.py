"""L2 快路径与双栏重建单元测试（不依赖 pymupdf）。"""

from __future__ import annotations

from public_kb.ingestion.transforms.pdf_fast_text import (
    TextLine,
    generate_markdown,
    remove_header_footer,
    remove_page_numbers,
    sort_single_column,
)
from public_kb.ingestion.transforms.pdf_two_column_reflow import (
    is_full_width_line,
    reflow_two_columns,
)


def _line(text, y0, x0=0, x1=100, size=10, bold=False, centered=False):
    return TextLine(
        text=text, x0=x0, y0=y0, x1=x1, y1=y0 + 12,
        size=size, bold=bold, centered=centered,
    )


# ── 单栏排序 ─────────────────────────────────────────────

def test_sort_single_column_by_y_then_x():
    a = _line("第一行", y0=100, x0=10)
    b = _line("第二行", y0=200, x0=10)
    c = _line("同 y 左", y0=200, x0=5)
    ordered = sort_single_column([b, a, c])
    assert [ln.text for ln in ordered] == ["第一行", "同 y 左", "第二行"]


# ── 页码剔除 ─────────────────────────────────────────────

def test_page_number_removed():
    lines = [_line("12", y0=10), _line("正文", y0=50)]
    assert [ln.text for ln in remove_page_numbers(lines)] == ["正文"]


def test_non_number_kept():
    lines = [_line("12号文", y0=10), _line("正文", y0=50)]
    assert len(remove_page_numbers(lines)) == 2


# ── 页眉页脚剔除（法律标题保留） ─────────────────────────

def test_header_footer_removed_by_position():
    h = _line("出版社名称", y0=5)
    body = _line("正文内容", y0=400)
    f = _line("页脚", y0=780)
    kept = remove_header_footer([h, body, f], page_height=792)
    assert [ln.text for ln in kept] == ["正文内容"]


def test_legal_heading_in_header_zone_kept():
    h = _line("第一章 总则", y0=5)
    body = _line("正文", y0=400)
    kept = remove_header_footer([h, body], page_height=792)
    assert "第一章 总则" in [ln.text for ln in kept]


# ── Markdown 标题生成 ────────────────────────────────────

def test_generate_markdown_heading_levels():
    lines = [
        _line("第一章 总则", y0=10, size=16, centered=True),
        _line("第一条 条文内容", y0=30, size=12),
        _line("一、列表项", y0=50, size=10),
        _line("正文段落", y0=70, size=10),
    ]
    md = generate_markdown(lines)
    assert "## 第一章 总则" in md
    assert "#### 第一条 条文内容" in md
    assert "#### 一、列表项" in md
    assert "正文段落" in md


# ── 双栏列序重建 ─────────────────────────────────────────

def test_reflow_left_then_right():
    left1 = _line("左栏第一条", y0=10, x0=50, x1=150)
    right1 = _line("右栏第一条", y0=10, x0=300, x1=400)
    left2 = _line("左栏第二条", y0=30, x0=50, x1=150)
    right2 = _line("右栏第二条", y0=30, x0=300, x1=400)
    result = reflow_two_columns(
        [right1, left1, right2, left2], split_x=225, page_width=500
    )
    assert [ln.text for ln in result.lines] == [
        "左栏第一条", "左栏第二条", "右栏第一条", "右栏第二条",
    ]
    assert result.n_left == 2
    assert result.n_right == 2


def test_reflow_full_width_title_first_and_once():
    title = _line("跨栏标题", y0=5, x0=10, x1=490, size=16)
    left1 = _line("左栏内容", y0=20, x0=50, x1=150)
    right1 = _line("右栏内容", y0=20, x0=300, x1=400)
    result = reflow_two_columns(
        [right1, title, left1], split_x=225, page_width=500
    )
    assert result.lines[0].text == "跨栏标题"
    assert result.n_full_width == 1
    # 跨栏标题只出现一次
    assert [ln.text for ln in result.lines].count("跨栏标题") == 1


def test_is_full_width_line_threshold():
    assert is_full_width_line(_line("宽", y0=10, x0=10, x1=490), page_width=500, ratio=0.7) is True
    assert is_full_width_line(_line("窄", y0=10, x0=50, x1=150), page_width=500, ratio=0.7) is False
