"""仅补偿 S3 正式评分中因裁判连接错误失败的指标。

这是运行级恢复脚本，不属于被测系统源码。它严格核对原始结果哈希与
q189-q200 四项失败指标，完整备份恢复前证据，逐项串行重试，并原子
替换 scores.jsonl。成功指标不会被重算。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.rag.scoring import METRIC_NAMES, RagasScorer, read_jsonl
from evaluation.rag.tokens import merge_token_snapshots


RUN_DIR = Path(__file__).resolve().parent
RESULTS_PATH = RUN_DIR / "results.jsonl"
SCORES_PATH = RUN_DIR / "scores.jsonl"
USAGE_PATH = RUN_DIR / "scoring_usage.json"
MANIFEST_PATH = RUN_DIR / "manifest.json"
SUMMARY_PATH = RUN_DIR / "summary.json"
REPORT_PATH = RUN_DIR / "report.md"
COMPARISON_JSON_PATH = RUN_DIR / "paired_comparison.json"
COMPARISON_MD_PATH = RUN_DIR / "paired_comparison.md"
AUDIT_PATH = RUN_DIR / "scoring_connection_recovery_attempts.jsonl"
RECOVERY_SOURCE_SNAPSHOT = (
    PROJECT_ROOT / "second_round_eval_data" / "s3_recovery_source_snapshot.json"
)

BACKUPS = {
    SCORES_PATH: RUN_DIR / "scores_before_connection_recovery.jsonl",
    USAGE_PATH: RUN_DIR / "scoring_usage_before_connection_recovery.json",
    MANIFEST_PATH: RUN_DIR / "manifest_before_connection_recovery.json",
    SUMMARY_PATH: RUN_DIR / "summary_before_connection_recovery.json",
    REPORT_PATH: RUN_DIR / "report_before_connection_recovery.md",
    COMPARISON_JSON_PATH: RUN_DIR / "paired_comparison_before_connection_recovery.json",
    COMPARISON_MD_PATH: RUN_DIR / "paired_comparison_before_connection_recovery.md",
}

EXPECTED_RESULTS_SHA256 = (
    "A75686E4B245D31011496D05C8DF4968B582FCA233A3E4B416F440272B67B84F"
)
EXPECTED_ORIGINAL_SCORES_SHA256 = (
    "5551C9A38E9F03A4398B88584A8A4472F1EA9F5A2FE36ABE5CB7C99CAA9B84D4"
)
EXPECTED_FAILED_SAMPLE_IDS = tuple(f"q{index:03d}" for index in range(189, 201))
EXPECTED_FAILURES = {
    (sample_id, metric_name)
    for sample_id in EXPECTED_FAILED_SAMPLE_IDS
    for metric_name in METRIC_NAMES
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".recovery.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".recovery.tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    temporary.replace(path)


def _failed_metrics(scores: list[dict[str, Any]]) -> list[tuple[str, str]]:
    failures: list[tuple[str, str]] = []
    for row in scores:
        sample_id = str(row.get("sample_id", ""))
        metrics = row.get("metrics") or {}
        for metric_name in METRIC_NAMES:
            item = metrics.get(metric_name) or {}
            if item.get("status") != "success" or item.get("value") is None:
                failures.append((sample_id, metric_name))
    return failures


def _validate_inputs(
    results: list[dict[str, Any]], scores: list[dict[str, Any]]
) -> list[tuple[str, str]]:
    if _sha256(RESULTS_PATH) != EXPECTED_RESULTS_SHA256:
        raise RuntimeError("results.jsonl 哈希不符合冻结值")
    if len(results) != 200 or len(scores) != 200:
        raise RuntimeError(
            f"恢复只接受完整200题：results={len(results)}, scores={len(scores)}"
        )
    for position, (result, score) in enumerate(zip(results, scores, strict=True), start=1):
        expected_id = f"q{position:03d}"
        if result.get("sample_id") != expected_id or score.get("sample_id") != expected_id:
            raise RuntimeError(f"results/scores 在第{position}题发生身份或顺序错位")
    failures = _failed_metrics(scores)
    unexpected = set(failures) - EXPECTED_FAILURES
    if unexpected:
        raise RuntimeError(f"存在不属于本次连接故障的失败指标: {sorted(unexpected)}")
    return failures


def _ensure_backups() -> None:
    for source, backup in BACKUPS.items():
        if not source.is_file():
            raise FileNotFoundError(f"缺少恢复输入: {source.name}")
        if not backup.exists():
            shutil.copy2(source, backup)
    if _sha256(BACKUPS[SCORES_PATH]) != EXPECTED_ORIGINAL_SCORES_SHA256:
        raise RuntimeError("恢复前 scores.jsonl 备份哈希不符合冻结值")


async def recover() -> None:
    _ensure_backups()
    results = read_jsonl(RESULTS_PATH)
    scores = read_jsonl(SCORES_PATH)
    failures = _validate_inputs(results, scores)
    if not failures:
        print("没有待恢复的失败指标")
        return

    result_by_id = {str(row["sample_id"]): row for row in results}
    score_by_id = {str(row["sample_id"]): row for row in scores}
    prior_usage = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    started_at = _utc_now()
    scorer = RagasScorer.from_environment(metric_timeout_s=600.0)
    recovered_this_attempt = 0

    try:
        for position, (sample_id, metric_name) in enumerate(failures, start=1):
            row = result_by_id[sample_id]
            old_item = score_by_id[sample_id]["metrics"][metric_name]
            new_item = await scorer._score_metric(
                metric_name,
                question=str(row.get("question", "")),
                reference=str(row.get("reference", "")),
                answer=str(row.get("answer", "")),
                contexts=[str(value) for value in (row.get("contexts") or []) if str(value)],
                system_status=str(row.get("status", "")),
                is_refusal=row.get("is_refusal") is True,
            )
            audit_row = {
                "attempted_at": _utc_now(),
                "sample_id": sample_id,
                "metric": metric_name,
                "recovery_position": position,
                "recovery_total_this_attempt": len(failures),
                "concurrency": 1,
                "inter_metric_delay_s": 2.0,
                "original": old_item,
                "result": new_item,
            }
            with AUDIT_PATH.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(audit_row, ensure_ascii=False) + "\n")

            combined_usage = merge_token_snapshots(prior_usage, scorer.usage.snapshot())
            _write_json(USAGE_PATH, combined_usage)
            if new_item.get("status") == "success" and new_item.get("value") is not None:
                score_by_id[sample_id]["metrics"][metric_name] = new_item
                _write_jsonl(SCORES_PATH, scores)
                recovered_this_attempt += 1
            print(
                f"恢复评分 {position}/{len(failures)} sample={sample_id} "
                f"metric={metric_name} status={new_item.get('status')}",
                flush=True,
            )
            if position < len(failures):
                await asyncio.sleep(2.0)
    finally:
        await scorer.close()

    remaining = _failed_metrics(scores)
    original_scores = read_jsonl(BACKUPS[SCORES_PATH])
    original_failure_count = len(_failed_metrics(original_scores))
    manifest["status"] = "scoring_complete" if not remaining else "scoring_incomplete"
    manifest["scoring_recovery"] = {
        "status": "complete" if not remaining else "incomplete",
        "method": "failed_metrics_only",
        "cause": "referee_connection_error",
        "judge_model": os.getenv("REFEREE_MODEL"),
        "metric_timeout_s": 600.0,
        "concurrency": 1,
        "inter_metric_delay_s": 2.0,
        "started_at": started_at,
        "completed_at": _utc_now(),
        "original_failed_sample_ids": list(EXPECTED_FAILED_SAMPLE_IDS),
        "original_failed_metric_count": original_failure_count,
        "recovered_metric_count_total": original_failure_count - len(remaining),
        "recovered_metric_count_this_attempt": recovered_this_attempt,
        "remaining_failed_metric_count": len(remaining),
        "remaining_failures": [
            {"sample_id": sample_id, "metric": metric_name}
            for sample_id, metric_name in remaining
        ],
        "original_scores_backup": BACKUPS[SCORES_PATH].name,
        "original_scores_sha256": _sha256(BACKUPS[SCORES_PATH]),
        "original_usage_backup": BACKUPS[USAGE_PATH].name,
        "original_usage_sha256": _sha256(BACKUPS[USAGE_PATH]),
        "recovery_audit": AUDIT_PATH.name,
        "recovery_script": Path(__file__).name,
        "recovery_script_sha256": _sha256(Path(__file__)),
        "recovery_source_snapshot": str(RECOVERY_SOURCE_SNAPSHOT),
        "recovery_source_snapshot_sha256": _sha256(RECOVERY_SOURCE_SNAPSHOT),
    }
    _write_json(MANIFEST_PATH, manifest)
    print(
        json.dumps(
            {
                "original_failed": original_failure_count,
                "recovered_total": original_failure_count - len(remaining),
                "remaining": len(remaining),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if remaining:
        raise RuntimeError(f"仍有{len(remaining)}个指标恢复失败")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-paid-recovery", action="store_true")
    args = parser.parse_args()
    if not args.confirm_paid_recovery:
        raise ValueError("恢复会调用真实裁判与评分Embedding，必须显式确认")
    asyncio.run(recover())


if __name__ == "__main__":
    main()
