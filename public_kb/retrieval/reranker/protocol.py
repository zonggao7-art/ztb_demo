"""Contracts for reranker clients."""

from __future__ import annotations

from typing import Any, Dict, List, Protocol

from public_kb.contracts import RerankerStatus


class Reranker(Protocol):
    """Protocol implemented by reranker clients used by retrieval."""

    last_status: RerankerStatus

    def rerank(
        self,
        query: str,
        documents: List[str],
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """Return API results sorted by descending relevance score."""
        ...
