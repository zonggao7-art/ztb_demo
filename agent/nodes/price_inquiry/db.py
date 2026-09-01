"""MySQL 连接池与 Settings 单例访问（价格查询链路的基础设施层）。"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

import pymysql

from public_kb.config import Settings

logger = logging.getLogger(__name__)

_pool_lock = threading.Lock()

_pool_connections: list[pymysql.Connection] = []

_pool_in_use: set[int] = set()

def _mysql_base_kwargs() -> dict[str, Any]:
    settings = _get_settings()
    return {
        "host": settings.mysql_host,
        "user": settings.mysql_user,
        "password": settings.mysql_password,
        "port": int(settings.mysql_port),
        "charset": "utf8mb4",
        "connect_timeout": min(10, settings.sql_query_timeout),
        "read_timeout": settings.sql_query_timeout,
        "write_timeout": settings.sql_query_timeout,
    }

# 业务库名统一从配置中心读取（.env: MYSQL_CLEAN_DB）
_CLEAN_DB = Settings().mysql_clean_db

_settings_cache: Optional[Settings] = None

def _get_settings() -> Settings:
    """获取 Settings 单例。"""
    global _settings_cache
    if _settings_cache is None:
        _settings_cache = Settings()
    return _settings_cache

def _get_connection(database: str) -> Optional[pymysql.Connection]:
    """从连接池获取 MySQL 连接。

    优先复用空闲连接（ping 验证存活），无空闲时创建新连接。
    """
    with _pool_lock:
        # 尝试复用空闲连接
        for i, conn in enumerate(_pool_connections):
            if i not in _pool_in_use:
                try:
                    conn.ping(reconnect=False)
                    _pool_in_use.add(i)
                    conn.select_db(database)
                    return conn
                except Exception:
                    # 连接已断开，移除
                    try:
                        conn.close()
                    except Exception:
                        pass
                    _pool_connections[i] = None

        # 创建新连接
        try:
            kwargs = _mysql_base_kwargs()
            # 流式游标可能需要长时间持有连接，延长超时避免断开
            kwargs.setdefault("read_timeout", 300)
            kwargs.setdefault("write_timeout", 300)
            kwargs.setdefault("connect_timeout", 30)
            conn = pymysql.connect(**kwargs, database=database)
            # 设置会话级超时，确保 SSDictCursor 长时间流式读取不被断开
            with conn.cursor() as cur:
                cur.execute("SET SESSION net_read_timeout=28800")
                cur.execute("SET SESSION net_write_timeout=28800")
                cur.execute("SET SESSION wait_timeout=28800")
            _pool_connections.append(conn)
            idx = len(_pool_connections) - 1
            _pool_in_use.add(idx)
            return conn
        except Exception as e:
            logger.error("连接 %s 失败: %s", database, e)
            return None

def _release_connection(conn: pymysql.Connection) -> None:
    """释放连接回池。"""
    with _pool_lock:
        for i, pooled in enumerate(_pool_connections):
            if pooled is conn:
                _pool_in_use.discard(i)
                return
    # 不在池中（异常情况），直接关闭
    try:
        conn.close()
    except Exception:
        pass
