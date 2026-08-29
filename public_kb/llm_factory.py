"""Compatibility re-exports for the moved LLM factory."""

from __future__ import annotations

from .services.llm import create_llm

__all__ = ["create_llm"]
