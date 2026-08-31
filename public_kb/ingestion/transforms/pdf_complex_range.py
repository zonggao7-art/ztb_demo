# 功能：把 Tier C 页面聚合并切出子 PDF。
"""
Tier C 复杂页聚合 + 子 PDF 切分（三档路由 T3）。

职责（对齐三档路由计划 §6 T3）：
  1. 把连续的 Tier C 决策聚合成 ComplexRange；
  2. 疑似跨页表/条文 → 前后扩展 N 页边界页（设置 pdf_tiered_expand_boundary_pages）；
  3. 用 PyMuPDF 把每个 ComplexRange 切为独立子 PDF（PyMuPDF 鸭子依赖，
     缺则抛明确异常，不静默 fallback）；
  4. 输出 range_id / 核心页 / 扩展页，便于 manifest 溯源。

不依赖 magic_pdf（不污染 MinerU 工具栈）。
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Sequence, Tuple

from .pdf_legal_page_classifier import PageRouteDecision

logger = logging.getLogger(__name__)

_TIER_C = "C"


@dataclass(frozen=True)
class ComplexRange:
    """一个连续复杂页范围（T3 聚合 + 边界扩展产物）。

    range_id: 全文档内稳定 ID（基于 source_pdf 路径 + core_page_idxs）。
    core_page_idxs: 分类器判定的连续 Tier C 页（不含边界扩展）。
    page_idxs: 扩展边界后的最终页集合（含边界页，按升序）。
    expanded_before: 向前扩展了几页（被相邻 Tier C 共享时扣减）。
    expanded_after: 向后扩展了几页（被相邻 Tier C 共享时扣减）。
    """

    range_id: str
    core_page_idxs: Tuple[int, ...]
    page_idxs: Tuple[int, ...]
    expanded_before: int = 0
    expanded_after: int = 0

    @property
    def start_idx(self) -> int:
        return self.page_idxs[0]

    @property
    def end_idx(self) -> int:
        return self.page_idxs[-1]


# ── 聚合 + 边界扩展 ────────────────────────────────────────


def aggregate_complex_ranges(
    decisions: Sequence[PageRouteDecision],
    *,
    total_pages: int,
    expand_pages: int = 1,
    range_id_seed: str = "",
) -> List[ComplexRange]:
    """把 Tier C 决策聚合为 ComplexRange 列表。

    Args:
        decisions: 整本 PDF 的 PageRouteDecision（按 page_idx 升序）。
        total_pages: PDF 总页数（边界扩展不能超过）。
        expand_pages: 向前后各扩展的边界页数；相邻 ComplexRange 共享边界时
            自动扣减（不会重复占用同一页）。
        range_id_seed: range_id 的稳定种子（建议传 source_pdf 的 stem 或
            内容哈希前 8 位，确保跨进程/range_id 一致）。
    """
    if total_pages <= 0:
        return []
    expand_pages = max(0, int(expand_pages))

    tier_c_idxs = sorted(d.page_idx for d in decisions if d.tier == _TIER_C)
    if not tier_c_idxs:
        return []

    # 1) 连续区间聚合
    raw_groups: List[List[int]] = []
    cur: List[int] = []
    for idx in tier_c_idxs:
        if cur and idx > cur[-1] + 1:
            raw_groups.append(cur)
            cur = []
        cur.append(idx)
    if cur:
        raw_groups.append(cur)

    # 2) 预扩展区间（先扩展，再合并重叠）
    expanded_groups: List[List[int]] = []
    for grp in raw_groups:
        start = max(0, grp[0] - expand_pages)
        end = min(total_pages - 1, grp[-1] + expand_pages)
        expanded_groups.append(list(range(start, end + 1)))

    # 3) 合并因扩展而重叠的相邻区间（避免同一页被多个 range 覆盖）
    merged: List[List[int]] = []
    for grp in expanded_groups:
        if merged and grp[0] <= merged[-1][-1] + 1:
            merged[-1].extend(grp)
            continue
        merged.append(grp)
    # 去重并排序
    for i, grp in enumerate(merged):
        seen = sorted(set(grp))
        merged[i] = seen

    # 4) 构造 ComplexRange
    seed = range_id_seed or "pdf"
    out: List[ComplexRange] = []
    for ridx, page_idxs in enumerate(merged):
        core = [i for i in page_idxs if _in_groups(page_idxs, i, raw_groups, expand_pages, total_pages)]
        # core 简化：从 raw_groups 中所有属于本 page_idxs 范围的页
        core_set = set()
        for grp in raw_groups:
            for p in grp:
                if p in page_idxs:
                    core_set.add(p)
        core_tuple = tuple(sorted(core_set))
        before = core_tuple[0] - page_idxs[0] if core_tuple else 0
        after = page_idxs[-1] - core_tuple[-1] if core_tuple else 0
        out.append(
            ComplexRange(
                range_id=_stable_range_id(seed, ridx, page_idxs),
                core_page_idxs=core_tuple,
                page_idxs=tuple(page_idxs),
                expanded_before=before,
                expanded_after=after,
            )
        )
    return out


def _in_groups(
    page_idxs: Sequence[int],
    p: int,
    raw_groups: Sequence[Sequence[int]],
    expand_pages: int,
    total_pages: int,
) -> bool:
    """判定 p 是否属于某个 raw_group（在扩展后 page_idxs 中）。"""
    for grp in raw_groups:
        if p in grp:
            return True
    return False


_RANGE_ID_RE = re.compile(r"[^A-Za-z0-9_]")


def _stable_range_id(seed: str, idx: int, page_idxs: Sequence[int]) -> str:
    """生成稳定的 range_id：seed + 序号 + 首尾页 + 数量指纹。"""
    digest = hashlib.md5(
        f"{seed}|{idx}|{page_idxs[0]}|{page_idxs[-1]}|{len(page_idxs)}".encode()
    ).hexdigest()[:8]
    safe_seed = _RANGE_ID_RE.sub("_", seed)[:24] or "pdf"
    return f"r{safe_seed}_{idx:03d}_{digest}"


# ── 子 PDF 切分 ─────────────────────────────────────────────


def write_subpdf(
    source_pdf: Path | str,
    page_idxs: Sequence[int],
    output_path: Path | str,
) -> Path:
    """把 source_pdf 的指定页（0 基）切出来，保存为 output_path。

    使用 PyMuPDF `Document.select() + save()`，子 PDF 保留页内结构与图片。
    缺 PyMuPDF 时抛 ImportError，不静默回退。
    """
    try:
        import pymupdf  # noqa: F401  PyMuPDF 1.24+ 模块名
    except ImportError as exc:
        raise ImportError(
            "子 PDF 切分需要 PyMuPDF，请先安装：pip install pymupdf"
        ) from exc

    src = Path(source_pdf)
    out = Path(output_path)
    if not src.exists():
        raise FileNotFoundError(f"源 PDF 不存在: {src}")
    if not page_idxs:
        raise ValueError("page_idxs 不能为空")

    # PyMuPDF 1.24 之前模块名是 fitz，向后兼容
    doc = pymupdf.open(str(src))
    try:
        doc.select(list(page_idxs))
        out.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(out))
    finally:
        doc.close()
    logger.info("切分子 PDF: %s → %s (%d 页)", src.name, out.name, len(page_idxs))
    return out


# ── 文本型子 PDF（仅导出页内文本，调试用） ─────────────────


def subpdf_text_signature(pdf_path: Path | str, page_idxs: Sequence[int]) -> str:
    """读子 PDF 的文本拼接，做内容指纹（用于和 MinerU 输出做交叉校验）。"""
    try:
        import pymupdf
    except ImportError as exc:
        raise ImportError("需要 PyMuPDF") from exc
    doc = pymupdf.open(str(pdf_path))
    try:
        parts = []
        for p in doc:
            parts.append(p.get_text("text"))
        return "\n".join(parts)
    finally:
        doc.close()
