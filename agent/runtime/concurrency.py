"""当前脚本：限流管理器，防止系统一次性发送太多请求
执行逻辑:
1、协程申请一张门票
2、如果有空闲门票就立即执行
3、没有空闲门票就排队等待
4、离开 async with 代码块后自动归还门票

事件循环适配（阶段 2 补充）：
    asyncio.Semaphore 一旦出现排队等待，就会绑定"第一个使用它的事件循环"
    （_LoopBoundMixin）。CLI 每轮问答都是独立的 asyncio.run()（新循环），
    如果全局只存一份 Semaphore，第二轮一旦发生竞争就会抛
    "is bound to a different event loop"。
    因此注册表按 (资源名, 当前循环id) 分桶：同一循环内共享同一份并发配额，
    跨循环各自独立 —— 这正是"进程内单循环并发上限"的语义。
"""

import asyncio
# 异步框架

from typing import Dict, Optional, Tuple
# 类型注解用：Dict[str, asyncio.Semaphore] = "key 是字符串、value 是信号量对象"的字典


# 全局注册表：key=(资源名, 事件循环id)，value=信号量对象
# 这是进程级的——整个 Python 进程里只有这一份
_REGISTRY: Dict[Tuple[str, Optional[int]], asyncio.Semaphore] = {}

# 各资源的并发上限（名字 → limit）；信号量本体按循环分桶懒建
_LIMITS: Dict[str, int] = {}


def _running_loop() -> Optional[asyncio.AbstractEventLoop]:
    """当前运行中的事件循环；不在协程上下文里时返回 None。"""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


def _semaphore_for(name: str) -> asyncio.Semaphore:
    """取（或懒建）当前循环下某资源的信号量。未注册的名字抛 KeyError。"""
    limit = _LIMITS.get(name)
    if limit is None:
        # 名字没注册过 → 抛 KeyError（让 bug 在第一现场暴露）
        raise KeyError(f"Semaphore {name!r} 未注册，请先调用 register()")
    key = (name, id(_running_loop()))
    sem = _REGISTRY.get(key)
    if sem is None:
        sem = asyncio.Semaphore(limit)
        _REGISTRY[key] = sem
    return sem


def register(name: str, limit: int) -> asyncio.Semaphore:
    # 注册一个"全局限流器"
    # name: 资源名（如 "llm"、"milvus_search"），必须全局唯一
    # limit: 这个资源的并发上限

    # 显式 register 覆盖上限并新建当前上下文的信号量（与阶段 1 覆盖语义一致）
    _LIMITS[name] = limit
    key = (name, id(_running_loop()))
    sem = asyncio.Semaphore(limit)
    _REGISTRY[key] = sem
    return sem
    # 返回信号量实例，调用方可以自己拿去做 async with（更常见的是通过 acquire() 拿）


def acquire(name: str) -> asyncio.Semaphore:
    # 拿一个已注册的信号量（自动绑定当前事件循环）
    return _semaphore_for(name)


def get_or_register(name: str, limit: int) -> asyncio.Semaphore:
    """已注册就返回，否则注册并返回（幂等）。"""
    if name not in _LIMITS:
        register(name, limit)
    return _semaphore_for(name)


def list_registered() -> list[str]:
    """返回所有已注册的信号量名（调试用）。"""
    return list(_LIMITS.keys())
