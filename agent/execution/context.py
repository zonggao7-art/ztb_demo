"""Trusted request context. Never serialized into prompts or checkpoint state."""
from __future__ import annotations

import json
import hashlib
import logging
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)
_RUN: ContextVar[RunContext | None] = ContextVar("execution_run", default=None)
_TOOL_END: ContextVar[float] = ContextVar("execution_tool_end", default=float("inf"))


class RunStopped(RuntimeError):
    """Finite reason code only: safe to return without leaking exception details."""


@dataclass
class RunContext:
    settings: Any
    question: str
    thread_id: str
    timeout_s: float | None = None
    architecture: str = "hybrid"
    request_id: str = field(default_factory=lambda: uuid4().hex)
    started: float = field(default_factory=time.monotonic)
    phase: str = "router"
    phase_end: float = float("inf")
    model_calls: int = 0
    tool_attempts: int = 0
    tool_executions: int = 0
    attempted_tools: list[str] = field(default_factory=list)
    executed_tools: list[str] = field(default_factory=list)
    inflight: int = 0
    cancelled: bool = False
    decision: Any = None
    sources: dict[str, str] = field(default_factory=dict)
    ledger: Any = None
    receipts: list[dict] = field(default_factory=list)
    cache: dict[str, Any] = field(default_factory=dict)
    failures: dict[str, int] = field(default_factory=dict)
    failure_code: str | None = None
    fallback_branch: str | None = None
    sink: Any = None
    lock: Any = field(default_factory=threading.RLock, repr=False)

    def __post_init__(self):
        limit = self.settings.agent_request_timeout_s
        if self.timeout_s is not None:
            if self.timeout_s <= 0:
                raise ValueError("deadline_s must be positive")
            limit = min(limit, self.timeout_s)
        self.ends_at = self.started + limit
        self.sources["u0"] = self.question

    def set_phase(self, phase: str, seconds: float):
        self.phase = phase
        self.phase_end = min(self.ends_at, time.monotonic() + seconds)

    def remaining(self) -> float:
        return max(0.0, min(self.ends_at, self.phase_end, _TOOL_END.get()) - time.monotonic())

    def check(self):
        if self.cancelled:
            raise RunStopped("cancelled")
        if self.remaining() <= 0:
            raise RunStopped("deadline_exceeded")

    def reserve_model(self):
        with self.lock:
            self.check()
            if self.model_calls >= self.settings.agent_max_model_calls:
                raise RunStopped("model_budget")
            self.model_calls += 1

    def reserve_tool(self, tool_name: str | None = None, *, enforce_attempt_budget: bool = True):
        with self.lock:
            self.check()
            if enforce_attempt_budget and self.tool_attempts >= self.settings.agent_max_tool_calls:
                raise RunStopped("tool_budget")
            self.tool_attempts += 1
            if tool_name:
                self.attempted_tools.append(tool_name)

    def record_tool_execution(self, tool_name: str, *, enforce_execution_budget: bool = False):
        with self.lock:
            self.check()
            if enforce_execution_budget and self.tool_executions >= self.settings.agent_max_tool_calls:
                raise RunStopped("tool_budget")
            self.tool_executions += 1
            self.executed_tools.append(tool_name)

    def check_prompt(self, content: Any, *, scope: str = "agent"):
        # UTF-8 byte count is deliberately conservative; does not guess the
        # tokenizer of an arbitrary OpenAI-compatible model.
        size = len(json.dumps(content, ensure_ascii=False, default=str).encode("utf-8"))
        if scope not in {"agent", "rag"}:
            raise ValueError("unknown input budget scope")
        limit = (self.settings.agent_rag_max_input_bytes if scope == "rag"
                 else self.settings.agent_max_input_bytes)
        if size > limit:
            self.stage("input_limit", bytes=size, limit=limit, scope=scope)
            raise RunStopped("context_budget")

    def stage(self, stage: str, **payload):
        if self.sink is not None and not self.cancelled:
            self.sink(stage, payload)

    def summary(self) -> dict:
        return {
            "request_id": self.request_id, "mode": self.architecture,
            "policy_version": "agent-loop-v2" if self.architecture == "unified" else "react-v1",
            "model": self.settings.llm_model,
            "model_calls": self.model_calls, "tool_attempts": self.tool_attempts,
            "tool_executions": self.tool_executions,
            "attempted_tools": list(self.attempted_tools),
            "executed_tools": list(self.executed_tools),
            "failure_code": self.failure_code,
            "inflight": self.inflight,
            "react_fallback": self.fallback_branch is not None,
            "actual_branch": self.fallback_branch or getattr(self.decision, "static_branch", None)
                             or getattr(self.decision, "execution_mode", None)
                             or getattr(self.decision, "status", "fallback"),
            "elapsed_s": round(time.monotonic() - self.started, 3),
            "reason_code": getattr(self.decision, "reason_code", None)
                           or ("AGENT_FINISH" if self.architecture == "unified" and self.decision is not None
                               else "AGENT_FAILED" if self.architecture == "unified" else "ROUTER_FAILED"),
            # Compatibility field used by the CLI: report real adapter entries,
            # while attempted_tools keeps rejected proposals visible for audits.
            "tools": list(self.executed_tools),
        }

    def audit(self, status: str):
        unified = self.architecture == "unified"
        logger.info("execution_audit %s", json.dumps({
            **self.summary(), "status": status,
            "evidence_ids": list(self.ledger.entries) if self.ledger is not None else [],
            "schema_version": "execution-v2/finish-v1" if unified else "execution-v1/answer-v1",
            "prompt_version": "unified-agent-v2" if unified else "react-router-v1",
            "question_hash": hashlib.sha256(self.question.encode()).hexdigest(),
            "receipts": [{k: r[k] for k in (
                "task_id", "step_id", "tool", "ok", "code", "outcome")
                          if k in r} for r in self.receipts],
        }, ensure_ascii=False))


def current_run() -> RunContext | None:
    return _RUN.get()


@contextmanager
def use_run(ctx: RunContext):
    token = _RUN.set(ctx)
    try:
        yield ctx
    finally:
        _RUN.reset(token)


@contextmanager
def tool_window(seconds):
    """Workers inherit this immutable deadline even after the parent starts finalization."""
    token = _TOOL_END.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _TOOL_END.reset(token)
