"""
LCEL 问答链 — 基于 LangChain 1.0+ Runnable 接口的 RAG 问答流水线。

严格遵守：
  - 使用 | 运算符拼接 Runnable（LCEL 语法）
  - 禁止使用任何 langchain.chains 下的旧版 Chain
  - 检索 + 拒答判断 + LLM 调用 + 格式化输出 一体化

扩展预留：
  - retriever 可替换为 BM25 混合检索（替换 _retrieve 方法）
  - 可在 RunnableLambda 前插入 Reranker 节点
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple, Optional

from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda, RunnablePassthrough
from langchain_milvus import Milvus as MilvusVectorStore
from .citations import CitationValidator, build_citations
from .config import Settings
from .contracts import (
    RetrievalDiagnostics,
    RetrievalMode,
    validate_question,
)
from .retrieval.reranker.siliconflow import SiliconFlowReranker
from .retrieval.entities import entity_to_doc, normalize_hit_entity
from .retrieval.milvus_search import (
    hybrid_search_with_full_fields,
    search_with_full_fields,
)
from .retrieval.fallback import dense_only_retrieve
from .retrieval.strategies import adaptive_threshold
from .retrieval.retriever import HybridRetriever, HybridRetrievalError


# Historical callers and tests patch this private symbol in qa_chain. Keep the
# alias while the module is split into retrieval and generation pipelines.
_SiliconFlowReranker = SiliconFlowReranker

_normalize_hit_entity = normalize_hit_entity
_entity_to_doc = entity_to_doc
_search_with_full_fields = search_with_full_fields
_hybrid_search_with_full_fields = hybrid_search_with_full_fields
_adaptive_threshold = adaptive_threshold
_dense_only_retrieve = dense_only_retrieve

logger = logging.getLogger(__name__)


# ============================================================
#  提示词模板（system 部分统一取自 config.Settings.system_prompt）
# ============================================================

# 内联引用指令（enable_inline_citations 开启时追加到 System Prompt）
INLINE_CITATION_INSTRUCTION = (
    "回答时必须在相关结论句末标注引用来源编号，格式如【来源1】【来源2】。\n"
    "未使用的参考资料不要标注其编号。"
)

USER_TEMPLATE = """参考资料：
{context}

用户问题：{question}"""


def _build_prompt(system_text: str, enable_inline_citations: bool = True) -> ChatPromptTemplate:
    """构建问答提示词模板。

    Args:
        system_text: system 提示词正文（来自 Settings.system_prompt）。
        enable_inline_citations: 是否要求 LLM 在回答中内联标注【来源N】。
    """
    system = system_text
    if enable_inline_citations:
        system += "\n\n" + INLINE_CITATION_INSTRUCTION
    return ChatPromptTemplate.from_messages([
        ("system", system),
        ("user",   USER_TEMPLATE),
    ])


# ============================================================
#  格式化工具函数
# ============================================================

def _format_docs(docs_with_scores: List[Tuple[Document, float]]) -> str:
    """将检索到的文档列表格式化为拼接上下文字符串。

    Args:
        docs_with_scores: (Document, similarity_score) 列表。

    Returns:
        带来源标注的拼接文本。
    """
    parts: List[str] = []
    for i, (doc, score) in enumerate(docs_with_scores, 1):
        doc_name = doc.metadata.get("doc_name", "未知文档")
        chapter = doc.metadata.get("chapter", "未知章节")
        parts.append(
            f"[来源{i}] 文档: {doc_name} | 章节: {chapter} | 相关度: {score:.2%}\n"
            f"{doc.page_content}"
        )
    return "\n\n---\n\n".join(parts)


def _build_sources(
    docs_with_scores: List[Tuple[Document, float]],
) -> List[Dict[str, Any]]:
    """从检索结果构建引用来源列表（legacy 视图，保持向后兼容）。

    Args:
        docs_with_scores: (Document, similarity_score) 列表。

    Returns:
        结构化来源信息列表。
    """
    return [
        {
            "doc": doc.metadata.get("doc_name", "未知文档"),
            "chapter": doc.metadata.get("chapter", "未知章节"),
            "chunk_index": doc.metadata.get("chunk_index", -1),
            "content_snippet": doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content,
            "score": round(score, 4),
        }
        for doc, score in docs_with_scores
    ]


class _RetrievalTrace:
    """一次检索调用的结果与诊断状态。"""

    def __init__(self) -> None:
        self.docs: List[Tuple[Document, float]] = []
        self.diagnostics = RetrievalDiagnostics(
            retrieval_mode=RetrievalMode.REFUSAL,
        )


# ============================================================
#  LCEL 问答链构建
# ============================================================

def build_qa_chain(
    vector_store: MilvusVectorStore,
    llm: BaseChatModel,
    settings: Settings,
    collection: Optional[Any] = None,
    embeddings: Optional[Any] = None,
) -> Any:
    """构建完整的 LCEL RAG 问答链（bge-m3 混合检索版）。

    链结构：
      question
        │
        ├─→ _retrieve(question) ─→ docs_with_scores
        │     │
        │     ├─ 稠密向量检索 (COSINE, k=30, nprobe=32)
        │     ├─ 稀疏向量检索 (BM25, k=30)
        │     ├─ RRF 融合 (k=60, 取 Top-30)
        │     ├─ Reranker 精排 (bge-reranker-v2-m3)
        │     └─ 动态阈值过滤
        │
        └─→ _decide_and_answer(docs_with_scores, question)
              │
              ├── 无相关结果 → 直接返回拒答
              └── 有结果 → prompt | llm | StrOutputParser → 返回回答 + 来源

    Args:
        vector_store: 已初始化的 Milvus 向量存储（langchain_milvus 包装器）。
        llm: LangChain 兼容的 ChatModel。
        settings: 全局配置。
        collection: MilvusClient 实例（用于 hybrid_search / search）。
        embeddings: Embedding 模型实例（用于生成稠密查询向量）。

    Returns:
        可调用的 LCEL Runnable 链，invoke(question) → dict。
    """
    prompt = _build_prompt(
        settings.system_prompt,
        enable_inline_citations=settings.enable_inline_citations,
    )

    # 引用溯源校验器（fail-soft：只产出结构化报告，不阻断回答）
    validator = CitationValidator(settings.citation_rules)

    retriever = HybridRetriever(
        vector_store=vector_store,
        collection=collection,
        embeddings=embeddings,
        settings=settings,
        reranker=_SiliconFlowReranker(
            model=settings.reranker_model,
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_base_url,
        ),
    )

    # ── 阶段 1: 混合检索（RunnableLambda 包装）──
    def _retrieve(raw_question: str) -> _RetrievalTrace:
        result = retriever.retrieve(raw_question)
        trace = _RetrievalTrace()
        trace.docs = result.docs
        trace.diagnostics = result.diagnostics
        return trace

    # ── 阶段 2: 判断 + 回答 ──
    def _decide_and_answer(inputs: Dict[str, Any]) -> Dict[str, Any]:
        """核心决策节点：检索为空则拒答，有结果则走 LLM。

        返回结构（引用溯源标准化）：
            {
                "answer": str,                    # 回答 / 拒答提示（含【来源N】内联标记）
                "sources": list,                  # legacy 视图（向后兼容）
                "citations": list,                # 标准化引用（chunk_id/chunk_uid/数据源位置/完整原文/元数据）
                "citation_validation": dict,      # 校验规则结构化报告（R1-R7，fail-soft）
            }
        """
        trace: _RetrievalTrace = inputs["retrieval"]
        docs_with_scores = trace.docs
        question = validate_question(inputs["question"])

        if not docs_with_scores:
            logger.info("检索结果不足，触发拒答")
            refusal_answer = "抱歉，公共知识库中暂无相关内容，无法提供可靠回答。"
            refusal_report = validator.validate(
                [], refusal_answer, [], is_refusal=True,
            )
            return {
                "answer": refusal_answer,
                "sources": [],
                "citations": [],
                "citation_validation": refusal_report.to_dict(),
                "retrieval_diagnostics": trace.diagnostics.to_dict(),
            }

        context = _format_docs(docs_with_scores)
        sources = _build_sources(docs_with_scores)
        citations = build_citations(docs_with_scores)

        answer_chain = prompt | llm | StrOutputParser()
        raw_answer: str = answer_chain.invoke({
            "context": context,
            "question": question,
        })
        answer = raw_answer.strip()

        # 引用溯源校验（无遗漏 / 无错误关联，fail-soft）
        context_ids = [
            doc.metadata.get("chunk_id") for doc, _ in docs_with_scores
        ]
        report = validator.validate(citations, answer, context_ids)

        logger.info(
            "引用校验: 上下文=%d块, 引用=%d条, 标记=%s, 全部通过=%s",
            len(context_ids), len(citations),
            report.cited_markers, report.all_passed,
        )

        return {
            "answer": answer,
            "sources": sources,
            "citations": [c.to_dict() for c in citations],
            "citation_validation": report.to_dict(),
            "retrieval_diagnostics": trace.diagnostics.to_dict(),
        }

    # ── 用 LCEL 管道符号 | 拼接 ──
    chain = (
        {
            "retrieval": RunnableLambda(_retrieve),
            "question": RunnablePassthrough(),
        }
        | RunnableLambda(_decide_and_answer)
    )

    logger.info("LCEL 问答链构建完成（bge-m3 混合检索模式）")
    return chain
