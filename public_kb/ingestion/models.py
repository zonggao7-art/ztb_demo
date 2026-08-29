"""Data contracts and stage statistics for ingestion pipelines."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from langchain_core.documents import Document


@dataclass(frozen=True)
class StageResult:
    """Execution statistics for one ingestion stage."""

    name: str
    input_count: int
    output_count: int
    skipped_count: int = 0
    elapsed_ms: float = 0.0
    diagnostics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SourceResult:
    """Normalized output from an ingestion source."""

    documents: List[Document]
    records: List[Dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class IngestionResult:
    """Aggregated result of one source processed by one pipeline."""

    source: str
    chunk_count: int
    inserted_count: int
    stage_results: tuple[StageResult, ...]
    status: str
    error: str | None = None
