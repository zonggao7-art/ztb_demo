"""
Checkpointer 工厂 — 对话记忆持久化抽象层。

Demo 阶段使用 MemorySaver（进程内存，重启丢失）。
通过 backend 参数可平滑升级至 SQLite / PostgreSQL / Redis，
业务代码零改动。
"""

from __future__ import annotations

import logging
from typing import Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.base import BaseCheckpointSaver

logger = logging.getLogger(__name__)


def create_checkpointer(
    backend: str = "memory",
    *,
    connection_string: Optional[str] = None,
) -> BaseCheckpointSaver:
    """Checkpointer 工厂，按需切换后端。

    Args:
        backend: 后端类型，可选 "memory" | "sqlite" | "postgres" | "redis"
        connection_string: 数据库连接字符串（memory 模式忽略）

    Returns:
        BaseCheckpointSaver 实例

    Examples:
        # Demo 阶段
        checkpointer = create_checkpointer("memory")

        # 未来接入 PostgreSQL
        checkpointer = create_checkpointer(
            "postgres",
            connection_string="postgresql://user:pass@host:5432/agent_memory",
        )
    """
    if backend == "memory":
        logger.info("Checkpointer: 使用 MemorySaver（进程内存）")
        return MemorySaver()

    if backend == "sqlite":
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError:
            raise NotImplementedError(
                "SqliteSaver 尚未安装，请执行: pip install langgraph-checkpoint-sqlite"
            )
        db_path = connection_string or "checkpoints.db"
        logger.info("Checkpointer: 使用 SqliteSaver -> %s", db_path)
        return SqliteSaver.from_conn_string(db_path)

    if backend == "postgres":
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
        except ImportError:
            raise NotImplementedError(
                "PostgresSaver 尚未安装，请执行: pip install langgraph-checkpoint-postgres"
            )
        if not connection_string:
            raise ValueError("PostgreSQL 模式需要提供 connection_string")
        logger.info("Checkpointer: 使用 PostgresSaver")
        return PostgresSaver.from_conn_string(connection_string)

    if backend == "redis":
        try:
            from langgraph.checkpoint.redis import RedisSaver
        except ImportError:
            raise NotImplementedError(
                "RedisSaver 尚未安装，请执行: pip install langgraph-checkpoint"
            )
        if not connection_string:
            connection_string = "redis://localhost:6379"
        logger.info("Checkpointer: 使用 RedisSaver -> %s", connection_string)
        return RedisSaver.from_conn_string(connection_string)

    raise ValueError(f"不支持的 checkpointer 后端: {backend}")
