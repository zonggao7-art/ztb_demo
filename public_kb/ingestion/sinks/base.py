"""Sink contract for ingestion pipelines."""

from __future__ import annotations

from typing import Any, Dict, Protocol, Sequence

from langchain_core.documents import Document


class Sink(Protocol):
    """A component that persists validated ingestion documents."""

    def write(
        self,
        documents: Sequence[Document],
        *,
        records: Sequence[Dict[str, Any]],
    ) -> int:
        """Persist documents and return the number of processed items."""
        ...
