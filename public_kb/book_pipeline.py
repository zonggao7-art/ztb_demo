"""Deterministic local rebuild and handoff serialization for the three books."""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import logging
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Iterable, Sequence

from langchain_core.documents import Document

from .chunker import SemanticChunker
from .handoff_validate import (
    ValidationReport,
    validate_coverage,
    validate_determinism,
    validate_documents,
)
from .normalize import (
    BOOK_PROFILES,
    BookProfile,
    SourceBlock,
    detect_book_profile,
    normalize_content_list,
    render_normalized_markdown,
)


HANDOFF_SCHEMA_VERSION = 1

logger = logging.getLogger(__name__)


def _json_dump(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(_json_dump(row) + "\n")


def _source_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _discover_sources(data_dir: Path) -> dict[str, Path]:
    if not data_dir.is_dir():
        raise FileNotFoundError(f"DATA 目录不存在: {data_dir}")
    found: dict[str, Path] = {}
    for path in sorted(data_dir.rglob("*_content_list.json")):
        try:
            profile = detect_book_profile(str(path))
        except ValueError:
            continue
        if profile.key in found:
            raise ValueError(
                f"书籍 {profile.key} 找到多个 content_list: {found[profile.key]} / {path}"
            )
        found[profile.key] = path
    missing = [key for key in BOOK_PROFILES if key not in found]
    if missing:
        raise FileNotFoundError(f"缺少三本书 content_list: {', '.join(missing)}")
    return found


def _expected_chapters(blocks: Sequence[SourceBlock], profile: BookProfile) -> int:
    structural = [
        block
        for block in blocks
        if block.kind == "heading"
        and (not profile.qa_mode or int(block.level or 9) <= 2)
    ]
    return max(1, len(structural))


def _build_documents(
    blocks: Sequence[SourceBlock], profile: BookProfile
) -> list[Document]:
    return SemanticChunker(
        max_chars=2000,
        overlap_chars=100,
        min_chars=200,
    ).chunk_blocks(blocks, profile.doc_name, qa_mode=profile.qa_mode)


def _build_book(
    source_path: Path,
    profile: BookProfile,
) -> tuple[list[SourceBlock], list[Document], ValidationReport]:
    items = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError(f"content_list 顶层必须是数组: {source_path}")
    blocks = normalize_content_list(items, profile)
    first = _build_documents(blocks, profile)
    second = _build_documents(blocks, profile)
    deterministic_issue = validate_determinism(first, second)
    report = validate_documents(
        first,
        expected_chapters=_expected_chapters(blocks, profile),
        qa_mode=profile.qa_mode,
    )
    if deterministic_issue is not None:
        report = ValidationReport(
            ok=False,
            metrics=report.metrics,
            issues=[*report.issues, deterministic_issue],
        )
    coverage_metrics_value, coverage_issue = validate_coverage(blocks, first)
    report = ValidationReport(
        ok=report.ok and coverage_issue is None,
        metrics={
            **report.metrics,
            "text_coverage": coverage_metrics_value["text_coverage"],
            "lost_block_count": coverage_metrics_value["lost_block_count"],
            "lost_chars": coverage_metrics_value["lost_chars"],
        },
        issues=[
            *report.issues,
            *([coverage_issue] if coverage_issue is not None else []),
        ],
    )
    if not report.ok:
        codes = ", ".join(issue.code for issue in report.issues)
        raise ValueError(f"{profile.doc_name} 未通过质量门禁: {codes}")
    return blocks, first, report


def _document_row(doc: Document) -> dict[str, Any]:
    return {"text": doc.page_content, "metadata": doc.metadata}


def prepare_handoff(
    data_dir: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Rebuild all three books and atomically write a validated handoff bundle."""

    source_root = Path(data_dir).resolve()
    target = Path(output_dir).resolve()
    if target.exists():
        raise FileExistsError(f"输出目录已存在，拒绝覆盖: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    sources = _discover_sources(source_root)

    built: dict[str, tuple[Path, BookProfile, list[SourceBlock], list[Document], ValidationReport]] = {}
    all_uids: list[str] = []
    for key in BOOK_PROFILES:
        profile = BOOK_PROFILES[key]
        source = sources[key]
        blocks, documents, report = _build_book(source, profile)
        built[key] = (source, profile, blocks, documents, report)
        all_uids.extend(str(doc.metadata["chunk_uid"]) for doc in documents)

    duplicates = [uid for uid, count in Counter(all_uids).items() if count > 1]
    if duplicates:
        raise ValueError(f"三书联合质量门禁失败: chunk_uid 冲突 {len(duplicates)} 个")
    uid_digest = hashlib.sha256("\n".join(all_uids).encode("utf-8")).hexdigest()
    manifest: dict[str, Any] = {
        "schema_version": HANDOFF_SCHEMA_VERSION,
        "parameters": {"max_chars": 2000, "overlap_chars": 100, "min_chars": 200},
        "source_root": str(source_root),
        "chunk_count": len(all_uids),
        "chunk_uid_digest": uid_digest,
        "chunk_uids": all_uids,
        "books": {},
        "milvus_modified": False,
    }

    with TemporaryDirectory(prefix=f".{target.name}.", dir=target.parent) as temp_root:
        staging = Path(temp_root) / target.name
        staging.mkdir()
        for key, (source, profile, blocks, documents, report) in built.items():
            book_dir = staging / key
            book_dir.mkdir()
            markdown = render_normalized_markdown(blocks)
            (book_dir / "normalized.md").write_text(markdown, encoding="utf-8", newline="\n")
            _write_jsonl(
                book_dir / "normalized.blocks.jsonl",
                (block.to_dict() for block in blocks),
            )
            _write_jsonl(
                book_dir / "documents.jsonl",
                (_document_row(doc) for doc in documents),
            )
            _write_json(book_dir / "quality_report.json", report.to_dict())
            uids = [str(doc.metadata["chunk_uid"]) for doc in documents]
            manifest["books"][key] = {
                "doc_name": profile.doc_name,
                "source_path": str(source),
                "source_sha256": _source_hash(source),
                "normalized_block_count": len(blocks),
                "chunk_count": len(documents),
                "chunk_uids": uids,
                "chunk_uid_digest": hashlib.sha256(
                    "\n".join(uids).encode("utf-8")
                ).hexdigest(),
                "quality": report.to_dict(),
            }
        _write_json(staging / "manifest.json", manifest)
        staging.replace(target)
    return manifest


def load_documents_jsonl(path: str | Path) -> list[Document]:
    """Load serialized documents and reject malformed rows."""

    source = Path(path).resolve()
    documents: list[Document] = []
    with source.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict) or not isinstance(row.get("metadata"), dict):
                raise ValueError(f"documents.jsonl 第 {line_number} 行格式错误")
            documents.append(Document(
                page_content=str(row.get("text", "")),
                metadata=row["metadata"],
            ))
    return documents


def _load_blocks_jsonl(path: Path) -> list[SourceBlock]:
    blocks: list[SourceBlock] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            try:
                blocks.append(SourceBlock(**row))
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"normalized.blocks.jsonl 第 {line_number} 行格式错误: {exc}"
                ) from exc
    return blocks


def load_markdown_with_sidecar(
    markdown_path: str | Path,
    metadata_jsonl_path: str | Path,
) -> list[Document]:
    """Verify normalized Markdown against its page-aware sidecar and re-chunk."""

    markdown = Path(markdown_path).resolve()
    sidecar = Path(metadata_jsonl_path).resolve()
    blocks = _load_blocks_jsonl(sidecar)
    expected = render_normalized_markdown(blocks)
    actual = markdown.read_text(encoding="utf-8")
    if actual.replace("\r\n", "\n") != expected:
        raise ValueError("normalized.md 与 normalized.blocks.jsonl 内容不一致")
    profile = BOOK_PROFILES.get(markdown.parent.name)
    if profile is None:
        profile = detect_book_profile(str(markdown))
    documents = _build_documents(blocks, profile)
    report = validate_documents(
        documents,
        expected_chapters=_expected_chapters(blocks, profile),
        qa_mode=profile.qa_mode,
    )
    if not report.ok:
        codes = ", ".join(issue.code for issue in report.issues)
        raise ValueError(f"Markdown 入库数据未通过质量门禁: {codes}")
    return documents


def load_handoff_bundle(handoff_dir: str | Path) -> list[Document]:
    """读取交接产物并执行入库前三重校验，返回合并后的待入库文档。

    校验（任一失败即抛错，入库硬门槛——防"产物被并发写坏"复发）：
      1. digest 复核：逐书与联合的 chunk_uid 有序序列 sha256 与 manifest 一致；
      2. 门禁复跑：逐书 validate_documents（P3 质量指标）；
      3. 联合唯一：跨书 chunk_uid / structural_id 无冲突、无完全重复文本。
    """

    root = Path(handoff_dir).resolve()
    manifest_path = root / "manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"交接产物缺少 manifest.json: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    books = manifest.get("books") or {}
    if not books:
        raise ValueError(f"manifest 缺少 books 记录: {manifest_path}")

    all_docs: list[Document] = []
    for key in sorted(books):
        book_meta = books[key]
        book_dir = root / key
        docs = load_documents_jsonl(book_dir / "documents.jsonl")
        if not docs:
            raise ValueError(f"{key} documents.jsonl 为空")

        uids = [str(doc.metadata["chunk_uid"]) for doc in docs]
        expected_uids = [str(uid) for uid in (book_meta.get("chunk_uids") or [])]
        if uids != expected_uids:
            raise ValueError(
                f"{key} chunk_uid 有序序列与 manifest 不一致"
                f"（文件 {len(uids)} 条 / manifest {len(expected_uids)} 条），"
                "产物可能被篡改或写坏，拒绝入库。"
            )
        digest = hashlib.sha256("\n".join(uids).encode("utf-8")).hexdigest()
        if digest != book_meta.get("chunk_uid_digest"):
            raise ValueError(f"{key} chunk_uid_digest 与 manifest 不一致，拒绝入库")

        profile = BOOK_PROFILES.get(key)
        report = validate_documents(
            docs,
            qa_mode=bool(profile.qa_mode) if profile else False,
        )
        if not report.ok:
            codes = ", ".join(issue.code for issue in report.issues)
            raise ValueError(f"{key} 入库前门禁未通过: {codes}")
        all_docs.extend(docs)

    all_uids = [str(doc.metadata["chunk_uid"]) for doc in all_docs]
    duplicates = [uid for uid, count in Counter(all_uids).items() if count > 1]
    if duplicates:
        raise ValueError(f"跨书 chunk_uid 冲突 {len(duplicates)} 个，拒绝入库")
    structural_ids = [
        str(doc.metadata.get("structural_id", "")) for doc in all_docs
    ]
    sid_duplicates = [s for s, count in Counter(structural_ids).items() if count > 1]
    if sid_duplicates:
        raise ValueError(f"跨书 structural_id 冲突 {len(sid_duplicates)} 个，拒绝入库")
    text_counts = Counter(doc.page_content for doc in all_docs)
    text_duplicates = [t for t, count in text_counts.items() if count > 1]
    if text_duplicates:
        raise ValueError(
            f"跨书完全重复文本 {len(text_duplicates)} 条，拒绝入库"
        )

    joined_digest = hashlib.sha256(
        "\n".join(all_uids).encode("utf-8")
    ).hexdigest()
    if joined_digest != manifest.get("chunk_uid_digest"):
        raise ValueError("三书联合 chunk_uid_digest 与 manifest 不一致，拒绝入库")

    logger.info(
        "交接产物校验通过: %d 个文档块（%s）",
        len(all_docs),
        ", ".join(f"{key}={len(books[key].get('chunk_uids') or [])}" for key in sorted(books)),
    )
    return all_docs
