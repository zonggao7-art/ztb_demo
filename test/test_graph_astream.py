from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage

from agent.graph import AgentGraph
from agent.streaming import EventType


class StubAgent(AgentGraph):
    """避免外部资源；只验证 custom 流入口的 envelope 合同。"""

    def __init__(self):
        self._graph = None

    async def astream(self, question: str, thread_id: str = "default", *, deadline_s=None):
        from uuid import uuid4
        request_id = uuid4().hex
        yield make(request_id, EventType.META, {})
        yield make(request_id, EventType.TOKEN, {"delta": "你好"})
        yield make(request_id, EventType.CITATIONS, {"citations": [{"chunk_id": 1}]})
        yield make(request_id, EventType.FINAL, {"answer": "你好"})


def make(request_id, event_type, payload):
    from agent.streaming import make_event
    return make_event(event_type, request_id, payload)


def test_astream_contract_stub():
    events = _collect()
    types = [event.type for event in events]
    assert types[0] is EventType.META
    assert types.count(EventType.META) == 1
    assert types.index(EventType.CITATIONS) < types.index(EventType.FINAL)
    assert types[-1] is EventType.FINAL
    assert len({event.request_id for event in events}) == 1


def _collect():
    async def iterate():
        return [event async for event in StubAgent().astream("test")]
    return asyncio.run(iterate())
