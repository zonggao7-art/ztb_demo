"""知识库检索工具 — 对接 public_kb RAG 引擎。

注册两个粒度的工具：
  - search_public_kb: 检索级，返回 top-K 法规证据片段（含 chunk_uid/章节/引用），
    由调用方 Agent 自行组织答案 —— Agent 平台的原生形态
  - knowledge_qa:     能力级，完整走 RAG 问答链（内含拒答判断与标准化引用），
    适合简单场景一步到位
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import StructuredTool

from public_kb.config import Settings

from .base import ERR_INVALID_PARAMS, make_error_result, make_tool_result, wrap_async_tool, wrap_sync_tool
from .registry import GLOBAL_TOOL_REGISTRY, ToolMeta
from .schemas import KnowledgeQAInput, SearchPublicKBInput

logger = logging.getLogger(__name__)

SEARCH_PUBLIC_KB_DESC = (
    "招投标法规知识检索：在权威公共知识库（法律法规 PDF 语料）中检索与问题相关的"
    "法规证据片段，返回片段原文、所属文档/章节、chunk_uid 与相关度分数。"
    "适用于：招标方式、评标规则、投标流程、履约验收、法律责任等专业法规问题。"
    "返回的是证据片段而非成品答案，需要你综合片段后作答并注明来源。"
)

KNOWLEDGE_QA_DESC = (
    "招投标专业知识问答（一步到位）：基于权威法规知识库生成完整回答，"
    "内含拒答判断与【来源N】标准化引用。适用于需要直接给出成品回答的法规咨询场景；"
    "若你希望自己组织答案请改用 search_public_kb。"
)


def _get_rag() -> Any:
    """复用 knowledge_qa 节点的惰性单例（同一引擎实例，避免重复加载 Milvus 集合）。"""
    from ..nodes.knowledge_qa import _get_rag as _node_get_rag

    return _node_get_rag()


def _validate_question(question: str) -> str | None:
    if not question or not question.strip():
        return "question 不能为空"
    return None


def _chunks_result(chunks: list[dict[str, Any]]) -> dict:
    data: dict[str, Any] = {"chunks": chunks}
    if not chunks:
        data["note"] = "知识库中未检索到相关法规内容，可尝试换一种问法或放宽关键词"
    return make_tool_result(
        data=data,
        metadata={"source": "milvus.public_kb", "chunk_count": len(chunks)},
    )


# ── search_public_kb 实现 ──

def _search_public_kb_impl(question: str, top_k: int | None = None) -> dict:
    bad = _validate_question(question)
    if bad:
        return _invalid(bad)
    rag = _get_rag()
    if top_k is None:
        top_k = Settings().agent_tool_default_top_k
    chunks = rag.retrieve(question.strip(), top_k=top_k)
    return _chunks_result(chunks)


async def _search_public_kb_async_impl(question: str, top_k: int | None = None) -> dict:
    bad = _validate_question(question)
    if bad:
        return _invalid(bad)
    rag = _get_rag()
    if top_k is None:
        top_k = Settings().agent_tool_default_top_k
    chunks = await rag.retrieve_async(question.strip(), top_k=top_k)
    return _chunks_result(chunks)


def _invalid(message: str) -> dict:
    return make_error_result(ERR_INVALID_PARAMS, message)


# ── knowledge_qa 实现 ──

def _knowledge_qa_impl(question: str) -> dict:
    bad = _validate_question(question)
    if bad:
        return _invalid(bad)
    rag = _get_rag()
    result = rag.query(question.strip())
    return make_tool_result(
        data={
            "answer": result.get("answer", ""),
            "sources": result.get("sources", []),
            "citations": result.get("citations", []),
            "citation_validation": result.get("citation_validation"),
        },
        metadata={
            "source": "milvus.public_kb",
            "citation_count": len(result.get("citations", [])),
        },
    )


async def _knowledge_qa_async_impl(question: str) -> dict:
    bad = _validate_question(question)
    if bad:
        return _invalid(bad)
    rag = _get_rag()
    result = await rag.aquery(question.strip())
    return make_tool_result(
        data={
            "answer": result.get("answer", ""),
            "sources": result.get("sources", []),
            "citations": result.get("citations", []),
            "citation_validation": result.get("citation_validation"),
        },
        metadata={
            "source": "milvus.public_kb",
            "citation_count": len(result.get("citations", [])),
        },
    )


def register_knowledge_tools(registry=GLOBAL_TOOL_REGISTRY) -> None:
    """向注册中心注册知识库工具。"""
    registry.register(
        StructuredTool.from_function(
            func=wrap_sync_tool("search_public_kb", _search_public_kb_impl),
            coroutine=wrap_async_tool("search_public_kb", _search_public_kb_async_impl),
            args_schema=SearchPublicKBInput,
            name="search_public_kb",
            description=SEARCH_PUBLIC_KB_DESC,
            response_format="content_and_artifact",
        ),
        ToolMeta(
            name="search_public_kb",
            description=SEARCH_PUBLIC_KB_DESC,
            tags=frozenset({"knowledge", "rag", "retrieval"}),
        ),
    )
    registry.register(
        StructuredTool.from_function(
            func=wrap_sync_tool("knowledge_qa", _knowledge_qa_impl),
            coroutine=wrap_async_tool("knowledge_qa", _knowledge_qa_async_impl),
            args_schema=KnowledgeQAInput,
            name="knowledge_qa",
            description=KNOWLEDGE_QA_DESC,
            response_format="content_and_artifact",
        ),
        ToolMeta(
            name="knowledge_qa",
            description=KNOWLEDGE_QA_DESC,
            tags=frozenset({"knowledge", "rag", "qa"}),
        ),
    )
