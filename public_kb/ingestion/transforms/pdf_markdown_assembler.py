# 功能：Markdown 装配层（三档路由 T4）— 页序缝合、块排序、表格标签、标题规范化、manifest。
"""
Markdown assembler for tiered PDF routing (T4).

职责（对齐三档路由计划 §6 T4 与附录 B）：
  1. 页序缝合：按 page_idx 升序 join 各页产物，穿插页（文本/MinerU 交替）还原；
  2. 页内块排序：PageBlock 按 order_key（块顶 y）排序，表格块原子插入正确位置；
  3. 表格占位标签：<!-- table: page=N, id=..., bbox=... --> 仅排查/溯源，不进 chunk metadata；
  4. 标题级别规范化：编→# 章→## 节→### 条→#### 款→##### 项→######；
  5. 去重页眉页脚 + 目录点线行；
  6. manifest：每页 route/parser/置信度/警告，供质量回查。

契约（先定后实现，见附录 B.1）：
  - 页级主键 page_idx（0 基）；表格占位标签不进下游 chunk metadata；
  - 本地表格与 MinerU 表格统一为 `|` Markdown 原子块（下游 PdfStructure 已有保护）。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .pdf_legal_page_classifier import PageRouteDecision
from .pdf_structure import strip_toc_noise

# 法规标题类型 → Markdown 级别（T4 标题规范化）
_LEGAL_HEADING_LEVELS: Tuple[Tuple[re.Pattern, int], ...] = (
    (re.compile(r"^第[一二三四五六七八九十百千\d]+编"), 1),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+章"), 2),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+节"), 3),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+条"), 4),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+款"), 5),
    (re.compile(r"^第[一二三四五六七八九十百千\d]+项"), 6),
)

# 页眉页脚去重豁免：法律标题行即使高频也不删
_LEGAL_HEADING_START_RE = re.compile(r"^第[一二三四五六七八九十百千\d]+[编章节条款项]")

# Markdown 标题行
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")

# 表格占位标签格式
_TABLE_TAG_RE = re.compile(
    r"^<!--\s*table:\s*page=(\d+),\s*id=([A-Za-z0-9_\-]+),\s*bbox=\(([^)]*)\)\s*-->$"
)


@dataclass(frozen=True)
class PageBlock:
    """页内原子块（图文表混排的最小装配单元）。

    order_key: 排序键（块顶 y 坐标），装配时按升序输出。
    kind: text / table / heading。
    content: 文本或 `|` Markdown 表格（表格为原子块，不被拆分）。
    bbox: (x0, y0, x1, y1) 溯源用，表格块必须有。
    """

    order_key: float
    kind: str
    content: str
    bbox: Tuple[float, ...] = ()


@dataclass(frozen=True)
class ParsedPage:
    """一页的解析产物（三档路由计划 §5.2 扩展）。

    page_idx: 0 基页号（页级主键，装配层据此还原顺序）。
    markdown: 该页的 Markdown（blocks 为空时使用）。
    parser: 实际解析器（fast_text / table_extractor / mineru）。
    route: 路由决定（含 tier/label/reason/confidence）。
    warnings: 非致命告警。
    blocks: 结构化块（可选；提供时优先于 markdown 做页内块排序）。
    """

    page_idx: int
    markdown: str = ""
    parser: str = ""
    route: Optional[PageRouteDecision] = None
    warnings: Tuple[str, ...] = ()
    blocks: Tuple[PageBlock, ...] = ()


# ── 表格占位标签 ─────────────────────────────────────────

def table_placeholder(page_idx: int, table_id: str, bbox: Sequence[float]) -> str:
    """生成表格占位标签（仅排查/溯源，不进 chunk metadata）。"""
    box = ",".join(str(round(float(v), 1)) for v in bbox)
    return f"<!-- table: page={page_idx + 1}, id={table_id}, bbox=({box}) -->"


def parse_table_placeholder(line: str) -> Optional[Dict]:
    """解析表格占位标签；非标签返回 None。"""
    m = _TABLE_TAG_RE.match(line.strip())
    if not m:
        return None
    page = int(m.group(1))
    tid = m.group(2)
    bbox = tuple(float(v.strip()) for v in m.group(3).split(",") if v.strip())
    return {"page": page, "id": tid, "bbox": bbox}


# ── 页内块排序 ───────────────────────────────────────────

def assemble_page_blocks(page_idx: int, blocks: Iterable[PageBlock]) -> str:
    """按 order_key 升序装配一页；表格块前插入占位标签。"""
    ordered = sorted(blocks, key=lambda b: b.order_key)
    parts: List[str] = []
    table_i = 0
    for block in ordered:
        if block.kind == "table":
            tid = f"t{page_idx}_{table_i}"
            parts.append(table_placeholder(page_idx, tid, block.bbox))
            table_i += 1
        parts.append(block.content)
    return "\n\n".join(parts)


# ── 页序缝合 ─────────────────────────────────────────────

def assemble_pages(
    pages: Iterable[ParsedPage],
    *,
    page_marker: bool = True,
) -> str:
    """按 page_idx 升序缝合全文档；穿插页（文本/MinerU 交替）按原页序还原。"""
    ordered = sorted(pages, key=lambda p: p.page_idx)
    parts: List[str] = []
    for page in ordered:
        md = assemble_page_blocks(page.page_idx, page.blocks) if page.blocks else page.markdown
        if page_marker:
            parts.append(f"<!-- page: {page.page_idx + 1} -->")
        parts.append(md)
    return "\n\n".join(parts)


# ── 标题级别规范化 ───────────────────────────────────────

def normalize_heading_levels(markdown: str) -> str:
    """把法规标题（编/章/节/条/款/项）统一映射到规范级别；非法规标题保留原样。"""
    out: List[str] = []
    for line in markdown.split("\n"):
        m = _MD_HEADING_RE.match(line.strip())
        if m:
            text = m.group(2).strip()
            for pattern, level in _LEGAL_HEADING_LEVELS:
                if pattern.match(text):
                    out.append("#" * level + " " + text)
                    break
            else:
                out.append(line)  # 非法规标题不改
        else:
            out.append(line)
    return "\n".join(out)


# ── 页眉页脚 / 目录去重 ──────────────────────────────────

def dedup_repeated_lines(
    markdown: str,
    *,
    min_count: int = 5,
    max_len: int = 80,
) -> str:
    """按行频次去重页眉/页脚；法律标题行（第X章/条等）即使高频也保留。"""
    lines = markdown.split("\n")
    stripped = [line.strip() for line in lines]
    counter = Counter(
        s for s in stripped
        if s and len(s) < max_len and not _LEGAL_HEADING_START_RE.match(s)
    )
    repeated = {s for s, count in counter.items() if count >= min_count}
    if not repeated:
        return markdown
    return "\n".join(line for line in lines if line.strip() not in repeated)


def clean_assembled_document(markdown: str) -> str:
    """装配后清理：去目录点线行 + 去重复页眉页脚 + 标题级别规范化。"""
    markdown = strip_toc_noise(markdown)
    markdown = dedup_repeated_lines(markdown)
    return normalize_heading_levels(markdown)


# ── manifest ─────────────────────────────────────────────

@dataclass(frozen=True)
class ManifestRecord:
    """单页路由/解析记录。"""

    page: int
    tier: str
    label: str
    parser: str
    planned_parser: str
    reason: str
    confidence: float
    warnings: Tuple[str, ...] = ()


def build_manifest(
    pages: Iterable[ParsedPage],
    *,
    parser_version: str = "",
) -> Dict:
    """生成路由质量报告：逐页记录 + tier/parser 分布摘要。"""
    ordered = sorted(pages, key=lambda p: p.page_idx)
    records: List[ManifestRecord] = []
    tier_count: Dict[str, int] = {}
    parser_count: Dict[str, int] = {}
    for page in ordered:
        route = page.route
        tier = route.tier if route else ""
        label = route.page_label if route else ""
        planned = route.parser if route else ""
        actual = page.parser or planned
        records.append(ManifestRecord(
            page=page.page_idx + 1,
            tier=tier,
            label=label,
            parser=actual,
            planned_parser=planned,
            reason=route.reason if route else "",
            confidence=route.confidence if route else 0.0,
            warnings=page.warnings,
        ))
        tier_count[tier] = tier_count.get(tier, 0) + 1
        parser_count[actual] = parser_count.get(actual, 0) + 1

    return {
        "parser_version": parser_version,
        "pages": [
            {
                "page": r.page,
                "tier": r.tier,
                "label": r.label,
                "parser": r.parser,
                "planned_parser": r.planned_parser,
                "reason": r.reason,
                "confidence": r.confidence,
                "warnings": list(r.warnings),
            }
            for r in records
        ],
        "summary": {
            "total_pages": len(records),
            "tier_distribution": tier_count,
            "parser_distribution": parser_count,
        },
    }
