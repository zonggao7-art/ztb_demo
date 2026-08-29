"""Optional Markdown sink for preview and debugging."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Union

from langchain_core.documents import Document

from ...csv_loader import save_chunks_to_markdown


class MarkdownSink:
    """Write document previews to Markdown; not a formal ingestion source."""

    def __init__(
        self,
        output_dir: Union[str, Path],
        *,
        source_file: Optional[str] = None,
    ) -> None:
        self._output_dir = str(output_dir)
        self._source_file = source_file

    def write(
        self,
        documents: Sequence[Document],
        *,
        records: Sequence[Dict[str, Any]],
    ) -> int:
        if not documents:
            return 0
        source_file = self._source_file or str(
            documents[0].metadata.get("source_file", "documents")
        )
        save_chunks_to_markdown(
            list(documents),
            self._output_dir,
            source_file,
            list(records),
        )
        return len(documents)
