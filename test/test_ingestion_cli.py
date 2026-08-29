"""Offline regression tests for consolidated CSV CLI entrypoints."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import pytest
from langchain_core.documents import Document

import public_kb.ingestion.cli as cli
import public_kb.process_csv as process_csv
from public_kb.ingestion.models import IngestionResult, SourceResult, StageResult


@dataclass
class FakeSettings:
    chunk_max_chars: int = 100
    chunk_overlap_chars: int = 10


@dataclass
class FakeIngestionResult:
    status: str = "completed"
    chunk_count: int = 2
    inserted_count: int = 2


class FakeCsvSource:
    instances: List[Any] = []
    responses: Dict[str, Any] = {}

    def __init__(self, csv_path: str, loader: Any = None) -> None:
        self.csv_path = csv_path
        self.loader = loader
        FakeCsvSource.instances.append(self)

    def load(self) -> SourceResult:
        response = self.responses[self.csv_path]
        if isinstance(response, Exception):
            raise response
        return response


class FakeMarkdownSink:
    instances: List[Any] = []

    def __init__(self, output_dir: str, *, source_file: str | None = None) -> None:
        self.output_dir = output_dir
        self.source_file = source_file
        FakeMarkdownSink.instances.append(self)

    def write(self, documents: List[Document], *, records: List[Dict[str, Any]]) -> int:
        return len(documents)


class FakeMilvusSink:
    instances: List[Any] = []

    def __init__(self, manager: Any, *, mode: str = "append") -> None:
        self.manager = manager
        self.mode = mode
        FakeMilvusSink.instances.append(self)


class FakeIngestionPipeline:
    instances: List[Any] = []

    def __init__(self, sinks: List[Any]) -> None:
        self.sinks = sinks
        FakeIngestionPipeline.instances.append(self)

    def run(self, source: Any) -> IngestionResult:
        self.source = source
        return IngestionResult("fake", 2, 2, (StageResult("fake", 2, 2)), "completed")


def _document(index: int = 0) -> Document:
    return Document(
        page_content=f"第{index}条 测试内容",
        metadata={"doc_name": "测试文档", "chapter": "第一章", "chunk_index": index},
    )


def _ingestion_result() -> IngestionResult:
    return IngestionResult("fake.csv", 2, 2, (StageResult("fake", 2, 2)), "completed")


def test_cli_dispatches_single_csv_ingestion(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[Dict[str, Any]] = []

    def fake_run(
        csv_path: str,
        settings: Any,
        *,
        markdown_output_dir: str | None,
        mode: str,
    ) -> IngestionResult:
        calls.append(
            {
                "csv_path": csv_path,
                "settings": settings,
                "markdown_output_dir": markdown_output_dir,
                "mode": mode,
            }
        )
        return _ingestion_result()

    monkeypatch.setattr(cli, "run_csv_ingestion", fake_run)
    exit_code = cli.main(
        ["--csv-path", "sample.csv", "--markdown-output-dir", "preview", "--mode", "append"]
    )

    assert exit_code == 0
    assert calls[0]["csv_path"] == "sample.csv"
    assert calls[0]["markdown_output_dir"] == "preview"
    assert calls[0]["mode"] == "append"


def test_cli_dispatches_batch_csv_ingestion(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[Dict[str, Any]] = []
    result = cli.BatchCSVIngestionResult((), ())

    def fake_run(
        csv_dir: str,
        settings: Any,
        *,
        group: str | None,
        no_import: bool,
        markdown_output_dir: str | None,
        mode: str,
    ) -> cli.BatchCSVIngestionResult:
        calls.append(
            {
                "csv_dir": csv_dir,
                "settings": settings,
                "group": group,
                "no_import": no_import,
                "markdown_output_dir": markdown_output_dir,
                "mode": mode,
            }
        )
        return result

    monkeypatch.setattr(cli, "run_batch_csv_ingestion", fake_run)
    exit_code = cli.main(["--csv-dir", "raw_policy", "--group", "A", "--no-import"])

    assert exit_code == 0
    assert calls[0]["csv_dir"] == "raw_policy"
    assert calls[0]["group"] == "A"
    assert calls[0]["no_import"] is True


def test_cli_rejects_both_csv_path_and_csv_dir() -> None:
    with pytest.raises(SystemExit):
        cli.main(["--csv-path", "sample.csv", "--csv-dir", "raw_policy"])


def test_cli_validate_only_does_not_require_csv_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    preview_dir = tmp_path / "preview"
    preview_dir.mkdir()
    (preview_dir / "sample_chunks.md").write_text("```text\nchunk\n```\n", encoding="utf-8")

    def fail(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("validate-only must not ingest CSV")

    monkeypatch.setattr(cli, "run_csv_ingestion", fail)
    monkeypatch.setattr(cli, "run_batch_csv_ingestion", fail)

    exit_code = cli.main(["--validate-only", "--markdown-output-dir", str(preview_dir)])

    assert exit_code == 0


def test_scan_csv_files_classifies_policy_qa_and_unknown(tmp_path: Path) -> None:
    (tmp_path / "policy_data.csv").write_text("title,content\n", encoding="utf-8-sig")
    (tmp_path / "qa_data.csv").write_text("question,answer\n", encoding="utf-8-sig")
    (tmp_path / "unknown_data.csv").write_text("other,value\n", encoding="utf-8-sig")

    policy_files, qa_files, unknown_files = cli.scan_csv_files(tmp_path)

    assert [Path(path).name for path in policy_files] == ["policy_data.csv"]
    assert [Path(path).name for path in qa_files] == ["qa_data.csv"]
    assert [Path(path).name for path in unknown_files] == ["unknown_data.csv"]


def test_process_csv_group_previews_and_ingests_with_manager(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    FakeCsvSource.instances = []
    FakeMarkdownSink.instances = []
    FakeMilvusSink.instances = []
    FakeIngestionPipeline.instances = []
    FakeCsvSource.responses = {
        "a.csv": SourceResult([_document(0), _document(1)], [{"title": "A"}, {"title": "B"}])
    }
    monkeypatch.setattr(cli, "CsvSource", FakeCsvSource)
    monkeypatch.setattr(cli, "MarkdownSink", FakeMarkdownSink)
    monkeypatch.setattr(cli, "MilvusSink", FakeMilvusSink)
    monkeypatch.setattr(cli, "IngestionPipeline", FakeIngestionPipeline)

    stats = cli.process_csv_group(
        ["a.csv"],
        "A",
        loader=FakeSettings(),
        manager={"manager": True},
        output_dir=str(tmp_path),
    )

    assert stats["processed"] == 1
    assert stats["total_rows"] == 2
    assert stats["total_chunks"] == 2
    assert stats["imported"] is True
    assert FakeMarkdownSink.instances[0].source_file == "a.csv"
    assert FakeMilvusSink.instances[0].manager == {"manager": True}
    assert FakeIngestionPipeline.instances[0].source.name == "csv_group_A"


def test_process_csv_group_counts_failed_files_and_continues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    FakeCsvSource.instances = []
    FakeMarkdownSink.instances = []
    FakeCsvSource.responses = {
        "good.csv": SourceResult([_document()], [{"title": "A"}]),
        "bad.csv": RuntimeError("broken csv"),
    }
    monkeypatch.setattr(cli, "CsvSource", FakeCsvSource)
    monkeypatch.setattr(cli, "MarkdownSink", FakeMarkdownSink)

    stats = cli.process_csv_group(
        ["bad.csv", "good.csv"],
        "A",
        loader=FakeSettings(),
        manager={"manager": True},
        output_dir=str(tmp_path),
        skip_import=True,
    )

    assert stats["processed"] == 1
    assert stats["failed"] == 1
    assert stats["failed_files"] == ["bad.csv"]
    assert stats["imported"] is False


def test_batch_csv_ingestion_does_not_create_manager_for_no_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: List[Any] = []

    def fake_scan(csv_dir: Any, *, loader: Any = None):
        return [["a.csv"], ["c.csv"], ["unknown.csv"]]

    def fake_process(
        files: List[str],
        group_label: str,
        loader: Any,
        manager: Any,
        output_dir: str,
        *,
        skip_import: bool,
        mode: str,
    ) -> Dict[str, Any]:
        calls.append((group_label, manager, skip_import))
        return cli._new_group_stats(group_label, len(files))

    monkeypatch.setattr(cli, "scan_csv_files", fake_scan)
    monkeypatch.setattr(cli, "process_csv_group", fake_process)

    result = cli.run_batch_csv_ingestion(
        tmp_path,
        FakeSettings(),
        no_import=True,
        markdown_output_dir=str(tmp_path / "preview"),
    )

    assert [call[0] for call in calls] == ["A", "C"]
    assert all(call[1] is None and call[2] is True for call in calls)
    assert result.total_rows == 0
    assert result.unknown_files == ("unknown.csv",)


def test_batch_csv_ingestion_uses_initialize_only_for_first_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: List[str] = []

    def fake_scan(csv_dir: Any, *, loader: Any = None):
        return [["a.csv"], ["c.csv"], []]

    def fake_process(
        files: List[str],
        group_label: str,
        loader: Any,
        manager: Any,
        output_dir: str,
        *,
        skip_import: bool,
        mode: str,
    ) -> Dict[str, Any]:
        calls.append(mode)
        return {**cli._new_group_stats(group_label, len(files)), "imported": True}

    monkeypatch.setattr(cli, "scan_csv_files", fake_scan)
    monkeypatch.setattr(cli, "process_csv_group", fake_process)

    result = cli.run_batch_csv_ingestion(
        tmp_path,
        FakeSettings(),
        manager={"manager": True},
        markdown_output_dir=str(tmp_path / "preview"),
        mode="initialize",
    )

    assert calls == ["initialize", "append"]
    assert all(stats["imported"] for stats in result.group_stats)


def test_legacy_process_csv_entry_forwards_to_consolidated_cli() -> None:
    assert process_csv.main is cli.main
    assert process_csv.scan_csv_files is cli.scan_csv_files
    assert process_csv.run_batch_csv_ingestion is cli.run_batch_csv_ingestion
    assert process_csv.validate_markdown_output is cli.validate_markdown_output
    assert process_csv.DEFAULT_OUTPUT_DIR == cli.DEFAULT_MARKDOWN_OUTPUT_DIR
