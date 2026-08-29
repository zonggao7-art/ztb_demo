"""Compatibility re-exports for the moved CSV loader."""

from __future__ import annotations

import warnings

from .ingestion.sources.csv_loader import CsvLoader, save_chunks_to_markdown

warnings.warn(
    "public_kb.csv_loader is deprecated; use "
    "public_kb.ingestion.sources.csv_loader",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["CsvLoader", "save_chunks_to_markdown"]
