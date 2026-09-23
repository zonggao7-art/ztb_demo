"""Read-only route diagnosis; no business tools run and no production prompts change."""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

QUESTION = "查询一下合肥市百益通商贸有限公司和合肥达斯辉机电设备有限公司的工商信息"
INCIDENT_HISTORY = [
    '查一下合肥盈拓成电子科技有限公司的工商信息，再告诉我招标的方式有哪些？",',
    "查一下合肥盈拓成电子科技有限公司的工商信息，再查询一下这家公司的处罚信息",
]


class ObservedRouterModel:
    manages_run_budget = True

    def __init__(self, model):
        self.model = model
        self.attempts = []

    def with_structured_output(self, schema, **kwargs):
        from langchain_core.callbacks import BaseCallbackHandler
        from langchain_core.runnables import RunnableLambda
        parser = self.model.with_structured_output(schema, **kwargs)

        async def observe(messages):
            attempt = {"error_type": None,
                       "schema_errors": [], "decision_arguments": []}
            class Capture(BaseCallbackHandler):
                def on_llm_end(self, response, **callback_kwargs):
                    # Observe the existing parser path; do not replace it with include_raw.
                    for generations in response.generations:
                        for generation in generations:
                            for call in getattr(generation.message, "tool_calls", []):
                                if call.get("name") == schema.__name__:
                                    attempt["decision_arguments"].append(call.get("args"))
            self.attempts.append(attempt)
            try:
                return await parser.ainvoke(messages, config={"callbacks": [Capture()]})
            except Exception as error:
                attempt["error_type"] = type(error).__name__
                if hasattr(error, "errors"):
                    attempt["schema_errors"] = [
                        {"loc": e["loc"], "type": e["type"], "msg": e["msg"]}
                        for e in error.errors(include_input=False, include_url=False)]
                raise
        return RunnableLambda(observe)


async def diagnose(trials, question=QUESTION, incident_history=False):
    from langchain_core.messages import AIMessage, HumanMessage
    from agent.execution.context import RunContext, RunStopped, use_run
    from agent.execution.router import decide
    from agent.tools import get_enabled_tools
    from public_kb.config import Settings
    from public_kb.llm_factory import create_llm
    settings = Settings()
    tools = get_enabled_tools(settings=settings)  # Schema registration only; never invoked.
    report = {"created_at": datetime.now(timezone.utc).isoformat(), "question": question,
              "history": INCIDENT_HISTORY if incident_history else [],
              "model": settings.llm_model, "business_tools_executed": 0, "trials": []}
    for index in range(trials):
        model = ObservedRouterModel(create_llm(settings, temperature=0.0))
        ctx = RunContext(settings, question, f"router-diagnosis-{index}")
        ctx.set_phase("router", settings.agent_router_timeout_s)
        stages = []
        ctx.sink = lambda stage, payload: stages.append({"stage": stage, **payload})
        trial = {"id": index + 1, "stages": stages, "attempts": model.attempts}
        messages = []
        for prior in INCIDENT_HISTORY if incident_history else []:
            # decide() reads the user text and completion marker, not assistant content.
            messages.extend([HumanMessage(content=prior), AIMessage(content="[completed]")])
        messages.append(HumanMessage(content=question))
        with use_run(ctx):
            try:
                decision = await decide(ctx, model, messages, tools)
                trial["decision"] = decision.model_dump()
            except Exception as exc:
                trial["failure"] = str(exc) if isinstance(exc, RunStopped) else type(exc).__name__
        trial["model_calls"] = ctx.model_calls
        trial["router_sources"] = ctx.sources
        report["trials"].append(trial)
        print(json.dumps(trial, ensure_ascii=False), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="最多每轮两次真实模型调用；不执行 SQL/RAG")
    parser.add_argument("--trials", type=int, choices=range(1, 4), default=3)
    parser.add_argument("--question", default=QUESTION)
    parser.add_argument("--incident-history", action="store_true", help="重放用户补充的前两轮问题；不重查业务数据")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.live:
        print("未指定 --live，不调用任何服务。")
        return
    report = asyncio.run(diagnose(args.trials, args.question, args.incident_history))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
