# 功能：把检索到的 Document 格式化为 LLM 上下文和返回 sources。
"""Context and source formatting for the public knowledge QA chain."""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from langchain_core.documents import Document


def format_docs(docs_with_scores: List[Tuple[Document, float]]) -> str:
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


def build_sources(
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
