"""agent.runtime — 异步运行时基础设施（阶段 1 交付物）。"""
from __future__ import annotations
import logging
from . import async_bridge, concurrency, deadlines, cancellation
from .async_bridge import configure_executors, run_blocking, gather_limited, shutdown_executors
from .concurrency import register, acquire, get_or_register
from .deadlines import Deadline, deadline, wait_for_with_deadline, DeadlineExceeded
from .cancellation import CancellationRegistry

logger = logging.getLogger(__name__)

def init_runtime_from_settings(settings) -> None:
    """从 Settings 初始化 executors + 默认信号量。失败不抛错（降级到默认）。"""
    try:
        configure_executors(
            io_workers=int(getattr(settings, "async_io_workers", 16)),
            cpu_workers=int(getattr(settings, "async_cpu_workers", 4)),
        )
    except Exception as e:
        logger.warning("configure_executors 失败，使用默认: %s", e)

    default_quotas = {
        "llm":            int(getattr(settings, "llm_max_concurrency", 8)),
        "embedding":      int(getattr(settings, "embedding_max_concurrency", 8)),
        "rerank":         int(getattr(settings, "rerank_max_concurrency", 4)),
        "milvus_search":  int(getattr(settings, "milvus_max_concurrency", 8)),
        "mysql_acquire":  int(getattr(settings, "mysql_max_pool_size", 16)),
        "price_recall":   int(getattr(settings, "price_recall_concurrency", 3)),
    }
    for name, limit in default_quotas.items():
        get_or_register(name, limit)
    logger.info("runtime 就绪：executors + %d 个信号量", len(default_quotas))

__all__ = [
    "async_bridge", "concurrency", "deadlines", "cancellation",
    "configure_executors", "run_blocking", "gather_limited", "shutdown_executors",
    "register", "acquire", "get_or_register",
    "Deadline", "deadline", "wait_for_with_deadline", "DeadlineExceeded",
    "CancellationRegistry", "init_runtime_from_settings",
]