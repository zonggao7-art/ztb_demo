# 功能：MinerU PDF Markdown 结构适配 — 表格原子块、目录噪声过滤、双栏可疑打标。
"""
PDF 结构适配器 — 把 MinerU 产出的电子书 Markdown 转为可安全分块的中间结构。

针对电子书类 PDF（双栏排版、内含表格、点线目录、书脊页眉）：
  1. 表格原子块：MinerU 输出的 `|` 分隔表格 Markdown 整体保留为独立 Document，
     不参与 SemanticChunker 的句子二次拆分，防止列语义被切碎（如 book3 表1-1）。
  2. 目录噪声过滤：识别点线目录行（"⋯⋯⋯(82)"）并从正文流剔除，避免污染上下文。
  3. 双栏可疑打标：纯文本层面无法可靠 reflow（坐标信息在 MinerU 中间产物中，
     本模块输入仅为 Markdown 文本），采用启发式检测疑似跨栏乱序的块，写入
     Document.metadata["pdf_layout_suspect"]，供人工抽检，不强行重排。

接入方式：PdfSource.load() 在 clean 之后、chunk 之前调用 adapt_pdf_markdown()。
"""

from __future__ import annotations

import logging
import re
from typing import List, Tuple

from langchain_core.documents import Document

from .chunker import SemanticChunker

logger = logging.getLogger(__name__)

# ── 表格识别 ─────────────────────────────────────────────
# MinerU 表格 Markdown 行以 | 开头；连续多行成块才视为表格。
_TABLE_ROW_RE = re.compile(r"^\s*\|.*\|\s*$")

# ── 点线目录行尾：如 "招标投标法实施条例⋯⋯⋯⋯(82)" ──────────
_TOC_PAGE_TAIL_RE = re.compile(r"[⋯·．…]{2,}\s*\(?\d{1,4}\)?\s*$")
_TOC_MAX_LINE_LEN = 120

# ── 双栏乱序启发式：条文号 ─────────────────────────────────
_ARTICLE_RE = re.compile(r"第[一二三四五六七八九十百千\d]+[条款]")
# 2000 字正常单栏块通常含 2-5 个条文号；双栏被顺读打乱时块内会堆积大量
# 被从中间截断的条文号，计数显著偏高，借此打标供人工核验。
_SUSPECT_ARTICLES_PER_BLOCK = 12

# ── 标题行内联正文：`## 第X条 正文…` → 拆为标题行 + 正文行 ──
# SemanticChunker 会把标题行整体丢弃（只进标题栈），若正文内联在
# "第X条" 之后（如 cleaned_v1 Markdown 与部分电子书），正文会整段丢失。
# 这里仅对 条/款/项 拆分（其后续内容是正文）；章/节 标题的后续内容
# （如 "第一章 总则" 的 "总则"）是标题本身，保留不动。
_HEADING_INLINE_ARTICLE_RE = re.compile(
    r"^(#{1,6})\s+(第[一二三四五六七八九十百千\d]+[条款项])(?:\s+)(.+)$"
)


def split_table_blocks(
    markdown: str, min_table_rows: int = 2
) -> List[Tuple[str, bool]]:
    """把 Markdown 拆为 (内容, 是否表格原子段) 序列。

    连续 ``>= min_table_rows`` 行以 ``|`` 开头的内容聚合为一个表格原子段；
    其余内容（含标题层级）保持原样，交给 SemanticChunker 处理。
    不足 min_table_rows 的孤立 `|` 行（常见于普通文本中的竖线）
    视为普通文本，不误判为表格。
    """
    lines = markdown.split("\n")
    segments: List[Tuple[str, bool]] = []
    buffer: List[str] = []
    i, n = 0, len(lines)

    while i < n:
        line = lines[i]
        if _TABLE_ROW_RE.match(line):
            j = i
            while j < n and _TABLE_ROW_RE.match(lines[j]):
                j += 1
            if j - i >= min_table_rows:
                if buffer:
                    segments.append(("\n".join(buffer), False))
                    buffer = []
                segments.append(("\n".join(lines[i:j]), True))
                i = j
                continue
            # 连续表行不足阈值：按普通文本处理
            buffer.append(line)
            i += 1
            continue
        buffer.append(line)
        i += 1

    if buffer:
        segments.append(("\n".join(buffer), False))
    return segments


def strip_toc_noise(markdown: str) -> str:
    """剔除点线目录行（如 "招标投标法实施条例⋯⋯⋯(82)"）。

    仅当行满足「长度小于 _TOC_MAX_LINE_LEN 且行尾为点线+页码」时删除，
    避免误伤正文中的省略号。
    """
    kept: List[str] = []
    for line in markdown.split("\n"):
        stripped = line.strip()
        if (
            stripped
            and len(stripped) < _TOC_MAX_LINE_LEN
            and _TOC_PAGE_TAIL_RE.search(stripped)
        ):
            continue
        kept.append(line)
    return "\n".join(kept)


def detect_reflow_suspect(text: str) -> bool:
    """启发式检测疑似双栏乱序块。

    双栏被顺读打乱时，同一块内会出现大量被从中间截断的条文号
    （计数显著高于正常单栏块），借此打标供人工核验。
    """
    if not text:
        return False
    count = len(_ARTICLE_RE.findall(text))
    return count >= _SUSPECT_ARTICLES_PER_BLOCK


def adapt_pdf_markdown(
    markdown: str,
    doc_name: str,
    chunker: SemanticChunker,
    *,
    min_table_rows: int = 2,
    enable_toc_filter: bool = True,
    enable_reflow_flag: bool = True,
) -> List[Document]:
    """编排：过滤目录噪声 → 拆表格原子块 → 文本段走 chunker → 合并 Document。

    表格原子段直接包装为 Document（``content_type="table"``），不参与句子拆分；
    文本段经 chunker 分块，并在启用时对疑似双栏乱序的块打标。

    Args:
        markdown: 清洗后的 MinerU Markdown 全文。
        doc_name: 来源文档名称（如文件名）。
        chunker: SemanticChunker 实例（使用其自身切片参数）。
        min_table_rows: 连续 | 表格行达到该行数才视为表格原子段。
        enable_toc_filter: 是否执行目录点线行过滤。
        enable_reflow_flag: 是否执行双栏乱序打标。

    Returns:
        Document 列表（含 content_type="table" 的表格原子块）。
    """
    if enable_toc_filter:
        markdown = strip_toc_noise(markdown)
    if not markdown.strip():
        return []

    documents: List[Document] = []
    for content, is_table in split_table_blocks(markdown, min_table_rows):
        if is_table:
            documents.append(Document(
                page_content=content.strip(),
                metadata={
                    "doc_name": doc_name,
                    "chapter": "表格块",
                    "chunk_index": len(documents),
                    "content_type": "table",
                },
            ))
            continue
        text = content.strip()
        if not text:
            continue
        # 标题行内联条文正文保护（见 _HEADING_INLINE_ARTICLE_RE 注释）
        text = _split_heading_inline_articles(text)
        for doc in chunker.chunk(text, doc_name):
            if enable_reflow_flag and detect_reflow_suspect(doc.page_content):
                doc.metadata["pdf_layout_suspect"] = True
            documents.append(doc)
    return documents


def _split_heading_inline_articles(markdown: str) -> str:
    """把 `## 第X条 正文…` 拆为 `## 第X条` + 正文行，防止正文随标题被丢弃。

    SemanticChunker 在遇到标题行时只把标题压入 heading_stack，丢弃标题行
    之后的附加文本。对条/款/项拆分后返回标题行 + 独立正文行；
    章/节标题（如 "第一章 总则"）的后续词是标题本身，不拆分。
    """
    out: List[str] = []
    for line in markdown.split("\n"):
        m = _HEADING_INLINE_ARTICLE_RE.match(line.strip())
        if m:
            out.append(f"{m.group(1)} {m.group(2)}")
            out.append(m.group(3))
        else:
            out.append(line)
    return "\n".join(out)
