"""Offline tests for configurable retrieval strategies."""

from __future__ import annotations

import pytest

from public_kb.config import Settings
from public_kb.retrieval.strategies import adaptive_threshold


def _settings(**overrides: float) -> Settings:
    values = {
        "rerank_high_confidence_score": 0.80,
        "rerank_medium_confidence_score": 0.55,
        "rerank_high_confidence_threshold": 0.35,
        "rerank_medium_confidence_threshold": 0.45,
        "rerank_low_confidence_threshold": 0.55,
    }
    values.update(overrides)
    return Settings(**values)


def test_adaptive_threshold_uses_configured_bands():
    settings = _settings()

    assert adaptive_threshold(0.90, settings) == 0.35
    assert adaptive_threshold(0.60, settings) == 0.45
    assert adaptive_threshold(0.30, settings) == 0.55


def test_adaptive_threshold_without_settings_keeps_legacy_defaults():
    assert adaptive_threshold(0.99) == 0.40
    assert adaptive_threshold(0.60) == 0.45
    assert adaptive_threshold(0.30) == 0.50


def test_adaptive_threshold_rejects_invalid_order():
    settings = _settings(rerank_high_confidence_score=0.40)

    with pytest.raises(ValueError, match="high confidence score"):
        adaptive_threshold(0.90, settings)
