"""L4 — pdf_complex_range 单元测试（不依赖 pymupdf/magic-pdf）。"""

from __future__ import annotations

from public_kb.ingestion.transforms.pdf_complex_range import (
    aggregate_complex_ranges,
)
from public_kb.ingestion.transforms.pdf_legal_page_classifier import (
    PageRouteDecision,
)


def _d(page_idx: int, tier: str, label: str = "visual_or_scan") -> PageRouteDecision:
    """构造一个最简单的 PageRouteDecision 用于聚合测试。"""
    return PageRouteDecision(
        page_idx=page_idx,
        page_label=label,
        tier=tier,
        reason="test",
        confidence=0.9,
        parser="mineru" if tier == "C" else "fast_text",
        features={},
    )


def test_no_tier_c_returns_empty():
    decisions = [_d(0, "A"), _d(1, "A"), _d(2, "A")]
    assert aggregate_complex_ranges(decisions, total_pages=10) == []


def test_single_tier_c_creates_one_range():
    decisions = [_d(3, "C")]
    ranges = aggregate_complex_ranges(decisions, total_pages=10)
    assert len(ranges) == 1
    r = ranges[0]
    assert r.core_page_idxs == (3,)
    assert r.page_idxs == (3,)
    assert r.expanded_before == 0
    assert r.expanded_after == 0


def test_consecutive_tier_c_aggregated():
    decisions = [_d(3, "C"), _d(4, "C"), _d(5, "C")]
    ranges = aggregate_complex_ranges(decisions, total_pages=10)
    assert len(ranges) == 1
    r = ranges[0]
    assert r.core_page_idxs == (3, 4, 5)
    assert r.page_idxs == (3, 4, 5)


def test_non_consecutive_split_into_two():
    decisions = [_d(2, "C"), _d(3, "C"), _d(7, "C"), _d(8, "C")]
    ranges = aggregate_complex_ranges(decisions, total_pages=10)
    assert len(ranges) == 2
    assert ranges[0].core_page_idxs == (2, 3)
    assert ranges[1].core_page_idxs == (7, 8)


def test_boundary_expansion_capped_to_doc():
    decisions = [_d(0, "C"), _d(1, "C")]
    ranges = aggregate_complex_ranges(decisions, total_pages=5, expand_pages=3)
    r = ranges[0]
    # expand_pages=3 但已到文档边界；只能扩展到 [0,4]
    assert r.page_idxs == (0, 1, 2, 3, 4)
    assert r.expanded_before == 0
    assert r.expanded_after == 3


def test_expand_does_not_dup_or_overflow():
    decisions = [_d(5, "C")]
    ranges = aggregate_complex_ranges(decisions, total_pages=10, expand_pages=2)
    r = ranges[0]
    assert r.page_idxs == (3, 4, 5, 6, 7)
    assert r.expanded_before == 2
    assert r.expanded_after == 2


def test_expand_overlapping_ranges_merged():
    """两个相邻 Tier C 段间隔很小（≤ 2*expand_pages），应被合并。"""
    decisions = [_d(2, "C"), _d(3, "C"), _d(6, "C"), _d(7, "C")]
    ranges = aggregate_complex_ranges(decisions, total_pages=10, expand_pages=2)
    # 段 [2,3] 扩展为 [0..5]；段 [6,7] 扩展为 [4..9]；重叠 → 合并为 [0..9]
    assert len(ranges) == 1
    assert ranges[0].page_idxs == tuple(range(0, 10))


def test_range_id_stable_for_same_seed():
    decisions = [_d(5, "C")]
    r1 = aggregate_complex_ranges(decisions, total_pages=10, range_id_seed="abc")
    r2 = aggregate_complex_ranges(decisions, total_pages=10, range_id_seed="abc")
    assert r1[0].range_id == r2[0].range_id


def test_range_id_differs_between_ranges():
    decisions = [_d(2, "C"), _d(5, "C")]
    ranges = aggregate_complex_ranges(decisions, total_pages=10, range_id_seed="seed")
    assert ranges[0].range_id != ranges[1].range_id


# 缓存 key 行为测试见 test_pdf_mineru_router.py
