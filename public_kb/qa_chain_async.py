"""
异步 LCEL 问答链（阶段 2）— build_async_qa_chain / AsyncRAGPipeline。

与同步链 public_kb.qa_chain.build_qa_chain 逐阶段对齐：
  混合检索（稠密 COSINE + 稀疏 BM25 → RRF 融合）
    → Reranker 精排 → 动态阈值过滤
    → 拒答判断 → prompt | llm | StrOutputParser
    → 引用溯源校验（R1-R7，fail-soft）

业务语义零退化的保证方式：
  - 所有纯函数（prompt 构建 / 格式化 / entity→Document / 动态阈值 /
    citations 构建 / 校验器）直接复用 qa_chain.py，不复制逻辑；
  - 拒答案文、结果 dict 结构、日志口径与同步版一致。

仅 I/O 方式不同（手册 §阶段2 步骤 3）：
  - Embedding: aembed_query（原生异步 HTTP，受 "embedding" 信号量约束）
  - Milvus describe/hybrid_search/search: run_blocking 桥接线程池，
    受 "milvus_search" 信号量约束
  - query 向量化与 schema 探测相互独立 → gather_limited(limit=2) 并行，
    压缩首字延迟
  - Reranker: AsyncSiliconFlowReranker（httpx.AsyncClient）
  - LLM: answer chain 走 ainvoke；流式走 astream（供 rag_engine.astream 使用）
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_milvus import Milvus as MilvusVectorStore
from pymilvus import AnnSearchRequest, RRFRanker

from .citations import CitationValidator, build_citations
from .config import Settings
from .qa_chain import (
    _adaptive_threshold,
    _build_prompt,
    _build_sources,
    _entity_to_doc,
    _format_docs,
    _hybrid_search_with_full_fields,
    _normalize_hit_entity,
)

logger = logging.getLogger(__name__)

# ── agent.runtime 桥接：缺席时本地兜底（独立运行 public_kb 场景） ──
try:
    from agent.runtime.async_bridge import gather_limited, run_blocking
    from agent.runtime.concurrency import get_or_register as _rt_get_or_register
except Exception:  # pragma: no cover — agent 包不可用时降级
    gather_limited = None
    run_blocking = None
    _rt_get_or_register = None
    _LOCAL_SEMAPHORES: Dict[str, asyncio.Semaphore] = {}

    async def run_blocking(function, /, *args, executor=None, **kwargs):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(executor, lambda: function(*args, **kwargs))

    async def gather_limited(coros, *, limit, return_exceptions=True):
        sem = asyncio.Semaphore(limit)

        async def _wrap(c):
            async with sem:
                return await c

        return await asyncio.gather(
            *(_wrap(c) for c in coros), return_exceptions=return_exceptions
        )

    def _rt_get_or_register(name, limit):
        sem = _LOCAL_SEMAPHORES.get(name)
        if sem is None:
            sem = asyncio.Semaphore(limit)
            _LOCAL_SEMAPHORES[name] = sem
        return sem


class AsyncRAGPipeline:
    """异步 RAG 流水线 — 检索/回答/流式回答/结果组装 各步骤可独立调用。

    rag_engine.aquery() 用它做非流式问答；
    rag_engine.astream() 额外使用 stream_answer() 产出 token 增量。
    """

    def __init__(
        self,
        vector_store: MilvusVectorStore,
        llm: BaseChatModel,
        settings: Settings,
        collection: Optional[Any] = None,
        embeddings: Optional[Any] = None,
        reranker: Optional[Any] = None,
    ) -> None:
        self.vector_store = vector_store
        self.llm = llm
        self.settings = settings
        self.collection = collection
        self.embeddings = embeddings
        self.prompt = _build_prompt(
            settings.system_prompt,
            enable_inline_citations=settings.enable_inline_citations,
        )
        self.validator = CitationValidator(settings.citation_rules)
        self._reranker = reranker

    # ── 基础设施 ──────────────────────────────────────────────

    def _semaphore(self, name: str, limit: int) -> asyncio.Semaphore:
        return _rt_get_or_register(name, max(1, int(limit)))

    def _get_reranker(self) -> Any:
        if self._reranker is None:
            from .reranker import AsyncSiliconFlowReranker

            self._reranker = AsyncSiliconFlowReranker.from_settings(self.settings)
        return self._reranker

    # ── 检索阶段 ──────────────────────────────────────────────

    async def embed_query_async(self, question: str) -> List[float]:
        """生成稠密查询向量（异步原生 + embedding 并发限流）。"""
        async with self._semaphore("embedding", self.settings.embedding_max_concurrency):
            return await self.embeddings.aembed_query(question)

    async def _describe_collection_async(self) -> Dict[str, Any]:
        """探测集合 schema（阻塞调用桥接到线程池）。"""
        async with self._semaphore("milvus_search", self.settings.milvus_max_concurrency):
            return await run_blocking(
                self.collection.describe_collection,
                self.settings.collection_name,
            )

    async def _hybrid_search_async(self, reqs: List[Any], ranker: Any, limit: int) -> List[Any]:
        """RRF 混合检索（pymilvus 同步 SDK 桥接线程池，全字段输出优先）。"""
        async with self._semaphore("milvus_search", self.settings.milvus_max_concurrency):
            return await run_blocking(
                _hybrid_search_with_full_fields,
                self.collection,
                self.settings,
                reqs=reqs,
                ranker=ranker,
                limit=limit,
            )

    async def retrieve_async(self, question: str) -> List[Tuple[Document, float]]:
        """异步混合检索：稠密+稀疏 → RRF → Reranker 精排 → 动态阈值过滤。

        与同步 _retrieve 的分支与降级路径一一对应。
        """
        s = self.settings

        if self.collection is None or self.embeddings is None:
            logger.info("未提供原生 collection/embeddings，使用纯稠密检索")
            return await self._dense_only_retrieve_async(question)

        try:
            # 1. query 向量化 与 schema 探测 相互独立 → 并行执行
            results = await gather_limited(
                [self.embed_query_async(question), self._describe_collection_async()],
                limit=2,
            )
            dense_vec, collection_info = results[0], results[1]
            if isinstance(dense_vec, BaseException):
                raise dense_vec
            if isinstance(collection_info, BaseException):
                raise collection_info

            # 2. 检查稀疏向量字段是否存在（旧 schema 可能没有）
            field_names = [
                f.get("name", "") for f in collection_info.get("fields", [])
            ]
            has_sparse = "sparse_vector" in field_names
            if not has_sparse:
                logger.info("当前 Schema 无稀疏向量字段，使用稠密+Reranker 模式")
                return await self._dense_only_retrieve_async(question)

            # 3. 构造双路检索请求
            dense_req = AnnSearchRequest(
                data=[dense_vec],
                anns_field="vector",
                param={
                    "metric_type": "COSINE",
                    "params": {"nprobe": s.nprobe},
                },
                limit=s.hybrid_dense_limit,
            )
            sparse_req = AnnSearchRequest(
                data=[question],  # 原始文本，BM25 Function 自动 tokenize
                anns_field="sparse_vector",
                param={"metric_type": "IP"},
                limit=s.hybrid_sparse_limit,
            )

            # 4. RRF 融合（全字段输出，含动态溯源元数据）
            rrf = RRFRanker(k=s.rrf_k)
            raw_hits = await self._hybrid_search_async(
                [dense_req, sparse_req], rrf, s.hybrid_fusion_limit
            )
            if not raw_hits:
                logger.debug("混合检索: 无命中结果")
                return []

            # 5. 转换为 (text, rrf_score, entity) 列表
            candidates: List[Tuple[str, float, dict]] = []
            for hit in raw_hits:
                entity = _normalize_hit_entity(hit.entity)
                candidates.append((entity.get("text", ""), hit.score, entity))

            logger.info(
                "混合检索(async): 稠密=%d路, 稀疏=%d路, RRF融合后=%d条",
                s.hybrid_dense_limit, s.hybrid_sparse_limit, len(candidates),
            )

            # 6. Reranker 精排（客户端内部自带并发限流与失败降级）
            reranker = self._get_reranker()
            docs_text = [c[0] for c in candidates]
            reranked = await reranker.rerank(
                query=question, documents=docs_text, top_k=s.retrieval_top_k,
            )
            if not reranked:
                logger.debug("Reranker 精排: 无有效结果")
                return []

            # 7. 动态阈值过滤（复用同步纯函数）
            top_score = reranked[0]["relevance_score"]
            threshold = _adaptive_threshold(top_score)

            results_final: List[Tuple[Document, float]] = []
            for item in reranked:
                if item["relevance_score"] >= threshold:
                    idx = item["index"]
                    if idx < len(candidates):
                        text, rrf_score, entity = candidates[idx]
                        doc = _entity_to_doc(entity, item["relevance_score"])
                        doc.metadata["rrf_score"] = round(rrf_score, 4)
                        results_final.append((doc, item["relevance_score"]))

            logger.info(
                "检索完成(async): RRF=%d条, 精排后=%d条, 过滤后=%d条 (threshold=%.2f)",
                len(candidates), len(reranked), len(results_final), threshold,
            )
            return results_final

        except Exception as e:
            logger.warning("异步混合检索异常 (%s)，回退到降级检索", e)
            return await self._dense_only_retrieve_async(question)

    async def _dense_only_retrieve_async(
        self, question: str
    ) -> List[Tuple[Document, float]]:
        """降级检索（异步版）— 对应同步 _dense_only_retrieve。

        方案 A: pymilvus 原生 search（可获取动态元数据），桥接线程池；
        方案 B: langchain_milvus similarity_search_with_score 兜底。
        """
        s = self.settings

        # ── 方案 A ──
        if self.collection is not None and self.embeddings is not None:
            try:
                dense_vec = await self.embed_query_async(question)
                from .qa_chain import _search_with_full_fields

                async with self._semaphore("milvus_search", s.milvus_max_concurrency):
                    raw_hits = await run_blocking(
                        _search_with_full_fields,
                        self.collection,
                        s,
                        data=[dense_vec],
                        anns_field="vector",
                        search_params={
                            "metric_type": "COSINE",
                            "params": {"nprobe": s.nprobe},
                        },
                        limit=s.hybrid_dense_limit,
                    )
                results: List[Tuple[Document, float]] = []
                for hit in raw_hits:
                    if hit.score >= s.similarity_threshold:
                        doc = _entity_to_doc(hit.entity, hit.score)
                        doc.metadata["score"] = round(hit.score, 4)
                        results.append((doc, hit.score))
                logger.info(
                    "pymilvus稠密检索(async): %d条命中, 过滤后=%d条 (threshold=%.2f)",
                    len(raw_hits), len(results), s.similarity_threshold,
                )
                return results[: s.retrieval_top_k]
            except Exception as e:
                logger.warning("pymilvus 原生检索失败 (%s)，回退到 langchain_milvus", e)

        # ── 方案 B ──
        try:
            raw = await run_blocking(
                self.vector_store.similarity_search_with_score,
                question,
                k=s.hybrid_dense_limit,
            )
        except Exception as e:
            logger.warning("langchain_milvus 检索也失败: %s", e)
            return []

        filtered = [
            (doc, score) for doc, score in raw
            if score >= s.similarity_threshold
        ]
        logger.info(
            "langchain_milvus降级检索(async): 原始=%d条, 过滤后=%d条 (threshold=%.2f)",
            len(raw), len(filtered), s.similarity_threshold,
        )
        return filtered[: s.retrieval_top_k]

    # ── 回答阶段 ──────────────────────────────────────────────

    async def answer_text_async(
        self, docs_with_scores: List[Tuple[Document, float]], question: str
    ) -> str:
        """非流式 LLM 回答（prompt | llm | StrOutputParser 的 ainvoke 版）。"""
        context = _format_docs(docs_with_scores)
        answer_chain = self.prompt | self.llm | StrOutputParser()
        async with self._semaphore("llm", self.settings.llm_max_concurrency):
            raw_answer = await answer_chain.ainvoke({
                "context": context,
                "question": question,
            })
        return raw_answer.strip()

    async def stream_answer(
        self, docs_with_scores: List[Tuple[Document, float]], question: str
    ) -> AsyncIterator[str]:
        """流式 LLM 回答 — 逐段 yield token 增量（供 rag_engine.astream 使用）。"""
        context = _format_docs(docs_with_scores)
        chain = self.prompt | self.llm
        async with self._semaphore("llm", self.settings.llm_max_concurrency):
            async for chunk in chain.astream({"context": context, "question": question}):
                content = getattr(chunk, "content", chunk)
                if isinstance(content, str) and content:
                    yield content

    # ── 结果组装（与同步 _decide_and_answer 一致） ────────────

    def build_refusal_result(self) -> Dict[str, Any]:
        """拒答结果 — 文案与结构与同步版完全一致。"""
        refusal_answer = "抱歉，公共知识库中暂无相关内容，无法提供可靠回答。"
        refusal_report = self.validator.validate(
            [], refusal_answer, [], is_refusal=True,
        )
        return {
            "answer": refusal_answer,
            "sources": [],
            "citations": [],
            "citation_validation": refusal_report.to_dict(),
        }

    def build_answer_result(
        self,
        docs_with_scores: List[Tuple[Document, float]],
        question: str,
        answer: str,
    ) -> Dict[str, Any]:
        """组装回答结果 + 引用溯源校验报告（R1-R7，fail-soft）。"""
        sources = _build_sources(docs_with_scores)
        citations = build_citations(docs_with_scores)

        context_ids = [
            doc.metadata.get("chunk_id") for doc, _ in docs_with_scores
        ]
        report = self.validator.validate(citations, answer, context_ids)

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
        }

    async def decide_and_answer_async(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """核心决策节点（异步版）：检索为空则拒答，有结果则走 LLM。"""
        docs_with_scores: List[Tuple[Document, float]] = inputs["docs"]
        question: str = inputs["question"]

        if not docs_with_scores:
            logger.info("检索结果不足，触发拒答")
            return self.build_refusal_result()

        answer = await self.answer_text_async(docs_with_scores, question)
        return self.build_answer_result(docs_with_scores, question, answer)


def build_async_qa_chain(
    vector_store: MilvusVectorStore,
    llm: BaseChatModel,
    settings: Settings,
    collection: Optional[Any] = None,
    embeddings: Optional[Any] = None,
    reranker: Optional[Any] = None,
) -> Any:
    """构建完整的异步 LCEL RAG 问答链（bge-m3 混合检索版）。

    结构与 build_qa_chain 一致，仅内部 Runnable 全部为协程实现：

        chain.ainvoke(question) → {
            "answer": str, "sources": list,
            "citations": list, "citation_validation": dict,
        }

    Args:
        vector_store: 已初始化的 Milvus 向量存储（langchain_milvus 包装器）。
        llm: LangChain 兼容的 ChatModel。
        settings: 全局配置。
        collection: MilvusClient 实例（用于 hybrid_search / search）。
        embeddings: Embedding 模型实例（用于生成稠密查询向量）。
        reranker: 可选的外部 AsyncSiliconFlowReranker（测试注入用）。

    Returns:
        可调用的 LCEL Runnable 链，ainvoke(question) → dict。
    """
    from langchain_core.runnables import RunnableLambda, RunnablePassthrough

    pipeline = AsyncRAGPipeline(
        vector_store, llm, settings,
        collection=collection, embeddings=embeddings, reranker=reranker,
    )

    chain = (
        {
            "docs": RunnableLambda(pipeline.retrieve_async),
            "question": RunnablePassthrough(),
        }
        | RunnableLambda(pipeline.decide_and_answer_async)
    )

    logger.info("异步 LCEL 问答链构建完成（bge-m3 混合检索模式）")
    return chain
