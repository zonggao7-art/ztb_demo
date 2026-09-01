"""L6 — parser_factory 接线测试（开关默认关闭 → MinerUParser；开启 → PdfRouter）。"""

from __future__ import annotations

from public_kb.config import Settings
from public_kb.ingestion.parser_factory import build_pdf_parser
from public_kb.ingestion.transforms.pdf_router import PdfRouter
from public_kb.services.mineru_parser import MinerUParser


def test_default_switch_returns_mineru_parser():
    """开关关闭 → MinerUParser（M1 行为不变）；显式关闭，不依赖 .env。"""
    s = Settings()
    s.pdf_tiered_routing_enabled = False
    assert s.pdf_tiered_routing_enabled is False
    p = build_pdf_parser(s)
    assert isinstance(p, MinerUParser)
    assert not isinstance(p, PdfRouter)


def test_enabled_switch_returns_pdf_router():
    """pdf_tiered_routing_enabled=true → PdfRouter。"""
    s = Settings()
    s.pdf_tiered_routing_enabled = True
    p = build_pdf_parser(s)
    assert isinstance(p, PdfRouter)
    assert not isinstance(p, MinerUParser)


def test_both_parsers_expose_parse_interface():
    """duck-typed：两个 parser 都有 .parse(pdf_path) -> str，PdfSource 不挑食。"""
    s_off = Settings()
    s_off.pdf_tiered_routing_enabled = False  # 显式关闭，不依赖 .env
    s_on = Settings()
    s_on.pdf_tiered_routing_enabled = True
    p_off = build_pdf_parser(s_off)
    p_on = build_pdf_parser(s_on)
    assert callable(getattr(p_off, "parse", None))
    assert callable(getattr(p_on, "parse", None))
