"""Milvus sink for initialized and incremental ingestion."""

from __future__ import annotations

from typing import Any, Dict, Literal, Sequence

from langchain_core.documents import Document

from ...contracts import validate_ingestion_documents


MilvusSinkMode = Literal["initialize", "append"]


class MilvusSink:
    """Persist validated documents through a MilvusStoreManager."""

    def __init__(self, manager: Any, *, mode: MilvusSinkMode = "append") -> None:
        self._manager = manager
        self._mode = mode

    def write(
        self,
        documents: Sequence[Document],
        *,
        records: Sequence[Dict[str, Any]],
    ) -> int:
        validated = validate_ingestion_documents(documents)
        if self._mode == "initialize":
            self._manager.initialize_collection(validated)
        else:
            self._manager.add_documents(validated)
        return len(validated)
