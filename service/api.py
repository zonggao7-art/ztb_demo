"""阶段 5 FastAPI SSE endpoint."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import aclosing, asynccontextmanager
from collections.abc import AsyncIterator
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from agent import AgentGraph
from agent.streaming import (
    EventType,
    format_heartbeat,
    format_sse,
)
from agent.streaming.protocol import normalize_custom_event
from .schemas import ChatRequest

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.agent = AgentGraph(async_enabled=True)
    logger.info("AgentGraph initialized for streaming service")
    try:
        yield
    finally:
        await app.state.agent.aclose()
        app.state.agent = None


app = FastAPI(title="Bidding Assistant Streaming API", lifespan=lifespan)

_TERMINAL_TYPES = {EventType.FINAL, EventType.ERROR, EventType.CANCELLED}


async def _merge_event_streams(
    primary: AsyncIterator[bytes],
    heartbeat: AsyncIterator[bytes],
) -> AsyncIterator[bytes]:
    """以 primary 为主通道；primary 终止时停止消费心跳。"""
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)

    async def pump(source, *, is_primary=False):
        try:
            async with aclosing(source):
                async for chunk in source:
                    await queue.put(chunk)
        except Exception as exc:
            await queue.put(exc)
        else:
            if is_primary:
                await queue.put(None)

    tasks = [
        asyncio.create_task(pump(primary, is_primary=True)),
        asyncio.create_task(pump(heartbeat)),
    ]
    try:
        while True:
            chunk = await queue.get()
            if chunk is None:
                return
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def chat_stream(req: ChatRequest):
    request_id = uuid4().hex
    started_at = time.monotonic()
    terminated = asyncio.Event()
    last_event_type = "none"
    agent: AgentGraph = app.state.agent

    async def event_bytes():
        nonlocal last_event_type
        try:
            async with aclosing(agent.astream(
                req.question,
                thread_id=req.thread_id,
                deadline_s=req.deadline_s,
            )) as events:
                async for raw_event in events:
                    event = normalize_custom_event(raw_event, request_id)
                    last_event_type = event.type.value
                    if event.type in _TERMINAL_TYPES:
                        terminated.set()
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
            if terminated.is_set():
                return
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
