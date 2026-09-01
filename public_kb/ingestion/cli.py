# 功能：CSV 单文件和批量向量化入库命令行入口。
"""Command-line entries for single-file and batch CSV ingestion."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..config import Settings
from .sources.csv_loader import CsvLoader
from ..services.embeddings import create_embeddings
from ..services.milvus_store import MilvusStoreManager
from .models import IngestionResult
from .sources.document_source import DocumentSource
from .pipeline import IngestionPipeline
from .sinks.markdown_sink import MarkdownSink
from .sinks.milvus_sink import MilvusSink, MilvusSinkMode
from .sources.csv_source import CsvSource


logger = logging.getLogger(__name__)

DEFAULT_MARKDOWN_OUTPUT_DIR = Path(__file__).resolve().parent.parent.parent / "DATA" / "raw_data"
DEFAULT_OUTPUT_DIR = DEFAULT_MARKDOWN_OUTPUT_DIR


@dataclass(frozen=True)
class BatchCSVIngestionResult:
    """Aggregated result for one batch CSV invocation."""

    group_stats: Tuple[Dict[str, object], ...]
    unknown_files: Tuple[str, ...]

    @property
    def total_rows(self) -> int:
        return sum(int(stat.get("total_rows", 0)) for stat in self.group_stats)

    @property
    def total_chunks(self) -> int:
        return sum(int(stat.get("total_chunks", 0)) for stat in self.group_stats)


def run_csv_ingestion(
    csv_path: str,
    settings: Settings,
    *,
    markdown_output_dir: str | None = None,
    manager: MilvusStoreManager | None = None,
    embeddings: Any = None,
    mode: MilvusSinkMode = "append",
) -> IngestionResult:
    """Load one CSV directly and write it to Milvus without Markdown round-trip."""
    resolved_embeddings = embeddings
    resolved_manager = manager
    if resolved_manager is None:
        resolved_embeddings = resolved_embeddings or create_embeddings(settings)
        resolved_manager = MilvusStoreManager(
            settings,
            resolved_embeddings,
        )
    loader = CsvLoader(
        max_chars=settings.chunk_max_chars,
        overlap_chars=settings.chunk_overlap_chars,
    )
    sinks = []
    if markdown_output_dir:
        sinks.append(MarkdownSink(markdown_output_dir))
    sinks.append(MilvusSink(resolved_manager, mode=mode))

    return IngestionPipeline(sinks).run(CsvSource(csv_path, loader=loader))


def scan_csv_files(
    csv_dir: str | Path,
    *,
    loader: CsvLoader | None = None,
) -> Tuple[List[str], List[str], List[str]]:
    """Scan ``*_data.csv`` files and classify them as policy or QA groups."""
    csv_path = Path(csv_dir)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV 目录不存在: {csv_path}")

    csv_files = sorted(csv_path.glob("*_data.csv"))
    if not csv_files:
        raise FileNotFoundError(f"目录中未找到 *_data.csv 文件: {csv_path}")

    csv_loader = loader or CsvLoader()
    policy_files: List[str] = []
    qa_files: List[str] = []
    unknown_files: List[str] = []
    for csv_file in csv_files:
        category = csv_loader.classify_file(str(csv_file))
        if category == "A":
            policy_files.append(str(csv_file))
        elif category == "C":
            qa_files.append(str(csv_file))
        else:
            unknown_files.append(str(csv_file))
    return policy_files, qa_files, unknown_files


def _new_group_stats(group_label: str, file_count: int) -> Dict[str, object]:
    return {
        "group": group_label,
        "total_files": file_count,
        "processed": 0,
        "failed": 0,
        "total_rows": 0,
        "total_chunks": 0,
        "failed_files": [],
        "imported": False,
    }


def process_csv_group(
    files: List[str],
    group_label: str,
    loader: CsvLoader,
    manager: Any,
    output_dir: str,
    *,
    skip_import: bool = False,
    mode: MilvusSinkMode = "append",
) -> Dict[str, object]:
    """Preview and ingest one classified group of CSV files."""
    stats = _new_group_stats(group_label, len(files))
    failed_files = stats["failed_files"]
    assert isinstance(failed_files, list)
    if not files:
        return stats

    if not skip_import and manager is None:
        raise ValueError("manager is required unless skip_import is true")

    all_documents = []
    for file_path in files:
        file_name = Path(file_path).name
        try:
            source_result = CsvSource(file_path, loader=loader).load()
            documents = source_result.documents
            records = source_result.records
            if not documents:
                stats["processed"] = int(stats["processed"]) + 1
                continue

            all_documents.extend(documents)
            stats["processed"] = int(stats["processed"]) + 1
            stats["total_rows"] = int(stats["total_rows"]) + len(records)
            stats["total_chunks"] = int(stats["total_chunks"]) + len(documents)
            MarkdownSink(output_dir, source_file=file_name).write(
                documents,
                records=records,
            )
        except Exception as error:
            stats["failed"] = int(stats["failed"]) + 1
            failed_files.append(file_name)
            logger.error("处理 %s 失败: %s", file_name, error)

    if skip_import:
        logger.info("已跳过入库（--no-import）")
        return stats
    if not all_documents:
        return stats

    try:
        ingestion_result = IngestionPipeline(
            [MilvusSink(manager, mode=mode)]
        ).run(
            DocumentSource(
                all_documents,
                source_name=f"csv_group_{group_label}",
            )
        )
        stats["imported"] = True
        logger.info(
            "组 %s 入库完成: chunks=%d, inserted=%d",
            group_label,
            ingestion_result.chunk_count,
            ingestion_result.inserted_count,
        )
    except Exception as error:
        logger.error("组 %s 入库失败: %s", group_label, error)
    return stats


def validate_markdown_output(output_dir: str | Path) -> Dict[str, int]:
    """Count preview chunks in each generated Markdown file."""
    result: Dict[str, int] = {}
    markdown_files = sorted(Path(output_dir).glob("*_chunks.md"))
    for markdown_file in markdown_files:
        try:
            content = markdown_file.read_text(encoding="utf-8")
            result[markdown_file.name] = content.count("```") // 2
        except Exception as error:
            logger.warning("校验 %s 失败: %s", markdown_file.name, error)
            result[markdown_file.name] = -1
    return result


def run_batch_csv_ingestion(
    csv_dir: str | Path,
    settings: Settings,
    *,
    group: str | None = None,
    no_import: bool = False,
    markdown_output_dir: str | None = None,
    manager: Any = None,
    embeddings: Any = None,
    mode: MilvusSinkMode = "append",
) -> BatchCSVIngestionResult:
    """Scan, preview, and ingest classified CSV files from one directory."""
    if group not in {None, "A", "C"}:
        raise ValueError("group 必须是 A、C 或 None")

    policy_files, qa_files, unknown_files = scan_csv_files(csv_dir)
    selected_groups = []
    if group is None or group == "A":
        selected_groups.append(("A", policy_files))
    if group is None or group == "C":
        selected_groups.append(("C", qa_files))

    resolved_output_dir = str(
        Path(markdown_output_dir or DEFAULT_MARKDOWN_OUTPUT_DIR)
    )
    Path(resolved_output_dir).mkdir(parents=True, exist_ok=True)

    if no_import:
        resolved_manager = None
    else:
        resolved_embeddings = embeddings
        resolved_manager = manager
        if resolved_manager is None:
            resolved_embeddings = resolved_embeddings or create_embeddings(settings)
            resolved_manager = MilvusStoreManager(
                settings,
                resolved_embeddings,
            )

    pending_mode = mode
    group_stats: List[Dict[str, object]] = []
    for group_label, files in selected_groups:
        stats = process_csv_group(
            files,
            group_label,
            CsvLoader(
                max_chars=settings.chunk_max_chars,
                overlap_chars=settings.chunk_overlap_chars,
            ),
            resolved_manager,
            resolved_output_dir,
            skip_import=no_import,
            mode=pending_mode,
        )
        group_stats.append(stats)
        if pending_mode == "initialize" and bool(stats.get("imported")):
            pending_mode = "append"
    return BatchCSVIngestionResult(tuple(group_stats), tuple(unknown_files))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest one or more CSV files into Milvus")
    path_group = parser.add_mutually_exclusive_group(required=False)
    path_group.add_argument("--csv-path", help="单个 CSV 文件路径")
    path_group.add_argument("--csv-dir", help="包含 *_data.csv 的目录")
    parser.add_argument(
        "--markdown-output-dir",
        "--output-dir",
        dest="markdown_output_dir",
        help="Markdown 预览输出目录；批量模式默认写入 DATA/raw_data",
    )
    parser.add_argument(
        "--mode",
        choices=("initialize", "append"),
        default="append",
    )
    parser.add_argument("--group", choices=("A", "C"), help="仅处理指定批量分组")
    parser.add_argument("--no-import", action="store_true", help="只生成预览，不写入 Milvus")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="只校验已有 Markdown 预览，不处理 CSV",
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    markdown_output_dir = args.markdown_output_dir

    if not args.validate_only and not args.csv_path and not args.csv_dir:
        parser.error("必须提供 --csv-path 或 --csv-dir")

    if args.validate_only:
        output_dir = markdown_output_dir or DEFAULT_MARKDOWN_OUTPUT_DIR
        validation = validate_markdown_output(output_dir)
        logger.info("校验完成: %d 个文件, 共 %d chunks", len(validation), sum(validation.values()))
        return 0

    settings = Settings()
    if args.csv_path:
        result = run_csv_ingestion(
            args.csv_path,
            settings,
            markdown_output_dir=markdown_output_dir,
            mode=args.mode,
        )
        logger.info(
            "CSV ingestion %s: chunks=%d, inserted=%d",
            result.status,
            result.chunk_count,
            result.inserted_count,
        )
    else:
        result = run_batch_csv_ingestion(
            args.csv_dir,
            settings,
            group=args.group,
            no_import=args.no_import,
            markdown_output_dir=markdown_output_dir,
            mode=args.mode,
        )
        logger.info("未知文件: %d 个", len(result.unknown_files))
        for stats in result.group_stats:
            logger.info(
                "组 %s: %d/%d 成功, %d 行, %d chunks, 入库=%s",
                stats.get("group"),
                stats.get("processed"),
                stats.get("total_files"),
                stats.get("total_rows"),
                stats.get("total_chunks"),
                stats.get("imported"),
            )
        logger.info("合计: %d 行, %d chunks", result.total_rows, result.total_chunks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
