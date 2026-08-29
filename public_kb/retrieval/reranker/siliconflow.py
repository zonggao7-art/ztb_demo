"""SiliconFlow reranker HTTP client."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

import requests

from public_kb.contracts import RerankerStatus


logger = logging.getLogger(__name__)


class SiliconFlowReranker:
    """SiliconFlow Reranker API client for the OpenAI-compatible /rerank endpoint."""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str,
        http_client: Any = requests,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/") if base_url else "https://api.siliconflow.cn/v1"
        self._http_client = http_client
        self.last_status = RerankerStatus.NOT_REQUESTED

    def rerank(
        self, query: str, documents: List[str], top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """Call SiliconFlow to rerank candidate documents.

        Args:
            query: User question.
            documents: Candidate document texts.
            top_k: Maximum number of results to return.

        Returns:
            ``[{"index": int, "relevance_score": float}, ...]`` in descending
            relevance order.
        """
        if not documents:
            self.last_status = RerankerStatus.NOT_REQUESTED
            return []
        try:
            resp = self._http_client.post(
                f"{self._base_url}/rerank",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model,
                    "query": query,
                    "documents": documents,
                    "top_n": min(top_k, len(documents)),
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            normalized = sorted(
                results,
                key=lambda item: item.get("relevance_score", 0),
                reverse=True,
            )
            self.last_status = RerankerStatus.SUCCESS
            return normalized
        except Exception as exc:
            self.last_status = RerankerStatus.FAILED
            logger.warning("Reranker API 调用失败: %s，保留 RRF 原始排序", exc)
            return []
