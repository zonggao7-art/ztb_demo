"""
异步 Reranker 客户端（阶段 2）— httpx.AsyncClient 版 SiliconFlow /rerank。

与同步实现 public_kb.qa_chain._SiliconFlowReranker 对齐：
  - 请求体 / 响应结构完全一致（业务语义零退化）
  - base_url 复用 EMBEDDING_BASE_URL（同一服务提供方），缺失即抛错
  - 失败时异常上抛（2026-09 整改 D3 零降级：移除"原始顺序 + 0.5 假分数"回退）

差异（手册 §阶段2 步骤 2）：
  - requests.post → httpx.AsyncClient（连接池复用，避免每请求新建连接）
  - 并发受 agent.runtime "rerank" 全局信号量约束
  - 超时/限流/5xx 统一走指数退避同配置重试（口径 B：重发同一请求，不降级）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# 可重试的瞬时错误：网络传输层异常 / 超时 / 限流与服务端临时故障
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# agent.runtime 缺席时的进程内兜底信号量（独立运行 public_kb 场景）
_LOCAL_SEMAPHORES: Dict[str, asyncio.Semaphore] = {}


def _should_retry(exc: BaseException) -> bool:
    """tenacity 重试判定：仅对瞬时错误重试，业务性 4xx 不浪费尝试次数。"""
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRYABLE_STATUS
    return False


def _acquire_rerank_semaphore(limit: int) -> asyncio.Semaphore:
    """获取全局 rerank 并发信号量；agent.runtime 不可用时本地兜底。"""
    try:
        from agent.runtime.concurrency import get_or_register

        return get_or_register("rerank", limit)
    except Exception as e:  # pragma: no cover — 独立运行 public_kb 时的降级路径
        logger.debug("agent.runtime 不可用(%s)，rerank 使用本地信号量", e)
        sem = _LOCAL_SEMAPHORES.get("rerank")
        if sem is None:
            sem = asyncio.Semaphore(limit)
            _LOCAL_SEMAPHORES["rerank"] = sem
        return sem


class AsyncSiliconFlowReranker:
    """SiliconFlow Reranker API 异步客户端（OpenAI 兼容 /rerank 端点）。

    Usage:
        reranker = AsyncSiliconFlowReranker.from_settings(settings)
        results = await reranker.rerank(query, docs_text, top_k=5)
        ...
        await reranker.aclose()
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str,
        *,
        timeout_s: float = 30.0,
        max_retries: int = 2,
        concurrency: int = 4,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        """初始化异步 Reranker 客户端。

        Args:
            model: Reranker 模型名（如 BAAI/bge-reranker-v2-m3）。
            api_key: API Key。
            base_url: 服务根地址（如 https://api.siliconflow.cn/v1）。
            timeout_s: 单次请求超时秒数。
            max_retries: 瞬时错误的最大尝试次数（含首次）。
            concurrency: 该客户端参与的全局并发上限（注册到 "rerank" 信号量）。
            client: 外部注入的 httpx.AsyncClient（测试用）；None 则自建。
        """
        self._model = model
        self._api_key = api_key
        # 端点必填无兜底（D6）：缺失即抛错，不回退硬编码默认地址
        self._base_url = (base_url or "").rstrip("/")
        if not self._base_url:
            raise ValueError(
                "Reranker base_url 缺失（复用 EMBEDDING_BASE_URL，.env 必填，无兜底）"
            )
        self._timeout_s = timeout_s
        self._max_retries = max(1, int(max_retries))
        self._semaphore = _acquire_rerank_semaphore(max(1, int(concurrency)))
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout_s),
            limits=httpx.Limits(
                max_connections=max(8, concurrency * 4),
                max_keepalive_connections=max(4, concurrency),
            ),
        )

    @classmethod
    def from_settings(
        cls, settings: Any, *, client: Optional[httpx.AsyncClient] = None
    ) -> "AsyncSiliconFlowReranker":
        """从全局 Settings 构建工厂方法（口径与同步版一致：复用 embedding 的 key/地址）。"""
        return cls(
            model=settings.reranker_model,
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_base_url,
            timeout_s=float(getattr(settings, "rerank_timeout_s", 30)),
            concurrency=int(getattr(settings, "rerank_max_concurrency", 4)),
            client=client,
        )

    # ── 核心接口 ──────────────────────────────────────────────

    async def rerank(
        self, query: str, documents: List[str], top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """调用 SiliconFlow Reranker API 精排（异步版，语义与同步版一致）。

        Args:
            query: 用户问题。
            documents: 候选文档文本列表。
            top_k: 返回 Top-N 结果。

        Returns:
            [{"index": int, "relevance_score": float}, ...] 按分数降序。

        Raises:
            重试耗尽后异常原样上抛（零降级：不回退假分数）。
        """
        if not documents:
            return []

        async with self._semaphore:
            data = await self._post_with_retry(query, documents, top_k)
            results = data.get("results", [])
            return sorted(
                results, key=lambda x: x.get("relevance_score", 0), reverse=True
            )

    async def aclose(self) -> None:
        """关闭底层 HTTP 连接池。"""
        await self._client.aclose()

    async def __aenter__(self) -> "AsyncSiliconFlowReranker":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()

    # ── 内部实现 ──────────────────────────────────────────────

    async def _post_with_retry(
        self, query: str, documents: List[str], top_k: int,
    ) -> Dict[str, Any]:
        """带指数退避的 POST；瞬时错误同配置重试（口径 B），耗尽后异常上抛。"""
        from tenacity import (
            AsyncRetrying,
            retry_if_exception,
            stop_after_attempt,
            wait_exponential,
        )

        retrier = AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=4.0),
            retry=retry_if_exception(_should_retry),
            reraise=True,
        )

        async for attempt in retrier:
            with attempt:
                resp = await self._client.post(
                    "/rerank",
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
                )
                resp.raise_for_status()
                return resp.json()
        raise RuntimeError("unreachable")  # tenacity reraise=True 保证不会走到这里
