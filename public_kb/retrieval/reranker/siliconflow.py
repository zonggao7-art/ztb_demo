"""SiliconFlow reranker HTTP client."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, List

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
        *,
        timeout: float = 30,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.25,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/") if base_url else "https://api.siliconflow.cn/v1"
        self._http_client = http_client
        self._timeout = timeout
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._sleep_fn = sleep_fn
        self.last_status = RerankerStatus.NOT_REQUESTED
        self.retry_count = 0

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
            self.retry_count = 0
            return []

        self.retry_count = 0
        max_attempts = self._max_retries + 1
        for attempt in range(max_attempts):
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
                    timeout=self._timeout,
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
                if attempt >= self._max_retries or not self._is_retryable(exc):
                    self.last_status = RerankerStatus.FAILED
                    logger.warning(
                        "Reranker API 调用失败: %s，保留 RRF 原始排序",
                        exc,
                    )
                    return []

                self.retry_count = attempt + 1
                delay = self._retry_backoff_seconds * (2**attempt)
                logger.warning(
                    "Reranker API 瞬时失败 (%s)，%.2fs 后重试 %d/%d",
                    exc,
                    delay,
                    self.retry_count,
                    self._max_retries,
                )
                self._sleep_fn(delay)

        self.last_status = RerankerStatus.FAILED
        return []

    @staticmethod
    def _is_retryable(exc: Exception) -> bool:
        if isinstance(exc, (ConnectionError, TimeoutError)):
            return True
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if isinstance(exc, requests.exceptions.RequestException):
            return status_code is None
        return status_code == 429 or (
            isinstance(status_code, int) and 500 <= status_code <= 599
        )
