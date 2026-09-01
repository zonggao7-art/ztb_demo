"""工具注册中心 — 工具库唯一入口（注册 / 发现 / 白名单导出 / 清单）。

Usage:
    from agent.tools import GLOBAL_TOOL_REGISTRY, register_default_tools

    register_default_tools()
    tools = GLOBAL_TOOL_REGISTRY.to_langchain_tools()   # 交给 LLM bind_tools
    meta  = GLOBAL_TOOL_REGISTRY.list_tools()           # 运维/平台侧发现
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool

from public_kb.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolMeta:
    """工具元数据 — 注册中心与后续平台化（MCP/OpenAPI 清单）共用的描述层。"""

    name: str
    description: str
    tags: frozenset[str] = field(default=frozenset())
    readonly: bool = True
    version: str = "1.0.0"


class ToolRegistry:
    """进程内工具注册中心。

    职责：唯一性注册、按名获取、按 tags 发现、白名单过滤导出 LangChain 工具、
    输出平台化清单（to_manifest，为 MCP/FastAPI 暴露预留）。
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._meta: dict[str, ToolMeta] = {}

    # ── 注册 ──

    def register(self, tool: BaseTool, meta: ToolMeta) -> None:
        """注册工具；name 冲突视为编程错误，直接抛出。"""
        name = meta.name or tool.name
        if name in self._tools:
            raise ValueError(f"工具重名注册: {name}")
        if name != tool.name:
            logger.warning("工具 meta.name(%s) 与 tool.name(%s) 不一致", name, tool.name)
        self._tools[name] = tool
        self._meta[name] = meta
        logger.info(
            "[TOOL_REGISTRY] 注册 %s (tags=%s, readonly=%s)",
            name, sorted(meta.tags), meta.readonly,
        )

    # ── 发现 ──

    def get(self, name: str) -> BaseTool | None:
        """按名获取工具。"""
        return self._tools.get(name)

    def meta(self, name: str) -> ToolMeta | None:
        return self._meta.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def list_tools(
        self, *, tags: set[str] | None = None
    ) -> list[ToolMeta]:
        """列出工具元数据；tags 给定时做交集过滤。"""
        metas = list(self._meta.values())
        if tags:
            metas = [m for m in metas if m.tags & set(tags)]
        return metas

    # ── 导出 ──

    def to_langchain_tools(
        self,
        *,
        tags: set[str] | None = None,
        whitelist: list[str] | None = None,
    ) -> list[BaseTool]:
        """导出可 bind_tools 的 LangChain 工具列表。

        Args:
            tags: 能力标签过滤（交集语义）。
            whitelist: 工具名白名单；None 表示未配置（全部放行）。
                显式空列表表示全部禁用。
        """
        metas = self.list_tools(tags=tags)
        if whitelist is not None:
            allowed = set(whitelist)
            metas = [m for m in metas if m.name in allowed]
        return [self._tools[m.name] for m in metas]

    def to_manifest(self) -> list[dict[str, Any]]:
        """平台化清单（MCP / OpenAPI / 运维面板预留输出格式）。"""
        manifest: list[dict[str, Any]] = []
        for m in self._meta.values():
            tool = self._tools[m.name]
            schema = getattr(tool, "args_schema", None)
            manifest.append(
                {
                    "name": m.name,
                    "description": m.description,
                    "tags": sorted(m.tags),
                    "readonly": m.readonly,
                    "version": m.version,
                    "parameters": schema.model_json_schema() if schema else {},
                }
            )
        return manifest

    def __len__(self) -> int:
        return len(self._tools)


GLOBAL_TOOL_REGISTRY = ToolRegistry()


def get_tool_whitelist(settings: Settings | None = None) -> list[str] | None:
    """从配置读取工具白名单。

    Returns:
        None  — 未配置白名单（全部放行）
        list  — 配置了白名单（可为空列表 = 全部禁用）
    """
    raw = (settings or Settings()).agent_tools_whitelist.strip()
    if not raw:
        return None
    return [item.strip() for item in raw.split(",") if item.strip()]
