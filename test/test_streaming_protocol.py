from __future__ import annotations

import asyncio

import pytest

from agent.streaming import (
    EventType,
    StreamEvent,
    format_heartbeat,
    format_sse,
    make_error_event,
    make_event,
    parse_sse,
    parse_sse_stream,
)


def _chunks(items: list[bytes]):
    async def iterator():
        for item in items:
            yield item
    return iterator()


@pytest.mark.parametrize("event_type", list(EventType))
def test_all_events_roundtrip(event_type):
    event = make_event(event_type, "roundtrip", {"delta": "x", "citations": []})
    parsed = parse_sse(format_sse(event))
    assert parsed == event


def test_multiple_frames_in_one_chunk():
    frames = b"".join([
        format_sse(make_event(EventType.META, "multi", {})),
        format_sse(make_event(EventType.TOKEN, "multi", {"delta": "hello"})),
        format_sse(make_error_event("multi", "boom", "failed", retryable=False)),
    ])
    events = asyncio.run(_collect(_chunks([frames])))
    assert [event.type for event in events] == [EventType.META, EventType.TOKEN, EventType.ERROR]
    assert events[-1].payload == {"code": "boom", "message": "failed", "retryable": False}


async def _collect(chunks):
    return [event async for event in parse_sse_stream(chunks)]


def test_partial_frame_across_chunks():
    frame = format_sse(make_event(EventType.TABLE, "partial", {"records": []}))
    events = asyncio.run(_collect(_chunks([frame[:17], frame[17:]])))
    assert len(events) == 1
    assert events[0].request_id == "partial"


def test_id_mismatch_rejected():
    event = make_event(EventType.FINAL, "right", {})
    raw = format_sse(event).replace(b"id: right", b"id: wrong")
    with pytest.raises(ValueError):
        parse_sse(raw)


def test_heartbeat_and_request_ids():
    assert parse_sse(format_heartbeat("hb")).type is EventType.HEARTBEAT
    first = make_event(EventType.STAGE, "same", {})
    second = StreamEvent.model_validate({**first.model_dump(mode="json")})
    assert {first.request_id, second.request_id} == {"same"}
