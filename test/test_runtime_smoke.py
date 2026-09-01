# -*- coding: utf-8 -*-
"""runtime/ 三件套 + cancellation 离线冒烟测试（不依赖基础设施）。"""
from __future__ import annotations

import asyncio
import time

import pytest

from agent.runtime import (
    init_runtime_from_settings,
    run_blocking,
    gather_limited,
    register,
    acquire,
    get_or_register,
    Deadline,
    deadline,
    wait_for_with_deadline,
    DeadlineExceeded,
    CancellationRegistry,
)
from agent.runtime import concurrency as concurrency_mod


@pytest.fixture(autouse=True)
def _reset_registry():
    """每个测试前清空信号量注册表，避免相互污染。"""
    concurrency_mod._REGISTRY.clear()
    yield
    concurrency_mod._REGISTRY.clear()


def test_init_runs():
    """init_runtime_from_settings 至少能跑完不抛错。"""
    from public_kb.config import Settings
    init_runtime_from_settings(Settings())


def test_run_blocking_returns_value():
    async def _t():
        return await run_blocking(lambda x: x * 2, 21)
    assert asyncio.run(_t()) == 42


def test_run_blocking_passes_kwargs():
    async def _t():
        return await run_blocking(lambda a, b=10: a + b, 5, b=3)
    assert asyncio.run(_t()) == 8


def test_gather_limited_respects_limit():
    async def _t():
        async def job(i):
            await asyncio.sleep(0.05)
            return i
        t0 = time.perf_counter()
        results = await gather_limited([job(i) for i in range(10)], limit=2)
        elapsed = time.perf_counter() - t0
        assert sorted(results) == list(range(10))
        assert elapsed >= 0.20  # 至少 ~5 波 × 50ms
    asyncio.run(_t())


def test_register_and_acquire():
    sem = register("test_sem", 2)
    assert isinstance(sem, asyncio.Semaphore)
    assert acquire("test_sem") is sem


def test_acquire_unknown_raises():
    with pytest.raises(KeyError):
        acquire("never_registered")


def test_get_or_register_idempotent():
    s1 = get_or_register("idem", 5)
    s2 = get_or_register("idem", 999)  # limit 不应生效（已注册）
    assert s1 is s2


def test_deadline_remaining_decreases():
    async def _t():
        async with deadline(0.5) as d:
            first = d.remaining()
            await asyncio.sleep(0.1)
            second = d.remaining()
            assert 0.4 <= first <= 0.5
            assert 0.2 <= second <= 0.4
            assert second < first
    asyncio.run(_t())


def test_wait_for_with_deadline_expired():
    async def _t():
        d = Deadline(time.monotonic(), 0.0)  # 立刻过期
        async def never():
            await asyncio.sleep(10)
        with pytest.raises(DeadlineExceeded):
            await wait_for_with_deadline(never(), d, label="test")
    asyncio.run(_t())


def test_wait_for_with_deadline_succeeds():
    async def _t():
        d = Deadline(time.monotonic(), 1.0)
        result = await wait_for_with_deadline(asyncio.sleep(0.05, result="done"), d, label="test")
        assert result is None or result == "done"
    asyncio.run(_t())


def test_cancellation_registry_runs_hooks():
    async def _t():
        reg = CancellationRegistry()
        called = []
        reg.register(lambda: called.append("sync"))
        async def _async_hook():
            called.append("async")
        reg.register(_async_hook)
        # 失败的钩子不影响其他
        reg.register(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        await reg.run_all()
        assert "sync" in called
        assert "async" in called
        assert len(reg) == 0  # 已清空
    asyncio.run(_t())