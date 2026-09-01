# -*- coding: utf-8 -*-
"""AgentGraph 双轨入口测试（mock 整个 build_graph，纯离线）。"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from agent.graph import AgentGraph, build_graph


@pytest.fixture
def patched_build_graph():
    """用 mock 替换 build_graph，让 AgentGraph 不连真实 LLM/DB。"""
    mock_compiled = MagicMock()
    mock_compiled.ainvoke = AsyncMock(
        return_value={
            "messages": [AIMessage(content="OK-async")],
            "router_intent": "knowledge_qa",
            "business_result": {"branch": "knowledge_qa", "answer": "OK-async"},
        }
    )
    mock_compiled.invoke = MagicMock(
        return_value={
            "messages": [AIMessage(content="OK-sync")],
            "router_intent": "knowledge_qa",
            "business_result": {"branch": "knowledge_qa", "answer": "OK-sync"},
        }
    )
    # astream_events
    async def _astream_events(*args, **kwargs):
        yield {"event": "on_chain_start", "name": "router"}
    mock_compiled.astream_events = _astream_events

    # stream
    def _stream(*args, **kwargs):
        yield {"router_intent": "knowledge_qa"}
    mock_compiled.stream = _stream

    with patch("agent.graph.build_graph", return_value=mock_compiled) as mock:
        yield mock, mock_compiled


def test_agent_graph_has_ainvoke():
    assert hasattr(AgentGraph, "ainvoke"), "ainvoke 必须存在"
    assert hasattr(AgentGraph, "astream"), "astream 必须存在"


def test_invoke_delegates_to_ainvoke_sync_path(patched_build_graph):
    """同步 invoke 在无 running loop 时委托给 ainvoke（§3.3 契约）。"""
    mock_build, mock_compiled = patched_build_graph
    agent = AgentGraph(async_enabled=False)
    result = agent.invoke("测试")
    # 同步 invoke 内部走 asyncio.run(self.ainvoke(...))，所以答案来自 ainvoke mock
    assert result["answer"] == "OK-async"
    assert result["intent"] == "knowledge_qa"
    mock_compiled.ainvoke.assert_called()  # 同步 invoke 内部调了 ainvoke


def test_ainvoke_returns_consistent_shape(patched_build_graph):
    """ainvoke 返回结构与 invoke 一致。"""
    mock_build, mock_compiled = patched_build_graph
    agent = AgentGraph(async_enabled=True)
    r1 = agent.invoke("测试")
    r2 = asyncio.run(agent.ainvoke("测试"))
    assert set(r1.keys()) == set(r2.keys()) == {"answer", "intent", "business_result"}


def test_ainvoke_uses_compiled_graph(patched_build_graph):
    mock_build, mock_compiled = patched_build_graph
    agent = AgentGraph(async_enabled=True)
    asyncio.run(agent.ainvoke("测试"))
    mock_compiled.ainvoke.assert_called_once()


def test_async_enabled_flag_passed(patched_build_graph):
    """async_enabled=True 时 build_graph 应收到 async_nodes=True。"""
    mock_build, _ = patched_build_graph
    AgentGraph(async_enabled=True)
    _, kwargs = mock_build.call_args
    assert kwargs.get("async_nodes") is True


def test_async_disabled_by_default(patched_build_graph):
    """async_enabled=None 时默认 False（保持与基线一致）。"""
    mock_build, _ = patched_build_graph
    AgentGraph()
    _, kwargs = mock_build.call_args
    assert kwargs.get("async_nodes") is False


def test_ainvoke_inside_running_loop_errors(monkeypatch):
    """在已有 loop 的线程里调同步 invoke 应抛 RuntimeError。"""
    mock_compiled = MagicMock()
    mock_compiled.invoke = MagicMock(return_value={
        "messages": [AIMessage(content="x")],
        "router_intent": "fallback",
        "business_result": {},
    })

    # 用一个假的 running loop 让 asyncio.get_running_loop() 成功
    fake_loop = MagicMock()

    def fake_get_running_loop():
        return fake_loop

    monkeypatch.setattr("asyncio.get_running_loop", fake_get_running_loop)

    with patch("agent.graph.build_graph", return_value=mock_compiled):
        agent = AgentGraph()
        with pytest.raises(RuntimeError, match="改用"):
            agent.invoke("测试")