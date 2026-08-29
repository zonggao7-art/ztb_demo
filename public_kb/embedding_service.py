"""Compatibility re-exports for the moved embedding service."""

from __future__ import annotations

from .services.embeddings import _SafeEmbeddings, create_embeddings

__all__ = ["_SafeEmbeddings", "create_embeddings"]
