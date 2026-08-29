"""Command-line entry for lossless CSV ingestion."""

from __future__ import annotations

import argparse
import logging
from typing import Any

from ..config import Settings
from ..csv_loader import CsvLoader
from ..services.embeddings import create_embeddings
from ..services.milvus_store import MilvusStoreManager
from .models import IngestionResult
from .pipeline import IngestionPipeline
from .sinks.markdown_sink import MarkdownSink
from .sinks.milvus_sink import MilvusSink, MilvusSinkMode
from .sources.csv_source import CsvSource


logger = logging.getLogger(__name__)


def run_csv_ingestion(
    csv_path: str,
    settings: Settings,
    *,
    markdown_output_dir: str | None = None,
    manager: MilvusStoreManager | None = None,
    embeddings: Any = None,
    mode: MilvusSinkMode = "append",
) -> IngestionResult:
    """Load one CSV directly and write it to Milvus without Markdown round-trip."""
    resolved_embeddings = embeddings or create_embeddings(settings)
    resolved_manager = manager or MilvusStoreManager(
        settings,
        resolved_embeddings,
    )
    loader = CsvLoader(
        max_chars=settings.chunk_max_chars,
        overlap_chars=settings.chunk_overlap_chars,
    )
    sinks = []
    if markdown_output_dir:
        sinks.append(MarkdownSink(markdown_output_dir))
    sinks.append(MilvusSink(resolved_manager, mode=mode))

    return IngestionPipeline(sinks).run(CsvSource(csv_path, loader=loader))


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest one CSV into Milvus")
    parser.add_argument("--csv-path", required=True)
    parser.add_argument("--markdown-output-dir")
    parser.add_argument(
        "--mode",
        choices=("initialize", "append"),
        default="append",
    )
    args = parser.parse_args()

    settings = Settings()
    result = run_csv_ingestion(
        args.csv_path,
        settings,
        markdown_output_dir=args.markdown_output_dir,
        mode=args.mode,
    )
    logger.info(
        "CSV ingestion %s: chunks=%d, inserted=%d",
        result.status,
        result.chunk_count,
        result.inserted_count,
    )


if __name__ == "__main__":
    main()
