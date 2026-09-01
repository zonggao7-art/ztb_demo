# -*- coding: utf-8 -*-
"""L0 页面画像采样 — 输出三本法规书的三档路由 Tier 分布与特征统计。

用法：
    python scripts/pdf_l0_profile.py
输出：
    DATA/raw_data/_pdf_tiered_manifest/l0_profile.json（完整每页画像）
    控制台打印每本书的粗分档 Tier 分布（初版启发式，供 L1 分类器校准）

只读：不修改任何 PDF，不写入 Milvus。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pymupdf

PDFS = {
    "book1_白如银实务": r"DATA/raw_data/law_pdf/招标投标法律解读与风险防范实务 (白如银) (Z-Library).pdf",
    "book2_法律法规全书": r"DATA/raw_data/law_pdf/中华人民共和国招标投标法律法规全书：含相关政策 (法律法规出版社法规中心) (Z-Library)(OCR).pdf",
    "book3_1200问": r"DATA/raw_data/law_pdf/政府采购、工程招标、投标与评标1200问（第3版）_刘海桑 编著.pdf",
}

ARTICLE_RE = re.compile(r"第[一二三四五六七八九十百千\d]+[章节条款]")

# L0 初版阈值（供 L1 校准，不承诺最终）
MIN_TEXT_CHARS = 50          # 低于此 → visual_or_scan 候选（主信号）
TWO_COL_GAP_RATIO = 0.15     # block x 间隙 > 页宽 * 该值 → 双栏
TABLE_MIN_LINES = 5          # 横/竖线任一 >= 该值 → 表格候选
BACKGROUND_IMG_RATIO = 0.85  # 图片 bbox 覆盖 > 该页面积比 → 全页背景图（水印/OCR底图），不参与图片占比


def profile_page(page) -> dict:
    w, h = page.rect.width, page.rect.height
    text = page.get_text("text")
    text_chars = len(text.strip())

    blocks = [b for b in page.get_text("blocks") if b[6] == 0]  # 文本块
    x_starts = sorted({round(b[0]) for b in blocks})
    two_col = False
    if len(x_starts) >= 2:
        gaps = [x_starts[i + 1] - x_starts[i] for i in range(len(x_starts) - 1)]
        two_col = any(g > w * TWO_COL_GAP_RATIO for g in gaps)

    # 表格线
    n_hline = n_vline = 0
    for d in page.get_drawings():
        for it in d["items"]:
            if it[0] == "l":
                if abs(it[1].y - it[2].y) < 0.5:
                    n_hline += 1
                elif abs(it[1].x - it[2].x) < 0.5:
                    n_vline += 1
    table_candidate = n_hline >= TABLE_MIN_LINES or n_vline >= TABLE_MIN_LINES

    # 图片面积（排除全页背景图/水印/OCR 底图）
    page_area = w * h
    content_img_area = 0.0
    for info in page.get_image_info():
        bbox = info.get("bbox")
        if bbox:
            area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            if page_area and area / page_area >= BACKGROUND_IMG_RATIO:
                continue  # 全页背景图，跳过
            content_img_area += area
    img_ratio = content_img_area / page_area if page_area else 0.0

    article_count = len(ARTICLE_RE.findall(text))

    return {
        "page": page.number + 1,
        "text_chars": text_chars,
        "n_blocks": len(blocks),
        "two_col": two_col,
        "table_candidate": table_candidate,
        "n_hline": n_hline,
        "n_vline": n_vline,
        "img_ratio": round(img_ratio, 3),
        "article_count": article_count,
    }


def coarse_tier(p: dict) -> str:
    """初版启发式粗分档（L0 采样用，L1 分类器会精细化）。

    扫描/图片页以「低文本」为主信号；全页背景图（水印/OCR 底图）不计入图片占比。
    """
    if p["text_chars"] < MIN_TEXT_CHARS:
        return "visual_or_scan" if p["img_ratio"] > 0 else "low_text"
    if p["table_candidate"]:
        return "table_candidate"
    if p["two_col"]:
        return "two_col_text"
    return "text"


def main() -> int:
    out_dir = Path("DATA/raw_data/_pdf_tiered_manifest")
    out_dir.mkdir(parents=True, exist_ok=True)

    summary = {}
    for name, path in PDFS.items():
        doc = pymupdf.open(path)
        pages = [profile_page(doc[i]) for i in range(doc.page_count)]
        doc.close()

        dist: dict[str, int] = {}
        for p in pages:
            dist[coarse_tier(p)] = dist.get(coarse_tier(p), 0) + 1
        total = len(pages)
        dist_pct = {k: f"{v}/{total} ({v/total*100:.1f}%)" for k, v in sorted(dist.items())}

        two_col_pages = sum(1 for p in pages if p["two_col"])
        table_pages = sum(1 for p in pages if p["table_candidate"])
        low_text_pages = sum(1 for p in pages if p["text_chars"] < MIN_TEXT_CHARS)

        summary[name] = {
            "total_pages": total,
            "tier_dist": dist_pct,
            "two_col_pages": two_col_pages,
            "table_candidate_pages": table_pages,
            "low_text_pages": low_text_pages,
            "avg_article_per_page": round(
                sum(p["article_count"] for p in pages) / max(1, total), 2
            ),
        }
        print(f"\n===== {name} ({total} 页) =====")
        for k, v in dist_pct.items():
            print(f"  {k:<18} {v}")
        print(f"  双栏页={two_col_pages}  表格候选页={table_pages}  低文本页={low_text_pages}")
        print(f"  平均条文号密度={summary[name]['avg_article_per_page']}/页")

    with open(out_dir / "l0_profile.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print(f"\n完整画像已写入: {out_dir / 'l0_profile.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
