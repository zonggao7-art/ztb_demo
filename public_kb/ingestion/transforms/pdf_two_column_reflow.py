# 功能：双栏条文页列序重建（三档路由 T2A）。
"""
Two-column reading-order reflow for legal text pages (T2A).

只做"阅读顺序重建"：把双栏页的行按左栏优先、右栏次之的顺序输出，
并处理跨栏标题（横跨两栏、居中、字号偏大的标题不重复输出）。
不负责抽取（抽取由 pdf_fast_text.iter_lines 完成），本模块只排序。

原则（对齐三档路由计划 §6 T2A / §10）：
  1. 用栏间隙中心 x 切分左右（来自 PageProfile.two_col_split_x），
     不用固定 0.49/0.51 页宽比例；
  2. 跨栏标题（宽度接近页宽、居中）先于左右栏整体输出一次；
  3. 页眉页脚/页码已在 pdf_fast_text 阶段剔除，本模块不再重复处理。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

from .pdf_fast_text import TextLine


@dataclass(frozen=True)
class ReflowResult:
    """双栏重建后的行序 + 诊断信息。"""

    lines: List[TextLine]
    n_left: int
    n_right: int
    n_full_width: int


def is_full_width_line(line: TextLine, page_width: float, ratio: float = 0.7) -> bool:
    """判断是否为跨栏通栏行（如跨栏标题）。"""
    if not page_width:
        return False
    return (line.x1 - line.x0) / page_width >= ratio


def reflow_two_columns(
    lines: Sequence[TextLine],
    *,
    split_x: float,
    page_width: float,
    full_width_ratio: float = 0.7,
) -> ReflowResult:
    """按左栏优先重建双栏阅读顺序。

    Args:
        lines: 按 y 升序排序前的行列表（内部会先按 y 排序）。
        split_x: 栏间隙中心 x 坐标（来自 PageProfile.two_col_split_x）。
        page_width: 页宽。
        full_width_ratio: 行宽/页宽超过该值 → 视为跨栏通栏行。

    Returns:
        ReflowResult：重建后的行序 + 左右栏与通栏行计数。
    """
    ordered = sorted(lines, key=lambda ln: (ln.y0, ln.x0))
    left: List[TextLine] = []
    right: List[TextLine] = []
    full: List[TextLine] = []

    for ln in ordered:
        if is_full_width_line(ln, page_width, full_width_ratio):
            full.append(ln)
        elif ln.x0 < split_x:
            left.append(ln)
        else:
            right.append(ln)

    # 通栏行（跨栏标题）放在最前，随后左栏、右栏
    return ReflowResult(
        lines=full + left + right,
        n_left=len(left),
        n_right=len(right),
        n_full_width=len(full),
    )


def reflow_page_markdown(
    lines: Sequence[TextLine],
    *,
    split_x: float,
    page_width: float,
    full_width_ratio: float = 0.7,
) -> str:
    """双栏重建后转 Markdown（复用 fast_text 的标题生成）。"""
    from .pdf_fast_text import generate_markdown

    result = reflow_two_columns(
        lines, split_x=split_x, page_width=page_width, full_width_ratio=full_width_ratio
    )
    return generate_markdown(result.lines)
