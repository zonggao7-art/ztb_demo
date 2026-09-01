# 功能：引用溯源 — 对外门面，re-export 拆分后的各模块（M5 Step B）。
"""引用溯源门面 — 拆分后保持既有导入路径不变。

原 public_kb.generation.citations 导出的全部符号现由三个子模块提供：
  citations_models / citations_build / citations_validate
本模块仅做 re-export，保证 `from public_kb.generation.citations import ...`
（测试、agent、__main__ 等调用方）无需改动。
"""

from __future__ import annotations

from .citations_build import build_citations, format_citations
from .citations_models import (
    Citation,
    RuleResult,
    CitationValidationReport,
    _compute_cited_sets,
    parse_citation_markers,
)
from .citations_validate import CitationValidator

__all__ = [
    "Citation",
    "RuleResult",
    "CitationValidationReport",
    "build_citations",
    "format_citations",
    "parse_citation_markers",
    "CitationValidator",
]
