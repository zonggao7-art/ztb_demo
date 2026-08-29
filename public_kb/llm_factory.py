"""Compatibility re-exports for the moved LLM factory."""

from __future__ import annotations

import warnings

from .services.llm import create_llm

warnings.warn(
    "public_kb.llm_factory is deprecated; use public_kb.services.llm",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["create_llm"]
