# 功能：中文法律标题 → Markdown 标题 转换 + Markdown 预览导出（M5 Step B 拆出）。
"""
从 csv_loader.py 拆出的两个独立关注点（M5 Step B）：
  1. structure_plain_text — 把纯文本中的中文法律标题转为 Markdown 标题，使
     SemanticChunker 能按结构切分（含交叉引用否定后顾）。
  2. save_chunks_to_markdown — 把分块结果按原始表格行分组保存为 Markdown 预览。

拆出理由：csv_loader.py(535行) 职责混合（解析/归一化/标题提取/结构转换/预览导出），
此两函数是纯函数/独立功能域，独立成模块后更易测试与复用。
csv_loader.py 保留 re-export（from .csv_loader import structure_plain_text 仍旧可用）。
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List

from langchain_core.documents import Document

# 匹配 "第X章" "第X节" "第X条" "第X款" 等中文法律文本结构
_CN_LEGAL_HEADING_RE = re.compile(
    r"^(第[一二三四五六七八九十百千\d]+[章节条款项部分编])\s*(.*)$"
)

# 匹配 "第一章 总则" 形式的全角空格分隔
_CN_HEADING_FULLWIDTH_RE = re.compile(
    r"^(第[一二三四五六七八九十百千\d]+[章节条款项部分编])[　\s]+(.+)$"
)

# 匹配 Markdown 标题（# 开头）
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)", re.MULTILINE)

# Phase A1: 章节级标题 — 始终拆分（章级标题极少为交叉引用）
_CN_CHAPTER_SPLIT_RE = re.compile(
    r"(第[一二三四五六七八九十百千\d]+章)"
)
# Phase A2: 条级标题 — 使用否定后顾避免拆分交叉引用
# "招标投标法第三条"、"本条例第七条"、《XX法》第三条 不会被拆分
_CN_ARTICLE_SPLIT_RE = re.compile(
    r"(?<![法条例规定办法决定》\)])"
    r"(第[一二三四五六七八九十百千\d]+条)"
)


def structure_plain_text(text: str) -> str:
    """将纯文本中的中文法律标题转换为 Markdown 标题，使 SemanticChunker 能正确切分。

    三阶段处理：
      Phase A1: 始终在「第X章」前插入换行（章级极少为交叉引用）
      Phase A2: 在「第X条」前插入换行，使用否定后顾避免拆分法律名称引用
      Phase B:  拆分紧邻的「章标题+条标题」组合（如"第一章 总则第一条"）
      Phase C:  逐行识别中文法律标题并转换为 Markdown 标题格式

    转换规则：
      - "第X章 …"  → "## 第X章 …"
      - "第X节 …"  → "### 第X节 …"
      - "第X条 …"  → "#### 第X条 …"
      - "第X款 …"  → "##### 第X款 …"

    保留原有 Markdown 标题不变。
    """
    if not text:
        return text

    # ── Phase A1: 章级标题（始终拆分）──
    text = _CN_CHAPTER_SPLIT_RE.sub(r"\n\1", text)

    # ── Phase A2: 条级标题（避免交叉引用）──
    text = _CN_ARTICLE_SPLIT_RE.sub(r"\n\1", text)

    # ── Phase B: 拆分紧邻的「章标题+条标题」组合 ──
    # 如 "第一章 总则第一条" → "第一章 总则\n第一条"
    text = re.sub(
        r"(第[一二三四五六七八九十百千\d]+章\s*[^\n]{0,50}?)(第[一二三四五六七八九十百千\d]+条)",
        r"\1\n\2",
        text,
    )

    lines = text.split("\n")
    result: List[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            result.append(line)
            continue

        # 如果已经是 Markdown 标题，保留原样
        if _MD_HEADING_RE.match(stripped):
            result.append(line)
            continue

        # 尝试匹配中文法律文本标题
        match = _CN_LEGAL_HEADING_RE.match(stripped)
        if match:
            unit = match.group(1)  # 如 "第一章"
            remainder = match.group(2).strip() if match.group(2) else ""

            # 根据层级确定 # 数量
            if "章" in unit:
                level = "##"
            elif "节" in unit:
                level = "###"
            elif "条" in unit:
                level = "####"
            elif "款" in unit:
                level = "#####"
            elif "项" in unit:
                level = "######"
            else:
                level = "##"

            # ── 章/节：标题通常有描述（如"第一章 总则"），整体作为标题 ──
            if "章" in unit or "节" in unit:
                if remainder:
                    result.append(f"{level} {unit} {remainder}")
                else:
                    result.append(f"{level} {unit}")
            # ── 条/款/项：标题之后的内容应作为正文，不放入标题行 ──
            else:
                result.append(f"{level} {unit}")
                if remainder:
                    result.append(remainder)
        else:
            result.append(line)

    return "\n".join(result)


def save_chunks_to_markdown(
    documents: List[Document],
    output_dir: str,
    source_file: str,
    rows: List[Dict[str, Any]],
) -> str:
    """将切分后的 Document 列表按原始表格行分组，以 Markdown 格式保存到指定目录。

    每个原始 CSV 文件对应一个 .md 输出文件。
    文件内按行组织，每行对应原始 CSV 中的一行数据，
    该行的多个 chunk 按 title 分组展示。

    Args:
        documents: 切分后的所有 Document 列表。
        output_dir: 输出目录根路径。
        source_file: 原始 CSV 文件名。
        rows: 标准化后的原始行数据列表。

    Returns:
        输出文件的绝对路径。
    """
    os.makedirs(output_dir, exist_ok=True)

    # 输出文件路径：以 CSV 文件名（去 .csv）命名
    base_name = os.path.splitext(source_file)[0]
    output_path = os.path.join(output_dir, f"{base_name}_chunks.md")

    # 按 source_file + _line_num 分组
    chunks_by_line: Dict[int, List[Document]] = {}
    for doc in documents:
        line_num = doc.metadata.get("_line_num", 0)
        chunks_by_line.setdefault(line_num, []).append(doc)

    # 构建行号 → 原始行数据 的查找表
    row_by_line: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        row_by_line[row.get("_line_num", 0)] = row

    lines_out: List[str] = []
    lines_out.append(f"# Chunks: {source_file}")
    lines_out.append("")
    lines_out.append(f"> 原始文件: `{source_file}`")
    lines_out.append(f"> 总行数: {len(rows)}")
    lines_out.append(f"> 总 Chunk 数: {len(documents)}")
    lines_out.append("> 生成时间: 自动生成")
    lines_out.append("")
    lines_out.append("---")
    lines_out.append("")

    # 按行号排序输出
    for line_num in sorted(chunks_by_line.keys()):
        row_data = row_by_line.get(line_num, {})
        title = row_data.get("title", "未知标题")
        content_preview = (row_data.get("content", "") or "")[:200].replace("\n", " ")

        lines_out.append(f"## 行 {line_num}: {title}")
        lines_out.append("")
        lines_out.append(f"- **政策名称**: {title}")
        lines_out.append(f"- **发布时间**: {row_data.get('publish_date', 'N/A')}")
        lines_out.append(f"- **来源**: {row_data.get('source_url', row_data.get('source', 'N/A'))}")
        if row_data.get("question"):
            lines_out.append(f"- **原始问题**: {row_data.get('question', '')}")
        lines_out.append(f"- **Content 预览**: {content_preview}...")
        lines_out.append("")

        # 按 chapter 分组展示 chunks
        chunks = chunks_by_line[line_num]
        if not chunks:
            lines_out.append("*(无内容块)*")
            lines_out.append("")
            continue

        # 按 chapter 分组
        chapters_order: List[str] = []
        chapters_chunks: Dict[str, List[Document]] = {}
        for doc in chunks:
            chapter = doc.metadata.get("chapter", "前言")
            if chapter not in chapters_chunks:
                chapters_chunks[chapter] = []
                chapters_order.append(chapter)
            chapters_chunks[chapter].append(doc)

        for chapter in chapters_order:
            chapter_chunks = chapters_chunks[chapter]
            lines_out.append(f"### {chapter}")
            lines_out.append("")

            for i, doc in enumerate(chapter_chunks):
                chunk_idx = doc.metadata.get("chunk_index", i)
                lines_out.append(f"#### Chunk {chunk_idx}")
                lines_out.append("")
                lines_out.append("```")
                lines_out.append(doc.page_content)
                lines_out.append("```")
                lines_out.append("")

        lines_out.append("---")
        lines_out.append("")

    # 写入文件
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines_out))

    return output_path