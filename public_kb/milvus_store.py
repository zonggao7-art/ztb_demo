"""Compatibility re-exports for the moved Milvus storage service."""

from __future__ import annotations

from .contracts import (
    ConfigurationContractError,
    IngestionContractError,
    MilvusCollectionContract,
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
