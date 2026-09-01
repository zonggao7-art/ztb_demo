from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import HumanMessage

from agent.nodes.knowledge_qa_async import node_knowledge_qa_async
from agent.streaming import EventType


class FakeRAG:
    async def astream(self, question):
        rid = "rag"
        for payload, typ in [
            ({"stage": "retrieval_start"}, EventType.STAGE),
            ({"candidates": []}, EventType.RETRIEVAL),
            ({"delta": "引用"}, EventType.TOKEN),
            ({"delta": "答案"}, EventType.TOKEN),
            ({"citations": [{"chunk_id": 9}]}, EventType.CITATIONS),
            ({
                "result": {
                    "answer": "引用答案",
                    "sources": [{"doc": "x"}],
                    "citations": [{"chunk_id": 9}],
                    "citation_validation": {},
                }
             }, EventType.FINAL),
        ]:
            from agent.streaming import make_event
            yield make_event(typ, rid, payload)


def test_node_relays_order_and_preserves_state(monkeypatch):
    import agent.nodes.knowledge_qa_async as module

    monkeypatch.setattr(module, "_STREAM_ACTIVE", type("T", (), {
        "get": staticmethod(lambda: True),
    })(), raising=False)
    async def fake_run_blocking(function, *args, **kwargs):
        return FakeRAG()
    monkeypatch.setattr(module, "run_blocking", fake_run_blocking)
    result = asyncio.run(node_knowledge_qa_async({"messages": [HumanMessage(content="q")]}))
    data = result["business_result"]["data"]
    assert data["sources"] == [{"doc": "x"}]
    assert data["citations"] == [{"chunk_id": 9}]
    assert result["messages"][-1].content == "引用答案"
