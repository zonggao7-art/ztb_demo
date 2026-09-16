from __future__ import annotations

import json
from pathlib import Path

import pytest

from public_kb.__main__ import _build_parser
from public_kb.book_pipeline import (
    _expected_chapters,
    load_documents_jsonl,
    load_markdown_with_sidecar,
    prepare_handoff,
)
from public_kb.normalize import BOOK_PROFILES, SourceBlock


def _write_content_list(root: Path, directory: str, filename: str, items: list[dict]) -> None:
    auto = root / directory / "auto"
    auto.mkdir(parents=True)
    (auto / filename).write_text(
        json.dumps(items, ensure_ascii=False), encoding="utf-8"
    )


def _fixture_root(tmp_path: Path, *, short_book1: bool = False) -> Path:
    root = tmp_path / "raw_data"
    long_book1 = "有效的招标投标法律实务正文。" * (5 if short_book1 else 90)
    long_book2 = "答：政府采购应当遵循公开透明、公平竞争、公正和诚实信用原则。" * 45
    long_book3 = "为了规范招标投标活动，保护国家利益和当事人的合法权益，制定本法。" * 40
    _write_content_list(
        root,
        "招标投标法律解读与风险防范实务 (白如银)",
        "招标投标法律解读与风险防范实务_content_list.json",
        [
            {"type": "text", "text": "目录", "page_idx": 8, "text_level": 1},
            {"type": "text", "text": "第一章 招标投标基础知识", "page_idx": 15},
            {"type": "text", "text": "一、基本概念", "page_idx": 15},
            {"type": "text", "text": long_book1, "page_idx": 16},
        ],
    )
    _write_content_list(
        root,
        "政府采购、工程招标、投标与评标1200问（第3版）_刘海桑",
        "政府采购、工程招标、投标与评标1200问_content_list.json",
        [
            {"type": "text", "text": "第一章", "page_idx": 6},
            {"type": "text", "text": "政府采购", "page_idx": 6},
            {"type": "text", "text": "第一节", "page_idx": 7},
            {"type": "text", "text": "政府采购概述", "page_idx": 7},
            {"type": "text", "text": "1.什么是政府采购？", "page_idx": 7},
            {"type": "text", "text": long_book2, "page_idx": 8},
        ],
    )
    _write_content_list(
        root,
        "中华人民共和国招标投标法律法规全书 (OCR)",
        "中华人民共和国招标投标法律法规全书_content_list.json",
        [
            {"type": "text", "text": "一、 招标投标", "page_idx": 12},
            {"type": "text", "text": "中华人民共和国招标投标法", "page_idx": 14, "text_level": 1},
            {"type": "text", "text": "第一章 总则", "page_idx": 14},
            {"type": "text", "text": "第一条【立法目的】" + long_book3, "page_idx": 14},
        ],
    )
    return root


def test_prepare_handoff_is_deterministic_and_writes_all_artifacts(tmp_path):
    source = _fixture_root(tmp_path)

    first = prepare_handoff(source, tmp_path / "handoff-a")
    second = prepare_handoff(source, tmp_path / "handoff-b")

    assert first["chunk_uids"] == second["chunk_uids"]
    assert first["chunk_uid_digest"] == second["chunk_uid_digest"]
    assert set(first["books"]) == {"book1", "book2", "book3"}
    for key in first["books"]:
        book_dir = tmp_path / "handoff-a" / key
        assert (book_dir / "normalized.md").exists()
        assert (book_dir / "normalized.blocks.jsonl").exists()
        assert (book_dir / "documents.jsonl").exists()
        assert (book_dir / "quality_report.json").exists()
        assert first["books"][key]["quality"]["ok"] is True
    assert (tmp_path / "handoff-a" / "manifest.json").exists()


def test_documents_jsonl_round_trip_preserves_text_and_metadata(tmp_path):
    source = _fixture_root(tmp_path)
    prepare_handoff(source, tmp_path / "handoff")

    docs = load_documents_jsonl(
        tmp_path / "handoff" / "book2" / "documents.jsonl"
    )

    assert docs
    assert docs[0].metadata["doc_name"].startswith("政府采购")
    assert docs[0].metadata["page_start"] == 7
    assert "1.什么是政府采购？" in docs[0].page_content
    assert "答：" in docs[0].page_content


def test_markdown_sidecar_round_trip_rebuilds_same_uids(tmp_path):
    source = _fixture_root(tmp_path)
    manifest = prepare_handoff(source, tmp_path / "handoff")
    book_dir = tmp_path / "handoff" / "book3"

    docs = load_markdown_with_sidecar(
        book_dir / "normalized.md",
        book_dir / "normalized.blocks.jsonl",
    )

    assert [doc.metadata["chunk_uid"] for doc in docs] == manifest["books"]["book3"]["chunk_uids"]


def test_failed_validation_leaves_no_partial_output(tmp_path):
    source = _fixture_root(tmp_path, short_book1=True)
    output = tmp_path / "handoff"

    with pytest.raises(ValueError, match="质量门禁"):
        prepare_handoff(source, output)

    assert not output.exists()


def test_prepare_refuses_to_overwrite_existing_output(tmp_path):
    source = _fixture_root(tmp_path)
    output = tmp_path / "handoff"
    output.mkdir()

    with pytest.raises(FileExistsError, match="已存在"):
        prepare_handoff(source, output)


def test_cli_parser_exposes_all_handoff_contracts():
    parser = _build_parser()

    prepare = parser.parse_args([
        "--prepare-handoff", "--data-dir", "DATA/raw_data",
        "--output-dir", "DATA/repaired_knowledge",
    ])
    ingest_jsonl = parser.parse_args(["--ingest-jsonl", "documents.jsonl"])
    ingest_markdown = parser.parse_args([
        "--ingest-markdown", "normalized.md",
        "--metadata-jsonl", "normalized.blocks.jsonl",
    ])

    assert prepare.prepare_handoff is True
    assert ingest_jsonl.ingest_jsonl == "documents.jsonl"
    assert ingest_markdown.metadata_jsonl == "normalized.blocks.jsonl"


def test_expected_chapter_count_includes_normalized_subsections_but_not_qa_questions():
    book1_blocks = [
        SourceBlock("heading", "第一章", 15, 1, "heading", 0),
        SourceBlock("heading", "第一节", 16, 2, "heading", 1),
        SourceBlock("heading", "一、专题", 17, 3, "heading", 2),
    ]
    book2_blocks = [
        SourceBlock("heading", "第一章 政府采购", 6, 1, "heading", 0),
        SourceBlock("heading", "第一节 概述", 7, 2, "heading", 1),
        SourceBlock("heading", "1.什么是采购？", 7, 3, "heading", 2),
    ]

    assert _expected_chapters(book1_blocks, BOOK_PROFILES["book1"]) == 3
    assert _expected_chapters(book2_blocks, BOOK_PROFILES["book2"]) == 2
