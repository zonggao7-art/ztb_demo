"""Validate review corpus; optionally evaluate the real router (read-only, API cost).

No flag: offline corpus validation, not an LLM accuracy measurement.
--live-router: semantic routing only; never invokes SQL/RAG business tools.
--require-reviewed: fail if even one sample has no human sign-off.
"""
from __future__ import annotations
import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_cases(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cases = data["cases"]
    if len({c["id"] for c in cases}) != len(cases):
        raise ValueError("duplicate case id")
    for case in cases:
        if not case["query"].strip() or not set(case["acceptable_modes"]) <= {"static", "react", "clarify", "unsupported"}:
            raise ValueError("invalid case")
    return cases


async def evaluate(cases):
    from langchain_core.messages import AIMessage, HumanMessage
    from public_kb.config import Settings
    from public_kb.llm_factory import create_llm
    from agent.execution.context import RunContext, use_run
    from agent.execution.router import decide
    from agent.tools import get_enabled_tools
    settings = Settings()
    model = create_llm(settings, temperature=0.0)
    tools = get_enabled_tools(settings=settings)
    results = []
    for case in cases:
        ctx = RunContext(settings, case["query"], "evaluation")
        ctx.set_phase("router", settings.agent_router_timeout_s)
        with use_run(ctx):
            try:
                history = []
                for q in case["history"]:
                    history.extend([HumanMessage(content=q), AIMessage(content="已完成上一轮答复。")])
                decision = await decide(ctx, model, history + [HumanMessage(content=case["query"])], tools)
                caps = {t.capability for t in decision.tasks}
                passed = (decision.execution_mode in case["acceptable_modes"]
                          and set(case["required_capabilities"]) <= caps)
                result = {"id": case["id"], "passed": passed, "mode": decision.execution_mode,
                          "capabilities": sorted(caps), "reason_code": decision.reason_code}
            except Exception:
                result = {"id": case["id"], "passed": False, "error": "router_failed"}
            results.append({**result, "model_attempts": ctx.model_calls})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=ROOT / "test/fixtures/react_eval_cases.json")
    parser.add_argument("--live-router", action="store_true")
    parser.add_argument("--require-reviewed", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    cases = load_cases(args.cases)
    reviewed = sum(bool(c.get("reviewed_by") and c.get("reviewed_at")) for c in cases)
    report = {"corpus_size": len(cases), "categories": dict(Counter(c["category"] for c in cases)),
              "human_reviewed": reviewed, "production_gate_passed": False,
              "note": "离线校验不代表路由准确率；真实业务证据、只读账户、认证与人工验收另行核验。"}
    if args.live_router:
        selected = cases[:args.limit] if args.limit else cases
        report["routing_results"] = asyncio.run(evaluate(selected))
        report["routing_passed"] = sum(r["passed"] for r in report["routing_results"])
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if args.require_reviewed and reviewed != len(cases) else 0


if __name__ == "__main__":
    raise SystemExit(main())
