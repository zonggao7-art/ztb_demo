# 功能：按 Markdown 标题和长度约束进行语义切片。
"""
语义切片器 — 基于 Markdown 标题层级进行智能分块。

切分策略（两级）：
  - 主策略：按 #、##、### 标题层级拆分，保留所属章节路径
  - 补策略：单块超过 max_chars 时，按句子边界二次拆分，带 overlap 重叠
"""

from __future__ import annotations

import re
from typing import List

from langchain_core.documents import Document


class SemanticChunker:
    """基于 Markdown 标题层级的语义切片器。"""

    # 匹配 Markdown 标题行（# 开头的行）
    _HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)", re.MULTILINE)

    # 中文句子分隔符
    _SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；\n])\s*")

    def __init__(self, max_chars: int = 500, overlap_chars: int = 50) -> None:
        """初始化切片器。

        Args:
            max_chars: 单块最大字符数，超出则按句子二次拆分。
            overlap_chars: 句子二次拆分时的重叠字符数。
        """
        self._max_chars = max_chars
        self._overlap_chars = overlap_chars

    def chunk(self, markdown_text: str, doc_name: str) -> List[Document]:
        """将 Markdown 文本切分为带元数据的 Document 列表。

        Args:
            markdown_text: 清洗后的 Markdown 全文。
            doc_name: 来源文档名称（如文件名）。

        Returns:
            List[Document]，每个 Document 携带 doc_name、chapter、chunk_index。
        """
        if not markdown_text.strip():
            return []

        documents: List[Document] = []
        heading_stack: List[str] = ["前言"]  # 首个 # 前的内容归入"前言"
        current_lines: List[str] = []
        chunk_index: int = 0

        lines = markdown_text.split("\n")

        for line in lines:
            match = self._HEADING_RE.match(line)
            if match:
                # —— 遇到标题：保存上一个块 ——
                documents.extend(
                    _flush_chunk(
                        lines=current_lines,
                        heading_stack=heading_stack,
                        doc_name=doc_name,
                        base_index=chunk_index,
                        max_chars=self._max_chars,
                        overlap_chars=self._overlap_chars,
                    )
                )
                # 更新 chunk_index 基准值（标题变，序号归零）
                chunk_index = 0

                # —— 更新标题路径栈 ——
                level = len(match.group(1))
                title = match.group(2).strip()
                self._update_heading_stack(heading_stack, level, title)

                current_lines = []
                chunk_index = 0
            else:
                current_lines.append(line)

        # —— 最后一个块 ——
        documents.extend(
            _flush_chunk(
                lines=current_lines,
                heading_stack=heading_stack,
                doc_name=doc_name,
                base_index=chunk_index,
                max_chars=self._max_chars,
                overlap_chars=self._overlap_chars,
            )
        )

        return documents

    @staticmethod
    def _update_heading_stack(
        stack: List[str], level: int, title: str
    ) -> None:
        """更新标题层级栈。

        例如：当前栈 ["第一章", "第一节"]，遇到 ## 新标题时：
        → 截断为 stack[:1] + ["新标题"] → ["第一章", "新标题"]
        """
        # 栈索引：标题级别 1 → stack[0], 2 → stack[1], ...
        target_idx = level - 1

        # 截断到同深度
        while len(stack) > target_idx:
            stack.pop()

        # 补齐缺失层级（处理跳跃，如 # 直接跳到 ###）
        while len(stack) < target_idx:
            stack.append("")

        stack.append(title)


def _flush_chunk(
    lines: List[str],
    heading_stack: List[str],
    doc_name: str,
    base_index: int,
    max_chars: int,
    overlap_chars: int,
) -> List[Document]:
    """将当前缓存的文本行转为 Document 列表（含二次句子拆分）。"""
    text = "\n".join(lines).strip()
    if not text:
        return []

    chapter = " > ".join(h for h in heading_stack if h)

    # 未超出上限，直接返回单块
    if len(text) <= max_chars:
        return [
            Document(
                page_content=text,
                metadata={
                    "doc_name": doc_name,
                    "chapter": chapter,
                    "chunk_index": base_index,
                },
            )
        ]

    # 超出上限：按句子二次拆分
    sub_chunks = _split_by_sentence(text, max_chars, overlap_chars)
    return [
        Document(
            page_content=chunk,
            metadata={
                "doc_name": doc_name,
                "chapter": chapter,
                "chunk_index": base_index + i,
            },
        )
        for i, chunk in enumerate(sub_chunks)
    ]


def _split_by_sentence(
    text: str, max_chars: int = 500, overlap_chars: int = 50
) -> List[str]:
    """按句子边界拆分长文本，相邻块之间带 overlap 重叠。

    Args:
        text: 待拆分的文本。
        max_chars: 单块最大字符数。
        overlap_chars: 相邻块重叠字符数。

    Returns:
        拆分后的文本块列表。
    """
    sentences: List[str] = [
        s.strip() for s in SemanticChunker._SENTENCE_SPLIT_RE.split(text)
        if s.strip()
    ]

    chunks: List[str] = []
    current: str = ""

    for sentence in sentences:
        if len(current) + len(sentence) <= max_chars:
            current += sentence
        else:
            if current.strip():
                chunks.append(current.strip())
            # 新块以重叠文本开头
            if overlap_chars > 0 and current:
                overlap_text = current[-overlap_chars:]
                current = overlap_text + sentence
            else:
                current = sentence

    if current.strip():
        chunks.append(current.strip())

    return chunks
