"""The former hybrid path remains available as an explicit rollback mode."""
import asyncio
import sys

import pytest
from langchain_core.messages import AIMessage

from agent.graph import AgentGraph
from agent.streaming import EventType
from agent.execution.service import hybrid_selected
from public_kb.config import Settings
from test_react_execution import ScriptedModel, proposal, fake_tool, call


@pytest.fixture
def default_entry(monkeypatch):
    for key in ("AGENT_EXECUTION_MODE", "AGENT_REACT_ROLLOUT_PERCENT", "AGENT_REACT_THREAD_ALLOWLIST"):
        monkeypatch.delenv(key, raising=False)


@pytest.mark.parametrize("mode", ["static", "react"])
@pytest.mark.parametrize("stream", [False, True])
def test_explicit_hybrid_graph_dispatches_by_validated_decision(default_entry, mode, stream):
    calls = []
    model = ScriptedModel(proposal=proposal(mode), responses=(
        [AIMessage(content="", tool_calls=[call()])] if mode == "react" else []))
    agent = AgentGraph(llm=model, tools=[fake_tool(calls)],
                       settings=Settings(agent_execution_mode="hybrid", agent_react_rollout_percent=100))
    assert hybrid_selected(agent._settings, "default-test")
    question = "查询测试有限公司的处罚"
    try:
        if stream:
            async def collect():
                return [e async for e in agent.astream(question, "default-test")]
            events = asyncio.run(collect())
            assert events[0].payload["mode"] == "hybrid"
            assert events[-1].type == EventType.FINAL
            assert any(e.payload.get("stage") == "router_done" and
                       e.payload.get("mode") == mode for e in events)
        else:
            result = agent.invoke(question, "default-test")
            assert result["business_result"]["execution_status"] == "complete"
            assert result["intent"] == ("price_inquiry" if mode == "static" else "react")
        assert calls == ["测试有限公司"]
        assert model.routed == 1
        assert model.calls == (0 if mode == "static" else 2)
    finally:
        agent.close()


@pytest.mark.parametrize("mode,percent,selected", [("legacy", "100", False), ("hybrid", "0", False),
                                                    ("unified", "100", False)])
def test_explicit_environment_overrides_default(default_entry, monkeypatch, mode, percent, selected):
    monkeypatch.setenv("AGENT_EXECUTION_MODE", mode)
    monkeypatch.setenv("AGENT_REACT_ROLLOUT_PERCENT", percent)
    assert hybrid_selected(Settings(), "normal-session") is selected


def test_cli_without_mode_flag_selects_unified_and_survives_clear(default_entry, monkeypatch, capsys):
    import agent.__main__ as cli
    from agent.execution.service import execution_path
    seen = []
    class Graph:
        def __init__(self, *, async_enabled, settings):
            self._settings = settings
        def close(self):
            seen.append("closed")
    def run(agent, *, deadline_s, thread_id):
        seen.append(execution_path(agent._settings, thread_id))
        cli._describe_session(agent, "cleared-session")
        seen.append(execution_path(agent._settings, "cleared-session"))
    monkeypatch.setattr(cli, "AgentGraph", Graph)
    monkeypatch.setattr(cli, "run_interactive_stream", run)
    monkeypatch.setattr(sys, "argv", ["agent", "--interactive", "--stream"])
    cli.main()
    assert seen == ["unified", "unified", "closed"]
    assert "默认统一主 Agent" in capsys.readouterr().out
