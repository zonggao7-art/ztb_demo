# -*- coding: utf-8 -*-
"""Agent 自助调用原型测试 — Fake LLM 发起 tool_call → 工具执行 → 最终回答（纯离线）。"""
from __future__ import annotations

from typing import Any, List, Optional

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool

from agent.agent_loop import _agent_invoke_config, build_tool_agent
from agent.tools.base import wrap_sync_tool
from agent.tools.registry import ToolMeta, ToolRegistry
from agent.tools.schemas import QueryCompanyPenaltyInput
from public_kb.config import Settings


class _FakeToolLLM(BaseChatModel):
    """首轮发起一次 tool_call；收到 ToolMessage 后返回最终回答。"""

    final_answer: str = "最终回答：测试公司无行政处罚记录"

    @property
    def _llm_type(self) -> str:
        return "fake-tool-llm"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        has_tool_result = any(isinstance(m, ToolMessage) for m in messages)
        if has_tool_result:
            msg = AIMessage(content=self.final_answer)
        else:
            msg = AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "query_company_penalty",
                        "args": {"company_name": "测试有限公司"},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            )
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _make_penalty_tool(calls: list):
    """与生产工具同构的 mock：wrap_sync_tool + content_and_artifact。"""

    def _impl(company_name: str, top_k: Optional[int] = None) -> dict:
        calls.append(company_name)
        return {
            "ok": True,
            "data": {"records": [{"company_name": company_name, "penalty_result": "无"}]},
            "error": None,
            "metadata": {"source": "test.company_penalty", "row_count": 1},
        }

    return StructuredTool.from_function(
        func=wrap_sync_tool("query_company_penalty", _impl),
        args_schema=QueryCompanyPenaltyInput,
        name="query_company_penalty",
        description="mock penalty tool",
        response_format="content_and_artifact",
    )


def test_agent_loop_calls_tool_and_answers():
    calls: list[str] = []
    settings = Settings(agent_tools_enabled=True, agent_loop_max_steps=6)
    compiled = build_tool_agent(
        llm=_FakeToolLLM(),
        tools=[_make_penalty_tool(calls)],
        settings=settings,
    )

    result = compiled.invoke(
        {"messages": [HumanMessage(content="测试有限公司有无不良记录？")]},
        config=_agent_invoke_config(settings, "t1"),
    )

    messages = result["messages"]
    assert calls == ["测试有限公司"]  # 工具真实执行了一次
    assert any(isinstance(m, ToolMessage) for m in messages)
    assert messages[-1].content == "最终回答：测试公司无行政处罚记录"

    # ToolMessage 双通道：content 为 LLM 可见 JSON，artifact 为完整 ToolResult
    tool_msg = next(m for m in messages if isinstance(m, ToolMessage))
    assert tool_msg.artifact["ok"] is True
    assert tool_msg.artifact["metadata"]["tool"] == "query_company_penalty"


def test_agent_mode_requires_enabled_switch():
    settings = Settings(agent_tools_enabled=False)
    with pytest.raises(RuntimeError, match="AGENT_TOOLS_ENABLED"):
        build_tool_agent(llm=_FakeToolLLM(), tools=[_make_penalty_tool([])], settings=settings)


def test_agent_mode_rejects_empty_tools():
    settings = Settings(agent_tools_enabled=True)
    with pytest.raises(RuntimeError, match="工具库为空"):
        build_tool_agent(llm=_FakeToolLLM(), tools=[], settings=settings)


def test_invoke_config_recursion_limit():
    # AGENT_LOOP_MAX_STEPS 语义为工具调用轮数 → recursion_limit = N*2 + 2
    assert _agent_invoke_config(Settings(agent_loop_max_steps=6), "t")["recursion_limit"] == 14


def test_enabled_tools_registry_smoke():
    """真实工具库装配冒烟：注册成功且白名单导出非空（不调用，不连基础设施）。"""
    from agent.tools import GLOBAL_TOOL_REGISTRY, get_enabled_tools, register_default_tools

    register_default_tools()
    assert len(GLOBAL_TOOL_REGISTRY) == 6
    tools = get_enabled_tools()
    assert {t.name for t in tools} == {
        "search_public_kb",
        "knowledge_qa",
        "query_company_info",
        "query_company_penalty",
        "query_bid_records",
        "search_business_data",
    }
