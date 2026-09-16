"""
Graph — StateGraph 构建与编译。

核心骨架层，负责：
  - 构建 StateGraph("agent") 主流程
  - 实现 _with_fallback 全局异常兜底
  - 注册所有业务节点和条件边
  - 注入 Checkpointer 并编译
  - 提供 AgentGraph 统一入口
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import logging
import time
import threading
from contextlib import aclosing, AsyncExitStack
from uuid import uuid4
from typing import Any, AsyncIterator, Callable, Dict, Iterator, Optional

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.base import BaseCheckpointSaver
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage

from public_kb.config import Settings as PublicKBSettings
from public_kb.llm_factory import create_llm

from .state import AgentState
from .router import build_router_node
from .checkpointer import create_checkpointer
from .streaming import EventType, make_error_event, make_event, normalize_custom_event
from .streaming.context import (
    _REQUEST_ID,
    _STREAM_ACTIVE,
    bind_request,
    current_request_id,
    emit,
)
from .nodes import (
    node_knowledge_qa,
    node_price_inquiry,
    node_general_chat,
    node_doc_qa,
    node_fallback,
)

logger = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════
# 全局异常兜底包装器
# ═════════════════════════════════════════════════════════

_BRANCH_LABELS: Dict[str, str] = {
    "node_knowledge_qa":  "专业知识问答",
    "node_price_inquiry": "智能询价",
    "node_general_chat":  "通用对话",
    "node_doc_qa":        "文档问答",
}


def _fallback_result(node_name: str, e: Exception) -> dict:
    """构造节点级兜底降级返回（同步/异步包装器共用）。"""
    label = _BRANCH_LABELS.get(node_name.removesuffix("_async"), "当前")
    logger.error("节点 [%s] 执行失败: %s", node_name, e, exc_info=True)

    error_msg = (
        f"抱歉，「{label}」功能暂时不可用，请稍后重试或尝试其他问题。\n\n"
        f"您可以：\n"
        f"  📚 咨询招投标法规知识\n"
        f"  💰 查询历史中标价格\n"
        f"  💬 进行通用对话"
    )

    return {
        "business_result": {
            "branch": "fallback",
            "answer": error_msg,
            "data": {
                "error": str(e)[:200],
                "failed_branch": node_name,
            },
        },
        "messages": [AIMessage(content=error_msg)],
    }


def _with_fallback(node_fn: Callable) -> Callable:
    """包装业务节点：任何未捕获异常 → 自动降级返回友好提示。

    包装后的节点仍然符合统一接口: (AgentState) → dict

    Args:
        node_fn: 原始业务节点函数

    Returns:
        包装后的节点函数
    """

    @functools.wraps(node_fn)
    def wrapped(state: AgentState) -> dict:
        try:
            return node_fn(state)
        except Exception as e:
            return _fallback_result(getattr(node_fn, "__name__", "unknown"), e)

    return wrapped


def _with_fallback_async(node_fn: Callable) -> Callable:
    """异步版兜底包装器（阶段 2）— 行为与 _with_fallback 完全一致。

    包装后的节点符合统一接口: async (AgentState) → dict
    """
    @functools.wraps(node_fn)
    async def wrapped(state: AgentState) -> dict:
        try:
            return await node_fn(state)
        except Exception as e:
            result = _fallback_result(getattr(node_fn, "__name__", "unknown"), e)
            if _STREAM_ACTIVE.get():
                # 降级路径也要给流式消费方终态，否则客户端只能报"流未正常结束"
                answer = str(result["business_result"]["answer"])
                emit(EventType.ERROR, {
                    "code": "node_failed", "message": answer, "retryable": True,
                    "business_result": {"branch": "fallback"},
                })
            return result

    return wrapped


# ═════════════════════════════════════════════════════════
# 路由条件函数
# ═════════════════════════════════════════════════════════

def _route_by_intent(state: AgentState) -> str:
    """条件边路由：直接返回 router_intent 枚举值。"""
    return state.get("router_intent", "fallback")


# ═════════════════════════════════════════════════════════
# 图构建
# ═════════════════════════════════════════════════════════

def build_graph(
    *,
    llm: Optional[BaseChatModel] = None,
    checkpointer: Optional[BaseCheckpointSaver] = None,
    async_nodes: bool = False,
) -> Any:
    """构建并编译 StateGraph。

    Args:
        llm: 对话模型实例。若为 None 则自动从配置创建。
        checkpointer: 检查点存储器。若为 None 则使用 MemorySaver。

    Returns:
        编译后的 CompiledStateGraph，可直接 invoke。
    """
    if llm is None:
        settings = PublicKBSettings()
        llm = create_llm(settings, temperature=0)
        logger.info("LLM 初始化: model=%s timeout=%ds max_retries=%d",
                     settings.llm_model, settings.llm_timeout, settings.llm_max_retries)

    if checkpointer is None:
        checkpointer = create_checkpointer(PublicKBSettings().checkpointer_backend)

    # ── 创建图 ──
    graph = StateGraph(AgentState)

    # ── 注册节点 ──
    # Router（不需要 _with_fallback，自身有异常处理；阶段 1 起支持异步版）
    if async_nodes:
        from .router import build_router_node_async
        router_node = build_router_node_async(llm)
    else:
        router_node = build_router_node(llm)
    graph.add_node("router", router_node)

    # 业务节点 — 统一包裹 _with_fallback
    # knowledge_qa 在异步图下走 async RAG 链路（阶段 2）；其余节点阶段 3+ 逐步异步化
    if async_nodes:
        from .nodes.knowledge_qa_async import node_knowledge_qa_async
        graph.add_node("knowledge_qa", _with_fallback_async(node_knowledge_qa_async))
    else:
        graph.add_node("knowledge_qa", _with_fallback(node_knowledge_qa))
    if async_nodes:
        from .nodes.price_inquiry.node_async import node_price_inquiry_async
        graph.add_node("price_inquiry", _with_fallback_async(node_price_inquiry_async))
    else:
        graph.add_node("price_inquiry", _with_fallback(node_price_inquiry))
    if async_nodes:
        from .nodes.general_chat import node_general_chat_async
        from .nodes.doc_qa import node_doc_qa_async
        graph.add_node("general_chat", _with_fallback_async(node_general_chat_async))
        graph.add_node("doc_qa", _with_fallback_async(node_doc_qa_async))
    else:
        graph.add_node("general_chat", _with_fallback(node_general_chat))
        graph.add_node("doc_qa", _with_fallback(node_doc_qa))
    # fallback 本身已是兜底，不包装
    if async_nodes:
        from .nodes.fallback import node_fallback_async
        graph.add_node("fallback", node_fallback_async)
    else:
        graph.add_node("fallback", node_fallback)

    # ── 边 ──
    # START → router
    graph.set_entry_point("router")

    # router → 条件边分发到业务节点
    graph.add_conditional_edges(
        "router",
        _route_by_intent,
        {
            "knowledge_qa": "knowledge_qa",
            "price_inquiry": "price_inquiry",
            "general_chat": "general_chat",
            "doc_qa": "doc_qa",
            "fallback": "fallback",
        },
    )

    # 所有业务节点 → END（直接终止，无中间 format_output 层）
    graph.add_edge("knowledge_qa", END)
    graph.add_edge("price_inquiry", END)
    graph.add_edge("general_chat", END)
    graph.add_edge("doc_qa", END)
    graph.add_edge("fallback", END)

    # ── 编译 ──
    compiled = graph.compile(checkpointer=checkpointer)
    logger.info("StateGraph 编译完成，checkpointer=%s", type(checkpointer).__name__)

    return compiled


# ═════════════════════════════════════════════════════════
# AgentGraph — 对外统一入口
# ═════════════════════════════════════════════════════════

class AgentGraph:
    """招投标智能助手 Agent 对外入口。

    Usage:
        agent = AgentGraph()
        result = agent.invoke("招标方式有哪些？")
        print(result["answer"])
    """

    def __init__(
        self,
        *,
        llm: Optional[BaseChatModel] = None,
        checkpointer_backend: Optional[str] = None,
        async_enabled: Optional[bool] = None,
        settings: Optional[PublicKBSettings] = None,
        tools: Optional[list] = None,
    ) -> None:
        """初始化 Agent。

        Args:
            llm: 对话模型。None 则按 Settings 自动创建（OpenRouter，见 public_kb.llm_factory）。
            checkpointer_backend: 记忆后端，可选 "memory" | "sqlite" | "postgres" | "redis"。
                None 则从 .env（CHECKPOINTER_BACKEND）读取，默认 "memory"。
            async_enabled: 是否启用异步图（router 用 ainvoke）。
                None 时从 Settings.async_backend_enabled 读取（默认 False）。
        """
        # 初始化运行时基础设施（线程池 + 信号量）
        from .runtime import init_runtime_from_settings
        settings = settings or PublicKBSettings()
        init_runtime_from_settings(settings)

        self._settings = settings
        self._tools = tools
        self._hybrid_graph = None
        self._unified_graph = None
        self._busy = set()
        self._busy_lock = threading.Lock()
        self._exit_stack = AsyncExitStack()
        self._init_lock = asyncio.Lock()
        self._sync_runner = None
        self._llm = llm
        self._async_enabled = (
            async_enabled if async_enabled is not None
            else settings.async_backend_enabled
        )
        backend = checkpointer_backend or settings.checkpointer_backend
        connection_string = None
        if backend == "postgres":
            connection_string = settings.checkpointer_postgres_dsn or None
        elif backend == "sqlite":
            connection_string = settings.checkpointer_sqlite_path
        self._backend, self._connection_string = backend, connection_string
        self._checkpointer = create_checkpointer(backend, connection_string=connection_string) if backend == "memory" else None
        self._graph = build_graph(
            llm=self._llm,
            checkpointer=self._checkpointer,
            async_nodes=self._async_enabled,
        ) if self._checkpointer is not None else None
        logger.info(
            "AgentGraph 就绪 (async=%s, checkpointer=%s)",
            self._async_enabled,
            type(self._checkpointer).__name__,
        )

    def invoke(self, question: str, thread_id: str = "default", *, deadline_s: Optional[float] = None) -> Dict[str, Any]:
        """单次问答（同步入口，内部委托 ainvoke）。

        Args:
            question: 用户问题
            thread_id: 会话线程 ID（用于多轮对话记忆隔离）
            deadline_s: 总超时（秒），None = 不设上限。

        Returns:
            {"answer": str, "intent": str, "business_result": dict}

        Raises:
            RuntimeError: 如果调用时已有事件循环在跑，应改用 ainvoke。
        """
        logger.info("用户提问(sync): hash=%s", hashlib.sha256(question.encode()).hexdigest()[:16])

        # §3.3 契约：如有运行中的 loop，提示用户改用 ainvoke
        try:
            asyncio.get_running_loop()
            raise RuntimeError(
                "检测到已运行的事件循环；请改用 await agent.ainvoke(...)"
            )
        except RuntimeError as e:
            if "改用" in str(e):
                raise
            # 正常路径：没找到 loop，继续走 asyncio.run
        if self._sync_runner is None:
            self._sync_runner = asyncio.Runner()
        return self._sync_runner.run(self.ainvoke(question, thread_id, deadline_s=deadline_s))

    async def ainvoke(
        self, question: str, thread_id: str = "default", *, deadline_s: Optional[float] = None
    ) -> Dict[str, Any]:
        """单次问答（异步入口）。

        每轮清空 business_result，保留对话消息；总超时覆盖整个图执行。
        async_enabled 决定使用异步节点，还是由 LangGraph 在线程池执行同步节点。
        """
        logger.info("用户提问(async=%s): hash=%s", self._async_enabled, hashlib.sha256(question.encode()).hexdigest()[:16])

        from .execution.service import execution_path
        path = execution_path(self._settings, thread_id)
        controlled = path != "legacy"
        limit = min(deadline_s or self._settings.agent_request_timeout_s,
                    self._settings.agent_request_timeout_s) if controlled else deadline_s
        started = time.monotonic()
        async with asyncio.timeout(limit):
            graph, path = await self._select_graph(thread_id)
            if path != "legacy":
                self._claim(thread_id)
            try:
                remaining = max(.001, limit - (time.monotonic() - started)) if limit else None
                result = await graph.ainvoke(
                    {"messages": [HumanMessage(content=question)], "business_result": {}},
                    config={"configurable": {"thread_id": thread_id}, "metadata": {"deadline_s": remaining}},
                )
            finally:
                if path != "legacy":
                    self._release(thread_id)

        messages = result.get("messages", [])
        answer = str(messages[-1].content) if messages else ""
        return {
            "answer": answer,
            "intent": result.get("router_intent", "unknown"),
            "business_result": result.get("business_result", {}),
        }

    def stream(self, question: str, thread_id: str = "default") -> Iterator[Dict[str, Any]]:
        """流式问答（同步生成器；输出统一 StreamEvent envelope）。"""
        logger.info("流式提问(sync): %s", question[:100])
        yield from asyncio.run(self._consume_astream(question, thread_id, None))

    async def astream(
        self, question: str, thread_id: str = "default", *, deadline_s: Optional[float] = None
    ) -> AsyncIterator[Dict[str, Any]]:
        """流式问答（异步；只对外输出统一 StreamEvent）。"""
        from .execution.service import execution_path
        path = execution_path(self._settings, thread_id)
        if path != "legacy":
            async with aclosing(self._controlled_astream(question, thread_id, deadline_s, path)) as events:
                async for event in events:
                    yield event
            return
        await self._select_graph(thread_id)
        request_id = current_request_id() or uuid4().hex
        yield make_event(
            EventType.META, request_id,
            {"mode": "legacy", "question_hash": hashlib.sha256(question.encode()).hexdigest(), "started_at": time.time()},
        )
        bind_token = bind_request(request_id)
        terminal_event = None
        terminal_types = {EventType.FINAL, EventType.ERROR, EventType.CANCELLED}
        try:
            logger.info("流式提问(async): request_id=%s question=%s", request_id, question[:100])
            async with asyncio.timeout(deadline_s):
                async with aclosing(self._graph.astream(
                    {"messages": [HumanMessage(content=question)], "business_result": {}},
                    config={
                        "configurable": {"thread_id": thread_id},
                        "metadata": {
                            "deadline_s": deadline_s,
                            "stream_request_id": request_id,
                        },
                    },
                    stream_mode="custom",
                )) as events:
                    async for item in events:
                        event = normalize_custom_event(item, request_id)
                        if event.type is EventType.META or terminal_event is not None:
                            continue
                        if event.type in terminal_types:
                            terminal_event = event
                        else:
                            yield event
            if terminal_event is None:
                terminal_event = make_error_event(
                    request_id, "incomplete_stream", "流式请求未完整结束，请重试。",
                )
        except TimeoutError:
            terminal_event = make_error_event(
                request_id, "deadline_exceeded", "请求处理超时，请稍后重试。",
            )
        except asyncio.CancelledError:
            logger.warning("流式请求取消: request_id=%s thread_id=%s", request_id, thread_id)
            raise
        except Exception as exc:
            logger.exception("流式请求失败: request_id=%s", request_id)
            terminal_event = make_error_event(
                request_id, "agent_stream_failed", "流式请求失败，请稍后重试。", retryable=True,
            )
        finally:
            reset_token, active_token = bind_token
            _REQUEST_ID.reset(reset_token)
            _STREAM_ACTIVE.reset(active_token)
        yield terminal_event

    async def _consume_astream(self, question, thread_id, deadline_s):
        return [event async for event in self.astream(question, thread_id, deadline_s=deadline_s)]

    def get_state(self, thread_id: str = "default") -> Optional[AgentState]:
        """获取指定会话的当前状态。"""
        if self._backend != "memory":
            if self._sync_runner is None:
                raise RuntimeError("异步持久化会话请使用 await agent.aget_state(thread_id)")
            return self._sync_runner.run(self.aget_state(thread_id))
        config = {"configurable": {"thread_id": thread_id}}
        from .execution.service import build_hybrid_graph, execution_path
        path = execution_path(self._settings, thread_id)
        graph = self._graph
        if path == "hybrid":
            if self._hybrid_graph is None:
                self._hybrid_graph = build_hybrid_graph(
                    self._settings, self._checkpointer, self._llm, self._tools)
            graph = self._hybrid_graph
        elif path == "unified":
            from .execution.unified import build_unified_graph
            if self._unified_graph is None:
                self._unified_graph = build_unified_graph(
                    self._settings, self._checkpointer, self._llm, self._tools)
            graph = self._unified_graph
        state = graph.get_state(config)
        return state.values if state else None

    async def _select_graph(self, thread_id):
        from .execution.service import build_hybrid_graph, execution_path
        from .execution.unified import build_unified_graph
        if self._checkpointer is None:
            async with self._init_lock:
                if self._checkpointer is None:
                    from .checkpointer import open_async_checkpointer
                    self._checkpointer = await self._exit_stack.enter_async_context(
                        open_async_checkpointer(self._backend, self._connection_string))
                    self._graph = build_graph(llm=self._llm, checkpointer=self._checkpointer, async_nodes=True)
        path = execution_path(self._settings, thread_id)
        if path == "legacy":
            return self._graph, path
        if path == "hybrid":
            if self._hybrid_graph is None:
                self._hybrid_graph = build_hybrid_graph(self._settings, self._checkpointer, self._llm, self._tools)
            return self._hybrid_graph, path
        if self._unified_graph is None:
            self._unified_graph = build_unified_graph(self._settings, self._checkpointer, self._llm, self._tools)
        return self._unified_graph, path

    def _claim(self, thread_id):
        with self._busy_lock:
            if thread_id in self._busy:
                raise RuntimeError("session_busy")
            self._busy.add(thread_id)

    def _release(self, thread_id):
        with self._busy_lock:
            self._busy.discard(thread_id)

    async def aclose(self):
        await self._exit_stack.aclose()
        if self._backend != "memory":
            self._checkpointer = self._graph = self._hybrid_graph = self._unified_graph = None

    def close(self):
        """Close resources owned by synchronous callers on the same event loop."""
        if self._sync_runner is not None:
            self._sync_runner.run(self.aclose())
            self._sync_runner.close()
            self._sync_runner = None

    async def aget_state(self, thread_id="default"):
        graph, _ = await self._select_graph(thread_id)
        state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
        return state.values if state else None

    async def _controlled_astream(self, question, thread_id, deadline_s, path):
        request_id = current_request_id() or uuid4().hex
        yield make_event(EventType.META, request_id, {"mode": path, "started_at": time.time()})
        token = bind_request(request_id)
        claimed = False
        terminal = None
        limit = min(deadline_s or self._settings.agent_request_timeout_s, self._settings.agent_request_timeout_s)
        started = time.monotonic()
        try:
            self._claim(thread_id)
            claimed = True
            async with asyncio.timeout(limit):
                graph, _ = await self._select_graph(thread_id)
            remaining = max(.001, limit - (time.monotonic() - started))
            config = {"configurable": {"thread_id": thread_id},
                      "metadata": {"deadline_s": remaining, "stream_request_id": request_id}}
            async with asyncio.timeout(remaining), aclosing(graph.astream(
                    {"messages": [HumanMessage(content=question)], "business_result": {}},
                    config=config, stream_mode=["custom", "values"])) as events:
                result = None
                async for kind, item in events:
                    if kind == "custom":
                        event = normalize_custom_event(item, request_id)
                        if event.type is EventType.STAGE:
                            yield event
                    else:
                        result = item.get("business_result")
            if not result or "execution_status" not in result:
                raise RuntimeError("incomplete_stream")
            # All tokens/citations/tables below are derived from the committed verified result.
            answer = result["answer"]
            for offset in range(0, len(answer), 80):
                yield make_event(EventType.TOKEN, request_id, {"delta": answer[offset:offset + 80]})
            data = result.get("data") or {}
            if data.get("records"):
                yield make_event(EventType.TABLE, request_id, {"records": data["records"]})
            if data.get("citations"):
                yield make_event(EventType.CITATIONS, request_id, {"citations": data["citations"]})
            terminal = make_event(EventType.FINAL, request_id,
                                  {"answer": answer, "business_result": result, "intent": result["branch"]})
        except asyncio.CancelledError:
            raise
        except Exception:
            terminal = make_error_event(request_id, f"{path}_stream_failed", "请求未完成，请稍后重试。")
        finally:
            if claimed:
                self._release(thread_id)
            _REQUEST_ID.reset(token[0])
            _STREAM_ACTIVE.reset(token[1])
        yield terminal
