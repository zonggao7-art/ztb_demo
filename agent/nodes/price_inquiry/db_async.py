"""MySQL 有界连接池（异步版）— 阶段 3 交付物。

基于 DBUtils.PooledDB 实现进程级有界连接池：
  - maxconnections=mysql_max_pool_size：并发连接数硬上限（验收 1）
  - blocking=True：池耗尽时 acquire 阻塞等待，配合 mysql_acquire_timeout_s 客户端超时
  - ping=4：每次执行查询前自动探活，断连自动重建（坏连接自愈）
  - setsession：每次新建连接时下发 SET SESSION MAX_EXECUTION_TIME（服务端语句超时）

对外接口：
  - acquire()       异步上下文管理器，await run_blocking(pool.connection) 取连接
  - health_check()  启动时验证池可用（SELECT 1）
  - close_pool()    关闭并清理池单例
"""

from __future__ import annotations

import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional

import pymysql
from dbutils.pooled_db import PooledDB

from agent.runtime.async_bridge import run_blocking
from .db import _CLEAN_DB, _get_settings, _mysql_base_kwargs

logger = logging.getLogger(__name__)

_pool: Optional[PooledDB] = None
_pool_lock = threading.Lock()


def _build_pool() -> PooledDB:
    """构造 PooledDB 有界连接池（每次新建连接自动下发服务端 SQL 超时）。"""
    settings = _get_settings()
    kwargs = _mysql_base_kwargs()
    kwargs["database"] = _CLEAN_DB
    # setsession 在 SteadyDBConnection._create() 中于每次新建连接时执行，
    # 即"acquire 钩子下发服务端超时"：所有 SQL 最多执行 mysql_stmt_timeout_s。
    return PooledDB(
        pymysql,
        mincached=2,
        maxcached=5,
        maxconnections=settings.mysql_max_pool_size,
        blocking=True,
        ping=4,
        setsession=[
            "SET SESSION MAX_EXECUTION_TIME=%d"
            % int(settings.sql_stmt_timeout_s * 1000)
        ],
        **kwargs,
    )


def _get_pool() -> PooledDB:
    """懒加载全局池单例（线程安全）。"""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                _pool = _build_pool()
    return _pool


def close_pool() -> None:
    """关闭池并清空单例（测试/热更新用）。"""
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.close()
            except Exception as e:
                logger.warning("关闭连接池失败: %s", e)
            _pool = None


@asynccontextmanager
async def acquire() -> AsyncIterator[Any]:
    """异步获取一个池连接，退出时归还池。

    池耗尽时阻塞等待，超过 mysql_acquire_timeout_s 抛 asyncio.TimeoutError。
    """
    settings = _get_settings()
    pool = _get_pool()
    try:
        conn = await asyncio.wait_for(
            run_blocking(pool.connection),
            timeout=settings.mysql_acquire_timeout_s,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "[DB_POOL] 获取连接超时(%ds)，池上限=%d，当前连接数=%d",
            settings.mysql_acquire_timeout_s,
            settings.mysql_max_pool_size,
            getattr(pool, "_connections", -1),
        )
        raise
    try:
        yield conn
    finally:
        try:
            # 归还池：PooledDedicatedDBConnection.close() 将底层连接放入 idle 缓存
            await run_blocking(conn.close)
        except Exception as e:
            logger.warning("[DB_POOL] 归还连接失败: %s", e)


def _select_1(conn: Any) -> bool:
    """同步探活语句（在 IO 线程中执行）。"""
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            row = cur.fetchone()
        return bool(row)
    except Exception as e:
        logger.error("[DB_POOL] health_check SELECT 1 失败: %s", e)
        return False


async def health_check() -> bool:
    """启动时验证连接池可用：建池 + 执行 SELECT 1。"""
    try:
        pool = _get_pool()
    except Exception as e:
        logger.error("[DB_POOL] 连接池初始化失败: %s", e)
        return False
    try:
        async with acquire() as conn:
            return await run_blocking(_select_1, conn)
    except Exception as e:
        logger.error("[DB_POOL] health_check 失败: %s", e)
        return False
