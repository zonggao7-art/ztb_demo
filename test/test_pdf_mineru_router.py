"""L4 — pdf_mineru_router 单元测试（mock MinerUApiParser，不连真实服务）。"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import List

import pytest

from public_kb.ingestion.transforms.pdf_complex_range import ComplexRange
from public_kb.ingestion.transforms.pdf_mineru_router import (
    MinerURouter,
    _LocalCache,
    compute_cache_key,
)
from public_kb.services.mineru_api_parser import MinerUApiParser


# ── 缓存 key ────────────────────────────────────────────────


def test_cache_key_stable_for_same_inputs():
    k1 = compute_cache_key(b"abc", [3, 4, 5], "1.3.12", "r1")
    k2 = compute_cache_key(b"abc", [3, 4, 5], "1.3.12", "r1")
    assert k1 == k2


def test_cache_key_changes_with_version():
    k1 = compute_cache_key(b"abc", [3, 4, 5], "1.3.12", "r1")
    k2 = compute_cache_key(b"abc", [3, 4, 5], "1.3.13", "r1")
    assert k1 != k2


def test_cache_key_changes_with_range():
    k1 = compute_cache_key(b"abc", [3, 4, 5], "1.3.12", "r1")
    k2 = compute_cache_key(b"abc", [4, 5, 6], "1.3.12", "r1")
    assert k1 != k2


def test_cache_key_changes_with_content():
    k1 = compute_cache_key(b"abc", [3, 4, 5], "1.3.12", "r1")
    k2 = compute_cache_key(b"def", [3, 4, 5], "1.3.12", "r1")
    assert k1 != k2


def test_cache_key_stable_for_permuted_page_idxs():
    """页码顺序不影响 key（同集合视为同一范围）。"""
    k1 = compute_cache_key(b"abc", [3, 4, 5], "1.3.12", "r1")
    k2 = compute_cache_key(b"abc", [5, 4, 3], "1.3.12", "r1")
    assert k1 == k2


# ── LocalCache ──────────────────────────────────────────────


def test_local_cache_miss_then_hit(tmp_path: Path):
    c = _LocalCache(tmp_path)
    assert c.get("nope") is None
    p = c.put("k1", "# hello")
    assert p.exists()
    assert c.get("k1").read_text(encoding="utf-8") == "# hello"


# ── MinerURouter（mock MinerUApiParser）──────────────────────


class _FakeParser(MinerUApiParser):
    """替身 MinerUApiParser：返回固定 Markdown，记录调用。"""

    def __init__(self) -> None:  # type: ignore[no-super-call]
        self.calls: List[Path] = []
        self.markdown_by_path: dict[Path, str] = {}

    def health(self) -> dict:
        return {"status": "ok", "parser": "mineru", "parser_version": "1.3.12"}

    def parse(self, pdf_path, *, page_range=None):  # type: ignore[override]
        self.calls.append(Path(pdf_path))
        return self.markdown_by_path.get(Path(pdf_path), f"# from {Path(pdf_path).name}\n")


class _Settings:
    mineru_output_dir: str = ""


def test_mineru_router_writes_subpdf_and_caches(tmp_path: Path):
    """构造一个最小可写的 fake PDF（用 pymupdf 创建），验证：
      1) 子 PDF 写到了 subpdf_dir；
      2) 调了 MinerUApiParser.parse 一次；
      3) 缓存命中时不再调 parse。
    """
    pytest.importorskip("pymupdf")

    import pymupdf

    # 1) 构造一个 3 页 PDF
    src = tmp_path / "src.pdf"
    doc = pymupdf.open()
    try:
        for i in range(3):
            doc.new_page(width=400, height=600)
        doc.save(str(src))
    finally:
        doc.close()

    # 2) 设 MinerUOutput_dir
    out_root = tmp_path / "out"
    out_root.mkdir()
    settings = _Settings()
    settings.mineru_output_dir = str(out_root)

    fake = _FakeParser()
    # 让 fake 对子 PDF 返回带范围标记的 markdown，便于断言
    fake.markdown_by_path = {}

    router = MinerURouter(settings, parser=fake)  # type: ignore[arg-type]

    rng = ComplexRange(
        range_id="r0",
        core_page_idxs=(1, 2),
        page_idxs=(0, 1, 2),
        expanded_before=1,
        expanded_after=0,
    )
    # 第一次：无缓存 → 调 fake.parse
    out1 = router.parse_ranges(
        source_pdf=src, ranges=[rng], parser_version="1.3.12",
        subpdf_dir=out_root / "_subs",
    )
    assert len(fake.calls) == 1
    assert len(out1) == 1
    assert out1[0].page_idx == 0  # range start_idx
    assert out1[0].parser == "mineru"
    assert out1[0].warnings == ()

    # 第二次：缓存命中 → 不再调 fake.parse
    out2 = router.parse_ranges(
        source_pdf=src, ranges=[rng], parser_version="1.3.12",
        subpdf_dir=out_root / "_subs",
    )
    assert len(fake.calls) == 1  # 没新增
    assert out2[0].warnings == ("cache_hit",)

    # 缓存文件存在
    cache_files = list((out_root / "_mineru_api_cache").glob("*.md"))
    assert len(cache_files) == 1


def test_mineru_router_version_change_bypasses_cache(tmp_path: Path):
    """解析器版本变化 → 缓存 key 变 → 重新调远端。"""
    pytest.importorskip("pymupdf")

    import pymupdf

    src = tmp_path / "src.pdf"
    doc = pymupdf.open()
    try:
        doc.new_page(width=400, height=600)
        doc.save(str(src))
    finally:
        doc.close()

    settings = _Settings()
    settings.mineru_output_dir = str(tmp_path)
    fake = _FakeParser()
    router = MinerURouter(settings, parser=fake)  # type: ignore[arg-type]

    rng = ComplexRange(
        range_id="r0", core_page_idxs=(0,), page_idxs=(0,),
    )
    router.parse_ranges(source_pdf=src, ranges=[rng], parser_version="1.0")
    router.parse_ranges(source_pdf=src, ranges=[rng], parser_version="1.0")
    router.parse_ranges(source_pdf=src, ranges=[rng], parser_version="2.0")
    # 前两次同版本 → 第二走缓存；版本变化 → 第三次走远端
    assert len(fake.calls) == 2
