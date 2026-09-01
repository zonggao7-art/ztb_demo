# -*- coding: utf-8 -*-
"""阶段 3：MySQL 有界连接池（db_async）离线测试。

mock PooledDB 与池连接，不依赖真实 MySQL 服务，覆盖：
  - 池构造参数：maxconnections 上限 / blocking / ping=4 / setsession 服务端超时
  - 池耗尽时 acquire 客户端超时（连接数不超上限）
  - acquire 会话退出后连接归还池
  - health_check 成功/失败路径
  - close_pool 清理单例
"""
from __future__ import annotations

import asyncio
import time

import pytest

import agent.nodes.price_inquiry.db_async as db_async_mod


class _FakePooledConn:
    """模拟 PooledDedicatedDBConnection：close 表示归还池。"""

    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakePooledDB:
    """记录 PooledDB 构造参数并模拟有上限的连接发放。"""

    def __init__(self, driver=None, mincached=0, maxcached=5, maxconnections=16,
                 blocking=True, ping=0, setsession=None, **kwargs):
        self.driver = driver
        self.mincached = mincached
        self.maxcached = maxcached
        self.maxconnections = maxconnections
        self.blocking = blocking
        self.ping = ping
        self.setsession = setsession or []
        self.kwargs = kwargs
        self._acquired = 0
        self.closed = False

    def connection(self):
        if self._acquired >= self.maxconnections:
            # 模拟 blocking=True 的阻塞等待；由 acquire 客户端超时兜底
            time.sleep(1.0)
            raise AssertionError("池耗尽后不应真正返回连接")
        self._acquired += 1
        return _FakePooledConn()

    def close(self):
        self.closed = True


def test_build_pool_params_respect_settings(monkeypatch):
    """验收①代码层：maxconnections 使用 MYSQL_MAX_POOL_SIZE，且下发服务端超时。"""
    captured = {}

    def fake_pooled_db(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakePooledDB(*args, **kwargs)

    monkeypatch.setattr(db_async_mod, "PooledDB", fake_pooled_db)
    pool = db_async_mod._build_pool()

    settings = db_async_mod._get_settings()
    assert captured["kwargs"]["maxconnections"] == settings.mysql_max_pool_size
    assert captured["kwargs"]["blocking"] is True
    assert captured["kwargs"]["ping"] == 4
    assert captured["kwargs"]["database"] == db_async_mod._CLEAN_DB
    assert pool.maxconnections == settings.mysql_max_pool_size
    # setsession 必须含服务端语句超时（毫秒）
    expected = "SET SESSION MAX_EXECUTION_TIME=%d" % int(settings.sql_stmt_timeout_s * 1000)
    assert expected in pool.setsession


def test_acquire_returns_connection_and_closes_on_exit(monkeypatch):
    """acquire 会话内拿到池连接，退出时 close 归还池。"""
    created: list[_FakePooledConn] = []

    class _TrackingPool(_FakePooledDB):
        def connection(self):
            conn = _FakePooledConn()
            created.append(conn)
            self._acquired += 1
            return conn

    pool = _TrackingPool(maxconnections=2)
    monkeypatch.setattr(db_async_mod, "_get_pool", lambda: pool)

    async def _t():
        async with db_async_mod.acquire() as conn:
            assert conn is created[0]
        assert created[0].closed, "退出 acquire 后连接应归还池"

    asyncio.run(_t())
    assert pool.closed is False


def test_pool_exhausted_acquire_times_out(monkeypatch):
    """池耗尽 → acquire 阻塞等待 → 超过 mysql_acquire_timeout_s 抛超时（不超上限）。"""

    class _S:
        mysql_acquire_timeout_s = 0.2
        mysql_max_pool_size = 16

    monkeypatch.setattr(db_async_mod, "_get_settings", lambda: _S())
    pool = _FakePooledDB(maxconnections=1)
    monkeypatch.setattr(db_async_mod, "_get_pool", lambda: pool)

    async def _t():
        async with db_async_mod.acquire():
            with pytest.raises(asyncio.TimeoutError):
                async with db_async_mod.acquire():
                    pass

    asyncio.run(_t())
    assert pool._acquired <= pool.maxconnections


def test_health_check_success(monkeypatch):
    """health_check：SELECT 1 命中返回 True。"""

    class _FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql):
            assert sql == "SELECT 1"
            return 1

        def fetchone(self):
            return (1,)

    class _HealthConn(_FakePooledConn):
        def cursor(self):
            return _FakeCursor()

    pool = _FakePooledDB(maxconnections=2)
    pool.connection = lambda: _HealthConn()
    monkeypatch.setattr(db_async_mod, "_get_pool", lambda: pool)

    assert asyncio.run(db_async_mod.health_check()) is True


def test_health_check_failure_returns_false(monkeypatch):
    """health_check：探活语句抛错返回 False 而不向上抛异常（启动自愈）。"""

    class _BadCursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql):
            raise RuntimeError("connection lost")

        def fetchone(self):
            return None

    class _BadConn(_FakePooledConn):
        def cursor(self):
            return _BadCursor()

    pool = _FakePooledDB(maxconnections=2)
    pool.connection = lambda: _BadConn()
    monkeypatch.setattr(db_async_mod, "_get_pool", lambda: pool)

    assert asyncio.run(db_async_mod.health_check()) is False


def test_health_check_pool_init_failure_returns_false(monkeypatch):
    """health_check：建池即失败返回 False（不抛异常）。"""

    def boom():
        raise RuntimeError("cannot connect")

    monkeypatch.setattr(db_async_mod, "_get_pool", boom)
    assert asyncio.run(db_async_mod.health_check()) is False


def test_close_pool_resets_singleton(monkeypatch):
    """close_pool：关闭池并清空单例；close 抛错也不阻断重置。"""
    pool = _FakePooledDB(maxconnections=2)
    monkeypatch.setattr(db_async_mod, "_pool", pool)
    db_async_mod.close_pool()
    assert pool.closed is True
    assert db_async_mod._pool is None

    pool2 = _FakePooledDB(maxconnections=2)
    pool2.close = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    monkeypatch.setattr(db_async_mod, "_pool", pool2)
    db_async_mod.close_pool()  # 不应抛异常
    assert db_async_mod._pool is None