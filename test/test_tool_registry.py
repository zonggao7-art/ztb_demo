# -*- coding: utf-8 -*-
"""ToolRegistry 单元测试 — 注册/发现/白名单/清单（纯离线，无基础设施依赖）。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.tools import tool as langchain_tool

from agent.tools.registry import ToolMeta, ToolRegistry, get_tool_whitelist


@langchain_tool
def _dummy_a(x: int) -> str:
    """dummy tool a"""
    return str(x)


@langchain_tool
def _dummy_b(x: int) -> str:
    """dummy tool b"""
    return str(x)


def _meta(name: str, tags: tuple = ()) -> ToolMeta:
    return ToolMeta(name=name, description=f"{name} desc", tags=frozenset(tags))


def test_register_and_get():
    reg = ToolRegistry()
    reg.register(_dummy_a, _meta("dummy_a"))
    assert reg.get("dummy_a") is _dummy_a
    assert reg.get("missing") is None
    assert reg.names() == ["dummy_a"]
    assert len(reg) == 1


def test_duplicate_register_raises():
    reg = ToolRegistry()
    reg.register(_dummy_a, _meta("dummy_a"))
    with pytest.raises(ValueError, match="重名"):
        reg.register(_dummy_a, _meta("dummy_a"))


def test_list_tools_tag_filter():
    reg = ToolRegistry()
    reg.register(_dummy_a, _meta("dummy_a", ("sql", "price")))
    reg.register(_dummy_b, _meta("dummy_b", ("knowledge", "rag")))

    assert [m.name for m in reg.list_tools()] == ["dummy_a", "dummy_b"]
    names = {m.name for m in reg.list_tools(tags={"sql"})}
    assert names == {"dummy_a"}
    names = {m.name for m in reg.list_tools(tags={"rag", "sql"})}
    assert names == {"dummy_a", "dummy_b"}  # 交集语义：任一 tag 命中即保留
    assert reg.list_tools(tags={"nope"}) == []


def test_to_langchain_tools_whitelist():
    reg = ToolRegistry()
    reg.register(_dummy_a, _meta(_dummy_a.name, ("sql",)))
    reg.register(_dummy_b, _meta(_dummy_b.name, ("rag",)))

    # 无白名单 → 全部放行
    assert len(reg.to_langchain_tools()) == 2
    # 空白名单 → 全部禁用
    assert reg.to_langchain_tools(whitelist=[]) == []
    # 指定白名单 → 只保留命中项
    tools = reg.to_langchain_tools(whitelist=[_dummy_b.name])
    assert [t.name for t in tools] == [_dummy_b.name]
    # tags + 白名单叠加
    assert reg.to_langchain_tools(tags={"sql"}, whitelist=[_dummy_b.name]) == []
    assert [t.name for t in reg.to_langchain_tools(tags={"sql"}, whitelist=[_dummy_a.name])] == [_dummy_a.name]


def test_to_manifest_shape():
    reg = ToolRegistry()
    reg.register(_dummy_a, _meta("dummy_a", ("sql",)))
    manifest = reg.to_manifest()
    assert len(manifest) == 1
    item = manifest[0]
    assert item["name"] == "dummy_a"
    assert item["description"] == "dummy_a desc"
    assert item["tags"] == ["sql"]
    assert item["readonly"] is True
    assert "x" in item["parameters"]["properties"]  # args_schema → JSON schema


def test_get_tool_whitelist_parsing():
    none_cfg = SimpleNamespace(agent_tools_whitelist="")
    assert get_tool_whitelist(none_cfg) is None

    some_cfg = SimpleNamespace(agent_tools_whitelist=" a, b,, c ")
    assert get_tool_whitelist(some_cfg) == ["a", "b", "c"]

    empty_all_cfg = SimpleNamespace(agent_tools_whitelist=",")
    assert get_tool_whitelist(empty_all_cfg) == []
