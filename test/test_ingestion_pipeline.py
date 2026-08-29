"""Offline tests for the ingestion pipeline and CSV source contracts."""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

from langchain_core.documents import Document

from public_kb.csv_loader import CsvLoader
from public_kb.ingestion.models import SourceResult
from public_kb.ingestion.pipeline import IngestionPipeline
from public_kb.ingestion.sinks.markdown_sink import MarkdownSink
from public_kb.ingestion.sinks.milvus_sink import MilvusSink
from public_kb.ingestion.sources.csv_source import CsvSource


class FakeSource:
    def __init__(self, documents: Sequence[Document]) -> None:
        self._documents = list(documents)

    def load(self) -> SourceResult:
        return SourceResult(documents=list(self._documents))


class CountingSink:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    def write(
        self,
        documents: Sequence[Document],
        *,
        records: Sequence[Dict[str, Any]],
    ) -> int:
        self.calls.append({"documents": list(documents), "records": list(records)})
        return len(documents)


def _document() -> Document:
    return Document(
        page_content="测试内容",
        metadata={
            "doc_name": "测试文档",
            "chapter": "第一章",
            "chunk_index": 0,
        },
    )


def test_pipeline_validates_documents_and_invokes_sink():
    sink = CountingSink()
    result = IngestionPipeline([sink]).run(FakeSource([_document()]))

    assert result.status == "completed"
    assert result.chunk_count == 1
    assert result.inserted_count == 1
    assert sink.calls[0]["documents"][0].metadata["doc_name"] == "测试文档"
    assert [stage.name for stage in result.stage_results] == [
        "source",
        "validate",
        "sink:CountingSink",
    ]


def test_pipeline_skips_empty_source_without_sink_write():
    sink = CountingSink()
    result = IngestionPipeline([sink]).run(FakeSource([]))

    assert result.status == "skipped"
    assert result.chunk_count == 0
    assert result.inserted_count == 0
    assert sink.calls == []


def test_csv_source_preserves_row_level_metadata(tmp_path):
    csv_path = tmp_path / "policy_data.csv"
    csv_path.write_text(
        "title,content,publish_date,source_url\n"
        "政策名称,\"# 政策名称\n第一条 这是用于验证行级元数据的政策内容。\","
        "2024-01-01,https://example.com/policy\n",
        encoding="utf-8",
    )
    source = CsvSource(
        csv_path,
        CsvLoader(max_chars=500, overlap_chars=20),
    )

    result = source.load()

    assert result.records
    assert result.documents
    for document in result.documents:
        assert document.metadata["title"] == "政策名称"
        assert document.metadata["publish_date"] == "2024-01-01"
        assert document.metadata["source_url"] == "https://example.com/policy"
        assert document.metadata["source_file"] == "policy_data.csv"
        assert "_line_num" in document.metadata


class FakeMilvusManager:
    def __init__(self) -> None:
        self.initialized: List[Sequence[Document]] = []
        self.added: List[Sequence[Document]] = []

    def initialize_collection(
        self,
        documents: Sequence[Document],
        *,
        recreate: bool = False,
    ) -> None:
        self.initialized.append(documents)

    def add_documents(self, documents: Sequence[Document]) -> None:
        self.added.append(documents)


def test_milvus_sink_supports_initialize_and_append_modes():
    manager = FakeMilvusManager()
    initialize_sink = MilvusSink(manager, mode="initialize")
    append_sink = MilvusSink(manager, mode="append")

    assert initialize_sink.write([_document()], records=[]) == 1
    assert append_sink.write([_document()], records=[]) == 1
    assert len(manager.initialized) == 1
    assert len(manager.added) == 1


def test_markdown_sink_writes_document_preview(tmp_path):
    document = _document()
    document.metadata["source_file"] = "sample.csv"
    sink = MarkdownSink(tmp_path, source_file="sample.csv")

    count = sink.write([document], records=[{"_line_num": 1, "title": "测试"}])

    assert count == 1
    assert (tmp_path / "sample_chunks.md").exists()
