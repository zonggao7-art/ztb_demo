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
import json
import logging
import time
from typing import Any, Awaitable, Callable, TypedDict

from ..streaming import EventType
from ..streaming.context import emit

logger = logging.getLogger(__name__)

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
    msg = str(e)
    if "知识库尚未初始化" in msg:
        return ERR_KB_NOT_INITIALIZED, "知识库尚未初始化，请先执行入库操作。", False
    if isinstance(e, TimeoutError) or "TimeoutError" in type(e).__name__:
        return ERR_TIMEOUT, f"工具执行超时: {msg[:200]}", True
    if isinstance(e, (ConnectionError, OSError)) or "pymysql" in type(e).__module__:
        return ERR_DB_UNAVAILABLE, f"数据库访问失败: {msg[:200]}", True
    return ERR_INTERNAL, f"工具内部错误: {msg[:200]}", True


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
            result = fn(*args, **kwargs)
        except Exception as e:
            code, message, retryable = classify_exception(e)
            logger.error("[TOOL] %s 执行失败: %s", tool_name, e, exc_info=True)
            result = make_error_result(code, message, retryable=retryable)
        elapsed = time.perf_counter() - start
        result["metadata"]["tool"] = tool_name
        result["metadata"]["elapsed_s"] = round(elapsed, 3)
        _emit_tool_stage(tool_name, "done", ok=result["ok"], elapsed_s=result["metadata"]["elapsed_s"])
        return render_tool_content(result), result

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
            result = await fn(*args, **kwargs)
        except Exception as e:
            code, message, retryable = classify_exception(e)
            logger.error("[TOOL] %s(async) 执行失败: %s", tool_name, e, exc_info=True)
            result = make_error_result(code, message, retryable=retryable)
        elapsed = time.perf_counter() - start
        result["metadata"]["tool"] = tool_name
        result["metadata"]["elapsed_s"] = round(elapsed, 3)
        _emit_tool_stage(tool_name, "done", ok=result["ok"], elapsed_s=result["metadata"]["elapsed_s"])
        return render_tool_content(result), result

    return wrapper


def _dumps(obj: Any) -> str:
    """JSON 序列化（优先 orjson，中文原样输出）。"""
    if _orjson is not None:
        return _orjson.dumps(obj).decode("utf-8")
    return json.dumps(obj, ensure_ascii=False, default=str)


def render_tool_content(
    result: ToolResult,
    *,
    max_rows: int = 10,
    max_chars: int = 4000,
) -> str:
    """ToolResult → LLM 可见精简 JSON。

    行数截断：data.records / data.chunks 超过 max_rows 时保留前 max_rows 行
    并追加 {"_truncated": N} 提示；字符截断：整体超过 max_chars 时追加省略标记。
    """
    view: dict[str, Any] = {
        "ok": result["ok"],
        "data": dict(result.get("data") or {}),
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
    if len(content) > max_chars:
        content = content[:max_chars] + f'...["_truncated_by_chars": 原始 {len(content)} 字符]'
    return content
