"""工具层统一契约 — ToolResult / 错误包装 / LLM 可见内容渲染。

设计要点（蓝图 ai_agent_architecture_upgrade_plan.md §5 / §6.3）：
  - 所有工具返回统一 ToolResult（ok / data / error / metadata）
  - 工具永不向调用方抛异常：as_tool 包装器统一捕获并转为 error 结果，
    与图级 _with_fallback 兜底哲学一致，Agent 拿到错误信息可自行纠正重试
  - LLM 可见内容与结构化数据分离：content_and_artifact 双通道，
    content 经行数 + 字符双重截断，防止 prompt 膨胀
"""

from __future__ import annotations

import functools
import asyncio
import json
import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
from typing import Any, Awaitable, Callable, TypedDict

from ..streaming import EventType
from ..streaming.context import emit

logger = logging.getLogger(__name__)
_SYNC_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="tool-sync")
_SYNC_SLOTS = threading.BoundedSemaphore(8)

try:  # orjson 更快且已在 requirements 中；缺失时退化为标准库
    import orjson as _orjson
except ImportError:  # pragma: no cover
    _orjson = None


class ToolResult(TypedDict):
    """统一工具返回契约。

    data     — 业务负载：{"records": [...]} / {"chunks": [...]} / {"answer": ...}
    error    — {"code", "message", "retryable"}；成功时为 None
    metadata — 观测信息：tool 名、耗时、来源、行数等
    """

    ok: bool
    data: dict
    error: dict | None
    metadata: dict


# 错误码约定（Agent 据此决定重试 / 纠正参数 / 放弃）
ERR_INVALID_PARAMS = "invalid_params"
ERR_KB_NOT_INITIALIZED = "kb_not_initialized"
ERR_DB_UNAVAILABLE = "db_unavailable"
ERR_TIMEOUT = "timeout"
ERR_INTERNAL = "internal_error"


def make_tool_result(
    *,
    data: dict | None = None,
    metadata: dict | None = None,
) -> ToolResult:
    """构造成功结果。"""
    return ToolResult(ok=True, data=data or {}, error=None, metadata=metadata or {})


def make_error_result(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    metadata: dict | None = None,
) -> ToolResult:
    """构造失败结果（不抛异常，交由调用方 Agent 决策）。"""
    return ToolResult(
        ok=False,
        data={},
        error={"code": code, "message": message[:300], "retryable": retryable},
        metadata=metadata or {},
    )


def classify_exception(e: Exception) -> tuple[str, str, bool]:
    """异常 → (错误码, 消息, 是否可重试)。"""
    from agent.execution.context import RunStopped
    if isinstance(e, RunStopped):
        return str(e), "工具调用未通过执行约束。", False
    msg = str(e)
    if "知识库尚未初始化" in msg:
        return ERR_KB_NOT_INITIALIZED, "知识库尚未初始化，请先执行入库操作。", False
    if isinstance(e, TimeoutError) or "TimeoutError" in type(e).__name__:
        return ERR_TIMEOUT, "工具执行超时，请稍后重试。", True
    if isinstance(e, (ConnectionError, OSError)) or "pymysql" in type(e).__module__:
        return ERR_DB_UNAVAILABLE, "数据库访问失败，请稍后重试。", True
    return ERR_INTERNAL, "工具执行失败，请稍后重试。", False


def _limits():
    from public_kb.config import Settings
    from ..execution.context import current_run
    run = current_run()
    settings = run.settings if run else Settings()
    timeout = settings.agent_tool_timeout_s
    if run:
        run.check()
        timeout = min(timeout, run.remaining())
    return settings, timeout


def _emit_tool_stage(tool_name: str, status: str, **extra: Any) -> None:
    """best-effort 上报 tool_call 阶段事件；非流式上下文静默忽略。"""
    try:
        emit(
            EventType.STAGE,
            {"stage": "tool_call", "tool": tool_name, "status": status, **extra},
        )
    except Exception:  # 流式基础设施不可用时不影响工具主流程
        pass


def wrap_sync_tool(tool_name: str, fn: Callable[..., ToolResult]) -> Callable[..., tuple[str, ToolResult]]:
    """同步工具包装：耗时统计 + 流式事件 + 统一异常兜底 + (content, artifact) 双通道。"""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> tuple[str, ToolResult]:
        start = time.perf_counter()
        _emit_tool_stage(tool_name, "running")
        try:
            settings, timeout = _limits()
            kwargs.pop("task_id", None)
            if not _SYNC_SLOTS.acquire(timeout=timeout):
                raise TimeoutError("tool capacity")
            context = copy_context()
            from agent.execution.context import current_run
            run = current_run()
            if run:
                with run.lock:
                    run.inflight += 1

            def release(_=None):
                _SYNC_SLOTS.release()
                if run:
                    with run.lock:
                        run.inflight -= 1

            def actual():
                if run:
                    run.check()
                    if fn.__module__ == "agent.tools.price_db":
                        from .strict_sql import query
                        return query(tool_name, kwargs)
                return fn(*args, **kwargs)

            try:
                future = _SYNC_POOL.submit(context.run, actual)
            except BaseException:
                release()
                raise
            future.add_done_callback(release)
            result = future.result(timeout=max(0.0, timeout - (time.perf_counter() - start)))
        except Exception as e:
            code, message, retryable = classify_exception(e)
            logger.warning("[TOOL] %s failed: %s", tool_name, code)
            result = make_error_result(code, message, retryable=retryable)
        elapsed = time.perf_counter() - start
        result["metadata"]["tool"] = tool_name
        result["metadata"]["elapsed_s"] = round(elapsed, 3)
        _emit_tool_stage(tool_name, "done", ok=result["ok"], elapsed_s=result["metadata"]["elapsed_s"])
        from public_kb.config import Settings
        return render_tool_content(result, max_chars=Settings().agent_tool_max_content_chars), result

    return wrapper


def wrap_async_tool(
    tool_name: str, fn: Callable[..., Awaitable[ToolResult]]
) -> Callable[..., Awaitable[tuple[str, ToolResult]]]:
    """异步工具包装（行为与 wrap_sync_tool 完全一致）。"""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> tuple[str, ToolResult]:
        start = time.perf_counter()
        _emit_tool_stage(tool_name, "running")
        try:
            settings, timeout = _limits()
            kwargs.pop("task_id", None)
            async with asyncio.timeout(timeout):
                from agent.execution.context import current_run
                from .strict_sql import TABLES, query
                if current_run() is not None and tool_name in {*TABLES, "search_business_data"}:
                    from agent.runtime import run_blocking
                    result = await run_blocking(query, tool_name, kwargs)
                else:
                    result = await fn(*args, **kwargs)
        except Exception as e:
            code, message, retryable = classify_exception(e)
            logger.warning("[TOOL] %s(async) failed: %s", tool_name, code)
            result = make_error_result(code, message, retryable=retryable)
        elapsed = time.perf_counter() - start
        result["metadata"]["tool"] = tool_name
        result["metadata"]["elapsed_s"] = round(elapsed, 3)
        _emit_tool_stage(tool_name, "done", ok=result["ok"], elapsed_s=result["metadata"]["elapsed_s"])
        from public_kb.config import Settings
        return render_tool_content(result, max_chars=Settings().agent_tool_max_content_chars), result

    return wrapper


def _dumps(obj: Any) -> str:
    """JSON 序列化（优先 orjson，中文原样输出）。"""
    if _orjson is not None:
        return _orjson.dumps(obj, default=str).decode("utf-8")
    return json.dumps(obj, ensure_ascii=False, default=str)


def render_tool_content(
    result: ToolResult,
    *,
    max_rows: int = 10,
    max_chars: int = 4000,
) -> str:
    """Bounded, parseable JSON; whole records/chunks are omitted, never sliced."""
    if max_chars < 128 or max_rows < 1:
        raise ValueError("tool content limits too small")
    view: dict[str, Any] = {
        "ok": result["ok"],
        "data": deepcopy(result.get("data") or {}),
    }
    if result.get("error"):
        view["error"] = result["error"]

    for key in ("records", "chunks"):
        rows = view["data"].get(key)
        if isinstance(rows, list) and len(rows) > max_rows:
            view["data"][key] = rows[:max_rows]
            view["data"]["_truncated"] = len(rows) - max_rows
            view["data"]["_hint"] = f"共 {len(rows)} 条，仅展示前 {max_rows} 条，完整数据见 artifact"

    content = _dumps(view)
    if len(content) <= max_chars:
        return content
    view["data"]["_truncated_by_chars"] = True
    # Remove entire list elements, including full legal texts. Artifact remains
    # unchanged; callers must not treat artifact-only rows as model-visible.
    for key in ("records", "chunks", "citations", "sources"):
        rows = view["data"].get(key)
        while isinstance(rows, list) and rows and len(_dumps(view)) > max_chars:
            rows.pop()
            view["data"]["_truncated"] = view["data"].get("_truncated", 0) + 1
    if len(_dumps(view)) > max_chars:
        view["data"] = {"_truncated_by_chars": True, "_hint": "结果超出容量，未展示原始数据"}
    if len(_dumps(view)) > max_chars:
        view.pop("error", None)
    return _dumps(view)
