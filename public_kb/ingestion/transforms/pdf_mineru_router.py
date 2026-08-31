# 功能：Tier C 复杂页的 MinerU 远程解析路由。
"""
Tier C 复杂页 → 远程 MinerU 解析路由（三档路由 T3）。

职责（对齐三档路由计划 §6 T3）：
  1. 接收 ComplexRange 列表和源 PDF，逐个范围调 MinerUApiParser 解析；
  2. 缓存 key = md5(sub_pdf_bytes) | parser_version | range_id；
  3. 本地缓存命中直接返回，未命中调远端 + 落盘缓存；
  4. 失败语义：
     - 默认 fail-fast（抛 RuntimeError，由编排器决定后续）；
     - 若 settings.pdf_tiered_allow_partial=true，可在调用处捕获并跳过。

输出：List[ParsedPage]。一个范围产出 1 个 ParsedPage（page_idx 取范围首
页，markdown 为整段范围 Markdown）；manifest 记录 range_pages，便于 T4 装
配层后续切分。
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import List, Optional, Sequence

from ...config import Settings
from ...services.mineru_api_parser import MinerUApiParser
from .pdf_complex_range import ComplexRange, write_subpdf
from .pdf_markdown_assembler import ParsedPage

logger = logging.getLogger(__name__)


# ── 缓存 ───────────────────────────────────────────────────


def compute_cache_key(
    sub_pdf_bytes: bytes,
    parser_version: str,
    range_id: str,
) -> str:
    """子 PDF 内容 + 解析器版本 + 范围 ID → MD5 hex 缓存键。

    对齐部署补充方案 §4.3（本地/服务器双侧一致）。
    """
    composite = f"{hashlib.md5(sub_pdf_bytes).hexdigest()}|{parser_version}|{range_id}"
    return hashlib.md5(composite.encode("utf-8")).hexdigest()


class _LocalCache:
    """本地磁盘缓存：mineru_output_dir / _mineru_api_cache / {key}.md。"""

    def __init__(self, cache_dir: Path) -> None:
        self._dir = cache_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> Optional[Path]:
        p = self._dir / f"{key}.md"
        return p if p.exists() else None

    def put(self, key: str, markdown: str) -> Path:
        p = self._dir / f"{key}.md"
        p.write_text(markdown, encoding="utf-8")
        return p


# ── 路由 ───────────────────────────────────────────────────


class MinerURouter:
    """Tier C 复杂页 → 远程 MinerU 解析路由。"""

    def __init__(
        self,
        settings: Settings,
        parser: Optional[MinerUApiParser] = None,
        cache: Optional[_LocalCache] = None,
    ) -> None:
        self._settings = settings
        self._parser = parser or MinerUApiParser(settings)
        cache_dir = Path(settings.mineru_output_dir) / "_mineru_api_cache"
        self._cache = cache or _LocalCache(cache_dir)

    @property
    def parser(self) -> MinerUApiParser:
        return self._parser

    def parse_ranges(
        self,
        *,
        source_pdf: Path | str,
        ranges: Sequence[ComplexRange],
        parser_version: str,
        subpdf_dir: Optional[Path] = None,
    ) -> List[ParsedPage]:
        """逐范围解析，返回 ParsedPage 列表（每范围一条）。

        Args:
            source_pdf: 源 PDF 路径。
            ranges: aggregate_complex_ranges 产出的复杂页范围。
            parser_version: 缓存 key 一部分；建议用 MinerUApiParser.health() 探测。
            subpdf_dir: 子 PDF 临时目录（默认用 mineru_output_dir/_mineru_subs）。
        """
        if not ranges:
            return []
        source_pdf = Path(source_pdf)
        subpdf_dir = subpdf_dir or (
            Path(self._settings.mineru_output_dir) / "_mineru_subs"
        )
        subpdf_dir.mkdir(parents=True, exist_ok=True)

        out: List[ParsedPage] = []
        for r in ranges:
            page_idxs = list(r.page_idxs)
            subpdf_path = subpdf_dir / f"{r.range_id}.pdf"
            write_subpdf(source_pdf, page_idxs, subpdf_path)
            subpdf_bytes = subpdf_path.read_bytes()
            cache_key = compute_cache_key(subpdf_bytes, parser_version, r.range_id)

            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.info(
                    "Tier C 命中本地缓存: range=%s key=%s (%d bytes)",
                    r.range_id, cache_key, cached.stat().st_size,
                )
                markdown = cached.read_text(encoding="utf-8")
                warnings: tuple = ("cache_hit",)
            else:
                markdown = self._parser.parse(subpdf_path)
                self._cache.put(cache_key, markdown)
                logger.info(
                    "Tier C 远端解析完成: range=%s key=%s (%d bytes)",
                    r.range_id, cache_key, len(markdown.encode("utf-8")),
                )
                warnings = ()

            out.append(
                ParsedPage(
                    page_idx=r.start_idx,
                    markdown=markdown,
                    parser="mineru",
                    route=None,  # 由编排器在汇总时填入
                    warnings=warnings,
                    # range_pages 在 ParsedPage 不存在此字段；编排器用 manifest 记录。
                )
            )
        return out
