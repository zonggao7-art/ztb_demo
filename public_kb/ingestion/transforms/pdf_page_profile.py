# 功能：页面级特征提取（三档路由 T1）— 对 PDF 页面做轻量画像。
"""
Page-level feature extraction for tiered PDF routing (T1).

只做特征抽取，不做分类。输入为"鸭子类型"的页面对象（生产环境是
PyMuPDF `Page`，单测用 fake 页面对象），因此本模块不强依赖 pymupdf，
离线 ingestion 之外不受影响。

特征（与三档路由计划 §6 T1 对应）：
  text_chars / n_blocks / x_starts / 双栏间隙 / 表格线计数 / 内容图片占比 /
  条文号密度 / 公式特征提示。

全页背景图（水印 / OCR 底图）不计入内容图片占比——这类图 bbox 覆盖接近
整页，会误判"图片密集页"，已在 L0 采样时发现并修正。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Sequence, Tuple

_ARTICLE_RE = re.compile(r"第[一二三四五六七八九十百千\d]+[章节条款]")


@dataclass(frozen=True)
class PageProfile:
    """一页 PDF 的轻量画像。"""

    page_idx: int
    width: float
    height: float
    text_chars: int
    n_blocks: int
    x_starts: Tuple[float, ...]
    two_col_gap: float          # 最大的 block 水平间隙（无则 0）
    two_col_split_x: float      # 该间隙的中心 x 坐标（用于左右栏切分；无则 0）
    has_two_col: bool
    table_hlines: int
    table_vlines: int
    table_candidate: bool
    img_ratio: float            # 内容图片面积 / 页面积（背景图已剔除）
    article_count: int
    formula_hint: bool = False
    fonts: Tuple[str, ...] = ()


def build_page_profile(
    page: Any,
    page_idx: int,
    *,
    background_img_ratio: float = 0.85,
    two_col_gap_ratio: float = 0.15,
    table_min_lines: int = 5,
) -> PageProfile:
    """从页面对象抽取特征，返回 PageProfile。

    Args:
        page: 鸭子类型页面对象，需支持 `get_text`、`get_drawings`、
            `get_image_info`（与 PyMuPDF Page 一致）。
        page_idx: 0 基页号。
        background_img_ratio: 图片 bbox 覆盖超过该页面积比 → 视为背景图剔除。
        two_col_gap_ratio: block 水平间隙 > 页宽 * 该值 → 判为双栏间隙。
        table_min_lines: 横线或竖线任一达到该数量 → 表格候选。
    """
    rect = getattr(page, "rect", None)
    width = float(getattr(rect, "width", 0) or 0)
    height = float(getattr(rect, "height", 0) or 0)

    text = page.get_text("text") or ""
    text_chars = len(text.strip())

    blocks = _text_blocks(page)
    x_starts = tuple(sorted({round(float(b[0]), 1) for b in blocks if len(b) >= 4}))

    two_col_gap, two_col_split_x = _max_gap(x_starts, width * two_col_gap_ratio)
    has_two_col = two_col_gap > 0

    table_hlines, table_vlines = _count_table_lines(page)
    table_candidate = table_hlines >= table_min_lines or table_vlines >= table_min_lines

    img_ratio = _content_img_ratio(page, width, height, background_img_ratio)

    article_count = len(_ARTICLE_RE.findall(text))

    fonts = _extract_fonts(page)
    formula_hint = any(
        ("math" in f.lower()) or ("symbol" in f.lower()) for f in fonts
    )

    return PageProfile(
        page_idx=page_idx,
        width=width,
        height=height,
        text_chars=text_chars,
        n_blocks=len(blocks),
        x_starts=x_starts,
        two_col_gap=round(two_col_gap, 1),
        two_col_split_x=round(two_col_split_x, 1),
        has_two_col=has_two_col,
        table_hlines=table_hlines,
        table_vlines=table_vlines,
        table_candidate=table_candidate,
        img_ratio=round(img_ratio, 3),
        article_count=article_count,
        formula_hint=formula_hint,
        fonts=fonts,
    )


def _text_blocks(page: Any) -> list:
    raw = page.get_text("blocks") or []
    return [b for b in raw if _block_type(b) == 0]


def _block_type(block: Any) -> int:
    """兼容 PyMuPDF 7 元组块 (x0,y0,x1,y1,text,block_no,block_type)。"""
    return int(block[6]) if isinstance(block, (tuple, list)) and len(block) >= 7 else 0


def _max_gap(x_starts: Sequence[float], threshold: float) -> tuple[float, float]:
    """返回 (最大水平间隙, 该间隙中心 x 坐标)；无满足阈值的间隙时返回 (0, 0)。"""
    best_gap = 0.0
    split_x = 0.0
    for i in range(len(x_starts) - 1):
        gap = x_starts[i + 1] - x_starts[i]
        if gap >= threshold and gap > best_gap:
            best_gap = gap
            split_x = (x_starts[i + 1] + x_starts[i]) / 2.0
    return best_gap, split_x


def _count_table_lines(page: Any) -> tuple[int, int]:
    h = v = 0
    for drawing in page.get_drawings() or []:
        for item in drawing.get("items", []):
            if item[0] == "l":
                p1, p2 = item[1], item[2]
                if abs(p1.y - p2.y) < 0.5:
                    h += 1
                elif abs(p1.x - p2.x) < 0.5:
                    v += 1
    return h, v


def _content_img_ratio(
    page: Any, width: float, height: float, background_img_ratio: float
) -> float:
    if not width or not height:
        return 0.0
    page_area = width * height
    content_area = 0.0
    for info in page.get_image_info() or []:
        bbox = info.get("bbox")
        if not bbox:
            continue
        area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
        if page_area and area / page_area >= background_img_ratio:
            continue  # 全页背景图（水印/OCR 底图）
        content_area += area
    return content_area / page_area


def _extract_fonts(page: Any) -> Tuple[str, ...]:
    fonts = set()
    try:
        d = page.get_text("dict") or {}
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    font = span.get("font")
                    if font:
                        fonts.add(str(font))
    except Exception:
        pass
    return tuple(sorted(fonts))
