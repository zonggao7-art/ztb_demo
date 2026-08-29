"""Retrieval filtering and threshold strategies."""


def adaptive_threshold(top_score: float) -> float:
    """基于 Reranker 最高分动态决定过滤阈值。

    策略：
    - top1 ≥ 0.75 → 高置信，放宽至 0.40，允许低分 chunk 补充上下文
    - top1 ≥ 0.50 → 中等置信，阈值 0.45
    - top1 < 0.50 → 直接使用 0.50（实际由 _decide_and_answer 判定是否拒答）
    """
    if top_score >= 0.75:
        return 0.40
    if top_score >= 0.50:
        return 0.45
    return 0.50
