"""Compatibility re-exports for the moved semantic chunker."""

from __future__ import annotations

import warnings

from .ingestion.transforms.chunker import SemanticChunker

warnings.warn(
    "public_kb.chunker is deprecated; use "
    "public_kb.ingestion.transforms.chunker",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["SemanticChunker"]
