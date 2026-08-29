"""Compatibility facade for the split retrieval and generation pipelines."""

from __future__ import annotations

from typing import Any, Optional

from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_milvus import Milvus as MilvusVectorStore

from .config import Settings
from .generation.chain import build_chain
from .generation.context import build_sources, format_docs
from .generation.prompts import (
    INLINE_CITATION_INSTRUCTION,
    USER_TEMPLATE,
    build_prompt,
)
from .retrieval.entities import entity_to_doc, normalize_hit_entity
from .retrieval.fallback import dense_only_retrieve
from .retrieval.milvus_search import (
    hybrid_search_with_full_fields,
    search_with_full_fields,
)
from .retrieval.reranker import SiliconFlowReranker
from .retrieval.strategies import adaptive_threshold
from .retrieval.retriever import HybridRetrievalError


_SiliconFlowReranker = SiliconFlowReranker
_normalize_hit_entity = normalize_hit_entity
_entity_to_doc = entity_to_doc
_search_with_full_fields = search_with_full_fields
_hybrid_search_with_full_fields = hybrid_search_with_full_fields
_adaptive_threshold = adaptive_threshold
_dense_only_retrieve = dense_only_retrieve
_build_prompt = build_prompt
_format_docs = format_docs
_build_sources = build_sources


def build_qa_chain(
    vector_store: MilvusVectorStore,
    llm: BaseChatModel,
    settings: Settings,
    collection: Optional[Any] = None,
    embeddings: Optional[Any] = None,
) -> Any:
    """构建公共知识库问答链，保持既有公共入口签名不变。"""
    return build_chain(
        vector_store,
        llm,
        settings,
        collection,
        embeddings,
        reranker_class=_SiliconFlowReranker,
    )
