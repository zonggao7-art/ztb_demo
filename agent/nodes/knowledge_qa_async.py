"""
knowledge_qa_async — 专业知识问答节点（异步版，阶段 2）。

与同步版 agent.nodes.knowledge_qa.node_knowledge_qa 对齐：
  - 结果结构完全一致：{"business_result": {...}, "messages": [...]}
  - 复用同一个 RAG 单例（_get_rag），不重复建立 Milvus 连接
差异仅在 I/O：
  - 单例初始化（Milvus load_existing）桥接到线程池，避免阻塞事件循环
  - 流式模式消费 rag.astream()；非流式继续使用 rag.aquery()
"""

from __future__ import annotations

import asyncio
import logging

from langchain_core.messages import AIMessage

from ..runtime.async_bridge import run_blocking
from ..streaming import EventType
from ..streaming.context import _STREAM_ACTIVE, emit
from ..state import AgentState

# 延迟导入：复用同步节点的 RAG 单例与初始化逻辑

logger = logging.getLogger(__name__)


async def node_knowledge_qa_async(state: AgentState) -> dict:
    """专业知识问答节点（异步）。

    调用 public_kb 的 async RAG 链路，
    保留其内部的混合检索、拒答判断和溯源引用逻辑。

    Args:
        state: AgentState

    Returns:
        {"business_result": {...}, "messages": [AIMessage]}
    """
    messages = state.get("messages", [])
    answer = ""
    citations: list[dict] = []
    sources: list[dict] = []
    if not messages:
        return {
            "business_result": {
                "branch": "knowledge_qa",
                "answer": "抱歉，没有收到您的问题，请重新输入。",
                "data": {"sources": []},
            },
        }

    question = str(messages[-1].content)
    logger.info("knowledge_qa(async): 处理问题 — %s", question[:80])

    # 延迟导入：复用同步节点的 RAG 单例与初始化逻辑
    from .knowledge_qa import _get_rag

    try:
        emit(EventType.STAGE, {"stage": "retrieval_start"})

        rag = await run_blocking(_get_rag)  # 首次初始化桥接到线程池
        parts: list[str] = []
        citations: list[dict] = []
        sources: list[dict] = []
        citation_validation: dict | None = None
        answer = ""

        if not hasattr(rag, "astream"):
            result = await rag.aquery(question)
            answer = result.get("answer", "抱歉，无法回答该问题。")
            sources = result.get("sources", [])
            citations = result.get("citations", [])
            citation_validation = result.get("citation_validation")
        else:
            async for event in rag.astream(question):
                if event.type is EventType.STAGE:
                    emit(EventType.STAGE, event.payload)
                elif event.type is EventType.RETRIEVAL:
                    emit(EventType.RETRIEVAL, event.payload)
                elif event.type is EventType.TOKEN:
                    delta = str(event.payload.get("delta", ""))
                    parts.append(delta)
                    emit(EventType.TOKEN, {"delta": delta})
                elif event.type is EventType.CITATIONS:
                    citations = event.payload.get("citations", [])
                    emit(EventType.CITATIONS, {"citations": citations})
                elif event.type is EventType.FINAL:
                    final_result = event.payload.get("result") or {}
                    sources = final_result.get("sources", [])
                    citation_validation = final_result.get("citation_validation")
                    citations = final_result.get("citations", citations)
                    answer = final_result.get(
                        "answer",
                        "".join(parts).strip() or "抱歉，无法回答该问题。",
                    )
                    break
            if not answer:
                answer = "".join(parts).strip() or "抱歉，无法回答该问题。"

    except asyncio.CancelledError:
        logger.warning("knowledge_qa(async): 流式任务取消 question=%.80s", question)
        raise

    except RuntimeError as e:
        # 知识库未初始化
        logger.warning("knowledge_qa(async): RAG 未就绪 — %s", e)
        return {
            "business_result": {
                "branch": "knowledge_qa",
                "answer": "知识库尚未初始化，请先执行入库操作。",
                "data": {"sources": [], "error": str(e)},
            },
            "messages": [AIMessage(content="⚠️ 知识库尚未初始化，请联系管理员。")],
        }

    if _STREAM_ACTIVE.get():
        emit(EventType.FINAL, {
            "answer": answer,
            "business_result": {
                "branch": "knowledge_qa",
                "sources": len(sources),
                "citations": len(citations),
            },
        })

    logger.info(
            "knowledge_qa(async): 回答完成，引用来源 %d 条", len(sources)
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
