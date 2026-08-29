"""Compatibility re-exports for the moved text cleaner."""

from __future__ import annotations

import warnings

from .ingestion.transforms.cleaner import TextCleaner

warnings.warn(
    "public_kb.text_cleaner is deprecated; use "
    "public_kb.ingestion.transforms.cleaner",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["TextCleaner"]
