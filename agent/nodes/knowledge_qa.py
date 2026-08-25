"""
knowledge_qa — 专业知识问答节点。

直接调用现有 public_kb 模块的 RAG 引擎，保留其内部拒答逻辑和溯源引用。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage

from ..state import AgentState

logger = logging.getLogger(__name__)

# 全局单例，首次调用时惰性初始化
_rag_engine: Any = None


def _get_rag():
    """惰性获取 PublicKnowledgeRAG 单例。"""
    global _rag_engine
    if _rag_engine is None:
        from public_kb import PublicKnowledgeRAG
        _rag_engine = PublicKnowledgeRAG()
        # 确保已加载已有集合（不重新入库）并构建问答链
        _rag_engine.ensure_loaded()
        logger.info("knowledge_qa: PublicKnowledgeRAG 初始化完成")
    return _rag_engine


def node_knowledge_qa(state: AgentState) -> dict:
    """专业知识问答节点。

    调用现有 public_kb 模块的 RAG 引擎，
    保留其内部的 LCEL 检索链、拒答判断和溯源引用逻辑。

    Args:
        state: AgentState

    Returns:
        {"business_result": {...}, "messages": [AIMessage]}
    """
    messages = state.get("messages", [])
    if not messages:
        return {
            "business_result": {
                "branch": "knowledge_qa",
                "answer": "抱歉，没有收到您的问题，请重新输入。",
                "data": {"sources": []},
            },
        }

    question = str(messages[-1].content)
    logger.info("knowledge_qa: 处理问题 — %s", question[:80])

    try:
        rag = _get_rag()
        result = rag.query(question)
        answer = result.get("answer", "抱歉，无法回答该问题。")
        sources = result.get("sources", [])
        citations = result.get("citations", [])
        citation_validation = result.get("citation_validation")

        logger.info(
            "knowledge_qa: 回答完成，引用来源 %d 条", len(sources)
        )

        return {
            "business_result": {
                "branch": "knowledge_qa",
                "answer": answer,
                "data": {
                    "sources": sources,                 # legacy 视图
                    "citations": citations,             # 测评标准化引用
                    "citation_validation": citation_validation,  # 校验报告
                },
            },
            "messages": [AIMessage(content=answer)],
        }

    except RuntimeError as e:
        # 知识库未初始化
        logger.warning("knowledge_qa: RAG 未就绪 — %s", e)
        return {
            "business_result": {
                "branch": "knowledge_qa",
                "answer": "知识库尚未初始化，请先执行入库操作。",
                "data": {"sources": [], "error": str(e)},
            },
            "messages": [AIMessage(content="⚠️ 知识库尚未初始化，请联系管理员。")],
        }
