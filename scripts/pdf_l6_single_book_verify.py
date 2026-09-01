# 功能：L6 单书链路验证——本地快路径 + 云端 MinerU 复杂页 + T4 拼接回完整文档。
"""L6 单书链路端到端核验（三档路由计划 §8 的单书缩减版）。

验证对象：一本真实法规 PDF（默认 book1，774 页）。
验证内容：
  1. PdfRouter 全流程：页面画像 → 三档分类 → Tier A 本地快路径 /
     Tier C 子 PDF 上云 MinerU → T4 按页序拼回一个完整 Markdown + manifest；
  2. 自动化质量核验：
     - G1 完整性：manifest 覆盖全部页、页标记单调无缺失重复
     - G2 章节标题保留率 >= 98%（对照 PDF 原文文本层，硬门槛）
     - G3 拼接产物非空且 Tier C 范围段落位非空
     - 报告项（不设门槛）：条款号连续性断档、页眉页脚残留、双栏页抽样、警告汇总

用法：
  python scripts/pdf_l6_single_book_verify.py                 # 默认 book1
  python scripts/pdf_l6_single_book_verify.py --pdf <path>    # 指定其他 PDF

产物（DATA 不入版本库）：
  DATA/raw_data/_pdf_tiered_manifest/<stem>.assembled.md   拼接回的完整文档
  DATA/raw_data/_pdf_tiered_manifest/<stem>.verify.json    核验结果
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 仓库根

from public_kb.config import Settings  # noqa: E402

# ── 中文数字 → int（用于 第X章/第X条 序列核验）────────────────

_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}


def cn_to_int(s: str) -> int:
    """中文数字/阿拉伯数字转 int；解析失败返回 -1。"""
    s = s.strip()
    if s.isdigit():
        return int(s)
    total, section, num = 0, 0, 0
    for ch in s:
        if ch in _CN_DIGIT:
            num = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            unit = _CN_UNIT[ch]
            section += (num if num else 1) * unit
            num = 0
            if unit >= 100:  # 百/千 结清当前段
                total += section
                section = 0
        # 「零」跳过
    if total == section == num == 0:
        return -1
    return total + section + num


_CHAPTER_RE = re.compile(r"^第([0-9零一二三四五六七八九十百千]+)章")
_ARTICLE_LINE_RE = re.compile(r"^第([0-9零一二三四五六七八九十百千]+)条")
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s*(.+)$")
_PAGE_MARKER_RE = re.compile(r"<!-- page: (\d+) -->")


def _canonical_chapter(text: str) -> int:
    m = _CHAPTER_RE.match(text.strip())
    return cn_to_int(m.group(1)) if m else -1


# ── 基线采集：直接从 PDF 文本层取章节标题集合 ─────────────────


def collect_pdf_chapters(pdf_path: Path) -> Set[int]:
    """扫 PDF 文本层，收集「第X章」标题行（短行）的章节号集合。"""
    import pymupdf

    chapters: Set[int] = set()
    doc = pymupdf.open(str(pdf_path))
    try:
        for page in doc:
            for line in page.get_text("text").split("\n"):
                line = line.strip()
                if line and len(line) <= 30:
                    n = _canonical_chapter(line)
                    if n > 0:
                        chapters.add(n)
    finally:
        doc.close()
    return chapters


def collect_md_chapters(markdown: str) -> Set[int]:
    """从拼接产物收集章节号集合（标题行或普通行首均可）。"""
    chapters: Set[int] = set()
    for line in markdown.split("\n"):
        line = line.strip()
        if not line or len(line) > 30:
            continue
        if _MD_HEADING_RE.match(line):
            line = _MD_HEADING_RE.match(line).group(2).strip()  # noqa: E501
        n = _canonical_chapter(line)
        if n > 0:
            chapters.add(n)
    return chapters


# ── 报告项核验 ─────────────────────────────────────────────


def check_article_sequence(markdown: str) -> Dict:
    """条款号连续性（报告项）：按章分段，统计行首「第X条」的断档/重复。"""
    segments: List[Tuple[str, List[int]]] = []
    current_title, current_nums = "(卷首)", []
    for line in markdown.split("\n"):
        stripped = line.strip()
        m = _CHAPTER_RE.match(stripped)
        if m:
            if current_nums:
                segments.append((current_title, current_nums))
            current_title, current_nums = stripped[:30], []
            continue
        am = _ARTICLE_LINE_RE.match(stripped)
        if am:
            n = cn_to_int(am.group(1))
            if n > 0:
                current_nums.append(n)
    if current_nums:
        segments.append((current_title, current_nums))

    gaps = dupes = out_of_order = 0
    for _, nums in segments:
        for a, b in zip(nums, nums[1:]):
            if b == a:
                dupes += 1
            elif b < a:
                out_of_order += 1
            elif b > a + 1:
                gaps += b - a - 1
    return {
        "segments": len(segments),
        "articles_total": sum(len(n) for _, n in segments),
        "gaps": gaps,
        "duplicates": dupes,
        "out_of_order": out_of_order,
    }


def check_repeated_lines(markdown: str, *, min_count: int = 5,
                         max_len: int = 80) -> List[Tuple[str, int]]:
    """页眉页脚残留检测（报告项）：装配去重后仍高频出现的短行。"""
    counter = Counter(
        line.strip() for line in markdown.split("\n")
        if line.strip() and len(line.strip()) < max_len
        and not _CHAPTER_RE.match(line.strip())
        and not _ARTICLE_LINE_RE.match(line.strip())
    )
    return [(s, c) for s, c in counter.most_common(20) if c >= min_count]


# ── 主流程 ─────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description="L6 单书链路验证")
    parser.add_argument(
        "--pdf",
        default=r"DATA/raw_data/law_pdf/"
                r"招标投标法律解读与风险防范实务 (白如银) (Z-Library).pdf",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"[FAIL] PDF 不存在: {pdf_path}")
        return 2

    s = Settings()
    for name, val in (
        ("pdf_tiered_routing_enabled", s.pdf_tiered_routing_enabled),
        ("mineru_api_base_url", bool(s.mineru_api_base_url)),
        ("mineru_api_token", bool(s.mineru_api_token)),
    ):
        if not val:
            print(f"[FAIL] 配置未就绪: {name}")
            return 2

    # ── 1) 三档路由全流程（Tier C 有本地缓存，重跑只补缺口）──
    from public_kb.ingestion.transforms.pdf_router import PdfRouter

    print(f"[1/4] PDF: {pdf_path.name} "
          f"({pdf_path.stat().st_size / 1024 / 1024:.1f} MB)", flush=True)
    t0 = time.time()
    md = PdfRouter(s).parse(pdf_path)
    elapsed = time.time() - t0
    print(f"[1/4] 解析完成 elapsed={elapsed:.1f}s markdown={len(md)} chars",
          flush=True)

    manifest_dir = Path(s.pdf_tiered_manifest_dir)
    manifest_path = manifest_dir / f"{pdf_path.stem}.manifest.json"
    assembled_path = manifest_dir / f"{pdf_path.stem}.assembled.md"
    assembled_path.write_text(md, encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # ── 2) 完整性核验（G1）──
    print("[2/4] 完整性核验…", flush=True)
    pages: List[Dict] = manifest["pages"]
    total_pages = manifest.get("total_pages") or len(pages)
    import pymupdf
    _doc = pymupdf.open(str(pdf_path))
    real_total = _doc.page_count
    _doc.close()

    tier_count: Counter = Counter(p["tier"] for p in pages)
    parser_count: Counter = Counter(p["parser"] for p in pages)
    label_count: Counter = Counter(p["label"] for p in pages)
    ranges = manifest.get("routing", {}).get("ranges", [])

    markers = [int(m.group(1)) for m in _PAGE_MARKER_RE.finditer(md)]
    marker_sorted = markers == sorted(markers)
    marker_dups = len(markers) - len(set(markers))
    # Tier A 页（已剔除范围覆盖页）+ 每个范围一个标记（范围内部页共享范围首标记）
    expected_markers = sum(1 for p in pages if p["tier"] == "A") + len(ranges)

    # 全页覆盖：A 行页 ∪ 所有范围页（范围内部页由 routing.ranges 承载，不单列行）
    a_pages_1b = {p["page"] for p in pages if p["tier"] == "A"}
    range_pages_1b = {idx + 1 for r in ranges for idx in r["page_idxs"]}
    covered_1b = a_pages_1b | range_pages_1b
    empty_tier_rows = sum(1 for p in pages if not p["tier"])

    g1_pass = (
        covered_1b == set(range(1, real_total + 1))
        and empty_tier_rows == 0
        and marker_sorted
        and marker_dups == 0
    )

    # ── 3) 章节标题保留率（G2，硬门槛 >= 98%）──
    print("[3/4] 章节标题保留率核验（对照 PDF 文本层）…", flush=True)
    pdf_chapters = collect_pdf_chapters(pdf_path)
    md_chapters = collect_md_chapters(md)
    missing = sorted(pdf_chapters - md_chapters)
    retention = (len(pdf_chapters & md_chapters) / len(pdf_chapters)
                 if pdf_chapters else 1.0)
    g2_pass = retention >= 0.98

    # ── 4) Tier C 落位 + 报告项 ──
    print("[4/4] Tier C 落位与报告项…", flush=True)
    tier_c_checks = []
    for r in ranges:
        start_page = r["page_idxs"][0] + 1  # 1 基
        seg = _section_after_marker(md, start_page)
        tier_c_checks.append({
            "range_id": r["range_id"],
            "pages": r["page_idxs"],
            "start_marker_found": start_page in set(markers),
            "section_chars": len(seg),
        })
    g3_pass = all(c["start_marker_found"] and c["section_chars"] > 0
                  for c in tier_c_checks) if ranges else True

    articles = check_article_sequence(md)
    repeated = check_repeated_lines(md)
    warnings_summary = Counter(
        w for p in pages for w in p.get("warnings", ()))

    # ── 汇总 ──
    result = {
        "pdf": str(pdf_path),
        "total_pages": real_total,
        "elapsed_sec": round(elapsed, 1),
        "markdown_chars": len(md),
        "tier_distribution": dict(tier_count),
        "label_distribution": dict(label_count),
        "parser_distribution": dict(parser_count),
        "complex_ranges": len(ranges),
        "tier_c_checks": tier_c_checks,
        "chapter_retention": round(retention, 4),
        "chapters_missing": missing,
        "articles": articles,
        "repeated_lines": repeated,
        "warnings_summary": dict(warnings_summary),
        "gates": {"G1_integrity": g1_pass,
                  "G2_chapter_retention>=98%": g2_pass,
                  "G3_tier_c_placement": g3_pass},
        "pass": g1_pass and g2_pass and g3_pass,
    }
    verify_path = manifest_dir / f"{pdf_path.stem}.verify.json"
    verify_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 64)
    print("L6 单书链路验证报告")
    print("=" * 64)
    print(f"总页数          : {real_total}")
    print(f"耗时            : {elapsed:.1f}s（含 Tier C 远程解析）")
    print(f"拼接产物        : {assembled_path} ({len(md)} chars)")
    print(f"路由分布(tier)  : {dict(tier_count)}")
    print(f"路由分布(label) : {dict(label_count)}")
    print(f"复杂范围        : {len(ranges)} 个")
    for c in tier_c_checks:
        print(f"  - {c['range_id']}: pages={c['pages']} "
              f"marker={'√' if c['start_marker_found'] else '×'} "
              f"chars={c['section_chars']}")
    print(f"章节标题保留率  : {retention:.2%}（{len(md_chapters)}/"
          f"{len(pdf_chapters)}）{'  ✓' if g2_pass else '  ✗'}")
    if missing:
        print(f"  缺失章节       : {missing}")
    print(f"条款连续性      : 段={articles['segments']} "
          f"条={articles['articles_total']} 断档={articles['gaps']} "
          f"重复={articles['duplicates']} 乱序={articles['out_of_order']}"
          "（报告项）")
    print(f"高频残留行      : {len(repeated)} 条（报告项）")
    for line_, cnt in repeated[:5]:
        print(f"  x{cnt}: {line_[:50]}")
    print(f"警告汇总        : {dict(warnings_summary) or '无'}")
    print(f"页标记          : {len(markers)} 个（预期 {expected_markers}）"
          f" 有序={'√' if marker_sorted else '×'} 重复={marker_dups}")
    print("-" * 64)
    verdict = "PASS ✅" if result["pass"] else "FAIL ❌"
    print(f"总体结论        : {verdict}  gates={result['gates']}")
    print(f"核验明细        : {verify_path}")
    return 0 if result["pass"] else 1


def _section_after_marker(markdown: str, page_1based: int) -> str:
    """取页标记 page_1based 之后到下一个页标记之间的文本。"""
    pattern = re.compile(r"<!-- page: (\d+) -->")
    matches = list(pattern.finditer(markdown))
    for i, m in enumerate(matches):
        if int(m.group(1)) == page_1based:
            start = m.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(markdown)
            return markdown[start:end]
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
