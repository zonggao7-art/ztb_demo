"""Stable entry for the public knowledge-base QA chain."""

from __future__ import annotations

from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_milvus import Milvus as MilvusVectorStore

from .config import Settings
from .generation.chain import build_chain
from .retrieval.fallback import dense_only_retrieve
from .retrieval.reranker import SiliconFlowReranker
from .retrieval.retriever import HybridRetrievalError


_SiliconFlowReranker = SiliconFlowReranker
_dense_only_retrieve = dense_only_retrieve


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


__all__ = [
    "HybridRetrievalError",
    "build_qa_chain",
]
