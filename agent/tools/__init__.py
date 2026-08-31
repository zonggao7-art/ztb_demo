"""agent.tools — 统一工具层（蓝图 P1 工具化落地）。

把 RAG 检索与 SQL 检索封装为标准 LangChain Tool，并由 ToolRegistry 统一维护。

Usage:
    from agent.tools import get_enabled_tools

    tools = get_enabled_tools()                 # 注册 + 白名单过滤后的工具列表
    llm_with_tools = llm.bind_tools(tools)

    # 或显式操作注册中心
    from agent.tools import GLOBAL_TOOL_REGISTRY, register_default_tools
    register_default_tools()
    GLOBAL_TOOL_REGISTRY.list_tools(tags={"sql"})
    GLOBAL_TOOL_REGISTRY.to_manifest()          # 平台化清单（MCP/OpenAPI 预留）
"""

from __future__ import annotations

from langchain_core.tools import BaseTool

from .base import (
    ERR_DB_UNAVAILABLE,
    ERR_INTERNAL,
    ERR_INVALID_PARAMS,
    ERR_KB_NOT_INITIALIZED,
    ERR_TIMEOUT,
    ToolResult,
    make_error_result,
    make_tool_result,
    render_tool_content,
)
from .registry import (
    GLOBAL_TOOL_REGISTRY,
    ToolMeta,
    ToolRegistry,
    get_tool_whitelist,
)

__all__ = [
    "GLOBAL_TOOL_REGISTRY",
    "ToolMeta",
    "ToolRegistry",
    "ToolResult",
    "get_tool_whitelist",
    "get_enabled_tools",
    "register_default_tools",
    "make_tool_result",
    "make_error_result",
    "render_tool_content",
    "ERR_DB_UNAVAILABLE",
    "ERR_INTERNAL",
    "ERR_INVALID_PARAMS",
    "ERR_KB_NOT_INITIALIZED",
    "ERR_TIMEOUT",
]

_REGISTERED = False


def register_default_tools() -> None:
    """装配全部内置工具（幂等；导入本包无副作用，显式调用才注册）。"""
    global _REGISTERED
    if _REGISTERED:
        return
    from .knowledge import register_knowledge_tools
    from .price_db import register_price_db_tools

    register_knowledge_tools(GLOBAL_TOOL_REGISTRY)
    register_price_db_tools(GLOBAL_TOOL_REGISTRY)
    _REGISTERED = True


def get_enabled_tools(*, tags: set[str] | None = None) -> list[BaseTool]:
    """注册全部工具并按配置白名单过滤，返回可 bind_tools 的工具列表。

    Agent 平台/原型的统一取用入口：白名单来自 .env AGENT_TOOLS_WHITELIST
    （空 = 全部放行），收口 public_kb.config.Settings。
    """
    register_default_tools()
    return GLOBAL_TOOL_REGISTRY.to_langchain_tools(
        tags=tags,
        whitelist=get_tool_whitelist(),
    )
