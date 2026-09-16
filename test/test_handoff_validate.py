from __future__ import annotations

from langchain_core.documents import Document

from public_kb.chunk_ids import compute_chunk_uid
from public_kb.handoff_validate import (
    coverage_metrics,
    validate_coverage,
    validate_determinism,
    validate_documents,
)
from public_kb.normalize import SourceBlock


def _doc(index: int, *, chapter: str = "第一章 总则", text: str | None = None) -> Document:
    body = text if text is not None else ("这是用于验证知识库切分质量的完整正文。" * 55)
    page_content = f"【{chapter}】\n{body}"
    metadata = {
        "doc_name": "测试书",
        "chapter": chapter,
        "chunk_index": index,
        "page_start": 10 + index,
        "page_end": 10 + index,
        "page_ordinal": 0,
        "structural_id": f"测试书|p{10 + index}|0",
    }
    metadata["chunk_uid"] = compute_chunk_uid(page_content, metadata)
    return Document(page_content=page_content, metadata=metadata)


def test_validator_accepts_documents_meeting_every_gate():
    report = validate_documents([_doc(0), _doc(1)], expected_chapters=2)

    assert report.ok is True
    assert report.issues == []
    assert report.metrics["page_coverage"] == 1.0
    assert report.metrics["breadcrumb_coverage"] == 1.0
    assert report.metrics["chunk_uid_collisions"] == 0


def test_validator_rejects_comment_short_ratio_missing_pages_and_duplicate_uid():
    first = _doc(0, text="<!-- page: 12 -->")
    second = _doc(1, text="短文本")
    second.metadata.pop("page_start")
    second.metadata.pop("page_end")
    second.metadata["chunk_uid"] = first.metadata["chunk_uid"]

    report = validate_documents([first, second])

    assert report.ok is False
    assert {issue.code for issue in report.issues} >= {
        "html_comment",
        "short_chunk_ratio",
        "page_coverage",
        "chunk_uid_duplicate",
    }


def test_validator_rejects_noise_bad_uid_missing_prefix_and_duplicate_structural_id():
    first = _doc(0, text="---")
    second = _doc(1)
    second.page_content = "没有面包屑前缀" + second.page_content
    second.metadata["structural_id"] = first.metadata["structural_id"]
    second.metadata["chunk_uid"] = "not-a-valid-uid"

    report = validate_documents([first, second])

    assert {issue.code for issue in report.issues} >= {
        "pure_noise",
        "breadcrumb_coverage",
        "structural_id_duplicate",
        "chunk_uid_format",
    }


def test_validator_rejects_excess_chapter_cardinality_and_front_matter():
    docs = [
        _doc(i, chapter=f"第{i}章", text=("有效正文。" * 180))
        for i in range(5)
    ]
    docs[0].page_content += "\n本电子版仅限用于个人学习与研究，不得用于商业用途"

    report = validate_documents(docs, expected_chapters=2)

    assert {issue.code for issue in report.issues} >= {
        "chapter_cardinality",
        "front_matter",
    }


def test_validator_rejects_median_below_800_even_when_chunks_exceed_100():
    docs = [_doc(i, text="中等长度正文。" * 30) for i in range(3)]

    report = validate_documents(docs)

    assert any(issue.code == "median_length" for issue in report.issues)


def test_determinism_compares_ordered_uid_sets():
    first = [_doc(0), _doc(1)]
    same = [_doc(0), _doc(1)]
    changed = [_doc(1), _doc(0)]

    assert validate_determinism(first, same) is None
    issue = validate_determinism(first, changed)
    assert issue is not None
    assert issue.code == "chunk_uid_nondeterministic"


def test_report_is_json_serializable_shape():
    report = validate_documents([_doc(0)])

    payload = report.to_dict()
    assert payload["ok"] is True
    assert isinstance(payload["metrics"], dict)
    assert payload["issues"] == []


def _body_block(text: str, ordinal: int) -> SourceBlock:
    return SourceBlock("text", text, 10 + ordinal, None, "text", ordinal)


def test_coverage_gate_passes_when_every_block_survives_chunking():
    body = "这段正文完整地进入了切块结果，用于验证覆盖率门禁不会误伤正常数据。" * 40
    blocks = [_body_block(body, 0)]
    docs = [_doc(0, text=body)]

    metrics = coverage_metrics(blocks, docs)
    issue = validate_coverage(blocks, docs)[1]

    assert metrics["text_coverage"] == 1.0
    assert metrics["lost_block_count"] == 0
    assert issue is None


def test_coverage_gate_flags_blocks_dropped_by_chunking():
    kept = "保留下来的正文内容，反复出现以便达到长度要求。" * 40
    dropped = "这一整段正文因为被误判为标题而在切分阶段消失，必须被覆盖率门禁捕获。" * 40
    blocks = [_body_block(kept, 0), _body_block(dropped, 1)]
    docs = [_doc(0, text=kept)]

    metrics, issue = validate_coverage(blocks, docs)

    assert issue is not None
    assert issue.code == "text_coverage"
    assert metrics["lost_block_count"] == 1
    assert metrics["lost_chars"] > 0
    sample = metrics["coverage_samples"][0]
    assert sample["kind"] == "text"
    assert sample["page_idx"] == 11


def test_coverage_gate_tolerates_windows_broken_by_long_block_splitting():
    sentence = "法条正文以句号收尾。" * 300
    blocks = [_body_block(sentence, 0)]
    docs = [_doc(0, text=sentence[:1500]), _doc(1, text=sentence[1500:])]

    metrics = coverage_metrics(blocks, docs)

    assert metrics["text_coverage"] > 0.995
    assert metrics["lost_block_count"] == 0


def test_coverage_gate_ignores_whitespace_only_differences():
    spaced = "第一段正文。\n第二段正文。"
    blocks = [_body_block(spaced, 0)]
    docs = [_doc(0, text="第一段正文。第二段正文。")]

    metrics, issue = validate_coverage(blocks, docs)

    assert metrics["text_coverage"] == 1.0
    assert issue is None
