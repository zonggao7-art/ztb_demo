"""Compatibility re-exports for the moved CSV loader."""

from __future__ import annotations

from .ingestion.sources.csv_loader import CsvLoader, save_chunks_to_markdown

__all__ = ["CsvLoader", "save_chunks_to_markdown"]
