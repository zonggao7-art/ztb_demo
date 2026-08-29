"""Compatibility re-exports for the moved Milvus storage service."""

from __future__ import annotations

import warnings

from .contracts import (
    ConfigurationContractError,
    IngestionContractError,
    MilvusCollectionContract,
)

warnings.warn(
    "public_kb.milvus_store is deprecated; use public_kb.services.milvus_store",
    DeprecationWarning,
    stacklevel=2,
)
from .services.milvus_store import (
    MilvusStoreManager,
    validate_embedding_batch,
    validate_ingestion_documents,
)

__all__ = [
    "ConfigurationContractError",
    "IngestionContractError",
    "MilvusCollectionContract",
    "MilvusStoreManager",
    "validate_embedding_batch",
    "validate_ingestion_documents",
]
