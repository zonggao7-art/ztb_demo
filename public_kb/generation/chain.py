"""LCEL question answering chain for the public knowledge base."""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnableLambda, RunnablePassthrough
from langchain_milvus import Milvus as MilvusVectorStore

from .citations import CitationValidator, build_citations
from ..config import Settings
from ..contracts import validate_question
from ..retrieval.reranker import Reranker
from ..retrieval.retriever import HybridRetriever, RetrievalResult
from .context import build_sources, format_docs
from .prompts import build_prompt


logger = logging.getLogger(__name__)


def build_chain(
    vector_store: MilvusVectorStore,
    llm: BaseChatModel,
    settings: Settings,
    collection: Optional[Any] = None,
    embeddings: Optional[Any] = None,
    *,
    reranker_class: Callable[..., Reranker],
) -> Any:
    """构建检索、拒答判断与回答生成组合的 LCEL 链。

    Args:
        vector_store: 已初始化的 Milvus 向量存储（langchain_milvus 包装器）。
        llm: LangChain 兼容的 ChatModel。
        settings: 全局配置。
        collection: MilvusClient 实例（用于 hybrid_search / search）。
        embeddings: Embedding 模型实例（用于生成稠密查询向量）。
        reranker_class: Reranker 构造类，便于兼容层注入或测试替换。

    Returns:
        可调用的 LCEL Runnable 链，invoke(question) → dict。
    """
    prompt = build_prompt(
        settings.system_prompt,
        enable_inline_citations=settings.enable_inline_citations,
    )
    validator = CitationValidator(settings.citation_rules)
    retriever = HybridRetriever(
        vector_store=vector_store,
        collection=collection,
        embeddings=embeddings,
        settings=settings,
        reranker=reranker_class(
            model=settings.reranker_model,
            api_key=settings.embedding_api_key,
            base_url=settings.embedding_base_url,
            timeout=settings.reranker_timeout,
            max_retries=settings.reranker_max_retries,
            retry_backoff_seconds=settings.reranker_retry_backoff_seconds,
        ),
    )

    def _retrieve(raw_question: str) -> RetrievalResult:
        return retriever.retrieve(raw_question)

    def _decide_and_answer(inputs: Dict[str, Any]) -> Dict[str, Any]:
        """核心决策节点：检索为空则拒答，有结果则走 LLM。"""
        result: RetrievalResult = inputs["retrieval"]
        docs_with_scores = result.docs
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
                "retrieval_diagnostics": result.diagnostics.to_dict(),
            }

        context = format_docs(docs_with_scores)
        sources = build_sources(docs_with_scores)
        citations = build_citations(docs_with_scores)

        answer_chain = prompt | llm | StrOutputParser()
        raw_answer: str = answer_chain.invoke({
            "context": context,
            "question": question,
        })
        answer = raw_answer.strip()

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
            "citations": [citation.to_dict() for citation in citations],
            "citation_validation": report.to_dict(),
            "retrieval_diagnostics": result.diagnostics.to_dict(),
        }

    chain = (
        {
            "retrieval": RunnableLambda(_retrieve),
            "question": RunnablePassthrough(),
        }
        | RunnableLambda(_decide_and_answer)
    )

    logger.info("LCEL 问答链构建完成（bge-m3 混合检索模式）")
    return chain
