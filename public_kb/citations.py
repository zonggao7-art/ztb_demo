"""Compatibility re-exports for the moved citation utilities."""

from __future__ import annotations

import warnings

from .generation.citations import (
    Citation,
    CitationValidator,
    build_citations,
    format_citations,
    parse_citation_markers,
)

warnings.warn(
    "public_kb.citations is deprecated; use public_kb.generation.citations",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "Citation",
    "CitationValidator",
    "build_citations",
    "format_citations",
    "parse_citation_markers",
]
