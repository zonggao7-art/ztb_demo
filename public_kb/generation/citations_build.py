# 功能：引用溯源子系统（M5 Step B 拆分）。
"""引用溯源模块 — 由原 citations.py(430行) 按职责拆分为：
  citations_models.py    pydantic 数据模型 + 标记解析 + 集合计算
  citations_build.py     引用构建 + 渲染
  citations_validate.py  CitationValidator 校验器
  citations.py           门面 re-export（既有导入路径保持可用）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from langchain_core.documents import Document

from ..chunk_ids import compute_chunk_uid
from .citations_models import Citation, _UNKNOWN_CHAPTER, _UNKNOWN_DOC_NAME


# ============================================================
#  构建与渲染
# ============================================================

def build_citations(
    docs_with_scores: List[Tuple[Document, float]],
) -> List[Citation]:
    """将检索结果（Document, score）标准化为 Citation 列表。

    Args:
        docs_with_scores: (Document, similarity_score) 列表，顺序与
            LLM 上下文 [来源N] 编号一一对应（第 i 项对应 来源{i+1}）。

    Returns:
        Citation 列表。
    """
    citations: List[Citation] = []
    for i, (doc, score) in enumerate(docs_with_scores, 1):
        meta = doc.metadata or {}
        chunk_uid = str(meta.get("chunk_uid", "") or "") or compute_chunk_uid(
            doc.page_content, meta
        )

        # 顶层字段之外的元数据全部透传（跳过内部字段与向量）
        extra: Dict[str, Any] = {}
        for key, value in meta.items():
            if key in (
                "doc_name", "chapter", "chunk_index",
                "chunk_id", "chunk_uid", "score", "rrf_score",
            ):
                continue
            if key.startswith("_"):
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                extra[key] = value

        citations.append(Citation(
            context_index=i,
            chunk_id=meta.get("chunk_id"),
            chunk_uid=chunk_uid,
            doc_name=str(meta.get("doc_name", "") or _UNKNOWN_DOC_NAME),
            chapter=str(meta.get("chapter", "") or _UNKNOWN_CHAPTER),
            chunk_index=int(meta.get("chunk_index", -1)),
            text=doc.page_content,
            score=round(float(score), 4),
            metadata=extra,
        ))
    return citations


def format_citations(
    citations: List[Dict[str, Any]],
    max_text_chars: Optional[int] = None,
) -> str:
    """将标准化引用列表渲染为可读文本块（呈现层使用）。

    输出保留【来源N】标签，逐条附带：
      - 所属文档名称、章节路径、块索引编号（数据源位置）
      - 关联元数据（页码、数据源文件、来源链接、发布日期、chunk_id/chunk_uid 等）
      - 绑定 chunk 的原文片段

    Args:
        citations: Citation.to_dict() 列表（或同构 dict）。
        max_text_chars: 原文片段最大展示字符数；None 表示完整输出。

    Returns:
        多行文本块；citations 为空时返回空字符串。
    """
    if not citations:
        return ""

    lines: List[str] = []
    lines.append(f"📚 引用来源（共 {len(citations)} 条）")
    lines.append("")

    for c in citations:
        idx = c.get("context_index", "")
        doc = c.get("doc_name") or "未知文档"
        chapter = c.get("chapter") or ""
        chunk_index = c.get("chunk_index")
        text = str(c.get("text") or "")

        header = f"【来源{idx}】{doc}"
        if chapter:
            header += f"｜{chapter}"
        if chunk_index is not None and chunk_index != -1:
            header += f"（块 {chunk_index}）"
        lines.append(header)

        meta_lines: List[str] = []
        meta = c.get("metadata") or {}
        if meta.get("page_number"):
            meta_lines.append(f"页码: {meta['page_number']}")
        if meta.get("title") and meta.get("title") != doc:
            meta_lines.append(f"标题: {meta['title']}")
        if meta.get("source_file"):
            meta_lines.append(f"数据源文件: {meta['source_file']}")
        if meta.get("source_url"):
            meta_lines.append(f"来源链接: {meta['source_url']}")
        if meta.get("publish_date"):
            meta_lines.append(f"发布日期: {meta['publish_date']}")
        if meta.get("publish_time"):
            meta_lines.append(f"发布时间: {meta['publish_time']}")
        if c.get("chunk_id") is not None:
            meta_lines.append(f"chunk_id: {c['chunk_id']}")
        if c.get("chunk_uid"):
            meta_lines.append(f"chunk_uid: {c['chunk_uid']}")
        if meta_lines:
            lines.append("  " + "\n  ".join(meta_lines))

        if max_text_chars and len(text) > max_text_chars:
            lines.append(f"  原文: {text[:max_text_chars]}…")
        else:
            lines.append(f"  原文: {text}")
        lines.append("")

    return "\n".join(lines).rstrip()
