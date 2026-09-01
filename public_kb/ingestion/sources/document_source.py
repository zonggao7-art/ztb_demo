# 功能：把内存中已解析 Document 包装为 pipeline 数据源。
"""In-memory document source for already-parsed chunks."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from langchain_core.documents import Document

from ..models import SourceResult


class DocumentSource:
    """Wrap pre-parsed documents as a pipeline source."""

    def __init__(
        self,
        documents: Sequence[Document],
        *,
        source_name: str = "documents",
        records: Optional[Sequence[Dict[str, Any]]] = None,
    ) -> None:
        self.name = source_name
        self._documents = list(documents)
        self._records = list(records or [])

    def load(self) -> SourceResult:
        return SourceResult(
            documents=list(self._documents),
            records=list(self._records),
        )
