"""Dense-only retrieval fallback helpers."""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Tuple

from langchain_core.documents import Document
from langchain_milvus import Milvus as MilvusVectorStore

from ..config import Settings
from .entities import entity_to_doc
from .milvus_search import search_with_full_fields


logger = logging.getLogger(__name__)


def dense_only_retrieve(
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
