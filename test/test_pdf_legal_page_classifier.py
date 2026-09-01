"""L1 页面分类器单元测试（不依赖 pymupdf，用 PageProfile 构造夹具）。"""

from __future__ import annotations

import pytest

from public_kb.ingestion.transforms.pdf_page_profile import PageProfile
from public_kb.ingestion.transforms.pdf_legal_page_classifier import (
    LegalPageClassifier,
    PageRouteDecision,
)


def _profile(**overrides) -> PageProfile:
    base = dict(
        page_idx=0,
        width=612.0,
        height=792.0,
        text_chars=1200,
        n_blocks=8,
        x_starts=(77.0, 107.0, 245.0),
        two_col_gap=0.0,
        two_col_split_x=0.0,
        has_two_col=False,
        table_hlines=0,
        table_vlines=0,
        table_candidate=False,
        img_ratio=0.0,
        article_count=5,
        formula_hint=False,
        fonts=("SimSun",),
    )
    base.update(overrides)
    return PageProfile(**base)


def _classifier(**overrides) -> LegalPageClassifier:
    return LegalPageClassifier(**overrides)


# ── Tier A：单栏文本 / 双栏条文 ──────────────────────────

def test_single_col_text_goes_tier_a():
    d = _classifier().classify(_profile())
    assert d.tier == "A"
    assert d.parser == "fast_text"
    assert d.page_label == "text"


def test_two_col_text_goes_tier_a_when_confident():
    p = _profile(
        has_two_col=True,
        two_col_gap=180.0,
        two_col_split_x=240.0,
        x_starts=(77.0, 90.0, 390.0, 400.0),
        n_blocks=4,
    )
    d = _classifier().classify(p)
    assert d.tier == "A"
    assert d.page_label == "two_col_text"
    assert d.parser == "fast_text"


def test_two_col_text_low_confidence_goes_tier_c():
    # 左右栏块数严重失衡 → 置信度低 → Tier C
    p = _profile(
        has_two_col=True,
        two_col_gap=150.0,
        two_col_split_x=240.0,
        x_starts=(77.0, 82.0, 85.0, 90.0, 95.0, 100.0, 390.0),
        n_blocks=7,
    )
    d = _classifier().classify(p)
    assert d.tier == "C"
    assert d.page_label == "uncertain"


# ── Tier B / C：表格 ─────────────────────────────────────

def test_regular_table_goes_tier_b():
    p = _profile(table_candidate=True, table_hlines=3, table_vlines=3)
    d = _classifier().classify(p)
    assert d.tier == "B"
    assert d.parser == "table_extractor"
    assert d.page_label == "table_regular"


def test_complex_table_goes_tier_c():
    # 有表格线线索但不成行列（如只有竖线无横线）→ 复杂表 → Tier C
    p = _profile(table_candidate=True, table_hlines=1, table_vlines=8)
    d = _classifier().classify(p)
    assert d.tier == "C"
    assert d.page_label == "table_complex"


# ── Tier C：扫描 / 低文本 / 图片密集 / 公式 ───────────────

def test_low_text_goes_tier_c():
    p = _profile(text_chars=10)
    d = _classifier().classify(p)
    assert d.tier == "C"
    assert d.page_label == "visual_or_scan"


def test_image_dense_goes_tier_c():
    p = _profile(img_ratio=0.5)
    d = _classifier().classify(p)
    assert d.tier == "C"


def test_formula_hint_goes_tier_c():
    p = _profile(formula_hint=True, fonts=("Cambria Math",))
    d = _classifier().classify(p)
    assert d.tier == "C"


# ── 阈值边界 ────────────────────────────────────────────

def test_min_text_chars_boundary():
    c = _classifier(min_text_chars=50)
    assert c.classify(_profile(text_chars=50)).tier == "A"   # 等于阈值 → 文本页
    assert c.classify(_profile(text_chars=49)).tier == "C"   # 低于阈值 → 扫描


def test_image_ratio_boundary():
    c = _classifier(image_area_ratio=0.35)
    assert c.classify(_profile(img_ratio=0.35)).tier == "A"
    assert c.classify(_profile(img_ratio=0.36)).tier == "C"


def test_route_decision_is_immutable_dataclass():
    d = _classifier().classify(_profile())
    assert isinstance(d, PageRouteDecision)
    assert d.features["text_chars"] == 1200
