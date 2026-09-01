# -*- coding: utf-8 -*-
"""streaming/ envelope 离线测试。"""
from __future__ import annotations

import pytest

from agent.streaming import EventType, StreamEvent, format_sse, format_jsonl


def test_event_types_complete():
    """阶段 5 规范事件和兼容别名必须同时存在。"""
    needed = {"router", "token", "message", "citations", "final", "error"}
    have = {t.value for t in EventType}
    assert needed.issubset(have), f"缺少事件类型: {needed - have}"


def test_stage5_normalized_event_types_complete():
    normalized = {
        "meta",
        "stage",
        "token",
        "retrieval",
        "citations",
        "table",
        "partial",
        "final",
        "error",
        "cancelled",
        "heartbeat",
    }
    assert set(EventType) >= {EventType[value.upper()] for value in normalized}


def test_envelope_roundtrip_sse():
    ev = StreamEvent(type=EventType.TOKEN, request_id="r1", payload={"text": "hi"})
    raw = format_sse(ev)
    assert b"event: token\n" in raw
    assert b"id: r1\n" in raw
    assert b"data:" in raw
    assert raw.endswith(b"\n\n")


def test_envelope_roundtrip_jsonl():
    ev = StreamEvent(type=EventType.FINAL, request_id="r2", payload={"answer": "OK"})
    raw = format_jsonl(ev)
    assert raw.endswith(b"\n")
    assert b"\"type\":\"final\"" in raw or b"\"type\": \"final\"" in raw


def test_envelope_default_payload_is_empty_dict():
    ev = StreamEvent(type=EventType.HEARTBEAT, request_id="r3")
    assert ev.payload == {}
    assert ev.ts == 0.0


def test_envelope_serializable():
    """payload 必须能 JSON 序列化（pydantic 自动保证）。"""
    import orjson
    ev = StreamEvent(
        type=EventType.CITATIONS,
        request_id="r4",
        payload={"citations": [{"chunk_id": 1, "text": "..."}]},
    )
    blob = orjson.dumps(ev.model_dump())
    assert b"citations" in blob
