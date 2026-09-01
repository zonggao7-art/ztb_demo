# 功能：PDF 三档路由顶层编排器。
"""
PDF 三档路由顶层编排器（三档路由计划 §3 总链路）。

主流程：
  PDF
   → profile(T1) → classify(T1) → 路由决定
   → Tier A: fast_text(T2A)  / Tier B: 预留（fallback 到 Tier C）/ Tier C: MinerU(T3)
   → T4 Markdown 装配 + manifest

行为对齐：
  - 总开关 pdf_tiered_routing_enabled 关闭 → 回退旧的 MinerUParser 全量链路（M1 行为）；
  - Tier A 用 ThreadPoolExecutor 并行（计划 §2.4 允许 / §10 每个 worker 独立开 PDF）；
  - Tier B 当前未实现：fallback 到 Tier C（本批有框表≈0，按计划 §11 处理）；
  - Tier C 失败默认 fail-fast；pdf_tiered_allow_partial=true 时跳过并写 warning。

对外接口 parse(pdf_path) → str（raw markdown），与 MinerUParser.parse 兼容，
下游 TextCleaner → SemanticChunker 不变。
"""
from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# PyMuPDF 在调用处 lazy import（_profile_and_classify / _total_pages /
# _dispatch_tier_a worker 内），便于测试环境即使没装 pymupdf 也能 import 模块。

from ...config import Settings
from ...services.mineru_api_parser import MinerUApiParser
from ...services.mineru_parser import MinerUParser
from .pdf_complex_range import (
    ComplexRange,
    aggregate_complex_ranges,
)
from .pdf_fast_text import extract_page_markdown
from .pdf_legal_page_classifier import (
    LegalPageClassifier,
    PageRouteDecision,
)
from .pdf_markdown_assembler import (
    ParsedPage,
    assemble_pages,
    build_manifest,
    clean_assembled_document,
    dedup_repeated_lines,
)
from .pdf_mineru_router import MinerURouter

logger = logging.getLogger(__name__)

_TIER_A = "A"
_TIER_B = "B"
_TIER_C = "C"


# ── 顶层入口 ───────────────────────────────────────────────


class PdfRouter:
    """PDF 三档路由顶层编排器。

    关闭开关时（默认）回退到 MinerUParser（M1 全量行为），保持现有数据契约
    `parser.parse(pdf_path) -> str`。
    """

    def __init__(
        self,
        settings: Settings,
        *,
        miner_u_parser: Optional[MinerUApiParser] = None,
    ) -> None:
        """构造 PDF 三档路由编排器。

        Args:
            settings: 全局配置。
            miner_u_parser: 可选注入的 MinerUApiParser（默认根据 settings 构造）；
                主要用于测试时注入 fake。生产环境不传，走默认路径。
        """
        self._settings = settings
        self._enabled = settings.pdf_tiered_routing_enabled

        # 总开关关闭时只保留 fallback
        self._fallback_parser: Optional[MinerUParser] = None
        # 三档路径用到的组件
        self._classifier: Optional[LegalPageClassifier] = None
        self._miner_u_router: Optional[MinerURouter] = None

        if self._enabled:
            self._classifier = LegalPageClassifier(
                min_text_chars=settings.pdf_tiered_min_text_chars,
                image_area_ratio=settings.pdf_tiered_image_area_ratio,
                two_col_confidence=settings.pdf_tiered_two_col_confidence,
                # table_min_lines 暂沿用分类器默认值；T2B 表格骨架启用时再传入
            )
            miner_u_api = miner_u_parser or MinerUApiParser(settings)
            self._miner_u_router = MinerURouter(settings, parser=miner_u_api)
        else:
            self._fallback_parser = MinerUParser(settings)

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── 对外主接口 ─────────────────────────────────────────

    def parse(self, pdf_path: Path | str) -> str:
        """解析 PDF，返回组装后的 raw markdown 字符串。

        关闭开关时走 MinerUParser 全量；开启时走三档路由。
        """
        if not self._enabled:
            assert self._fallback_parser is not None
            return self._fallback_parser.parse(pdf_path)

        assert self._classifier is not None
        assert self._miner_u_router is not None

        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

        # 1) 整本 PDF 画像 + 分类
        t0 = time.time()
        decisions = self._profile_and_classify(pdf_path)
        # 1.5) Tier B 暂未实现 → 升级到 Tier C，由 MinerU 统一兜底
        decisions = [
            replace(
                d,
                tier=_TIER_C,
                reason=f"tier_b_unimplemented_fallback_to_c: {d.reason}",
                parser="mineru",
            )
            if d.tier == _TIER_B
            else d
            for d in decisions
        ]
        # 2) Tier C 聚合 + 边界扩展
        ranges = aggregate_complex_ranges(
            decisions,
            total_pages=self._total_pages(pdf_path),
            expand_pages=self._settings.pdf_tiered_expand_boundary_pages,
            range_id_seed=pdf_path.stem,
        )
        # 3) 各档并行解析
        parsed_pages = self._dispatch_all(pdf_path, decisions, ranges)
        # 4) T4 装配
        assembled = assemble_pages(parsed_pages)
        cleaned = clean_assembled_document(dedup_repeated_lines(assembled))
        elapsed = time.time() - t0
        # 5) manifest
        manifest = build_manifest(
            parsed_pages, parser_version=self._probe_parser_version()
        )
        manifest["elapsed_sec"] = round(elapsed, 3)
        manifest["routing"] = {
            "enabled": True,
            "ranges": [asdict(r) for r in ranges],
        }
        self._write_manifest(pdf_path, manifest)
        logger.info(
            "PDF 三档路由完成: %s tier_a=%d tier_b=%d tier_c_ranges=%d elapsed=%.2fs",
            pdf_path.name,
            sum(1 for d in decisions if d.tier == _TIER_A),
            sum(1 for d in decisions if d.tier == _TIER_B),
            len(ranges),
            elapsed,
        )
        return cleaned

    # ── 内部步骤 ───────────────────────────────────────────

    def _profile_and_classify(self, pdf_path: Path) -> List[PageRouteDecision]:
        """整本 PDF → 每页 PageRouteDecision。"""
        # 延迟导入避免循环 + 仅在开关开启时强依赖
        import pymupdf
        from .pdf_page_profile import build_page_profile

        assert self._classifier is not None
        out: List[PageRouteDecision] = []
        doc = pymupdf.open(str(pdf_path))
        try:
            for page_idx, page in enumerate(doc):
                profile = build_page_profile(page, page_idx)
                out.append(self._classifier.classify(profile))
        finally:
            doc.close()
        return out

    @staticmethod
    def _total_pages(pdf_path: Path) -> int:
        import pymupdf
        doc = pymupdf.open(str(pdf_path))
        try:
            return doc.page_count
        finally:
            doc.close()

    def _dispatch_all(
        self,
        pdf_path: Path,
        decisions: Sequence[PageRouteDecision],
        ranges: Sequence[ComplexRange],
    ) -> List[ParsedPage]:
        """Tier A / Tier B / Tier C 分别派发，合并为 ParsedPage 列表。

        Tier B 已在上游升级为 Tier C（fallback），此处不再单独处理。
        """
        # Tier C：远程 MinerU 解析
        assert self._miner_u_router is not None
        try:
            tier_c_results = self._miner_u_router.parse_ranges(
                source_pdf=pdf_path,
                ranges=ranges,
                parser_version=self._probe_parser_version(),
            )
        except RuntimeError as exc:
            if self._settings.pdf_tiered_allow_partial:
                logger.warning(
                    "MinerU 不可达且 pdf_tiered_allow_partial=true，跳过 Tier C 页: %s",
                    exc,
                )
                tier_c_results = []
            else:
                raise

        # Tier A：并行快路径。被 Tier C 范围覆盖的页（含边界扩展页）统一使用
        # MinerU 产物，避免同一页同时出现快路径和 MinerU 内容（计划 §6 T3）。
        tier_c_page_set = {idx for r in ranges for idx in r.page_idxs}
        tier_a_pages = [
            d for d in decisions
            if d.tier == _TIER_A and d.page_idx not in tier_c_page_set
        ]
        tier_a_results = self._dispatch_tier_a(pdf_path, tier_a_pages)

        # 合并
        parsed: List[ParsedPage] = []
        for d, md, warnings in tier_a_results:
            parsed.append(
                ParsedPage(
                    page_idx=d.page_idx,
                    markdown=md,
                    parser=d.parser,
                    route=d,
                    warnings=tuple(warnings),
                )
            )
        # Tier C 范围产物：补填路由信息（MinerURouter 返回 route=None，
        # 由编排器在汇总时填入，保证 manifest 每页可溯源）。
        range_by_start = {r.start_idx: r for r in ranges}
        for tp in tier_c_results:
            r = range_by_start.get(tp.page_idx)
            if r is not None and tp.route is None:
                tp = replace(
                    tp,
                    route=PageRouteDecision(
                        page_idx=tp.page_idx,
                        page_label="tier_c_range",
                        tier=_TIER_C,
                        reason="tier_c_range pages="
                              + ",".join(str(i) for i in r.page_idxs),
                        confidence=1.0,
                        parser="mineru",
                        features={},
                    ),
                )
            parsed.append(tp)
        # 按 page_idx 升序
        parsed.sort(key=lambda p: p.page_idx)
        return parsed

    def _dispatch_tier_a(
        self,
        pdf_path: Path,
        decisions: Sequence[PageRouteDecision],
    ) -> List[Tuple[PageRouteDecision, str, List[str]]]:
        """Tier A：并行快路径（每个 worker 独立打开 PDF）。"""
        if not decisions:
            return []

        max_workers = max(1, self._settings.pdf_tiered_fast_max_workers)

        def worker(d: PageRouteDecision) -> Tuple[PageRouteDecision, str, List[str]]:
            warnings: List[str] = []
            import pymupdf
            doc = pymupdf.open(str(pdf_path))  # 每个线程独立 Document
            try:
                page = doc.load_page(d.page_idx)
                if d.page_label == "two_col_text":
                    from .pdf_page_profile import build_page_profile
                    from .pdf_fast_text import iter_lines
                    from .pdf_two_column_reflow import reflow_page_markdown

                    profile = build_page_profile(page, d.page_idx)
                    lines = iter_lines(page)
                    md = reflow_page_markdown(
                        lines,
                        split_x=profile.two_col_split_x,
                        page_width=profile.width,
                    )
                else:
                    md = extract_page_markdown(page, page_idx=d.page_idx)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"fast_text_failed: {exc}")
                md = ""
            finally:
                doc.close()
            return d, md, warnings

        out: List[Tuple[PageRouteDecision, str, List[str]]] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(worker, d): d for d in decisions}
            for fut in as_completed(futures):
                out.append(fut.result())
        return out

    def _probe_parser_version(self) -> str:
        """探测 MinerU 服务端的 parser_version；失败时回退到空串。"""
        assert self._miner_u_router is not None
        try:
            return str(self._miner_u_router.parser.health().get("parser_version") or "")
        except Exception:  # noqa: BLE001
            return ""

    def _write_manifest(self, pdf_path: Path, manifest: Dict[str, Any]) -> None:
        out_dir = Path(self._settings.pdf_tiered_manifest_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{pdf_path.stem}.manifest.json"
        tmp = out_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, out_path)
        logger.info("manifest 已落盘: %s", out_path)
