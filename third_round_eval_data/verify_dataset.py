"""Offline verifier for the third-round unified-Agent dataset."""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent
sys.path.insert(0, str(ROOT))

from agent.execution.contracts import FinishAction
from agent.tools.schemas import (
    KnowledgeQAInput,
    QueryCompanyAwardHistoryInput,
    QueryCompanyBusinessScopeInput,
    QueryCompanyPenaltyInput,
    QueryCompanyRegistrationInput,
    QueryProjectAwardInput,
)

SCHEMA_VERSION = "third-round-unified-eval-v1"
ALLOWED_TOOLS = {
    "knowledge_qa",
    "query_company_registration",
    "query_company_business_scope",
    "query_company_penalty",
    "query_project_award",
    "query_company_award_history",
}
EXPECTED_CATEGORIES = {
    "rag_anchor": 70,
    "single_sql": 40,
    "independent_multi_action": 35,
    "result_dependent": 35,
    "sql_rag_cross_capability": 40,
    "negative": 60,
}
TOOL_SCHEMAS = {
    "knowledge_qa": KnowledgeQAInput,
    "query_company_registration": QueryCompanyRegistrationInput,
    "query_company_business_scope": QueryCompanyBusinessScopeInput,
    "query_company_penalty": QueryCompanyPenaltyInput,
    "query_project_award": QueryProjectAwardInput,
    "query_company_award_history": QueryCompanyAwardHistoryInput,
}


def load_jsonl(name: str) -> list[dict[str, Any]]:
    path = BASE / name
    rows = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise AssertionError(f"{name}:{line_no}: invalid JSON: {exc}") from exc
    return rows


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def review_subject_hash(rows: list[dict[str, Any]]) -> str:
    canonical_rows = []
    for row in rows:
        item = dict(row)
        item.pop("review_status", None)
        item.pop("review", None)
        canonical_rows.append(item)
    payload = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for row in canonical_rows
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest().upper()


def verify() -> dict[str, Any]:
    main = load_jsonl("main_cases.jsonl")
    rag_gold = load_jsonl("rag_gold.jsonl")
    sql_truth = load_jsonl("sql_truth_snapshot.jsonl")
    reliability = load_jsonl("reliability_scenarios.jsonl")
    manifest = json.loads((BASE / "manifest.json").read_text(encoding="utf-8"))
    review_record = json.loads((BASE / "human_review_record.json").read_text(encoding="utf-8"))
    thresholds = json.loads((BASE / "formal_thresholds.json").read_text(encoding="utf-8"))

    assert len(main) == 280, len(main)
    assert Counter(row["category"] for row in main) == Counter(EXPECTED_CATEGORIES)
    assert len(reliability) == 24
    assert 20 <= len(reliability) <= 30
    assert len(rag_gold) == 110
    assert Counter(row["use_group"] for row in rag_gold) == Counter({"rag_anchor": 70, "sql_rag_cross_capability": 40})
    assert Counter(row["query_type"] for row in rag_gold) == Counter({"single_hop": 55, "multi_hop": 55})

    for rows, label in ((main, "main"), (rag_gold, "rag_gold"), (sql_truth, "sql_truth"), (reliability, "reliability")):
        ids = [row["id"] for row in rows]
        assert len(ids) == len(set(ids)), f"duplicate ids in {label}"
        assert all(row["schema_version"] == SCHEMA_VERSION for row in rows), f"schema mismatch in {label}"

    queries = [row["query"] for row in main]
    assert len(queries) == len(set(queries)), "duplicate main query"
    assert all(row["history"] == [] for row in main), "main history must be empty"
    assert sorted(row["file_order"] for row in main) == list(range(1, 281))
    assert all(row["review_status"] == "human_approved" for row in main)
    assert all(row.get("review") == {
        "result": "passed",
        "reviewer": "zz",
        "reviewed_on": "2026-09-12",
        "note": "逐题人工审核通过",
    } for row in main)
    assert all(row["review_status"] == "human_approved" for row in rag_gold)
    assert all(row.get("review") == {
        "result": "passed",
        "reviewer": "zz",
        "reviewed_on": "2026-09-12",
        "note": "RAG参考答案与标准Chunk审核通过",
    } for row in rag_gold)
    assert all(row["review_status"] == "human_approved" for row in sql_truth)
    assert all(row.get("review") == {
        "result": "passed",
        "reviewer": "zz",
        "reviewed_on": "2026-09-12",
        "note": "SQL冻结真值与字段边界审核通过",
    } for row in sql_truth)

    rag_by_id = {row["id"]: row for row in rag_gold}
    sql_by_id = {row["id"]: row for row in sql_truth}
    used_rag: set[str] = set()
    used_sql: set[str] = set()
    action_counts = Counter()

    for row in main:
        graph = row["expected"]["action_graph"]
        node_ids = [action["node_id"] for action in graph]
        assert len(node_ids) == len(set(node_ids)) <= 6, row["id"]
        seen: set[str] = set()
        action_by_id = {action["node_id"]: action for action in graph}
        for action in graph:
            assert action["tool"] in ALLOWED_TOOLS, (row["id"], action["tool"])
            assert set(action["depends_on"]) <= seen, (row["id"], action["node_id"])
            TOOL_SCHEMAS[action["tool"]].model_validate(action["args"])
            action_counts[action["tool"]] += 1
            for field, source in action["arg_sources"].items():
                assert field in action["args"]
                if source["kind"] == "current_user":
                    assert source["value"] == action["args"][field]
                    assert source["value"] in row["query"], (row["id"], source["value"])
                elif source["kind"] == "prior_verified_result":
                    assert source["action_node"] in action["depends_on"]
                    assert source["resolved_value"] == action["args"][field]
                    parent = action_by_id[source["action_node"]]
                    parent_truth = sql_by_id[parent["sql_truth_ref"]]
                    values = {
                        str(record.get(source["field"]) or "").strip()
                        for record in parent_truth["records"]
                        if str(record.get(source["field"]) or "").strip()
                    }
                    assert values == {source["resolved_value"]}, (row["id"], values, source["resolved_value"])
                else:
                    raise AssertionError((row["id"], source["kind"]))
            if action["tool"] == "knowledge_qa":
                ref = action.get("rag_gold_ref")
                assert ref in rag_by_id, (row["id"], ref)
                assert rag_by_id[ref]["question"] == action["args"]["question"]
                used_rag.add(ref)
                assert "sql_truth_ref" not in action
            else:
                ref = action.get("sql_truth_ref")
                assert ref in sql_by_id, (row["id"], ref)
                assert sql_by_id[ref]["tool"] == action["tool"]
                assert sql_by_id[ref]["args"] == action["args"]
                used_sql.add(ref)
                assert "rag_gold_ref" not in action
            seen.add(action["node_id"])
        io = row["expected"]["external_io"]
        assert io["min_calls"] == len(graph) == io["max_calls"], row["id"]
        if row["category"] == "negative":
            assert not graph and io["max_calls"] == 0
            assert set(row["expected"]["allowed_finish_status"]) <= {"clarify", "unsupported"}
        for status in row["expected"]["allowed_finish_status"]:
            FinishAction.model_validate({
                "status": status,
                "missing_fields": row["expected"]["expected_missing_fields"],
            })

    assert used_rag == set(rag_by_id), "orphan or unused RAG gold"
    assert used_sql == set(sql_by_id), "orphan or unused SQL truth"
    assert all(row["snapshot_row_count"] == len(row["records"]) == len(row["record_hashes"]) for row in sql_truth)
    assert all(row["snapshot_row_count"] <= row["default_top_k"] for row in sql_truth)
    assert all((row["total_matching_rows"] == 0) == (row["result_status"] == "no_match") for row in sql_truth)

    reliability_ids = [row["id"] for row in reliability]
    assert len(reliability_ids) == len(set(reliability_ids))
    reliability_counts = Counter(row["category"] for row in reliability)
    assert reliability_counts == Counter({
        "cross_turn_isolation": 8,
        "fault_recovery": 4,
        "cancellation_cleanup": 4,
        "concurrency_isolation": 4,
        "dependency_and_budget_fault": 4,
    })
    assert all(row["review_status"] == "human_approved" for row in reliability)
    assert all(row.get("review") == {
        "result": "passed",
        "reviewer": "zz",
        "reviewed_on": "2026-09-12",
        "note": "可靠性场景人工审核通过",
    } for row in reliability)

    assert review_record["result"] == "passed"
    assert review_record["reviewer"] == "zz"
    assert review_record["reviewed_on"] == "2026-09-12"
    assert review_record["scope"]["main_case_ids"] == [row["id"] for row in main]
    assert review_record["scope"]["rag_gold_ids"] == [row["id"] for row in rag_gold]
    assert review_record["scope"]["sql_truth_ids"] == [row["id"] for row in sql_truth]
    assert review_record["scope"]["reliability_scenario_ids"] == reliability_ids
    assert review_record["review_subject_hashes"]["main_cases_without_review_annotation"] == review_subject_hash(main)
    assert review_record["review_subject_hashes"]["rag_gold_without_review_annotation"] == review_subject_hash(rag_gold)
    assert review_record["review_subject_hashes"]["sql_truth_without_review_annotation"] == review_subject_hash(sql_truth)
    assert review_record["review_subject_hashes"]["reliability_scenarios_without_review_annotation"] == review_subject_hash(reliability)

    assert thresholds["status"] == "frozen"
    assert thresholds["approved_by"] == "zz"
    assert thresholds["approved_on"] == "2026-09-12"
    assert thresholds["system_thresholds"]["A8"]["value"] == 0.95
    assert thresholds["efficiency_policy"]["first_formal_run_mode"] == "report_only_not_a_blocking_gate"
    assert thresholds["pass_policy"]["formal_core_runs"] == 3

    assert manifest["status"] in {
        "formal_preparation_complete_pending_authorization",
        "formal_authorized_not_started",
    }
    assert manifest["scope"]["formal_main_count"] == 280
    assert manifest["scope"]["reliability_scenario_count"] == 24
    assert manifest["review"]["main_cases_human_approved"] == 280
    assert manifest["review"]["rag_gold_human_approved"] == 110
    assert manifest["review"]["sql_truth_human_approved"] == 204
    assert manifest["review"]["reliability_scenarios_human_approved"] == 24
    assert manifest["review"]["reviewer"] == "zz"
    assert manifest["review"]["reviewed_on"] == "2026-09-12"
    assert manifest["thresholds"]["status"] == "frozen"
    authorization = json.loads(
        (BASE / "formal_authorization_record.json").read_text(encoding="utf-8")
    )
    if manifest["status"] == "formal_preparation_complete_pending_authorization":
        assert manifest["review"]["formal_run_authorized"] is False
        assert authorization["status"] == "pending_explicit_user_authorization"
    else:
        assert manifest["review"]["formal_run_authorized"] is True
        assert authorization["status"] == "authorized"
        assert authorization["acknowledgement"] == "START FORMAL PAID RUN"
    for name, expected_hash in manifest["artifact_hashes"].items():
        assert sha256(BASE / name) == expected_hash, f"hash mismatch: {name}"

    return {
        "status": "passed",
        "paid_api_called": manifest.get("paid_api_called") is True,
        "milvus_called": manifest.get("milvus_called") is True,
        "main_cases": len(main),
        "category_counts": dict(sorted(Counter(row["category"] for row in main).items())),
        "action_counts": dict(sorted(action_counts.items())),
        "rag_gold": len(rag_gold),
        "sql_truth": len(sql_truth),
        "reliability_scenarios": len(reliability),
        "reliability_counts": dict(sorted(reliability_counts.items())),
        "history_non_empty_main_cases": 0,
        "duplicate_main_queries": 0,
        "human_review_complete": True,
        "human_review": {
            "result": "passed",
            "reviewer": "zz",
            "reviewed_on": "2026-09-12",
            "main_cases_human_approved": 280,
            "rag_gold_human_approved": 110,
            "sql_truth_human_approved": 204,
            "reliability_scenarios_human_approved": 24,
        },
        "thresholds_frozen": True,
        "threshold_set_id": thresholds["threshold_set_id"],
        "formal_run_authorized": manifest["review"]["formal_run_authorized"],
    }


if __name__ == "__main__":
    result = verify()
    (BASE / "verification_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
