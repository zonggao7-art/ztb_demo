"""节点内向 LangGraph custom 流写入统一 envelope 的工具。"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from langgraph.config import get_stream_writer

from .events import EventType
from .protocol import make_event

_REQUEST_ID: ContextVar[str] = ContextVar("stream_request_id", default="")
_STREAM_ACTIVE: ContextVar[bool] = ContextVar("stream_active", default=False)


def bind_request(request_id: str) -> object:
    """为当前执行上下文绑定 request_id，并标记 custom 流可用。"""
    request_token = _REQUEST_ID.set(request_id)
    active_token = _STREAM_ACTIVE.set(True)
    return (request_token, active_token)


def current_request_id() -> str:
    return _REQUEST_ID.get()


def emit(event_type: EventType, payload: dict[str, Any] | None = None) -> bool:
    """在 graph 节点内发送事件；非流式上下文静默忽略。"""
    if not _STREAM_ACTIVE.get():
        return False
    get_stream_writer()(
        make_event(event_type, current_request_id(), payload)
    )
    return True
