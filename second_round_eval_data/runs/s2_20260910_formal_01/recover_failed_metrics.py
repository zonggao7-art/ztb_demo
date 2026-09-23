"""仅补偿本次正式运行中因裁判 API 429 失败的质量指标。

这是运行级恢复脚本，不属于被测系统源码。它保留原始 scores.jsonl，
逐项串行重试失败指标，并分别记录恢复审计与追加 Token 用量。
"""

from __future__ import annotations

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

from evaluation.rag.scoring import RagasScorer, read_jsonl
from evaluation.rag.tokens import merge_token_snapshots


RUN_DIR = Path(__file__).resolve().parent
RESULTS_PATH = RUN_DIR / "results.jsonl"
SCORES_PATH = RUN_DIR / "scores.jsonl"
USAGE_PATH = RUN_DIR / "scoring_usage.json"
MANIFEST_PATH = RUN_DIR / "manifest.json"
BACKUP_PATH = RUN_DIR / "scores_before_429_recovery.jsonl"
AUDIT_PATH = RUN_DIR / "scoring_recovery_attempts.jsonl"


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
        for metric_name, item in (row.get("metrics") or {}).items():
            if item.get("status") == "scoring_failure":
                failures.append((sample_id, metric_name))
    return failures


async def main() -> None:
    required = (RESULTS_PATH, SCORES_PATH, USAGE_PATH, MANIFEST_PATH)
    missing = [str(path.name) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("缺少恢复输入: " + ", ".join(missing))

    if not BACKUP_PATH.exists():
        shutil.copy2(SCORES_PATH, BACKUP_PATH)

    results = read_jsonl(RESULTS_PATH)
    scores = read_jsonl(SCORES_PATH)
    if len(results) != 200 or len(scores) != 200:
        raise RuntimeError(
            f"恢复只接受完整 200 题：results={len(results)}, scores={len(scores)}"
        )
    for result, score in zip(results, scores, strict=True):
        if result.get("sample_id") != score.get("sample_id"):
            raise RuntimeError("results/scores sample_id 顺序不一致")

    initial_failures = _failed_metrics(scores)
    if not initial_failures:
        print("没有待恢复的 scoring_failure")
        return

    result_by_id = {str(row["sample_id"]): row for row in results}
    score_by_id = {str(row["sample_id"]): row for row in scores}
    prior_usage = json.loads(USAGE_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    started_at = _utc_now()
    scorer = RagasScorer.from_environment(metric_timeout_s=600.0)
    recovered = 0

    try:
        for position, (sample_id, metric_name) in enumerate(initial_failures, start=1):
            row = result_by_id[sample_id]
            old_item = score_by_id[sample_id]["metrics"][metric_name]
            new_item = await scorer._score_metric(
                metric_name,
                question=str(row.get("question", "")),
                reference=str(row.get("reference", "")),
                answer=str(row.get("answer", "")),
                contexts=[
                    str(value) for value in (row.get("contexts") or []) if str(value)
                ],
                system_status=str(row.get("status", "")),
                is_refusal=row.get("is_refusal") is True,
            )
            audit_row = {
                "attempted_at": _utc_now(),
                "sample_id": sample_id,
                "metric": metric_name,
                "recovery_position": position,
                "recovery_total": len(initial_failures),
                "concurrency": 1,
                "original": old_item,
                "result": new_item,
            }
            with AUDIT_PATH.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(audit_row, ensure_ascii=False) + "\n")

            if new_item.get("status") == "success":
                score_by_id[sample_id]["metrics"][metric_name] = new_item
                _write_jsonl(SCORES_PATH, scores)
                recovered += 1

            combined_usage = merge_token_snapshots(prior_usage, scorer.usage.snapshot())
            _write_json(USAGE_PATH, combined_usage)
            print(
                f"恢复评分 {position}/{len(initial_failures)} "
                f"sample={sample_id} metric={metric_name} "
                f"status={new_item.get('status')}",
                flush=True,
            )
            if position < len(initial_failures):
                await asyncio.sleep(2.0)
    finally:
        await scorer.close()

    remaining = _failed_metrics(scores)
    completed_at = _utc_now()
    manifest["status"] = "scoring_complete" if not remaining else "scoring_incomplete"
    manifest["scoring_recovery"] = {
        "status": "complete" if not remaining else "incomplete",
        "method": "failed_metrics_only",
        "cause": "referee_api_429_token_plan_rate_limit",
        "judge_model": os.getenv("REFEREE_MODEL"),
        "judge_base_url": os.getenv("REFEREE_BASE_URL"),
        "metric_timeout_s": 600.0,
        "concurrency": 1,
        "inter_metric_delay_s": 2.0,
        "started_at": started_at,
        "completed_at": completed_at,
        "original_failed_metric_count": len(initial_failures),
        "recovered_metric_count": recovered,
        "remaining_failed_metric_count": len(remaining),
        "remaining_failures": [
            {"sample_id": sample_id, "metric": metric_name}
            for sample_id, metric_name in remaining
        ],
        "original_scores_backup": BACKUP_PATH.name,
        "original_scores_sha256": _sha256(BACKUP_PATH),
        "recovery_audit": AUDIT_PATH.name,
    }
    _write_json(MANIFEST_PATH, manifest)
    print(
        json.dumps(
            {
                "original_failed": len(initial_failures),
                "recovered": recovered,
                "remaining": len(remaining),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if remaining:
        raise RuntimeError(f"仍有 {len(remaining)} 个指标恢复失败")


if __name__ == "__main__":
    asyncio.run(main())
