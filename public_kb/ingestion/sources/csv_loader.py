# 功能：读取和规范化多 Schema CSV，完成文本切片与 Markdown 预览辅助。
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
from .csv_loader_structure import (
    _CN_LEGAL_HEADING_RE,  # 标题提取复用（M5 Step B 拆出后单源定义）
    _MD_HEADING_RE,        # 供本模块内部标题检测使用
    save_chunks_to_markdown,  # re-export：既有外部导入路径保持可用（见 __init__ / cli）
    structure_plain_text,     # 同上
)

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
# （M5 Step B：与 structure_plain_text 一同拆至 csv_loader_structure.py 单源定义）

# 匹配 "第一章 总则" 形式的全角空格分隔
_CN_HEADING_FULLWIDTH_RE = re.compile(
    r"^(第[一二三四五六七八九十百千\d]+[章节条款项部分编])[　\s]+(.+)$"
)


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
        # M5 Step B：structure_plain_text 已拆至 csv_loader_structure.py
        if not _MD_HEADING_RE.search(content):
            content = structure_plain_text(content)

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
            # 法条时效性（任务 M3）：施行日期作为 effective_date（可空）
            imple_time = row.get("imple_time") or ""
            doc.metadata["effective_date"] = str(imple_time).strip() if str(imple_time).strip() else ""
            doc.metadata["status"] = ""
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
