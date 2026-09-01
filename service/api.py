"""阶段 5 FastAPI SSE endpoint."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from agent import AgentGraph
from agent.streaming import (
    EventType,
    format_heartbeat,
    format_sse,
    make_event,
)
from agent.streaming.protocol import normalize_custom_event
from .schemas import ChatRequest

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.agent = AgentGraph(async_enabled=True)
    logger.info("AgentGraph initialized for streaming service")
    yield
    app.state.agent = None


app = FastAPI(title="Bidding Assistant Streaming API", lifespan=lifespan)

_TERMINAL_TYPES = {EventType.FINAL, EventType.ERROR, EventType.CANCELLED}


async def _merge_event_streams(
    primary: AsyncIterator[bytes],
    heartbeat: AsyncIterator[bytes],
) -> AsyncIterator[bytes]:
    """以 primary 为主通道；primary 终止时停止消费心跳。"""
    heartbeat_task = asyncio.create_task(_drain_heartbeat(heartbeat))
    try:
        async for chunk in primary:
            yield chunk
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass


async def _drain_heartbeat(heartbeat: AsyncIterator[bytes]):
    async for chunk in heartbeat:
        raise RuntimeError("heartbeat must be consumed by merge helper")


async def chat_stream(req: ChatRequest):
    request_id = uuid4().hex
    started_at = time.monotonic()
    terminated = asyncio.Event()
    last_event_type = "none"
    agent: AgentGraph = app.state.agent

    async def event_bytes():
        nonlocal last_event_type
        try:
            async for raw_event in agent.astream(
                req.question,
                thread_id=req.thread_id,
                deadline_s=req.deadline_s,
            ):
                event = normalize_custom_event(raw_event, request_id)
                last_event_type = event.type.value
                yield format_sse(event)
                if event.type in _TERMINAL_TYPES:
                    break
            if not terminated.is_set():
                terminated.set()
        except asyncio.CancelledError:
            terminated.set()
            logger.warning(
                "SSE client disconnected: request_id=%s thread_id=%s elapsed_ms=%.1f last=%s",
                request_id, req.thread_id, (time.monotonic() - started_at) * 1000, last_event_type,
            )
            raise
        finally:
            terminated.set()

    idle_seconds = getattr(agent._settings, "stream_heartbeat_s", 15)

    async def heartbeat_bytes():
        while not terminated.is_set():
            await asyncio.sleep(idle_seconds)
            yield format_heartbeat(request_id)

    return StreamingResponse(
        _merge_event_streams(event_bytes(), heartbeat_bytes()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


app.post("/chat/stream")(chat_stream)
