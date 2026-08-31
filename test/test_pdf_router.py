"""L4 — pdf_router 编排器单元测试（覆盖开关分支 + Tier B 自动升级）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from public_kb.config import Settings
from public_kb.ingestion.transforms.pdf_router import PdfRouter
from public_kb.services.mineru_parser import MinerUParser


# ── 开关关闭 → 回退 MinerUParser（M1 行为）───────────────────


def test_router_disabled_uses_mineru_parser_fallback():
    s = Settings()
    # 默认 pdf_tiered_routing_enabled=false
    assert s.pdf_tiered_routing_enabled is False
    r = PdfRouter(s)
    assert r.enabled is False
    assert isinstance(r._fallback_parser, MinerUParser)


def test_router_enabled_creates_components():
    s = Settings()
    s.pdf_tiered_routing_enabled = True
    r = PdfRouter(s)
    assert r.enabled is True
    assert r._classifier is not None
    assert r._miner_u_router is not None
    assert r._fallback_parser is None


def test_router_threshold_propagates_to_classifier():
    s = Settings()
    s.pdf_tiered_routing_enabled = True
    s.pdf_tiered_min_text_chars = 123
    s.pdf_tiered_image_area_ratio = 0.42
    s.pdf_tiered_two_col_confidence = 0.77
    r = PdfRouter(s)
    assert r._classifier is not None
    assert r._classifier._min_text_chars == 123
    assert abs(r._classifier._image_area_ratio - 0.42) < 1e-6
    assert abs(r._classifier._two_col_confidence - 0.77) < 1e-6


# ── Tier B 自动升级到 Tier C（纯逻辑）────────────────────────


def test_tier_b_upgrade_logic():
    """从 dataclasses.replace 推导：Tier B 决策 → Tier C，reason 加前缀。"""
    from dataclasses import replace

    from public_kb.ingestion.transforms.pdf_legal_page_classifier import (
        PageRouteDecision,
    )

    b = PageRouteDecision(
        page_idx=5,
        page_label="table_regular",
        tier="B",
        reason="检测到规整有框表格",
        confidence=0.85,
        parser="table_extractor",
        features={},
    )
    upgraded = replace(
        b, tier="C",
        reason=f"tier_b_unimplemented_fallback_to_c: {b.reason}",
        parser="mineru",
    )
    assert upgraded.tier == "C"
    assert upgraded.parser == "mineru"
    assert "tier_b_unimplemented_fallback_to_c" in upgraded.reason
    assert upgraded.page_idx == 5  # 其他字段保留


# ── 下列端到端测试需要 pymupdf + 真实 PDF，本机未装时跳过 ──


@pytest.mark.skipif(
    not pytest.importorskip("pymupdf", reason="pymupdf not installed"),
    reason="pymupdf required",
)
def test_router_end_to_end_with_mocked_mineru(tmp_path, monkeypatch):
    """构造一个最小 PDF，跑三档路由全流程，mock MinerUApiParser。

    本测试需要 pymupdf + langchain_core（依赖从 transforms/__init__ 引入）。
    """
    import pymupdf

    from public_kb.ingestion.transforms.pdf_mineru_router import MinerURouter

    # 构造 5 页单栏 PDF
    src = tmp_path / "src.pdf"
    doc = pymupdf.open()
    try:
        for _ in range(5):
            doc.new_page(width=400, height=600)
        doc.save(str(src))
    finally:
        doc.close()

    s = Settings()
    s.pdf_tiered_routing_enabled = True
    s.mineru_output_dir = str(tmp_path)
    s.pdf_tiered_manifest_dir = str(tmp_path / "manifest")
    s.pdf_tiered_allow_partial = False

    # 用 fake 替换 MinerURouter 的远端解析
    from public_kb.services.mineru_api_parser import MinerUApiParser

    class _FakeApi:
        """替身 MinerUApiParser：返回固定 Markdown，记录调用。"""

        def __init__(self) -> None:
            self.calls: list = []

        def health(self) -> dict:
            return {"parser_version": "1.3.12"}

        def parse(self, pdf_path, *, page_range=None):
            self.calls.append(Path(pdf_path))
            return f"<!-- mineru fake for {Path(pdf_path).name} -->\n"

    fake = _FakeApi()
    r = PdfRouter(s, miner_u_parser=fake)
    md = r.parse(src)
    assert isinstance(md, str)
    # 没有 Tier C 页时，markdown 应只有页标记 + 快路径产物
    assert "<!-- page:" in md

    # manifest 已落盘
    manifests = list((tmp_path / "manifest").glob("*.manifest.json"))
    assert manifests, "manifest 未生成"
