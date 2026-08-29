"""Compatibility re-exports for the moved semantic chunker."""

from __future__ import annotations

from .ingestion.transforms.chunker import SemanticChunker

__all__ = ["SemanticChunker"]
