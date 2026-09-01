# 功能：法规书结构化快路径文本抽取（三档路由 T2A）。
"""
Fast-path structured text extraction for legal PDFs (T2A).

输入为鸭子类型页面对象（生产环境是 PyMuPDF `Page`，单测用 fake 页面），
不强制依赖 pymupdf。职责（对齐三档路由计划 §6 T2A）：
  1. 从 `get_text("dict")` 提取行级文本 + 字号/加粗/居中特征；
  2. 剔除独立页码；
  3. 按页面位置剔除页眉/页脚（法律标题即使位于页眉区也保留，避免误删章节）；
  4. 单栏按 (y, x) 稳定排序；
  5. 依据字号 + 居中 + 法规标题模式生成 Markdown 标题。
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence

# 独立页码：如 "12"
_PAGE_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$")

# 法规标题模式（含级别映射）
_LEGAL_HEADING_PATTERNS = (
    (re.compile(r"^第[一二三四五六七八九十百千\d]+[编章]"), 2),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+节"), 3),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+[条款项]"), 4),
)

# 中文列表项：一、 / 二、 等
_CN_LIST_RE = re.compile(r"^[一二三四五六七八九十]+、")
# 括号列表项：（一）/（二）等
_CN_PAREN_LIST_RE = re.compile(r"^（[一二三四五六七八九十]+）")
# 数字编号（1200 问体例）：55. / 55、
_NUM_ITEM_RE = re.compile(r"^\d{1,4}[.、]")
# 答案起始行：答：/ 答
_ANSWER_RE = re.compile(r"^答\s*[:：]?")


@dataclass(frozen=True)
class TextLine:
    """一行文本及其版面特征。"""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float
    bold: bool
    centered: bool


def iter_lines(page: Any) -> List[TextLine]:
    """从页面 dict 提取行级文本特征（合并 span）。"""
    d = page.get_text("dict") or {}
    width = float(d.get("width") or 0)
    lines: List[TextLine] = []
    for block in d.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            text = "".join(str(s.get("text", "")) for s in spans)
            if not text.strip():
                continue
            sizes = [float(s.get("size", 0) or 0) for s in spans]
            size = max(sizes) if sizes else 0.0
            bold = any(int(s.get("flags", 0)) & 16 for s in spans)
            bbox = line.get("bbox") or (0, 0, 0, 0)
            x0, y0, x1, y1 = (float(v) for v in bbox[:4])
            center_x = (x0 + x1) / 2.0
            centered = bool(width) and abs(center_x - width / 2.0) / max(width, 1.0) < 0.06
            lines.append(TextLine(
                text=text.strip(), x0=x0, y0=y0, x1=x1, y1=y1,
                size=size, bold=bold, centered=centered,
            ))
    return lines


def remove_page_numbers(lines: Sequence[TextLine]) -> List[TextLine]:
    """剔除独立页码行。"""
    return [ln for ln in lines if not _PAGE_NUMBER_RE.match(ln.text)]


def remove_header_footer(
    lines: Sequence[TextLine],
    page_height: float,
    *,
    top_ratio: float = 0.12,
    bottom_ratio: float = 0.08,
) -> List[TextLine]:
    """按页面位置剔除页眉/页脚；法律标题即使位于页眉区也保留。"""
    if not page_height:
        return list(lines)
    kept: List[TextLine] = []
    for ln in lines:
        in_top = ln.y0 < page_height * top_ratio
        in_bottom = ln.y0 > page_height * (1 - bottom_ratio)
        if (in_top or in_bottom) and not _is_legal_heading(ln.text):
            continue
        kept.append(ln)
    return kept


def sort_single_column(lines: Sequence[TextLine]) -> List[TextLine]:
    """单栏阅读顺序：按 y 升序，同 y 按 x 升序。"""
    return sorted(lines, key=lambda ln: (ln.y0, ln.x0))


def _is_legal_heading(text: str) -> bool:
    for pattern, _ in _LEGAL_HEADING_PATTERNS:
        if pattern.match(text):
            return True
    return False


def _heading_level(text: str, size: float, body_size: float, centered: bool) -> Optional[int]:
    """返回 Markdown 标题级别（1-6），非标题返回 None。"""
    for pattern, level in _LEGAL_HEADING_PATTERNS:
        if pattern.match(text):
            return level
    if _CN_LIST_RE.match(text):
        return 4
    if _CN_PAREN_LIST_RE.match(text):
        return 5
    if _NUM_ITEM_RE.match(text):
        return 4
    if body_size > 0:
        if centered and size >= body_size * 1.2:
            return 2
        if size >= body_size * 1.15:
            return 3
    return None


def generate_markdown(lines: Sequence[TextLine]) -> str:
    """把行列表转为轻量 Markdown（标题行加 #，正文行原样保留）。"""
    sizes = [ln.size for ln in lines if ln.size > 0]
    body_size = statistics.median(sizes) if sizes else 0.0

    out: List[str] = []
    for ln in lines:
        text = ln.text
        level = _heading_level(text, ln.size, body_size, ln.centered)
        if level is not None:
            out.append(f"{'#' * level} {text}")
        else:
            out.append(text)
    return "\n".join(out)


def extract_page_markdown(page: Any, page_idx: int) -> str:
    """快路径单页抽取：提取 → 去页码 → 去页眉页脚 → 排序 → 生成 Markdown。"""
    d = page.get_text("dict") or {}
    page_height = float(d.get("height") or 0)
    lines = iter_lines(page)
    lines = remove_page_numbers(lines)
    lines = remove_header_footer(lines, page_height)
    lines = sort_single_column(lines)
    return generate_markdown(lines)
