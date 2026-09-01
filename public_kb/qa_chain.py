# 功能：在线问答链稳定入口，保持旧 build_qa_chain 签名并转发到 generation.chain。
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


# 下划线别名说明（M6 治理）：此模块是"稳定兼容壳"，保留旧 build_qa_chain
# 签名。下划线别名（_SiliconFlowReranker / _dense_only_retrieve）仅用于
# 让 test_public_kb_layout.py 的 AST 守卫能锚定"稳定入口只依赖这两个符号"，
# 不参与任何业务逻辑；业务实现一律走 generation/retrieval 正式路径。
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
