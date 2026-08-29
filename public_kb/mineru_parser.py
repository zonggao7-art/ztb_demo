"""Compatibility re-exports for the moved MinerU parser."""

from __future__ import annotations

import warnings

from .services.mineru_parser import MinerUParser

warnings.warn(
    "public_kb.mineru_parser is deprecated; use public_kb.services.mineru_parser",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["MinerUParser"]
