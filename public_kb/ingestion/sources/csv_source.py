# 功能：把单个 CSV 文件包装为 pipeline 可消费的数据源。
"""CSV source adapter for lossless, in-memory ingestion."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Union

from ..models import SourceResult
from ..transforms import SemanticChunker, TextCleaner
from .csv_loader import CsvLoader


class CsvSource:
    """Load CSV rows directly as chunk documents without Markdown round-trip."""

    def __init__(
        self,
        csv_path: Union[str, Path],
        loader: Optional[CsvLoader] = None,
        *,
        max_chars: int = 2000,
        overlap_chars: int = 100,
    ) -> None:
        self._csv_path = Path(csv_path).resolve()
        self._loader = loader or CsvLoader(
            cleaner=TextCleaner(),
            chunker=SemanticChunker(
                max_chars=max_chars,
                overlap_chars=overlap_chars,
            ),
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )

    @property
    def csv_path(self) -> Path:
        return self._csv_path

    def load(self) -> SourceResult:
        documents, records = self._loader.load_file(str(self._csv_path))
        return SourceResult(documents=documents, records=records)
