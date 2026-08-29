"""Compatibility re-exports for the moved citation utilities."""

from __future__ import annotations

from .generation.citations import (
    Citation,
    CitationValidator,
    build_citations,
    format_citations,
    parse_citation_markers,
)

__all__ = [
    "Citation",
    "CitationValidator",
    "build_citations",
    "format_citations",
    "parse_citation_markers",
]
