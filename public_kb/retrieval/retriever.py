# 功能：在线混合检索编排器，串联稠密/稀疏、RRF、阈值过滤和重排。
"""Hybrid retrieval orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple

from langchain_core.documents import Document
from langchain_milvus import Milvus as MilvusVectorStore
from pymilvus import AnnSearchRequest, RRFRanker

from ..config import Settings
from ..contracts import (
    MilvusCollectionContract,
    RetrievalDiagnostics,
    RetrievalMode,
    RerankerStatus,
    build_effective_expr,
    validate_question,
)
from .entities import entity_to_doc, normalize_hit_entity
from .fallback import dense_only_retrieve
from .milvus_search import hybrid_search_with_full_fields
from .reranker import Reranker
from .strategies import adaptive_threshold


logger = logging.getLogger(__name__)


class HybridRetrievalError(RuntimeError):
    """严格验证模式下，混合检索未按契约执行。"""


@dataclass(frozen=True)
class RetrievalResult:
    """一次检索的最终文档与诊断结果。"""

    docs: List[Tuple[Document, float]]
    diagnostics: RetrievalDiagnostics


class HybridRetriever:
    """编排 dense、服务端 BM25、RRF、Reranker 与降级路径。"""

    def __init__(
        self,
        *,
        vector_store: MilvusVectorStore,
        collection: Optional[Any],
        embeddings: Optional[Any],
        settings: Settings,
        reranker: Reranker,
    ) -> None:
        self._vector_store = vector_store
        self._collection = collection
        self._embeddings = embeddings
        self._settings = settings
        self._reranker = reranker
        self._has_sparse_cache: Optional[bool] = None

    def retrieve(self, raw_question: str) -> RetrievalResult:
        """稠密 COSINE + 服务端 BM25 → RRF → 可选 Reranker。"""
        question = validate_question(raw_question)
        expr = self._effective_expr()

        if self._collection is None or self._embeddings is None:
            logger.info("未提供原生 collection/embeddings，使用纯稠密检索")
            return self._dense_fallback(
                question,
                "native_collection_or_embeddings_missing",
                expr=expr,
            )

        dense_vec: Optional[List[float]] = None
        try:
            dense_vec = self._embeddings.embed_query(question)

            if self._has_sparse_cache is None:
                collection_info = self._collection.describe_collection(
                    self._settings.collection_name,
                )
                field_names = {
                    field.get("name", "")
                    for field in collection_info.get("fields", [])
                }
                self._has_sparse_cache = "sparse_vector" in field_names

            if not self._has_sparse_cache:
                reason = "sparse_vector_field_missing"
                if self._settings.strict_hybrid_validation:
                    raise HybridRetrievalError(reason)
                logger.info("当前 Schema 无稀疏向量字段，使用纯稠密检索")
                return self._dense_fallback(question, reason, dense_vec, expr)

            return self._hybrid_retrieve(question, dense_vec, expr)
        except HybridRetrievalError:
            raise
        except Exception as error:
            if self._settings.strict_hybrid_validation:
                raise HybridRetrievalError(str(error)) from error
            logger.warning("混合检索异常 (%s)，回退到纯稠密检索", error)
            return self._dense_fallback(
                question,
                f"hybrid_error:{type(error).__name__}",
                dense_vec,
                expr,
            )

    def _effective_expr(self) -> Optional[str]:
        """按开关生成法条时效过滤表达式；关闭时返回 None（不过滤）。"""
        if not self._settings.enable_effective_filter:
            return None
        from datetime import date
        return build_effective_expr(date.today())

    def _hybrid_retrieve(
        self,
        question: str,
        dense_vec: List[float],
        expr: Optional[str] = None,
    ) -> RetrievalResult:
        contract = MilvusCollectionContract()
        dense_req = AnnSearchRequest(
            data=[dense_vec],
            anns_field=contract.dense_field,
            param={
                "metric_type": contract.dense_metric,
                "params": {"nprobe": self._settings.nprobe},
            },
            limit=self._settings.hybrid_dense_limit,
        )
        sparse_req = AnnSearchRequest(
            data=[question],
            anns_field=contract.sparse_field,
            param={"metric_type": contract.sparse_metric, "params": {}},
            limit=self._settings.hybrid_sparse_limit,
        )

        raw_hits = hybrid_search_with_full_fields(
            self._collection,
            self._settings,
            reqs=[dense_req, sparse_req],
            ranker=RRFRanker(k=self._settings.rrf_k),
            limit=self._settings.hybrid_fusion_limit,
            expr=expr,
        )
        candidates: List[Tuple[str, float, dict]] = []
        for hit in raw_hits:
            entity = normalize_hit_entity(hit.entity)
            candidates.append((str(entity.get("text", "")), hit.score, entity))

        if not candidates:
            return RetrievalResult(
                docs=[],
                diagnostics=RetrievalDiagnostics(
                    retrieval_mode=RetrievalMode.REFUSAL,
                    dense_count=self._settings.hybrid_dense_limit,
                    sparse_count=self._settings.hybrid_sparse_limit,
                ),
            )

        reranked = self._reranker.rerank(
            query=question,
            documents=[candidate[0] for candidate in candidates],
            top_k=self._settings.retrieval_top_k,
        )

        if self._reranker.last_status is RerankerStatus.SUCCESS and reranked:
            return self._rerank_result(reranked, candidates)

        # Reranker 失败时保留 RRF 排序，不构造虚假相关度分数。
        results: List[Tuple[Document, float]] = []
        for _, rrf_score, entity in candidates[:self._settings.retrieval_top_k]:
            doc = entity_to_doc(entity, rrf_score)
            doc.metadata["rrf_score"] = round(rrf_score, 4)
            doc.metadata["score_type"] = "rrf"
            results.append((doc, rrf_score))
        return RetrievalResult(
            docs=results,
            diagnostics=RetrievalDiagnostics(
                retrieval_mode=RetrievalMode.HYBRID_RRF,
                dense_count=self._settings.hybrid_dense_limit,
                sparse_count=self._settings.hybrid_sparse_limit,
                fusion_count=len(candidates),
                reranker_status=self._reranker.last_status,
                fallback_reason="reranker_failed",
            ),
        )

    def _rerank_result(
        self,
        reranked: List[dict],
        candidates: List[Tuple[str, float, dict]],
    ) -> RetrievalResult:
        threshold = adaptive_threshold(
            reranked[0]["relevance_score"],
            settings=self._settings,
        )
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
        return RetrievalResult(
            docs=results,
            diagnostics=RetrievalDiagnostics(
                retrieval_mode=RetrievalMode.HYBRID_RERANK,
                dense_count=self._settings.hybrid_dense_limit,
                sparse_count=self._settings.hybrid_sparse_limit,
                fusion_count=len(candidates),
                reranker_status=RerankerStatus.SUCCESS,
                threshold=threshold,
            ),
        )

    def _dense_fallback(
        self,
        question: str,
        reason: str,
        dense_vec: Optional[List[float]] = None,
        expr: Optional[str] = None,
    ) -> RetrievalResult:
        docs = dense_only_retrieve(
            question,
            self._vector_store,
            self._settings,
            self._collection,
            self._embeddings,
            dense_vec=dense_vec,
            expr=expr,
        )
        return RetrievalResult(
            docs=docs,
            diagnostics=RetrievalDiagnostics(
                retrieval_mode=(
                    RetrievalMode.DENSE_NATIVE
                    if self._collection is not None and self._embeddings is not None
                    else RetrievalMode.DENSE_LANGCHAIN
                ),
                dense_count=len(docs),
                fallback_reason=reason,
            ),
        )
