"""Deadline：用于把一个"总体时间预算"沿调用链向下传递并截断。"""

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Awaitable


class DeadlineExceeded(Exception):
    """统一的超时异常。业务节点可用 `except DeadlineExceeded` 兜底。"""
    pass


@dataclass
class Deadline:
    started_at: float
    # 用 time.monotonic() 记录的开始时刻（浮点数秒）
    # 为什么不用 time.time()？
    #   time.time():    墙上挂钟，可读但能被 NTP/时区/手动调表影响，可能回拨
    #   time.monotonic(): 单调递增，永远不回拨，专给"测时间间隔"用

    timeout_s: float
    # 这个请求总共允许的时间（秒）

    def remaining(self) -> float:
        # 计算"还剩多少时间"
        return max(0.0, self.timeout_s - (time.monotonic() - self.started_at))
        # max(0.0, ...): 哪怕超了一点，也至少返回 0
        # 因为 wait_for 不接受负数 timeout


@asynccontextmanager
async def deadline(timeout_s: float):
    """异步上下文管理器工厂。

    用法：
        async with deadline(5.0) as d:
            ...  # 块内可用 d.remaining() 查剩余时间
    """
    d = Deadline(time.monotonic(), timeout_s)
    try:
        yield d
    finally:
        pass  # 占位：阶段 1 暂不记录实际耗时


async def wait_for_with_deadline(
    coro: Awaitable,
    deadline_obj: Deadline,
    *,
    label: str,
):
    """等一个协程跑完，但最多等 deadline 剩余的时间。

    Args:
        coro:          要等的协程
        deadline_obj:  Deadline 实例
        label:         等待名（超时报错信息用，如 "milvus.search"）

    Raises:
        DeadlineExceeded / asyncio.TimeoutError: 超时
    """
    remaining = deadline_obj.remaining()
    if remaining <= 0:
        # 上层给的时间预算早就花光了——直接抛错
        raise DeadlineExceeded(f"{label} deadline already expired (0s remaining)")

    try:
        return await asyncio.wait_for(coro, timeout=remaining)
    except asyncio.TimeoutError as e:
        # 把标准 asyncio.TimeoutError 包成我们的 DeadlineExceeded，方便业务节点统一 except
        raise DeadlineExceeded(f"{label} exceeded {remaining:.2f}s") from e