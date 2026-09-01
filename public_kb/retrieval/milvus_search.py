# 功能：执行 Milvus 稠密、稀疏 BM25 和混合检索。
"""Milvus dense, sparse, and hybrid search operations."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..config import Settings


logger = logging.getLogger(__name__)

_OUTPUT_FIELDS_ALL = ["*"]
_OUTPUT_FIELDS_FALLBACK = ["text", "id", "doc_name", "chapter", "chunk_index"]


def search_with_full_fields(
    collection: Any,
    settings: Settings,
    *,
    data: List[Any],
    anns_field: str,
    search_params: Dict[str, Any],
    limit: int,
    expr: Optional[str] = None,
) -> List[Any]:
    """pymilvus search，优先 output_fields=['*'] 全字段（含动态元数据），
    服务端不支持时回退基础字段列表。

    expr：可选标量过滤表达式（如法条时效过滤，任务 M3）；None 表示不过滤。
    """
    kwargs: Dict[str, Any] = {
        "data": data,
        "anns_field": anns_field,
        "search_params": search_params,
        "limit": limit,
        "output_fields": _OUTPUT_FIELDS_ALL,
    }
    if expr:
        kwargs["filter"] = expr
    try:
        return collection.search(
            settings.collection_name, **kwargs
        )[0]
    except Exception as e:
        logger.warning("output_fields=['*'] 检索失败 (%s)，回退基础字段", e)
        kwargs["output_fields"] = _OUTPUT_FIELDS_FALLBACK
        return collection.search(
            settings.collection_name, **kwargs
        )[0]


def hybrid_search_with_full_fields(
    collection: Any,
    settings: Settings,
    *,
    reqs: List[Any],
    ranker: Any,
    limit: int,
    expr: Optional[str] = None,
) -> List[Any]:
    """pymilvus hybrid_search，output_fields=['*'] 优先，失败回退基础字段。

    expr：可选标量过滤表达式（任务 M3）；None 表示不过滤。
    """
    kwargs: Dict[str, Any] = {
        "reqs": reqs,
        "ranker": ranker,
        "limit": limit,
        "output_fields": _OUTPUT_FIELDS_ALL,
    }
    if expr:
        kwargs["filter"] = expr
    try:
        return collection.hybrid_search(
            settings.collection_name, **kwargs
        )[0]
    except Exception as e:
        logger.warning("hybrid_search output_fields=['*'] 失败 (%s)，回退基础字段", e)
        kwargs["output_fields"] = _OUTPUT_FIELDS_FALLBACK
        return collection.hybrid_search(
            settings.collection_name, **kwargs
        )[0]
