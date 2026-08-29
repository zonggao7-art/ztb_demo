"""
CSV 数据加载器 — 将 raw_policy 目录下的多 Schema CSV 文件标准化为 Document 列表。

核心功能：
  1. Schema 自动探测与列名归一化
  2. UTF-8 BOM 透明处理
  3. 多行 content 字段解析（csv.reader 标准库）
  4. title 缺失补全（从 content Markdown 标题或中文标题提取）
  5. 中文法律文本标题 → Markdown 标题转换（"第X章""第X条" → ##）
  6. QA 格式特殊处理（xunfei0002 / xunfei0003 系列）
  7. 复用 TextCleaner 清洗 + SemanticChunker 切片
"""

from __future__ import annotations

import csv
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.documents import Document

from ..transforms.chunker import SemanticChunker
from ..transforms.cleaner import TextCleaner

logger = logging.getLogger(__name__)

# ============================================================
# 列名归一化映射表
# ============================================================
_COLUMN_ALIAS_MAP: Dict[str, str] = {
    # 标题列
    "title": "title",
    "rule_title": "title",
    # 正文列
    "content": "content",
    # 发布时间列
    "publish_date": "publish_date",
    "publish_time": "publish_date",
    "release_time": "publish_date",
    "date": "publish_date",
    "imple_time": "imple_time",
    # 来源 URL
    "source_url": "source_url",
    "source": "source",
    "url": "url",
    # 其他可能携带的字段
    "category": "category",
    "project_type": "project_type",
    "word_count": "word_count",
    "doc_type": "doc_type",
    "created_at": "created_at",
    "inserted_time": "inserted_time",
    # QA 专用字段
    "question": "question",
    "answer": "answer",
    "page_number": "page_number",
    "chunk_index": "chunk_index",
}

# ============================================================
# 中文法律文本标题识别正则
# ============================================================
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


class CsvLoader:
    """CSV 政策数据加载器。

    将 raw_policy 目录下不同 Schema 的 CSV 文件统一解析为标准化 dict，
    再经由 TextCleaner + SemanticChunker 转为 Document 列表。
    """

    def __init__(
        self,
        cleaner: Optional[TextCleaner] = None,
        chunker: Optional[SemanticChunker] = None,
        max_chars: int = 2000,
        overlap_chars: int = 100,
    ) -> None:
        """初始化加载器。

        Args:
            cleaner: TextCleaner 实例，为 None 则创建默认实例。
            chunker: SemanticChunker 实例，为 None 则按参数创建。
            max_chars: 单块最大字符数。
            overlap_chars: 句子二次拆分重叠字符数。
        """
        self._cleaner = cleaner or TextCleaner()
        self._chunker = chunker or SemanticChunker(
            max_chars=max_chars, overlap_chars=overlap_chars
        )

    # ── 公开方法 ──────────────────────────────────────────

    def load_file(self, csv_path: str) -> Tuple[List[Document], List[Dict[str, Any]]]:
        """加载单个 CSV 文件，返回 (Document 列表, 原始行数据列表)。

        Args:
            csv_path: CSV 文件绝对路径。

        Returns:
            (documents, rows): documents 为切分后的 LangChain Document 列表，
                               rows 为标准化后的原始行数据（含 title/content 等）。
        """
        file_name = os.path.basename(csv_path)
        logger.info("正在加载: %s", file_name)

        # Step 1: 解析 CSV → 标准化行数据
        rows = self._parse_csv(csv_path)

        if not rows:
            logger.warning("%s 无有效数据行，跳过", file_name)
            return [], []

        logger.info("  → 解析到 %d 行有效数据", len(rows))

        # Step 2: 对每行进行 清洗 → 结构化增强 → 切片
        all_docs: List[Document] = []
        for row in rows:
            docs = self._process_row(row, file_name)
            all_docs.extend(docs)

        logger.info("  → 切分为 %d 个文档块", len(all_docs))
        return all_docs, rows

    def classify_file(self, csv_path: str) -> str:
        """根据 CSV header 判断文件所属组别。

        Returns:
            "A" — 完整政策文档（含 title + content）
            "C" — QA 问答对（含 question + answer，无 content）
            "unknown" — 无法识别
        """
        header = self._read_header(csv_path)
        if not header:
            return "unknown"

        normalized = {self._normalize_column(col): col for col in header}

        if "question" in normalized and "answer" in normalized and "content" not in normalized:
            return "C"
        if "content" in normalized:
            return "A"
        return "unknown"

    # ── CSV 解析 ──────────────────────────────────────────

    def _parse_csv(self, csv_path: str) -> List[Dict[str, Any]]:
        """解析 CSV 文件，返回标准化行数据列表。"""
        rows: List[Dict[str, Any]] = []

        try:
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames is None:
                    logger.warning("%s: 无法读取表头", os.path.basename(csv_path))
                    return []

                for line_num, raw_row in enumerate(reader, start=2):
                    normalized = self._normalize_row(raw_row)
                    if normalized is None:
                        continue
                    normalized["_source_file"] = os.path.basename(csv_path)
                    normalized["_line_num"] = line_num
                    rows.append(normalized)
        except Exception as e:
            logger.error("解析 %s 失败: %s", os.path.basename(csv_path), e)
            return []

        return rows

    def _normalize_row(self, raw_row: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """将原始 CSV 行转为标准化 dict，跳过空行。"""
        normalized: Dict[str, Any] = {}

        for raw_key, value in raw_row.items():
            norm_key = self._normalize_column(raw_key)
            if norm_key:
                # content 字段保留原始值（含换行），其他字段 strip
                if norm_key == "content":
                    # 去除前导换行符（问题4）
                    val = value.lstrip("\n") if value else ""
                else:
                    val = value.strip() if value else ""
                normalized[norm_key] = val

        # 判断文件类型并提取核心字段
        has_content = "content" in normalized and normalized["content"]
        has_qa = "question" in normalized and "answer" in normalized

        if has_content:
            # 组 A：完整政策文档
            # title 缺失补全（问题5）
            if not normalized.get("title") or normalized.get("title") == "Null":
                normalized["title"] = self._extract_title_from_content(
                    normalized.get("content", "")
                )
            return normalized

        elif has_qa:
            # 组 C：QA 问答对 → 将 answer 作为 content
            normalized["content"] = normalized.get("answer", "")
            if not normalized.get("title"):
                normalized["title"] = normalized.get("question", "")[:100]
            return normalized

        else:
            # 既无 content 也无 QA，跳过
            return None

    @staticmethod
    def _normalize_column(raw_name: str) -> Optional[str]:
        """将 CSV 列名归一化为标准字段名。"""
        # 去除 BOM 和空白
        clean = raw_name.strip().lstrip("﻿").lower()
        return _COLUMN_ALIAS_MAP.get(clean)

    @staticmethod
    def _read_header(csv_path: str) -> List[str]:
        """读取 CSV 文件的表头行。"""
        try:
            with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                header = next(reader, [])
                return header
        except Exception:
            return []

    # ── 标题提取与补全 ───────────────────────────────────

    @staticmethod
    def _extract_title_from_content(content: str) -> str:
        """从 content 文本中提取首个标题作为政策名称。

        优先级：
          1. Markdown # 标题行
          2. 中文法律文本标题（第X章 …）
          3. 首行前 100 字符
        """
        if not content:
            return "未知文档"

        # 尝试 Markdown 标题
        md_match = _MD_HEADING_RE.search(content)
        if md_match:
            return md_match.group(2).strip()

        # 尝试中文法律文本标题
        cn_match = _CN_LEGAL_HEADING_RE.search(content)
        if cn_match:
            full = cn_match.group(0).strip()
            return full[:100]

        # 回退：首行前 100 字符
        first_line = content.split("\n")[0].strip()
        return first_line[:100] if first_line else "未知文档"

    # ── 中文标题 → Markdown 标题转换 ─────────────────────

    # Phase A1: 章节级标题 — 始终拆分（章级标题极少为交叉引用）
    _CN_CHAPTER_SPLIT_RE = re.compile(
        r"(第[一二三四五六七八九十百千\d]+章)"
    )
    # Phase A2: 条级标题 — 使用否定后顾避免拆分交叉引用
    # "招标投标法第三条"、"本条例第七条"、"《XX法》第三条" 不会被拆分
    _CN_ARTICLE_SPLIT_RE = re.compile(
        r"(?<![法条例规定办法决定》\)])"
        r"(第[一二三四五六七八九十百千\d]+条)"
    )

    @staticmethod
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
        text = CsvLoader._CN_CHAPTER_SPLIT_RE.sub(r"\n\1", text)

        # ── Phase A2: 条级标题（避免交叉引用）──
        text = CsvLoader._CN_ARTICLE_SPLIT_RE.sub(r"\n\1", text)

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

    # ── 逐行处理（清洗 → 结构增强 → 切片）──────────────

    def _process_row(
        self, row: Dict[str, Any], file_name: str
    ) -> List[Document]:
        """处理单行数据：清洗 → 结构化增强 → 切片。

        Args:
            row: 标准化行数据（含 content、title 等）。
            file_name: 来源 CSV 文件名。

        Returns:
            Document 列表。
        """
        content = row.get("content", "")
        title = row.get("title", "")

        if not content or not content.strip():
            return []

        # Step 2a: 中文标题 → Markdown 标题（仅对无 Markdown 标题的纯文本）
        if not _MD_HEADING_RE.search(content):
            content = self.structure_plain_text(content)

        # Step 2b: TextCleaner 清洗
        cleaned = self._cleaner.clean(content)

        if not cleaned.strip():
            return []

        # Step 2c: SemanticChunker 切片
        # 用 title 作为 doc_name，若无 title 则用文件名
        doc_name = title if title and title != "Null" else file_name
        documents = self._chunker.chunk(cleaned, doc_name)

        # Step 2d: 附加元数据
        for doc in documents:
            doc.metadata["title"] = title if title and title != "Null" else doc_name
            doc.metadata["source_file"] = file_name
            doc.metadata["source_url"] = row.get("source_url", row.get("source", ""))
            doc.metadata["publish_date"] = row.get("publish_date", "")
            doc.metadata["imple_time"] = row.get("imple_time", "")
            doc.metadata["_line_num"] = row.get("_line_num", 0)
            # QA 专用元数据
            if "question" in row:
                doc.metadata["source_question"] = row.get("question", "")
            if "page_number" in row:
                doc.metadata["page_number"] = row.get("page_number", "")
            # 内容类型标记
            if "question" in row and "answer" in row:
                doc.metadata["content_type"] = "qa_pair"
            else:
                doc.metadata["content_type"] = "policy_fulltext"

        return documents


# ============================================================
# Chunk 中间存储工具：以 Markdown 格式保存
# ============================================================

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

    logger.info("中间 Markdown 已保存: %s (%d 行, %d chunks)", output_path, len(rows), len(documents))
    return output_path
