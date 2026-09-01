# 功能：实现检索阈值、过滤和相似度策略。
"""Retrieval filtering and threshold strategies."""

from __future__ import annotations

from ..config import Settings


def adaptive_threshold(
    top_score: float,
    settings: Settings | None = None,
) -> float:
    """基于 Reranker 最高分动态决定过滤阈值。

    策略：
    - top1 ≥ 0.75 → 高置信，放宽至 0.40，允许低分 chunk 补充上下文
    - top1 ≥ 0.50 → 中等置信，阈值 0.45
    - top1 < 0.50 → 直接使用 0.50（实际由 _decide_and_answer 判定是否拒答）
    """
    configured = settings or Settings()
    high_score = configured.rerank_high_confidence_score
    medium_score = configured.rerank_medium_confidence_score
    high_threshold = configured.rerank_high_confidence_threshold
    medium_threshold = configured.rerank_medium_confidence_threshold
    low_threshold = configured.rerank_low_confidence_threshold

    values = (
        high_score,
        medium_score,
        high_threshold,
        medium_threshold,
        low_threshold,
    )
    if any(value < 0 or value > 1 for value in values):
        raise ValueError("Reranker threshold values must be within [0, 1]")
    if high_score < medium_score:
        raise ValueError("high confidence score must be >= medium confidence score")
    if not high_threshold <= medium_threshold <= low_threshold:
        raise ValueError("rerank thresholds must satisfy high <= medium <= low")

    if top_score >= high_score:
        return high_threshold
    if top_score >= medium_score:
        return medium_threshold
    return low_threshold
