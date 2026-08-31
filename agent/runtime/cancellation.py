"""结构化取消：业务节点可注册资源清理钩子（阶段 1 交付物）。"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Union

logger = logging.getLogger(__name__)

CleanupHook = Callable[[], Union[Awaitable[None], None]]


class CancellationRegistry:
    """收集并执行清理钩子。

    用法：
        registry = CancellationRegistry()
        registry.register(lambda: close_connection())
        # 业务节点完成后或异常时：
        await registry.run_all()
    """

    def __init__(self) -> None:
        self._hooks: list[CleanupHook] = []

    def register(self, hook: CleanupHook) -> None:
        self._hooks.append(hook)

    async def run_all(self) -> None:
        """依次执行所有清理钩子；任何一个失败不影响后续。"""
        for hook in self._hooks:
            try:
                result = hook()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:  # noqa: BLE001
                logger.warning("cleanup hook 失败: %s", e)
        self._hooks.clear()

    def clear(self) -> None:
        self._hooks.clear()

    def __len__(self) -> int:
        return len(self._hooks)