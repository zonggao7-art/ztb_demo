"""SSE 序列化、反序列化和 LangGraph custom 流适配。"""
from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from pydantic import ValidationError

import orjson

from .events import EventType, StreamEvent

logger = logging.getLogger(__name__)


def format_sse(event: StreamEvent) -> bytes:
    """单条 SSE 帧：id / event / data 三行 + 空行。

    阶段 5 在 FastAPI 端用：StreamingResponse(format_sse(ev) for ev in events)
    """
    payload = orjson.dumps(event.model_dump()).decode("utf-8")
    return (
        f"id: {event.request_id}\n"
        f"event: {event.type.value}\n"
        f"data: {payload}\n\n"
    ).encode("utf-8")


def format_jsonl(event: StreamEvent) -> bytes:
    """JSON Lines 格式（一行一条 JSON），用于 CLI / 日志。"""
    return orjson.dumps(event.model_dump()) + b"\n"


def make_event(
    event_type: EventType,
    request_id: str,
    payload: dict[str, Any] | None = None,
) -> StreamEvent:
    """构造统一 envelope，并保证时间戳总是有效浮点数。"""
    return StreamEvent(
        type=event_type,
        request_id=request_id or "unknown",
        payload=payload or {},
        ts=time.time(),
    )


def format_heartbeat(request_id: str) -> bytes:
    return format_sse(make_event(EventType.HEARTBEAT, request_id))


def make_error_event(
    request_id: str,
    code: str,
    message: str,
    *,
    retryable: bool = True,
) -> StreamEvent:
    return make_event(
        EventType.ERROR,
        request_id,
        {"code": code, "message": message, "retryable": retryable},
    )


def parse_sse(block: str | bytes) -> StreamEvent:
    """解析一个空行结尾的 SSE frame。"""
    if isinstance(block, bytes):
        block = block.decode("utf-8")

    fields: dict[str, str] = {}
    for line in block.strip().splitlines():
        if not line or line.startswith(":"):
            continue
        name, separator, value = line.partition(":")
        if not separator:
            continue
        if value.startswith(" "):
            value = value[1:]
        if name == "data" and "data" in fields:
            raise ValueError("SSE frame 包含多个 data 字段")
        fields[name] = value

    try:
        raw_data = fields["data"]
        event_id = fields["id"]
        event_name = fields["event"]
    except KeyError as exc:
        raise ValueError(f"SSE frame 缺少字段: {exc.args[0]}") from exc

    try:
        loaded = orjson.loads(raw_data)
    except orjson.JSONDecodeError as exc:
        raise ValueError("SSE data 不是合法 JSON") from exc

    if not isinstance(loaded, dict):
        raise ValueError("SSE data 必须是 JSON object")
    try:
        event = StreamEvent.model_validate(loaded)
    except ValidationError as exc:
        raise ValueError("SSE data 不符合 StreamEvent schema") from exc

    if event.type.value != event_name or event.request_id != event_id:
        raise ValueError("SSE 元数据与 envelope 不一致")
    return event


async def parse_sse_stream(chunks: AsyncIterator[bytes]) -> AsyncIterator[StreamEvent]:
    """解析 HTTP chunk 边界上的多个或半个 SSE frame。"""
    buffer = bytearray()
    async for chunk in chunks:
        buffer.extend(chunk)
        while True:
            end = buffer.find(b"\n\n")
            crlf_end = buffer.find(b"\r\n\r\n")
            if crlf_end != -1 and (end == -1 or crlf_end < end):
                size = crlf_end + 4
            elif end != -1:
                size = end + 2
            else:
                break
            yield parse_sse(bytes(buffer[:size]))
            del buffer[:size]


def normalize_custom_event(item: Any, request_id: str) -> StreamEvent:
    """把 LangGraph custom 输出转换为稳定 StreamEvent。"""
    if isinstance(item, StreamEvent):
        return item.model_copy(update={"request_id": request_id or item.request_id})
    if isinstance(item, EventType):
        return make_event(item, request_id)
    if not isinstance(item, dict):
        return make_event(
            EventType.PARTIAL, request_id,
            {"kind": "langgraph", "text": str(item)},
        )

    try:
        candidate = StreamEvent.model_validate(item)
    except ValidationError:
        unknown_kind = item.get("type", item.get("event", "partial"))
        try:
            normalized_type = EventType(unknown_kind)
        except ValueError:
            normalized_type = EventType.PARTIAL
        candidate = make_event(
            normalized_type,
            str(item.get("request_id") or request_id),
            dict(item.get("payload") or {}),
        )
    return candidate.model_copy(update={"request_id": request_id or candidate.request_id})


def adapt_langgraph_event(lg_event: dict) -> StreamEvent:
    """把 LangGraph 原生 astream_events 的事件适配成我们的 StreamEvent。

    阶段 1 不接入业务，留给阶段 5 router / 业务节点真正改造时使用。
    """
    kind = lg_event.get("event", "message")
    name = lg_event.get("name", "")
    metadata = lg_event.get("metadata", {}) or {}

    type_map = {
        "on_chain_start": EventType.ROUTER,
        "on_chain_end": EventType.MESSAGE,
        "on_chat_model_start": EventType.ROUTER,
        "on_chat_model_stream": EventType.TOKEN,
        "on_chat_model_end": EventType.MESSAGE,
        "on_tool_start": EventType.ROUTER,
        "on_tool_end": EventType.MESSAGE,
    }
    event_type = type_map.get(kind, EventType.MESSAGE)

    payload: dict = {"langgraph_kind": kind, "name": name}
    data = lg_event.get("data")
    if data is not None:
        # 输出数据要可 JSON 序列化，否则丢掉
        try:
            orjson.dumps(data)
            payload["data"] = data
        except (TypeError, ValueError):
            payload["data"] = str(data)[:500]

    return StreamEvent(
        type=event_type,
        request_id=str(metadata.get("thread_id", "")),
        payload=payload,
        ts=lg_event.get("run_id", 0).__hash__() if False else 0.0,
    )
