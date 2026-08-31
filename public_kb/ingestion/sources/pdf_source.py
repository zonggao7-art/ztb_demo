# 功能：把 PDF 经 MinerU 解析、清洗、分块后包装为 pipeline 数据源。
"""PDF source adapter for MinerU-based ingestion."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from ..models import SourceResult
from ..transforms import SemanticChunker, TextCleaner


class PdfSource:
    """Load one PDF as parser -> clean -> semantic chunks.

    `parser` duck-typed：需要 `parse(pdf_path) -> str`。
    生产用 MinerUParser（M1 全量）；三档路由开启时用 PdfRouter（计划 §6 T3）。
    """

    def __init__(
        self,
        pdf_path: Union[str, Path],
        parser: object,
        cleaner: Optional[TextCleaner] = None,
        chunker: Optional[SemanticChunker] = None,
    ) -> None:
        self._pdf_path = Path(pdf_path).resolve()
        self._parser = parser
        self._cleaner = cleaner or TextCleaner()
        self._chunker = chunker or SemanticChunker()

    @property
    def pdf_path(self) -> Path:
        return self._pdf_path

    def load(self) -> SourceResult:
        raw_markdown = self._parser.parse(self._pdf_path)
        cleaned_markdown = self._cleaner.clean(raw_markdown)
        documents = self._chunker.chunk(cleaned_markdown, self._pdf_path.name)
        return SourceResult(documents=documents)
