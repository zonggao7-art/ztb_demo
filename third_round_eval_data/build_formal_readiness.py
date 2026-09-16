"""Build the immutable pre-formal-run readiness record.

This command does not authorize or start a formal run. It only consolidates
already-produced evidence and freezes the exact evaluation/runtime sources.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
sys.path.insert(0, str(ROOT))

from evaluation.unified.dataset import load_bundle, run_preflight, sha256


A2_RUN = BASE / "runs" / "third_round_smoke_a2_retest_20260912_01"
RELIABILITY_RUN = BASE / "runs" / "third_round_reliability_smoke_20260912_01"
JUDGE_RUN = BASE / "judge_variance" / "judge_variance_s4plus1_20260912_01"
LIVE_PREFLIGHT = BASE / "live_data_preflight_20260912_01.json"
TEST_REPORT = BASE / "preparation_test_report_20260912_01.json"
OUTPUT = BASE / "formal_readiness_20260912_01.json"

SOURCE_FILES = [
    "scripts/run_third_round_eval.py",
    "evaluation/rag/scoring.py",
    "evaluation/unified/collector.py",
    "evaluation/unified/dataset.py",
    "evaluation/unified/judge_variance.py",
    "evaluation/unified/reliability.py",
    "evaluation/unified/report.py",
    "evaluation/unified/scoring.py",
    "agent/execution/unified.py",
    "agent/execution/context.py",
    "agent/execution/contracts.py",
    "agent/execution/evidence.py",
    "agent/execution/output.py",
    "agent/execution/policy.py",
    "agent/tools/schemas.py",
    "agent/tools/knowledge.py",
    "agent/tools/price_db.py",
    "agent/tools/strict_sql.py",
    "public_kb/config.py",
    "requirements.txt",
    "requirements-eval.txt",
]


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout


def main() -> None:
    required = [
        A2_RUN / "run_plan.json",
        A2_RUN / "results.jsonl",
        A2_RUN / "score_summary.json",
        RELIABILITY_RUN / "reliability_plan.json",
        RELIABILITY_RUN / "reliability_results.jsonl",
        RELIABILITY_RUN / "reliability_summary.json",
        JUDGE_RUN / "scoring_config.json",
        JUDGE_RUN / "repeat_1" / "scores.jsonl",
        JUDGE_RUN / "repeat_2" / "scores.jsonl",
        JUDGE_RUN / "repeat_2" / "usage.json",
        JUDGE_RUN / "summary.json",
        LIVE_PREFLIGHT,
        TEST_REPORT,
        BASE / "formal_run_config.json",
    ]
    missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]
    if missing:
        raise SystemExit(f"readiness evidence missing: {missing}")

    bundle = load_bundle(BASE)
    preflight = run_preflight(
        bundle,
        run_kind="smoke",
        require_live_configuration=True,
        verify_live_data=False,
    )
    a2 = read_json(A2_RUN / "score_summary.json")
    reliability = read_json(RELIABILITY_RUN / "reliability_summary.json")
    judge = read_json(JUDGE_RUN / "summary.json")
    live = read_json(LIVE_PREFLIGHT)
    tests = read_json(TEST_REPORT)
    config = bundle.formal_run_config

    a2_repeats = {
        repeat: detail.get("metrics", {}).get("A2", {})
        for repeat, detail in a2.get("per_repeat", {}).items()
    }
    checks = {
        "offline_preflight": preflight.passed,
        "human_review": bundle.review.get("result") == "passed",
        "thresholds_frozen": bundle.thresholds.get("status") == "frozen",
        "formal_config_frozen": config.get("status") == "frozen_for_authorization",
        "a2_target_retest_three_repeats": (
            len(a2_repeats) == 3
            and all(metric.get("value") == 1.0 for metric in a2_repeats.values())
        ),
        "reliability_live_smoke": (
            reliability.get("decision") == "passed"
            and reliability.get("collection_complete") is True
            and reliability.get("scenario_attempts") == 5
        ),
        "live_sql_and_rag_identity": live.get("status") == "passed",
        "judge_variance": (
            judge.get("status") == "passed"
            and judge.get("all_scores_complete") is True
            and judge.get("every_repeat_meets_frozen_thresholds") is True
        ),
        "preparation_tests": tests.get("status") == "passed",
        "formal_run_directories_absent": (
            not (BASE / "runs" / config["main_collection"]["run_id"]).exists()
            and not (
                BASE / "runs" / config["reliability_collection"]["run_id"]
            ).exists()
        ),
        "formal_run_not_started": bundle.manifest.get("review", {}).get(
            "formal_run_authorized"
        ) is False,
    }
    blocking_failures = [name for name, passed in checks.items() if passed is not True]

    status_text = git("status", "--porcelain=v1") or ""
    diff_text = git("diff", "--binary", "HEAD") or ""
    evidence_hashes = {
        str(path.relative_to(ROOT)).replace("\\", "/"): sha256(path)
        for path in required
    }
    source_hashes = {
        name: sha256(ROOT / name)
        for name in SOURCE_FILES
        if (ROOT / name).exists()
    }
    payload = {
        "schema_version": "third-round-formal-readiness-v1",
        "readiness_id": "third_round_formal_readiness_20260912_01",
        "status": "ready_for_authorization" if not blocking_failures else "blocked",
        "dataset_id": bundle.dataset_id,
        "threshold_set_id": bundle.thresholds["threshold_set_id"],
        "formal_run_config_id": config["config_id"],
        "formal_run_authorized": False,
        "formal_run_started": False,
        "checks": checks,
        "blocking_failures": blocking_failures,
        "evidence": {
            "a2_repeat_values": {
                repeat: metric.get("value") for repeat, metric in a2_repeats.items()
            },
            "reliability_smoke_decision": reliability.get("decision"),
            "judge_variance_repeats": judge.get("repeats"),
            "judge_variance_aggregates": judge.get("per_repeat"),
            "live_data_status": live.get("status"),
            "test_report": tests,
        },
        "capacity_budget": config["capacity_budget"],
        "evidence_hashes": evidence_hashes,
        "code_snapshot": {
            "git_commit": (git("rev-parse", "HEAD") or "").strip() or None,
            "git_branch": (git("branch", "--show-current") or "").strip() or None,
            "worktree_dirty": bool(status_text),
            "changed_path_count": len(status_text.splitlines()),
            "git_status_sha256": hashlib.sha256(status_text.encode()).hexdigest().upper(),
            "tracked_diff_sha256": hashlib.sha256(diff_text.encode()).hexdigest().upper(),
            "source_hashes": source_hashes,
        },
        "next_gate": "explicit_human_authorization_to_start_formal_paid_run",
    }
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({
        "status": payload["status"],
        "blocking_failures": blocking_failures,
        "output": str(OUTPUT),
    }, ensure_ascii=False, indent=2))
    if blocking_failures:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
