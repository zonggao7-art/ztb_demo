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
from pymilvus import AnnSearchRequest, RRFRanker

from .citations import CitationValidator, build_citations
from .config import Settings
from .contracts import (
    RerankerStatus,
    RetrievalDiagnostics,
    RetrievalMode,
    validate_question,
)
from .retrieval.reranker.protocol import Reranker
from .retrieval.reranker.siliconflow import SiliconFlowReranker
from .retrieval.entities import entity_to_doc, normalize_hit_entity
from .retrieval.milvus_search import (
    hybrid_search_with_full_fields,
    search_with_full_fields,
)


# Historical callers and tests patch this private symbol in qa_chain. Keep the
# alias while the module is split into retrieval and generation pipelines.
_SiliconFlowReranker = SiliconFlowReranker

_normalize_hit_entity = normalize_hit_entity
_entity_to_doc = entity_to_doc
_search_with_full_fields = search_with_full_fields
_hybrid_search_with_full_fields = hybrid_search_with_full_fields

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


class HybridRetrievalError(RuntimeError):
    """严格验证模式下，混合检索未按契约执行。"""


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

    # ── Reranker 客户端（延迟初始化）──
    _reranker: Optional[Reranker] = None

    def _get_reranker() -> "Reranker":
        nonlocal _reranker
        if _reranker is None:
            _reranker = _SiliconFlowReranker(
                model=settings.reranker_model,
                api_key=settings.embedding_api_key,
                base_url=settings.embedding_base_url,
            )
        return _reranker

    # Schema 能力只探测一次；真实集成测试可通过重建问答链重新探测。
    _has_sparse_cache: Optional[bool] = None

    def _dense_fallback(
        question: str,
        reason: str,
        dense_vec: Optional[List[float]] = None,
    ) -> _RetrievalTrace:
        docs = _dense_only_retrieve(
            question,
            vector_store,
            settings,
            collection,
            embeddings,
            dense_vec=dense_vec,
        )
        trace = _RetrievalTrace()
        trace.docs = docs
        trace.diagnostics = RetrievalDiagnostics(
            retrieval_mode=(
                RetrievalMode.DENSE_NATIVE
                if collection is not None and embeddings is not None
                else RetrievalMode.DENSE_LANGCHAIN
            ),
            dense_count=len(docs),
            fallback_reason=reason,
        )
        return trace

    # ── 阶段 1: 混合检索（RunnableLambda 包装）──
    def _retrieve(raw_question: str) -> _RetrievalTrace:
        """稠密 COSINE + 服务端 BM25 → RRF → 可选 Reranker。"""
        nonlocal _has_sparse_cache
        question = validate_question(raw_question)

        if collection is None or embeddings is None:
            logger.info("未提供原生 collection/embeddings，使用纯稠密检索")
            return _dense_fallback(question, "native_collection_or_embeddings_missing")

        dense_vec: Optional[List[float]] = None
        try:
            dense_vec = embeddings.embed_query(question)

            if _has_sparse_cache is None:
                collection_info = collection.describe_collection(settings.collection_name)
                field_names = {
                    field.get("name", "")
                    for field in collection_info.get("fields", [])
                }
                _has_sparse_cache = "sparse_vector" in field_names

            if not _has_sparse_cache:
                reason = "sparse_vector_field_missing"
                if settings.strict_hybrid_validation:
                    raise HybridRetrievalError(reason)
                logger.info("当前 Schema 无稀疏向量字段，使用纯稠密检索")
                return _dense_fallback(question, reason, dense_vec)

            dense_req = AnnSearchRequest(
                data=[dense_vec],
                anns_field="vector",
                param={
                    "metric_type": "COSINE",
                    "params": {"nprobe": settings.nprobe},
                },
                limit=settings.hybrid_dense_limit,
            )
            sparse_req = AnnSearchRequest(
                data=[question],
                anns_field="sparse_vector",
                param={"metric_type": "BM25", "params": {}},
                limit=settings.hybrid_sparse_limit,
            )

            raw_hits = hybrid_search_with_full_fields(
                collection,
                settings,
                reqs=[dense_req, sparse_req],
                ranker=RRFRanker(k=settings.rrf_k),
                limit=settings.hybrid_fusion_limit,
            )
            candidates: List[Tuple[str, float, dict]] = []
            for hit in raw_hits:
                entity = normalize_hit_entity(hit.entity)
                candidates.append((str(entity.get("text", "")), hit.score, entity))

            trace = _RetrievalTrace()
            if not candidates:
                trace.diagnostics = RetrievalDiagnostics(
                    retrieval_mode=RetrievalMode.REFUSAL,
                    dense_count=settings.hybrid_dense_limit,
                    sparse_count=settings.hybrid_sparse_limit,
                )
                return trace

            reranker = _get_reranker()
            reranked = reranker.rerank(
                query=question,
                documents=[candidate[0] for candidate in candidates],
                top_k=settings.retrieval_top_k,
            )

            if reranker.last_status is RerankerStatus.SUCCESS and reranked:
                threshold = _adaptive_threshold(reranked[0]["relevance_score"])
                results: List[Tuple[Document, float]] = []
                for item in reranked:
                    score = float(item["relevance_score"])
                    index = int(item["index"])
                    if score >= threshold and 0 <= index < len(candidates):
                        _, rrf_score, entity = candidates[index]
                        doc = entity_to_doc(entity, score)
                        doc.metadata["rrf_score"] = round(rrf_score, 4)
                        doc.metadata["score_type"] = "reranker"
                        results.append((doc, score))
                trace.docs = results
                trace.diagnostics = RetrievalDiagnostics(
                    retrieval_mode=RetrievalMode.HYBRID_RERANK,
                    dense_count=settings.hybrid_dense_limit,
                    sparse_count=settings.hybrid_sparse_limit,
                    fusion_count=len(candidates),
                    reranker_status=RerankerStatus.SUCCESS,
                    threshold=threshold,
                )
                return trace

            # Reranker 失败时保留 RRF 排序，不构造虚假相关度分数。
            results = []
            for _, rrf_score, entity in candidates[:settings.retrieval_top_k]:
                doc = entity_to_doc(entity, rrf_score)
                doc.metadata["rrf_score"] = round(rrf_score, 4)
                doc.metadata["score_type"] = "rrf"
                results.append((doc, rrf_score))
            trace.docs = results
            trace.diagnostics = RetrievalDiagnostics(
                retrieval_mode=RetrievalMode.HYBRID_RRF,
                dense_count=settings.hybrid_dense_limit,
                sparse_count=settings.hybrid_sparse_limit,
                fusion_count=len(candidates),
                reranker_status=reranker.last_status,
                fallback_reason="reranker_failed",
            )
            return trace

        except HybridRetrievalError:
            raise
        except Exception as error:
            if settings.strict_hybrid_validation:
                raise HybridRetrievalError(str(error)) from error
            logger.warning("混合检索异常 (%s)，回退到纯稠密检索", error)
            return _dense_fallback(question, f"hybrid_error:{type(error).__name__}", dense_vec)

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


# ============================================================
#  动态阈值 & 降级兜底
# ============================================================

def _adaptive_threshold(top_score: float) -> float:
    """基于 Reranker 最高分动态决定过滤阈值。

    策略：
    - top1 ≥ 0.75 → 高置信，放宽至 0.40，允许低分 chunk 补充上下文
    - top1 ≥ 0.50 → 中等置信，阈值 0.45
    - top1 < 0.50 → 直接使用 0.50（实际由 _decide_and_answer 判定是否拒答）
    """
    if top_score >= 0.75:
        return 0.40
    if top_score >= 0.50:
        return 0.45
    return 0.50


def _dense_only_retrieve(
    question: str,
    vector_store: MilvusVectorStore,
    settings: Settings,
    collection: Optional[Any] = None,
    embeddings: Optional[Any] = None,
    dense_vec: Optional[List[float]] = None,
) -> List[Tuple[Document, float]]:
    """降级检索：优先使用 pymilvus 原生 search（可获取动态元数据），
    失败时回退到 langchain_milvus similarity_search_with_score。

    Args:
        question: 用户问题。
        vector_store: Milvus 向量存储（langchain_milvus 包装器）。
        settings: 全局配置。
        collection: MilvusClient（可选，用于获取元数据）。
        embeddings: Embedding 模型实例（可选，用于生成查询向量）。
        dense_vec: 已生成的稠密查询向量；提供时避免重复调用 Embedding。

    Returns:
        (Document, score) 列表，含完整元数据，经阈值过滤。
    """
    # ── 方案 A: pymilvus 原生搜索（可获取动态字段 metadata）──
    if collection is not None and embeddings is not None:
        try:
            query_vector = dense_vec if dense_vec is not None else embeddings.embed_query(question)
            raw_hits = search_with_full_fields(
                collection, settings,
                data=[query_vector],
                anns_field="vector",
                search_params={
                    "metric_type": "COSINE",
                    "params": {"nprobe": settings.nprobe},
                },
                limit=settings.hybrid_dense_limit,
            )
            results: List[Tuple[Document, float]] = []
            for hit in raw_hits:
                if hit.score >= settings.similarity_threshold:
                    doc = entity_to_doc(hit.entity, hit.score)
                    doc.metadata["score"] = round(hit.score, 4)
                    results.append((doc, hit.score))
            logger.info(
                "pymilvus稠密检索: %d条命中, 过滤后=%d条 (threshold=%.2f)",
                len(raw_hits), len(results), settings.similarity_threshold,
            )
            return results[:settings.retrieval_top_k]
        except Exception as e:
            logger.warning("pymilvus 原生检索失败 (%s)，回退到 langchain_milvus", e)

    # ── 方案 B: langchain_milvus 降级（metadata 可能不完整）──
    try:
        raw = vector_store.similarity_search_with_score(
            question, k=settings.hybrid_dense_limit,
        )
    except Exception as e:
        logger.warning("langchain_milvus 检索也失败: %s", e)
        return []

    filtered = [
        (doc, score) for doc, score in raw
        if score >= settings.similarity_threshold
    ]
    logger.info(
        "langchain_milvus降级检索: 原始=%d条, 过滤后=%d条 (threshold=%.2f)",
        len(raw), len(filtered), settings.similarity_threshold,
    )
    return filtered[:settings.retrieval_top_k]
