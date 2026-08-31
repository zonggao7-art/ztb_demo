# -*- coding: utf-8 -*-
"""router async 离线测试（mock LLM ainvoke）。"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from agent.router import (
    RouterDecision,
    build_router_node_async,
    _route_via_tool_calling_async,
    _route_via_structured_output_async,
)
from agent.state import AgentState


class _FakeStructured:
    """模拟 with_structured_output(...)().ainvoke(...)"""

    def __init__(self, intent: str) -> None:
        self._intent = intent

    async def ainvoke(self, _messages):
        return RouterDecision(intent=self._intent, confidence=0.9, reason="test")


def test_structured_async_returns_intent():
    llm = MagicMock()
    llm.with_structured_output.return_value = _FakeStructured("knowledge_qa")

    async def _t():
        return await _route_via_structured_output_async(
            llm, "history", "评标怎么算？"
        )
    assert asyncio.run(_t()) == "knowledge_qa"


def test_tool_calling_async_returns_intent():
    llm = MagicMock()
    fake_response = MagicMock()
    fake_response.tool_calls = [{"name": "route_price_inquiry", "args": {}}]
    llm.bind_tools.return_value.ainvoke = AsyncMock(return_value=fake_response)

    async def _t():
        return await _route_via_tool_calling_async(
            llm, "history", "XX公司中标了哪些项目？"
        )
    assert asyncio.run(_t()) == "price_inquiry"


def test_tool_calling_async_falls_back_when_no_tool_calls():
    llm = MagicMock()
    fake_response = MagicMock()
    fake_response.tool_calls = []
    llm.bind_tools.return_value.ainvoke = AsyncMock(return_value=fake_response)

    async def _t():
        return await _route_via_tool_calling_async(llm, "history", "x")
    assert asyncio.run(_t()) == "fallback"


def test_build_router_node_async_empty_messages():
    llm = MagicMock()
    node = build_router_node_async(llm)
    state: AgentState = {"messages": []}
    result = asyncio.run(node(state))
    assert result["router_intent"] == "fallback"


def test_build_router_node_async_happy_path():
    llm = MagicMock()
    llm.with_structured_output.return_value = _FakeStructured("general_chat")
    node = build_router_node_async(llm)
    state: AgentState = {
        "messages": [HumanMessage(content="你能做什么？")]
    }
    result = asyncio.run(node(state))
    assert result["router_intent"] == "general_chat"


def test_build_router_node_async_switches_to_tool_on_unsupported():
    """首次 structured 抛 response_format 错误时，运行时切到 Tool Calling。"""
    llm = MagicMock()
    # 第一次 ainvoke 抛错，第二次（Tool Calling）成功
    structured = MagicMock()
    structured.ainvoke = AsyncMock(
        side_effect=RuntimeError("response_format not supported")
    )
    llm.with_structured_output.return_value = structured

    fake_tool_resp = MagicMock()
    fake_tool_resp.tool_calls = [{"name": "route_knowledge_qa", "args": {}}]
    llm.bind_tools.return_value.ainvoke = AsyncMock(return_value=fake_tool_resp)

    node = build_router_node_async(llm)
    state: AgentState = {"messages": [HumanMessage(content="评标怎么算？")]}

    # 第一次：触发 fallback 到 tool
    r1 = asyncio.run(node(state))
    assert r1["router_intent"] == "knowledge_qa"