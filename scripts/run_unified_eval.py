"""Evaluate only the top-level model's action sequence against fixed fake tools.

This stage deliberately removes MySQL, Milvus, retrieval quality, and free-text
judging from the measurement. It measures tool choice, argument binding, order,
stopping, and terminal status under a fixed model and temperature.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

from langchain_core.tools import StructuredTool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.execution.context import RunContext
from agent.execution.unified import run_unified_request
from agent.tools.base import make_error_result, make_tool_result
from agent.tools.knowledge import KNOWLEDGE_QA_DESC
from agent.tools.price_db import (
    QUERY_COMPANY_AWARD_HISTORY_DESC,
    QUERY_COMPANY_BUSINESS_SCOPE_DESC,
    QUERY_COMPANY_PENALTY_DESC,
    QUERY_COMPANY_REGISTRATION_DESC,
    QUERY_PROJECT_AWARD_DESC,
)
from agent.tools.schemas import (
    KnowledgeQAInput,
    QueryCompanyAwardHistoryInput,
    QueryCompanyBusinessScopeInput,
    QueryCompanyPenaltyInput,
    QueryCompanyRegistrationInput,
    QueryProjectAwardInput,
)
from public_kb.config import Settings
from public_kb.llm_factory import create_llm


def load_cases(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != "unified-eval-v1":
        raise ValueError("unsupported evaluation schema")
    cases = data.get("cases") or []
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("duplicate case id")
    return cases


def _rag_result(question: str) -> dict:
    return {
        "answer": f"固定知识库结果：{question}【来源1】",
        "is_refusal": False,
        "sources": [],
        "citations": [{
            "context_index": 1,
            "chunk_id": "fake-kb-1",
            "chunk_uid": "fake-kb-uid-1",
            "doc_name": "固定法规样本",
            "chapter": "第一条",
            "text": "这是只用于编排测评的固定法规证据。",
        }],
        "citation_validation": {"all_passed": True, "is_refusal": False},
    }


def fixed_tools() -> list:
    async def knowledge_qa(question, task_id=None):
        result = make_tool_result(data=_rag_result(question), metadata={"source": "fixed_fake"})
        return json.dumps(result, ensure_ascii=False), result

    async def company_registration(company_name, top_k=None, task_id=None):
        result = make_tool_result(data={"records": [{
            "company_name": company_name,
            "credit_code": "91340000FAKE000001",
            "business_status": "存续",
        }]}, metadata={"source": "fixed_fake", "exact_scope": True})
        return json.dumps(result, ensure_ascii=False), result

    async def company_business_scope(company_name, top_k=None, task_id=None):
        result = make_tool_result(data={"records": [{
            "company_name": company_name,
            "business_scope": "软件开发与技术服务",
        }]}, metadata={"source": "fixed_fake", "exact_scope": True})
        return json.dumps(result, ensure_ascii=False), result

    async def company_penalty(company_name, top_k=None, task_id=None):
        if company_name == "故障有限公司":
            result = make_error_result("db_unavailable", "固定故障", retryable=False)
        else:
            records = [] if company_name == "空结果有限公司" else [{
                "company_name": company_name,
                "penalty_date": "2024-01-01",
                "penalty_result": "固定处罚样本",
            }]
            result = make_tool_result(data={"records": records}, metadata={
                "source": "fixed_fake", "exact_scope": True})
        return json.dumps(result, ensure_ascii=False), result

    def award_record(project_number=None, company_name=None):
        return {
            "project_number": project_number or "FAKE-001",
            "project_name": "固定项目样本",
            "purchaser": "固定采购人",
            "successful_bidder": company_name or "甲方科技有限公司",
            "winning_amount": "100",
            "winning_date": "2024-01-01",
        }

    async def project_award(project_number, top_k=None, task_id=None):
        result = make_tool_result(data={"records": [{
            **award_record(project_number=project_number),
        }]}, metadata={"source": "fixed_fake", "exact_scope": True})
        return json.dumps(result, ensure_ascii=False), result

    async def company_award_history(company_name, top_k=None, task_id=None):
        result = make_tool_result(data={"records": [{
            **award_record(company_name=company_name),
        }]}, metadata={"source": "fixed_fake", "exact_scope": True})
        return json.dumps(result, ensure_ascii=False), result

    specs = [
        ("knowledge_qa", knowledge_qa, KnowledgeQAInput, KNOWLEDGE_QA_DESC),
        ("query_company_registration", company_registration, QueryCompanyRegistrationInput, QUERY_COMPANY_REGISTRATION_DESC),
        ("query_company_business_scope", company_business_scope, QueryCompanyBusinessScopeInput, QUERY_COMPANY_BUSINESS_SCOPE_DESC),
        ("query_company_penalty", company_penalty, QueryCompanyPenaltyInput, QUERY_COMPANY_PENALTY_DESC),
        ("query_project_award", project_award, QueryProjectAwardInput, QUERY_PROJECT_AWARD_DESC),
        ("query_company_award_history", company_award_history, QueryCompanyAwardHistoryInput, QUERY_COMPANY_AWARD_HISTORY_DESC),
    ]
    return [StructuredTool.from_function(
        coroutine=fn,
        name=name,
        description=description,
        args_schema=schema,
        response_format="content_and_artifact",
    ) for name, fn, schema, description in specs]


def _action_matches(actual: dict, expected: dict) -> bool:
    if actual.get("tool") != expected["tool"] or actual.get("ok") is not expected.get("ok", True):
        return False
    args = actual.get("args") or {}
    return all(args.get(key) == value for key, value in expected.get("args", {}).items())


async def evaluate(cases: list[dict], repeats: int) -> dict:
    settings = Settings(agent_execution_mode="unified")
    model = create_llm(settings, temperature=0.0)
    tools = fixed_tools()
    results = []
    for repeat in range(1, repeats + 1):
        for case in cases:
            ctx = RunContext(settings, case["query"], f"eval-{repeat}-{case['id']}",
                             architecture="unified")
            result = await run_unified_request(ctx, model, tools)
            actions = result.get("data", {}).get("actions", [])
            expected = case["expected_actions"]
            trace_ok = len(actions) == len(expected) and all(
                _action_matches(actual, wanted) for actual, wanted in zip(actions, expected))
            status_ok = result["execution_status"] in case["expected_status"]
            results.append({
                "id": case["id"],
                "category": case["category"],
                "repeat": repeat,
                "passed": trace_ok and status_ok,
                "trace_ok": trace_ok,
                "status_ok": status_ok,
                "actual_status": result["execution_status"],
                "actual_actions": actions,
                "expected_actions": expected,
                "model_calls": ctx.model_calls,
                "tool_attempts": ctx.tool_attempts,
                "tool_executions": ctx.tool_executions,
                "failure_code": ctx.failure_code,
            })
    counts = Counter(item["category"] for item in results)
    passed = Counter(item["category"] for item in results if item["passed"])
    return {
        "schema_version": "unified-model-eval-v1",
        "model": settings.llm_model,
        "temperature": 0,
        "repeats": repeats,
        "total": len(results),
        "passed": sum(item["passed"] for item in results),
        "pass_rate": sum(item["passed"] for item in results) / len(results) if results else 0,
        "by_category": {key: {"passed": passed[key], "total": value}
                        for key, value in sorted(counts.items())},
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path,
                        default=ROOT / "test" / "fixtures" / "unified_eval_cases.json")
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "test_report" / "unified_model_eval.json")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    cases = load_cases(args.cases)
    if args.limit:
        cases = cases[:args.limit]
    report = asyncio.run(evaluate(cases, args.repeats))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "schema_version", "model", "temperature", "repeats", "total", "passed", "pass_rate", "by_category")},
        ensure_ascii=False, indent=2))
    print(f"details: {args.output}")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
