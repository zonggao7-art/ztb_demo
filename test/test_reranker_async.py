# -*- coding: utf-8 -*-
"""AsyncSiliconFlowReranker 离线测试（mock httpx.AsyncClient / MockTransport）。

覆盖手册 §阶段2 测试要求：
  - 正常精排（分数降序）
  - 超时/传输错误 → 重试后仍失败 → 降级为原始顺序 + 0.5 分（不抛错）
  - 429/5xx → 指数退避重试后成功
  - 并发受 "rerank" 信号量约束
"""
from __future__ import annotations

import asyncio
import itertools
import json

import httpx
import pytest

from public_kb.reranker import AsyncSiliconFlowReranker, _should_retry


@pytest.fixture(autouse=True)
def _clean_concurrency_registry():
    """每个测试前后清空并发注册表，避免跨测试的配额污染。"""
    from agent.runtime import concurrency as conc

    conc._LIMITS.clear()
    conc._REGISTRY.clear()
    yield
    conc._LIMITS.clear()
    conc._REGISTRY.clear()


def _client(handler) -> httpx.AsyncClient:
    """构建 MockTransport 驱动的 AsyncClient（不触网）。"""
    return httpx.AsyncClient(
        base_url="https://fake.rerank/v1", transport=httpx.MockTransport(handler)
    )


def _ok_handler(scores: list[dict], *, counter: list[int] | None = None,
                fail_first: int = 0):
    """返回成功响应的 handler；fail_first>0 时前 N 次返回 500。"""
    attempts = itertools.count()

    def handler(request: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter.append(1)
        if next(attempts) < fail_first:
            return httpx.Response(500, json={"error": "boom"})
        return httpx.Response(200, json={"results": scores})

    return handler


# ── 重试判定 ──────────────────────────────────────────────────


def test_should_retry_classification():
    req = httpx.Request("POST", "https://x/rerank")
    timeout_exc = httpx.ConnectTimeout("t", request=req)
    assert _should_retry(timeout_exc) is True

    resp_429 = httpx.Response(429, request=req)
    assert _should_retry(httpx.HTTPStatusError("rl", request=req, response=resp_429)) is True

    resp_400 = httpx.Response(400, request=req)
    assert _should_retry(httpx.HTTPStatusError("bad", request=req, response=resp_400)) is False

    assert _should_retry(ValueError("biz")) is False


# ── 核心行为 ──────────────────────────────────────────────────


def test_rerank_success_sorted_desc():
    scores = [
        {"index": 0, "relevance_score": 0.6},
        {"index": 1, "relevance_score": 0.95},
        {"index": 2, "relevance_score": 0.75},
    ]
    rr = AsyncSiliconFlowReranker("m", "k", "https://fake.rerank/v1",
                                  client=_client(_ok_handler(scores)))
    out = asyncio.run(rr.rerank("q", ["a", "b", "c"], top_k=3))
    assert [r["index"] for r in out] == [1, 2, 0]
    asyncio.run(rr.aclose())


def test_rerank_empty_documents_short_circuits():
    calls: list[int] = []

    async def _never(request):  # pragma: no cover — 不应被调用
        raise AssertionError("空文档不应发起请求")

    rr = AsyncSiliconFlowReranker("m", "k", "https://fake.rerank/v1",
                                  client=_client(_never))
    assert asyncio.run(rr.rerank("q", [], top_k=3)) == []
    asyncio.run(rr.aclose())


def test_rerank_timeout_degrades_to_original_order():
    """超时重试耗尽 → 与同步版一致的降级：原始顺序 + relevance_score=0.5。"""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow upstream", request=request)

    rr = AsyncSiliconFlowReranker("m", "k", "https://fake.rerank/v1",
                                  max_retries=2, client=_client(handler))
    out = asyncio.run(rr.rerank("q", ["d0", "d1", "d2"], top_k=2))
    assert out == [
        {"index": 0, "relevance_score": 0.5},
        {"index": 1, "relevance_score": 0.5},
    ]
    asyncio.run(rr.aclose())


def test_rerank_retries_on_500_then_succeeds():
    counter: list[int] = []
    scores = [{"index": 0, "relevance_score": 0.9}]
    rr = AsyncSiliconFlowReranker(
        "m", "k", "https://fake.rerank/v1", max_retries=3,
        client=_client(_ok_handler(scores, counter=counter, fail_first=2)),
    )
    out = asyncio.run(rr.rerank("q", ["d0"], top_k=1))
    assert len(counter) == 3          # 失败 2 次 + 成功 1 次
    assert out == scores
    asyncio.run(rr.aclose())


def test_rerank_no_retry_on_business_4xx():
    counter: list[int] = []
    scores = [{"index": 0, "relevance_score": 0.9}]

    def handler(request: httpx.Request) -> httpx.Response:
        counter.append(1)
        if len(counter) == 1:
            return httpx.Response(400, json={"error": "bad request"})
        return httpx.Response(200, json={"results": scores})

    rr = AsyncSiliconFlowReranker("m", "k", "https://fake.rerank/v1",
                                  max_retries=3, client=_client(handler))
    # 400 不应重试 → 直接走降级
    out = asyncio.run(rr.rerank("q", ["d0"], top_k=1))
    assert len(counter) == 1
    assert out == [{"index": 0, "relevance_score": 0.5}]
    asyncio.run(rr.aclose())


def test_rerank_respects_concurrency_semaphore():
    """并发限流：同时在途请求数不超过注册的 rerank 配额。"""
    inflight = 0
    peak = 0

    async def slow_handler(request: httpx.Request) -> httpx.Response:
        nonlocal inflight, peak
        inflight += 1
        peak = max(peak, inflight)
        await asyncio.sleep(0.05)
        inflight -= 1
        return httpx.Response(200, json={"results": []})

    async def _t():
        from agent.runtime.concurrency import get_or_register
        get_or_register("rerank", 2)

        tasks = [
            asyncio.create_task(rr.rerank(f"q{i}", ["d"], top_k=1))
            for i in range(6)
        ]
        await asyncio.gather(*tasks)

    rr = AsyncSiliconFlowReranker("m", "k", "https://fake.rerank/v1",
                                  concurrency=2,
                                  client=httpx.AsyncClient(
                                      base_url="https://fake.rerank/v1",
                                      transport=httpx.MockTransport(slow_handler),
                                  ))
    asyncio.run(_t())
    assert peak <= 2
    asyncio.run(rr.aclose())


def test_from_settings_factory(monkeypatch):
    """工厂方法从 Settings 取口径（与同步版一致：复用 embedding 的 key/base_url）。"""
    from public_kb.config import Settings

    s = Settings()
    rr = AsyncSiliconFlowReranker.from_settings(s, client=_client(_ok_handler([])))
    assert rr._model == s.reranker_model
    assert rr._api_key == s.embedding_api_key
    asyncio.run(rr.aclose())


def test_request_payload_shape():
    """请求体字段与同步版完全一致（model/query/documents/top_n + Bearer 头）。"""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        seen["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"results": []})

    rr = AsyncSiliconFlowReranker("BAAI/bge-reranker-v2-m3", "sk-test",
                                  "https://fake.rerank/v1", client=_client(handler))
    asyncio.run(rr.rerank("招标方式", ["公开招标", "邀请招标"], top_k=5))

    assert seen["json"]["model"] == "BAAI/bge-reranker-v2-m3"
    assert seen["json"]["query"] == "招标方式"
    assert seen["json"]["documents"] == ["公开招标", "邀请招标"]
    assert seen["json"]["top_n"] == 2  # min(top_k, len(documents))
    assert seen["headers"].get("authorization") == "Bearer sk-test"
    asyncio.run(rr.aclose())
