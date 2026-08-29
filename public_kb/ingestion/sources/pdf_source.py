"""PDF source adapter for MinerU-based ingestion."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from ..models import SourceResult
from ..transforms import SemanticChunker, TextCleaner
from ...services.mineru_parser import MinerUParser


class PdfSource:
    """Load one PDF as parser -> clean -> semantic chunks."""

    def __init__(
        self,
        pdf_path: Union[str, Path],
        parser: MinerUParser,
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
