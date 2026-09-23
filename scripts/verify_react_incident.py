"""Opt-in live check of the three reported queries through the actual CLI stream consumer."""
import argparse
import asyncio
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
from io import StringIO
import json
import logging
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

QUESTIONS = [
    "查一下合肥盈拓成电子科技有限公司的工商信息，再告诉我招标的方式有哪些？",
    "查一下合肥盈拓成电子科技有限公司的工商信息，再差一下评审委员会的职责有哪些？",
    "查一下合肥盈拓成电子科技有限公司的工商信息，再查询一下这家公司有没有处罚记录？",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="调用真实模型及只读SQL/RAG，会产生API成本")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.live:
        print("未指定 --live，不调用任何服务。")
        return
    from agent.graph import AgentGraph
    from agent.__main__ import _consume_astream_turn
    from agent.streaming import EventType
    from agent.tools.knowledge import _get_rag
    from public_kb.config import Settings
    logging.basicConfig(level=logging.WARNING)
    thread_id = "incident-" + uuid4().hex
    settings = replace(Settings(), agent_execution_mode="hybrid",
                       agent_react_rollout_percent=0, agent_react_thread_allowlist=thread_id)
    agent = AgentGraph(settings=settings, async_enabled=True)
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "thread_id": thread_id,
              "entry": "CLI _consume_astream_turn / AgentGraph.astream",
              "model": settings.llm_model, "production_gate_passed": False, "cases": []}
    with asyncio.Runner() as runner:
        try:
            for index, question in enumerate(QUESTIONS, 1):
                capture = StringIO()
                with redirect_stdout(capture):
                    event = _consume_astream_turn(agent, question, thread_id, runner=runner)
                biz = event.payload.get("business_result", {}) if event else {}
                data = biz.get("data", {})
                execution = biz.get("execution", {})
                citations = data.get("citations", [])
                backcheck = []
                for citation in citations:
                    try:
                        rows = _get_rag()._store_manager.collection.get(
                            settings.collection_name, ids=[citation["chunk_id"]],
                            output_fields=["text", "chunk_uid"], timeout=10)
                        backcheck.append(bool(rows) and rows[0].get("chunk_uid") == citation["chunk_uid"]
                                         and rows[0].get("text") == citation["text"])
                    except Exception:
                        backcheck.append(False)
                expected_tools = {"query_company_info", "search_public_kb" if index < 3 else "query_company_penalty"}
                task_ids = {t["task_id"] for t in data.get("task_results", [])}
                checks = {
                    "normal_terminal": event is not None and event.type == EventType.FINAL,
                    "two_tasks": task_ids == {"t1", "t2"},
                    "expected_tools": expected_tools <= set(execution.get("tools", [])),
                    "complete": biz.get("execution_status") == "complete",
                    "citation_rules": index == 3 or data.get("citation_validation", {}).get("all_passed") is True,
                    "milvus_backcheck": index == 3 or bool(backcheck) and all(backcheck),
                }
                case = {"id": index, "query": question, "checks": checks, "passed": all(checks.values()),
                        "business_result": biz, "console": capture.getvalue()}
                report["cases"].append(case)
                print(json.dumps({"id": index, "status": biz.get("execution_status"),
                                  "checks": checks, "execution": execution}, ensure_ascii=False), flush=True)
        finally:
            runner.run(agent.aclose())
    report["passed"] = all(c["passed"] for c in report["cases"])
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
