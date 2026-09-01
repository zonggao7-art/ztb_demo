# 功能：定义离线入库 Source 的统一接口契约。
"""Source contract for ingestion pipelines."""

from __future__ import annotations

from typing import Protocol

from ..models import SourceResult


class Source(Protocol):
    """A component that loads source data as normalized documents."""

    def load(self) -> SourceResult:
        """Load and transform source records into documents."""
        ...
