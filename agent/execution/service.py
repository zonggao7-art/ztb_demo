"""Request coordinator: isolated context, deadlines, verified publishing only."""
from __future__ import annotations
import asyncio
import hashlib
import time

from langchain_core.messages import AIMessage
from langgraph.graph import StateGraph, END
from langgraph.config import get_stream_writer
from agent.state import AgentState
from agent.streaming import make_event, EventType
from public_kb.llm_factory import create_llm

from .context import RunContext, RunStopped, use_run
from .evidence import EvidenceLedger
from .router import decide
from .executor import fixed_execute, react_execute
from .output import render, safe_failure, conservative_candidate, GENERAL


def hybrid_selected(settings, thread_id):
    if settings.agent_execution_mode != "hybrid":
        return False
    allowlist = {s.strip() for s in settings.agent_react_thread_allowlist.split(",") if s.strip()}
    bucket = int(hashlib.sha256(thread_id.encode()).hexdigest()[:8], 16) % 100
    return thread_id in allowlist or bucket < settings.agent_react_rollout_percent


def execution_path(settings, thread_id):
    """Resolve the configured top-level architecture for one request."""
    if settings.agent_execution_mode == "unified":
        return "unified"
    return "hybrid" if hybrid_selected(settings, thread_id) else "legacy"


async def run_request(ctx, llm, messages, tools):
    ctx.ledger = EvidenceLedger()
    with use_run(ctx):
        try:
            ctx.set_phase("router", ctx.settings.agent_router_timeout_s)
            ctx.stage("router_start")
            ctx.decision = await decide(ctx, llm, messages, tools)
            ctx.stage("router_done", mode=ctx.decision.execution_mode,
                      reason=ctx.decision.reason_code,
                      tasks=[{"task_id": t.task_id, "capability": t.capability}
                             for t in ctx.decision.tasks])
            decision = ctx.decision
            if decision.execution_mode in {"clarify", "unsupported"}:
                labels = {"company_name": "要查询的主体名称", "project_number": "项目编号",
                          "time_range": "明确时间范围", "amount_unit": "金额单位", "task_scope": "要查询的事项（如工商信息、处罚或中标历史）",
                          "entity": "明确查询对象", "evidence": "可核验的依据", "service": "可用数据服务"}
                answer = ("当前版本每轮独立处理，请在本次问题中补充：" + "、".join(labels[x] for x in decision.missing_fields) + "。"
                          if decision.execution_mode == "clarify" else GENERAL)
                return {"branch": "clarify" if decision.execution_mode == "clarify" else "fallback", "answer": answer, "data": {},
                        "execution_status": decision.execution_mode, "execution": ctx.summary()}
            if all(t.capability == "general_chat" for t in decision.tasks):
                return {"branch": "general_chat", "answer": GENERAL, "data": {},
                        "execution_status": "complete", "execution": ctx.summary()}
            execution_time = min(ctx.settings.agent_react_timeout_s,
                                 ctx.ends_at - time.monotonic() - ctx.settings.agent_finalize_reserve_s)
            if execution_time <= 0:
                raise RunStopped("deadline_exceeded")
            ctx.set_phase("execution", execution_time)
            ctx.stage("execution_start", mode=decision.execution_mode)
            tool_map = {t.name: t for t in tools}
            try:
                async with asyncio.timeout(ctx.remaining()):
                    candidate = (await fixed_execute(ctx, tool_map) if decision.execution_mode == "static"
                                 else await react_execute(ctx, llm, tool_map))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # No new tools on timeout/failed validation. Reuse verified snapshots only.
                ctx.failure_code = str(exc) if isinstance(exc, RunStopped) else "execution_failed"
                ctx.stage("execution_degraded", code=ctx.failure_code)
                caps = {t.capability for t in decision.tasks}
                fallback_branch = ("knowledge_qa" if caps == {"public_kb_qa"} else "price_inquiry"
                                   if caps <= {"company_registration", "company_business_scope",
                                               "company_penalty", "project_award",
                                               "company_award_history"} else None)
                eligible = (decision.execution_mode == "react" and ctx.failure_code == "unstructured_answer"
                            and fallback_branch and not any(t.depends_on for t in decision.tasks)
                            and not ctx.inflight and not any(not r["ok"] for r in ctx.receipts)
                            and ctx.remaining() > 0 and ctx.tool_attempts < ctx.settings.agent_max_tool_calls)
                if eligible:
                    ctx.fallback_branch = fallback_branch
                    ctx.stage("static_fallback", branch=fallback_branch)
                    try:
                        async with asyncio.timeout(ctx.remaining()):
                            candidate = await fixed_execute(ctx, tool_map, skip_completed=True)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        candidate = conservative_candidate(ctx)
                else:
                    candidate = conservative_candidate(ctx)
            ctx.set_phase("finalize", ctx.settings.agent_finalize_reserve_s)
            ctx.check()
            ctx.stage("validation_start")
            result = render(ctx, candidate)
            ctx.stage("validation_done")
            return result
        except asyncio.CancelledError:
            ctx.cancelled = True
            ctx.audit("cancelled")
            raise
        except Exception as exc:
            return safe_failure(ctx, str(exc) if isinstance(exc, RunStopped) else "verification_failed")


def build_hybrid_graph(settings, checkpointer, llm=None, tools=None):
    from agent.tools import get_enabled_tools
    model = llm or create_llm(settings, temperature=0.0)
    enabled = list(tools) if tools is not None else get_enabled_tools(settings=settings)

    async def execute(state: AgentState, config):
        from agent.streaming.context import current_request_id
        metadata = config.get("metadata", {})
        ctx = RunContext(settings, str(state["messages"][-1].content),
                         config.get("configurable", {}).get("thread_id", "default"),
                         timeout_s=metadata.get("deadline_s"))
        ctx.request_id = metadata.get("stream_request_id") or current_request_id() or ctx.request_id
        writer = get_stream_writer()
        ctx.sink = lambda stage, payload: writer(make_event(EventType.STAGE, ctx.request_id,
                                                           {"stage": stage, **payload}))
        result = await run_request(ctx, model, state["messages"], enabled)
        result["data"].setdefault("status", result["execution_status"])
        result["data"].setdefault("execution", result["execution"])
        if ctx.decision is not None:
            result["data"].setdefault("missing_fields", ctx.decision.missing_fields)
        ctx.audit(result["execution_status"])
        # Inner traces never enter the outer checkpointer. Only the verified final reply does.
        return {"router_intent": result["branch"], "business_result": result,
                "messages": [AIMessage(content=result["answer"])]}

    graph = StateGraph(AgentState)
    graph.add_node("controlled_execution", execute)
    graph.set_entry_point("controlled_execution")
    graph.add_edge("controlled_execution", END)
    return graph.compile(checkpointer=checkpointer)
