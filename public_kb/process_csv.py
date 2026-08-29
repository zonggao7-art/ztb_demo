"""Deprecated compatibility entry for batch CSV ingestion."""

from __future__ import annotations

import warnings

from .ingestion.cli import (
    DEFAULT_MARKDOWN_OUTPUT_DIR as DEFAULT_OUTPUT_DIR,
    main,
    process_csv_group as process_group,
    run_batch_csv_ingestion,
    scan_csv_files,
    validate_markdown_output,
)

warnings.warn(
    "public_kb.process_csv is deprecated; use public_kb.ingestion.cli",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "main",
    "process_group",
    "run_batch_csv_ingestion",
    "scan_csv_files",
    "validate_markdown_output",
]


if __name__ == "__main__":
    raise SystemExit(main())
