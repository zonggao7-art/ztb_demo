"""Compatibility re-exports for the moved embedding service."""

from __future__ import annotations

import warnings

from .services.embeddings import _SafeEmbeddings, create_embeddings

warnings.warn(
    "public_kb.embedding_service is deprecated; use public_kb.services.embeddings",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = ["_SafeEmbeddings", "create_embeddings"]
