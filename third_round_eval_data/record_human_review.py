"""Record the approved human review and freeze formal metric thresholds.

This is deliberately separate from the dataset builder. Rebuilding the dataset
must return it to a review-pending state instead of silently carrying approval
onto changed questions or database truth.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


BASE = Path(__file__).resolve().parent
ROOT = BASE.parent

MAIN_PATH = BASE / "main_cases.jsonl"
RAG_GOLD_PATH = BASE / "rag_gold.jsonl"
SQL_TRUTH_PATH = BASE / "sql_truth_snapshot.jsonl"
RELIABILITY_PATH = BASE / "reliability_scenarios.jsonl"
REVIEW_PATH = BASE / "human_review_record.json"
THRESHOLDS_PATH = BASE / "formal_thresholds.json"
MANIFEST_PATH = BASE / "manifest.json"

REVIEWER = "zz"
REVIEWED_ON = "2026-09-12"
REVIEW_ID = "third_round_human_review_20260912_zz"
THRESHOLD_SET_ID = "third_round_formal_thresholds_20260912_v1"
REVIEWED_DATASET_ID = "third_round_unified_agent_20260912_reviewed_01"
READY_STATUS = "formal_preparation_complete_pending_authorization"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def review_subject_hash(rows: list[dict[str, Any]]) -> str:
    """Hash reviewed content while excluding the approval annotation itself."""
    canonical_rows = []
    for row in rows:
        item = dict(row)
        item.pop("review_status", None)
        item.pop("review", None)
        canonical_rows.append(item)
    payload = "".join(
        json.dumps(
            row,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
        for row in canonical_rows
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper()


def threshold_document() -> dict[str, Any]:
    return {
        "schema_version": "third-round-unified-eval-thresholds-v1",
        "threshold_set_id": THRESHOLD_SET_ID,
        "status": "frozen",
        "approved_by": REVIEWER,
        "approved_on": REVIEWED_ON,
        "applies_to": {
            "dataset_id": REVIEWED_DATASET_ID,
            "system": "unified_agent",
            "history_policy": "current_turn_only_for_model_input_and_tool_parameters",
        },
        "pass_policy": {
            "formal_core_runs": 3,
            "each_run_must_pass": True,
            "best_run_selection_forbidden": True,
            "report_raw_numerator_and_denominator": True,
            "required_group_breakdowns": [
                "case_category",
                "finish_status",
                "authorized_tool",
                "single_or_multi_entity",
                "normal_empty_or_failure",
                "rag_single_or_multi_hop",
                "normal_adversarial_or_fault",
                "formal_run",
            ],
        },
        "system_thresholds": {
            "A1": {"operator": "eq", "value": 0, "unit": "events", "blocking": True},
            "A2": {"operator": "gte", "value": 0.98, "unit": "audited_decision_points"},
            "A3": {"operator": "gte", "value": 0.98, "unit": "positive_cases"},
            "A4": {
                "operator": "lte",
                "value": 0.02,
                "unit": "actual_business_calls",
                "additional_gate": {"unauthorized_external_calls": 0},
            },
            "A5": {"operator": "gte", "value": 0.99, "unit": "gold_required_calls"},
            "A6": {"operator": "eq", "value": 1.0, "unit": "gold_dependency_edges", "blocking": True},
            "A7": {"operator": "gte", "value": 0.98, "unit": "attempted_cases"},
            "A8": {"operator": "gte", "value": 0.95, "unit": "positive_cases"},
            "A9": {"operator": "eq", "value": 1.0, "unit": "verifiable_facts", "blocking": True},
            "A10": {"operator": "gte", "value": 0.98, "unit": "negative_cases"},
        },
        "rag_thresholds": {
            "comparison_scope": "same_110_s4_samples_used_by_the_third_round_dataset",
            "comparison_conditions": [
                "same_question_and_reference",
                "same_corpus_chunk_mapping_and_milvus_collection",
                "same_knowledge_qa_configuration_and_answer_model",
                "same_ragas_judge_embedding_and_scoring_configuration",
                "final_citations_only",
            ],
            "s4_same_subset_baseline": {
                "source_run": "s4_20260911_formal_01",
                "sample_count": 110,
                "expected_chunk_count": 165,
                "matched_chunk_count": 146,
                "B1_context_recall": 0.969394,
                "B2_context_precision": 0.871010,
                "B3_answer_correctness": 0.817819,
                "B4_faithfulness": 0.961130,
                "B5_exact_chunk_macro_recall": 0.895455,
                "B6_exact_chunk_micro_recall": 0.884848,
            },
            "thresholds": {
                "B1": {"operator": "gte", "value": 0.95},
                "B2": {"operator": "gte", "value": 0.85},
                "B3": {"operator": "gte", "value": 0.80},
                "B4": {"operator": "gte", "value": 0.95},
                "B5": {"operator": "gte", "value": 0.88},
                "B6": {"operator": "gte", "value": 0.87},
            },
            "judge_variance_check": {
                "required_before_formal_run": True,
                "method": "rescore_frozen_s4_same_subset_outputs_2_to_3_times",
                "does_not_rerun_tested_system": True,
            },
        },
        "sql_thresholds": {
            "C1": {"operator": "gte", "value": 0.99, "unit": "gold_required_sql_calls"},
            "C2": {"operator": "eq", "value": 1.0, "unit": "published_sql_facts", "blocking": True},
            "C3": {"operator": "gte", "value": 0.98, "unit": "gold_required_sql_facts"},
            "C4": {"operator": "eq", "value": 1.0, "unit": "applicable_cases", "blocking": True},
            "C5": {"operator": "eq", "value": 1.0, "unit": "applicable_cases", "blocking": True},
        },
        "efficiency_policy": {
            "metrics": ["D1", "D2", "D3", "D4", "D5", "D6"],
            "first_formal_run_mode": "report_only_not_a_blocking_gate",
            "reason": "No comparable full ReAct latency and cost baseline exists yet.",
        },
        "reliability_thresholds": {
            "E1": {"mode": "report_distribution"},
            "E2": {"operator": "eq", "value": 1.0, "expected_passed": 16, "expected_total": 16},
            "E3": {"operator": "eq", "value": 0, "unit": "violations", "scenario_count": 8, "blocking": True},
            "E4": {"operator": "eq", "value": 0, "unit": "violations", "scenario_count": 4, "blocking": True},
            "E5": {"operator": "eq", "value": 1.0, "expected_passed": 16, "expected_total": 16, "blocking": True},
            "E6": {"operator": "eq", "consecutive_passing_runs": 3},
        },
    }


def main() -> None:
    authorization_path = BASE / "formal_authorization_record.json"
    if authorization_path.exists():
        authorization = json.loads(authorization_path.read_text(encoding="utf-8"))
        if authorization.get("status") == "authorized":
            raise SystemExit("refusing to rewrite review state after formal authorization")
    main_cases = read_jsonl(MAIN_PATH)
    rag_gold = read_jsonl(RAG_GOLD_PATH)
    sql_truth = read_jsonl(SQL_TRUTH_PATH)
    reliability = read_jsonl(RELIABILITY_PATH)
    if (len(main_cases), len(rag_gold), len(sql_truth), len(reliability)) != (280, 110, 204, 24):
        raise AssertionError("review scope changed; expected 280 main, 110 RAG, 204 SQL and 24 reliability rows")

    main_subject_hash = review_subject_hash(main_cases)
    rag_subject_hash = review_subject_hash(rag_gold)
    sql_subject_hash = review_subject_hash(sql_truth)
    reliability_subject_hash = review_subject_hash(reliability)

    for row in main_cases:
        row["review_status"] = "human_approved"
        row["review"] = {
            "result": "passed",
            "reviewer": REVIEWER,
            "reviewed_on": REVIEWED_ON,
            "note": "逐题人工审核通过",
        }
    for row in reliability:
        row["review_status"] = "human_approved"
        row["review"] = {
            "result": "passed",
            "reviewer": REVIEWER,
            "reviewed_on": REVIEWED_ON,
            "note": "可靠性场景人工审核通过",
        }
    for row in rag_gold:
        row["review_status"] = "human_approved"
        row["review"] = {
            "result": "passed",
            "reviewer": REVIEWER,
            "reviewed_on": REVIEWED_ON,
            "note": "RAG参考答案与标准Chunk审核通过",
        }
    for row in sql_truth:
        row["review_status"] = "human_approved"
        row["review"] = {
            "result": "passed",
            "reviewer": REVIEWER,
            "reviewed_on": REVIEWED_ON,
            "note": "SQL冻结真值与字段边界审核通过",
        }

    write_jsonl(MAIN_PATH, main_cases)
    write_jsonl(RAG_GOLD_PATH, rag_gold)
    write_jsonl(SQL_TRUTH_PATH, sql_truth)
    write_jsonl(RELIABILITY_PATH, reliability)

    review_record = {
        "schema_version": "third-round-unified-eval-review-v1",
        "review_id": REVIEW_ID,
        "dataset_id": REVIEWED_DATASET_ID,
        "result": "passed",
        "reviewer": REVIEWER,
        "reviewed_on": REVIEWED_ON,
        "scope": {
            "main_cases": len(main_cases),
            "rag_gold_rows": len(rag_gold),
            "sql_truth_rows": len(sql_truth),
            "reliability_scenarios": len(reliability),
            "main_case_ids": [row["id"] for row in main_cases],
            "rag_gold_ids": [row["id"] for row in rag_gold],
            "sql_truth_ids": [row["id"] for row in sql_truth],
            "reliability_scenario_ids": [row["id"] for row in reliability],
        },
        "review_subject_hashes": {
            "main_cases_without_review_annotation": main_subject_hash,
            "rag_gold_without_review_annotation": rag_subject_hash,
            "sql_truth_without_review_annotation": sql_subject_hash,
            "reliability_scenarios_without_review_annotation": reliability_subject_hash,
        },
        "attestation": {
            "questions_and_expected_actions_reviewed": True,
            "gold_answers_and_evidence_references_reviewed": True,
            "finish_status_and_negative_boundaries_reviewed": True,
            "reliability_assertions_reviewed": True,
        },
    }
    write_json(REVIEW_PATH, review_record)
    write_json(THRESHOLDS_PATH, threshold_document())

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    manifest["dataset_id"] = REVIEWED_DATASET_ID
    manifest["status"] = READY_STATUS
    manifest["paid_api_called"] = True
    manifest["milvus_called"] = True
    manifest["mysql_access"] = "read_only_snapshot_and_identity_queries"
    manifest["updated_on"] = REVIEWED_ON
    manifest["review"] = {
        "status": "passed",
        "review_id": REVIEW_ID,
        "reviewer": REVIEWER,
        "reviewed_on": REVIEWED_ON,
        "main_cases_human_approved": len(main_cases),
        "rag_gold_human_approved": len(rag_gold),
        "sql_truth_human_approved": len(sql_truth),
        "reliability_scenarios_human_approved": len(reliability),
        "rag_gold_inherited_from_s4": manifest["rag_gold"]["count"],
        "record": REVIEW_PATH.name,
        "authorization_record": "formal_authorization_record.json",
        "formal_run_authorized": False,
    }
    manifest["thresholds"] = {
        "status": "frozen",
        "threshold_set_id": THRESHOLD_SET_ID,
        "approved_by": REVIEWER,
        "approved_on": REVIEWED_ON,
        "record": THRESHOLDS_PATH.name,
        "efficiency_first_run": "report_only_not_a_blocking_gate",
    }
    manifest["latest_smoke"] = {
        "initial_run_id": "third_round_smoke_20260912_01",
        "selected_main_cases": 6,
        "selected_categories": 6,
        "authorized_tools_covered": 6,
        "required_actions": 8,
        "collected_results": 6,
        "collection_complete": True,
        "redline_events": 0,
        "initial_a2": 0.875,
        "a2_finding": "two rejected parallel tool proposals in TR-CROSS-005",
        "a2_fix": "prompt requires exactly one tool call per model response; policy rejection remains unchanged",
        "a2_retest_run_id": "third_round_smoke_a2_retest_20260912_01",
        "a2_retest_repeats": 3,
        "a2_retest_values": [1.0, 1.0, 1.0],
        "reliability_run_id": "third_round_reliability_smoke_20260912_01",
        "reliability_selected_scenarios": 5,
        "reliability_live_smoke_completed": True,
        "reliability_live_smoke_decision": "passed",
        "formal_result": False,
    }
    manifest["preparation"] = {
        "live_data_identity": {
            "status": "passed",
            "record": "live_data_preflight_20260912_01.json",
        },
        "judge_variance": {
            "status": "passed",
            "run_id": "judge_variance_s4plus1_20260912_01",
            "record": "judge_variance/judge_variance_s4plus1_20260912_01/summary.json",
        },
        "formal_run_config": {
            "status": "frozen_for_authorization",
            "record": "formal_run_config.json",
        },
        "formal_run_started": False,
    }

    report_path = ROOT / "docs" / "eval_docs" / "ReAct系统测评指标构建报告_20260912.md"
    report_key = str(report_path.relative_to(ROOT)).replace("\\", "/")
    manifest["source_hashes"][report_key] = sha256(report_path)
    artifact_paths = [
        MAIN_PATH,
        RAG_GOLD_PATH,
        SQL_TRUTH_PATH,
        RELIABILITY_PATH,
        BASE / "dataset_schema.json",
        REVIEW_PATH,
        THRESHOLDS_PATH,
        BASE / "formal_run_config.json",
        BASE / "live_data_preflight_20260912_01.json",
        BASE / "formal_authorization_record.json",
    ]
    test_report = BASE / "preparation_test_report_20260912_01.json"
    if test_report.exists():
        artifact_paths.append(test_report)
    readiness = BASE / "formal_readiness_20260912_01.json"
    if readiness.exists():
        readiness_payload = json.loads(readiness.read_text(encoding="utf-8"))
        if readiness_payload.get("status") != "ready_for_authorization":
            raise AssertionError("formal readiness record is not ready_for_authorization")
        artifact_paths.append(readiness)
        manifest["readiness"] = {
            "status": readiness_payload["status"],
            "readiness_id": readiness_payload["readiness_id"],
            "record": readiness.name,
            "next_gate": readiness_payload["next_gate"],
        }
    manifest["artifact_hashes"] = {path.name: sha256(path) for path in artifact_paths}
    write_json(MANIFEST_PATH, manifest)

    print(
        json.dumps(
            {
                "dataset_id": REVIEWED_DATASET_ID,
                "review": "passed",
                "reviewer": REVIEWER,
                "reviewed_on": REVIEWED_ON,
                "main_cases_human_approved": len(main_cases),
                "rag_gold_human_approved": len(rag_gold),
                "sql_truth_human_approved": len(sql_truth),
                "reliability_scenarios_human_approved": len(reliability),
                "thresholds": "frozen",
                "formal_run_authorized": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
