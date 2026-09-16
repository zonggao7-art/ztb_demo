"""--agent-mode compatibility entry for the verified unified Agent pipeline.

The official loop lives in execution/executor.py (langchain.create_agent).
Public messages contain verified final replies only, never raw tool traces.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

from public_kb.config import Settings

from .tools import get_enabled_tools

logger = logging.getLogger(__name__)

def build_tool_agent(
    *,
    llm: Optional[BaseChatModel] = None,
    checkpointer: Any = None,
    tools: Optional[list[BaseTool]] = None,
    settings: Optional[Settings] = None,
):
    """构建 tool-calling Agent 原型。

    Args:
        llm: 对话模型；None 则按 Settings 自动创建。
        checkpointer: 会话记忆后端；None 使用请求入口的内存后端，不落盘。
        tools: 显式工具列表（测试注入用）；None 则从工具库按白名单取用。
        settings: 配置；None 则从 .env 加载。

    Raises:
        RuntimeError: 总开关未开启 / 工具库为空。
    """
    settings = settings or Settings()
    if not settings.agent_tools_enabled:
        raise RuntimeError(
            "Agent 自助调用未启用：请在 .env 设置 AGENT_TOOLS_ENABLED=true 后重试"
        )
    if tools is None:
        tools = get_enabled_tools(settings=settings)
    if not tools:
        raise RuntimeError(
            "工具库为空：请检查 AGENT_TOOLS_WHITELIST 是否过滤掉了全部工具"
        )

    # Explicit --agent-mode opts this entry into the verified unified path.
    from dataclasses import replace
    from .graph import AgentGraph
    agent = AgentGraph(llm=llm, async_enabled=True, tools=tools,
                       settings=replace(settings, agent_execution_mode="unified"))
    if checkpointer is not None:
        agent._checkpointer = checkpointer
    return _VerifiedAgentAdapter(agent)


class _VerifiedAgentAdapter:
    def __init__(self, agent):
        self.agent = agent

    async def ainvoke(self, value, config=None):
        messages = value.get("messages") or []
        if not messages or not isinstance(messages[-1], HumanMessage):
            raise ValueError("A final HumanMessage is required")
        thread_id = (config or {}).get("configurable", {}).get("thread_id", "default")
        result = await self.agent.ainvoke(str(messages[-1].content), thread_id)
        return {**result, "messages": [AIMessage(content=result["answer"])]}

    def invoke(self, value, config=None):
        import asyncio
        if self.agent._sync_runner is None:
            self.agent._sync_runner = asyncio.Runner()
        return self.agent._sync_runner.run(self.ainvoke(value, config))

    def close(self):
        self.agent.close()


def _agent_invoke_config(settings: Settings, thread_id: str) -> dict:
    """Agent 调用 config。

    recursion_limit 按 graph 超级步计（每轮工具调用消耗 model+tools 两步），
    AGENT_LOOP_MAX_STEPS 语义为「工具调用轮数」，故 ×2 后留 2 步余量给首尾。
    """
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": settings.agent_loop_max_steps * 2 + 2,
    }


def _render_tool_trace(new_messages: list) -> list[str]:
    """从新增消息中提取工具调用轨迹（供 CLI 展示）。"""
    lines: list[str] = []
    for msg in new_messages:
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            for call in msg.tool_calls:
                args_preview = str(call.get("args", {}))[:120]
                lines.append(f"🔧 调用工具 {call.get('name')}({args_preview})")
        elif isinstance(msg, ToolMessage):
            content = str(msg.content)
            status = "✅" if getattr(msg, "status", "success") == "success" else "⚠️"
            lines.append(f"   {status} {content[:160]}")
    return lines


def run_interactive_agent(compiled: Any, settings: Settings) -> None:
    """Agent 自助调用交互会话（--agent-mode 入口）。"""
    print("🤖 Agent 自助调用模式（工具库驱动，quit/exit 退出，clear 清空会话）")
    print("─" * 60)
    tools_desc = ", ".join(t.name for t in get_enabled_tools())
    print(f"可用工具: {tools_desc}\n")

    from uuid import uuid4
    thread_id = uuid4().hex
    seen = 0
    turn = 0
    while True:
        try:
            question = input(f"[{turn}] 🙋 您: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 再见！")
            break
        if not question:
            continue
        if question.lower() in ("quit", "exit"):
            print("👋 再见！")
            break
        if question.lower() == "clear":
            thread_id = uuid4().hex
            seen = 0
            print("🔄 已清空会话\n")
            continue

        print("⏳ Agent 思考与调用工具中...")
        try:
            result = compiled.invoke(
                {"messages": [HumanMessage(content=question)]},
                config=_agent_invoke_config(settings, thread_id),
            )
            messages = result.get("messages", [])
            for line in _render_tool_trace(messages[seen:]):
                print(line)
            seen = len(messages)

            final = next(
                (m.content for m in reversed(messages) if isinstance(m, AIMessage) and m.content),
                "",
            )
            print(f"\n🤖 助手: {final or '（未产出回答）'}\n")
            turn += 1
        except KeyboardInterrupt:
            print("\n⏹️ 已取消\n")
        except Exception as e:
            print(f"❌ 错误: {e}\n")

    if hasattr(compiled, "close"):
        compiled.close()


def run_single_agent(compiled: Any, settings: Settings, question: str) -> None:
    """Agent 自助调用单次问答（--agent-mode --question 入口）。"""
    print(f"🙋 问题: {question}\n⏳ Agent 思考与调用工具中...\n")
    try:
        result = compiled.invoke(
            {"messages": [HumanMessage(content=question)]},
            config=_agent_invoke_config(settings, "single-agent"),
        )
        messages = result.get("messages", [])
        for line in _render_tool_trace(messages):
            print(line)
        final = next(
            (m.content for m in reversed(messages) if isinstance(m, AIMessage) and m.content),
            "",
        )
        print(f"\n🤖 回答:\n{final or '（未产出回答）'}")
    except Exception as e:
        print(f"❌ 错误: {e}")
    finally:
        if hasattr(compiled, "close"):
            compiled.close()
