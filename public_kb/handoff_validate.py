"""Fail-closed quality gates for repaired knowledge-base handoff data."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import re
import statistics
from typing import Any, Sequence

from langchain_core.documents import Document

from .normalize import SourceBlock


_UID_RE = re.compile(r"^ck-[0-9a-f]{32}$")
_PREFIX_RE = re.compile(r"^【[^】]+】\s*")
_CONTENT_RE = re.compile(r"[\w\u3400-\u9fff]")
_FRONT_MATTER_MARKERS = (
    "本电子版仅限用于个人学习与研究",
    "图书在版编目(CIP)数据",
    "版权所有·侵权必究",
)
_DIRECTORY_LINE_RE = re.compile(r"(?m)^\s*(?:总目录|目\s*录|目录)\s*$")


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str
    count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    metrics: dict[str, Any]
    issues: list[ValidationIssue]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "metrics": self.metrics,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def _issue(issues: list[ValidationIssue], code: str, message: str, count: int) -> None:
    if count:
        issues.append(ValidationIssue(code=code, message=message, count=count))


def validate_documents(
    documents: Sequence[Document],
    *,
    expected_chapters: int | None = None,
    qa_mode: bool = False,
) -> ValidationReport:
    """Validate all P3 handoff gates without repairing or downgrading data."""

    total = len(documents)
    if total == 0:
        return ValidationReport(
            ok=False,
            metrics={"total_chunks": 0},
            issues=[ValidationIssue("empty_documents", "没有可交接的切块", 1)],
        )

    texts = [doc.page_content or "" for doc in documents]
    lengths = [len(text) for text in texts]
    chapters = [str(doc.metadata.get("chapter", "")).strip() for doc in documents]
    uids = [str(doc.metadata.get("chunk_uid", "")).strip() for doc in documents]
    structural_ids = [
        str(doc.metadata.get("structural_id", "")).strip() for doc in documents
    ]

    html_count = sum("<!--" in text for text in texts)
    short_count = sum(length < 100 for length in lengths)
    short_ratio = short_count / total
    median_length = float(statistics.median(lengths))
    noise_count = 0
    for text in texts:
        body = _PREFIX_RE.sub("", text).strip()
        if not body or not _CONTENT_RE.search(body):
            noise_count += 1

    breadcrumb_count = sum(
        bool(chapter and chapter != "前言" and text.startswith(f"【{chapter}】"))
        for text, chapter in zip(texts, chapters)
    )
    breadcrumb_coverage = breadcrumb_count / total
    page_count = sum(
        isinstance(doc.metadata.get("page_start"), int)
        and isinstance(doc.metadata.get("page_end"), int)
        and doc.metadata["page_start"] >= 0
        and doc.metadata["page_end"] >= doc.metadata["page_start"]
        for doc in documents
    )
    page_coverage = page_count / total

    missing_structural = sum(not value for value in structural_ids)
    structural_counts = Counter(value for value in structural_ids if value)
    structural_collisions = sum(count - 1 for count in structural_counts.values() if count > 1)
    bad_uid_count = sum(not _UID_RE.fullmatch(uid) for uid in uids)
    uid_counts = Counter(uid for uid in uids if uid)
    uid_collisions = sum(count - 1 for count in uid_counts.values() if count > 1)
    front_matter_count = sum(
        any(marker in text for marker in _FRONT_MATTER_MARKERS)
        or bool(_DIRECTORY_LINE_RE.search(_PREFIX_RE.sub("", text)))
        for text in texts
    )
    unique_chapters = len(set(chapter for chapter in chapters if chapter))
    chapter_limit = (
        expected_chapters * 1.2 if expected_chapters is not None else None
    )

    metrics: dict[str, Any] = {
        "total_chunks": total,
        "median_length": median_length,
        "min_length": min(lengths),
        "max_length": max(lengths),
        "short_chunk_count": short_count,
        "short_chunk_ratio": round(short_ratio, 6),
        "html_comment_count": html_count,
        "pure_noise_count": noise_count,
        "unique_chapters": unique_chapters,
        "expected_chapters": expected_chapters,
        "chapter_limit": chapter_limit,
        "breadcrumb_coverage": round(breadcrumb_coverage, 6),
        "page_coverage": round(page_coverage, 6),
        "structural_id_missing": missing_structural,
        "structural_id_collisions": structural_collisions,
        "chunk_uid_bad_format": bad_uid_count,
        "chunk_uid_collisions": uid_collisions,
        "front_matter_count": front_matter_count,
        "qa_mode": qa_mode,
    }

    issues: list[ValidationIssue] = []
    _issue(issues, "html_comment", "正文仍包含 HTML 注释", html_count)
    _issue(issues, "pure_noise", "存在纯标记、纯标点或空白切块", noise_count)
    if short_ratio > 0.02:
        issues.append(ValidationIssue(
            "short_chunk_ratio",
            f"小于 100 字的切块占比 {short_ratio:.2%} 超过 2%",
            short_count,
        ))
    if median_length < 800:
        issues.append(ValidationIssue(
            "median_length",
            f"切块中位长度 {median_length:.0f} 低于 800",
            total,
        ))
    if breadcrumb_coverage < 0.8:
        issues.append(ValidationIssue(
            "breadcrumb_coverage",
            f"有效面包屑覆盖率 {breadcrumb_coverage:.2%} 低于 80%",
            total - breadcrumb_count,
        ))
    if page_coverage < 1.0:
        issues.append(ValidationIssue(
            "page_coverage",
            f"页码元数据覆盖率 {page_coverage:.2%} 低于 100%",
            total - page_count,
        ))
    _issue(
        issues,
        "structural_id_missing",
        "存在缺少结构 ID 的切块",
        missing_structural,
    )
    _issue(
        issues,
        "structural_id_duplicate",
        "结构 ID 不是全量唯一",
        structural_collisions,
    )
    _issue(
        issues,
        "chunk_uid_format",
        "chunk_uid 不符合 ck-<32hex>",
        bad_uid_count,
    )
    _issue(
        issues,
        "chunk_uid_duplicate",
        "chunk_uid 存在冲突",
        uid_collisions,
    )
    _issue(
        issues,
        "front_matter",
        "目录或 front matter 内容仍在切块中",
        front_matter_count,
    )
    if chapter_limit is not None and unique_chapters > chapter_limit:
        issues.append(ValidationIssue(
            "chapter_cardinality",
            f"unique(chapter)={unique_chapters} 超过允许值 {chapter_limit:.1f}",
            unique_chapters,
        ))

    return ValidationReport(ok=not issues, metrics=metrics, issues=issues)


_COVERAGE_WINDOW = 12
_COVERAGE_SAMPLE_STEP = 6
_LOST_BLOCK_RATIO = 0.5
_MIN_TEXT_COVERAGE = 0.995
_WHITESPACE_RE = re.compile(r"\s+")
_COVERAGE_SAMPLE_LIMIT = 5


def _compact(text: str) -> str:
    return _WHITESPACE_RE.sub("", text)


def coverage_metrics(
    blocks: Sequence[SourceBlock],
    documents: Sequence[Document],
) -> dict[str, Any]:
    """Measure how much normalized source text survives into chunks.

    Guards the "mislabelled heading with no body is silently dropped" failure
    mode: every normalized block must be findable in the chunk corpus, allowing
    for the few sample windows lost where a long block was hard-wrapped.
    """

    corpus = _compact("".join(doc.page_content or "" for doc in documents))
    grams = {
        corpus[offset : offset + _COVERAGE_WINDOW]
        for offset in range(0, max(0, len(corpus) - _COVERAGE_WINDOW + 1))
    }

    total_windows = 0
    missing_windows = 0
    lost_blocks = 0
    lost_chars = 0
    worst: list[dict[str, Any]] = []
    for block in blocks:
        compact = _compact(block.text)
        if len(compact) < _COVERAGE_WINDOW:
            windows = 1 if compact else 0
            missing = 1 if compact and compact not in corpus else 0
        else:
            windows = range(0, len(compact) - _COVERAGE_WINDOW + 1, _COVERAGE_SAMPLE_STEP)
            window_list = [compact[offset : offset + _COVERAGE_WINDOW] for offset in windows]
            missing = sum(1 for gram in window_list if gram not in grams)
        if not windows:
            continue
        ratio = missing / (windows if isinstance(windows, int) else len(windows))
        total_windows += windows if isinstance(windows, int) else len(windows)
        missing_windows += missing
        if ratio > _LOST_BLOCK_RATIO:
            lost_blocks += 1
            lost_chars += int(len(compact) * ratio)
            if len(worst) < _COVERAGE_SAMPLE_LIMIT:
                worst.append({
                    "page_idx": block.page_idx,
                    "kind": block.kind,
                    "chars": len(compact),
                    "missing_ratio": round(ratio, 4),
                    "sample": block.text[:60],
                })

    coverage = 1.0 if not total_windows else 1 - missing_windows / total_windows
    return {
        "text_coverage": round(coverage, 6),
        "lost_block_count": lost_blocks,
        "lost_chars": lost_chars,
        "coverage_samples": worst,
    }


def validate_coverage(
    blocks: Sequence[SourceBlock],
    documents: Sequence[Document],
) -> tuple[dict[str, Any], ValidationIssue | None]:
    """Return coverage metrics plus a fail-closed issue below the threshold."""

    metrics = coverage_metrics(blocks, documents)
    coverage = float(metrics["text_coverage"])
    if coverage >= _MIN_TEXT_COVERAGE:
        return metrics, None
    return metrics, ValidationIssue(
        "text_coverage",
        f"正文覆盖率 {coverage:.2%} 低于 {_MIN_TEXT_COVERAGE:.1%}，"
        f"疑似丢失块 {metrics['lost_block_count']} 个 / {metrics['lost_chars']} 字符",
        metrics["lost_block_count"],
    )


def validate_determinism(
    first: Sequence[Document],
    second: Sequence[Document],
) -> ValidationIssue | None:
    """Require identical ordered UID sequences across repeated rebuilds."""

    left = [str(doc.metadata.get("chunk_uid", "")) for doc in first]
    right = [str(doc.metadata.get("chunk_uid", "")) for doc in second]
    if left == right:
        return None
    differing = abs(len(left) - len(right)) + sum(
        a != b for a, b in zip(left, right)
    )
    return ValidationIssue(
        "chunk_uid_nondeterministic",
        "相同输入和参数的有序 chunk_uid 集合不一致",
        max(1, differing),
    )
