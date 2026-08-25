"""
Agent 可插拔骨架 — 基于 LangGraph StateGraph 的招投标智能助手。

对外统一入口：
    from agent import AgentGraph

    agent = AgentGraph()
    result = agent.invoke("招标方式有哪些？")

CLI 入口：
    python -m agent --question "招标方式有哪些？"
    python -m agent --interactive
"""

from .graph import AgentGraph

__all__ = ["AgentGraph"]
