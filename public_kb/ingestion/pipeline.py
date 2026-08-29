"""Explicit orchestration for offline ingestion."""

from __future__ import annotations

import time
from typing import Sequence

from langchain_core.documents import Document

from ..contracts import validate_ingestion_documents
from .models import IngestionResult, SourceResult, StageResult
from .sinks.base import Sink
from .sources.base import Source


class IngestionPipeline:
    """Run a source through validation and one or more sinks."""

    def __init__(self, sinks: Sequence[Sink]) -> None:
        if not sinks:
            raise ValueError("ingestion pipeline requires at least one sink")
        self._sinks = tuple(sinks)

    def run(self, source: Source) -> IngestionResult:
        """Load, validate, and write source documents."""
        source_name = type(source).__name__
        started_at = time.perf_counter()

        source_result = source.load()
        source_elapsed = (time.perf_counter() - started_at) * 1000
        source_stage = StageResult(
            name="source",
            input_count=1,
            output_count=len(source_result.documents),
            skipped_count=len(source_result.records) - len(source_result.documents),
            elapsed_ms=source_elapsed,
            diagnostics={"source": source_name},
        )

        if not source_result.documents:
            return IngestionResult(
                source=source_name,
                chunk_count=0,
                inserted_count=0,
                stage_results=(source_stage,),
                status="skipped",
            )

        validation_started_at = time.perf_counter()
        documents: list[Document] = validate_ingestion_documents(
            source_result.documents
        )
        validation_elapsed = (time.perf_counter() - validation_started_at) * 1000
        validation_stage = StageResult(
            name="validate",
            input_count=len(source_result.documents),
            output_count=len(documents),
            elapsed_ms=validation_elapsed,
        )

        inserted_count = 0
        sink_stages: list[StageResult] = []
        for sink in self._sinks:
            sink_started_at = time.perf_counter()
            count = sink.write(
                documents,
                records=source_result.records,
            )
            sink_elapsed = (time.perf_counter() - sink_started_at) * 1000
            inserted_count += count
            sink_stages.append(
                StageResult(
                    name=f"sink:{type(sink).__name__}",
                    input_count=len(documents),
                    output_count=count,
                    elapsed_ms=sink_elapsed,
                )
            )

        return IngestionResult(
            source=source_name,
            chunk_count=len(documents),
            inserted_count=inserted_count,
            stage_results=(source_stage, validation_stage, *sink_stages),
            status="completed",
        )
