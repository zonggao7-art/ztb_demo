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

import functools
import logging
from typing import Any, Callable, Dict, Optional

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.base import BaseCheckpointSaver
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage

from public_kb.config import Settings as PublicKBSettings
from public_kb.services.llm import create_llm

from .state import AgentState
from .router import build_router_node
from .checkpointer import create_checkpointer
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
            node_name = getattr(node_fn, "__name__", "unknown")
            label = _BRANCH_LABELS.get(node_name, "当前")
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
        llm = create_llm(settings)
        logger.info("LLM 初始化: model=%s timeout=%ds max_retries=%d",
                     settings.llm_model, settings.llm_timeout, settings.llm_max_retries)

    if checkpointer is None:
        checkpointer = create_checkpointer("memory")

    # ── 创建图 ──
    graph = StateGraph(AgentState)

    # ── 注册节点 ──
    # Router（不需要 _with_fallback，自身有异常处理）
    router_node = build_router_node(llm)
    graph.add_node("router", router_node)

    # 业务节点 — 统一包裹 _with_fallback
    graph.add_node("knowledge_qa", _with_fallback(node_knowledge_qa))
    graph.add_node("price_inquiry", _with_fallback(node_price_inquiry))
    graph.add_node("general_chat", _with_fallback(node_general_chat))
    graph.add_node("doc_qa", _with_fallback(node_doc_qa))
    # fallback 本身已是兜底，不包装
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
        checkpointer_backend: str = "memory",
    ) -> None:
        """初始化 Agent。

        Args:
            llm: 对话模型。None 则自动创建 ChatOpenAI(deepseek-chat)。
            checkpointer_backend: 记忆后端，可选 "memory" | "sqlite" | "postgres" | "redis"
        """
        self._llm = llm
        self._checkpointer = create_checkpointer(checkpointer_backend)
        self._graph = build_graph(llm=self._llm, checkpointer=self._checkpointer)
        logger.info("AgentGraph 就绪")

    def invoke(self, question: str, thread_id: str = "default") -> Dict[str, Any]:
        """单次问答。

        Args:
            question: 用户问题
            thread_id: 会话线程 ID（用于多轮对话记忆隔离）

        Returns:
            {"answer": str, "intent": str, "business_result": dict}
        """
        logger.info("用户提问: %s", question[:100])

        result = self._graph.invoke(
            {"messages": [HumanMessage(content=question)]},
            config={"configurable": {"thread_id": thread_id}},
        )

        # 提取回答文本
        messages = result.get("messages", [])
        answer = str(messages[-1].content) if messages else ""

        business_result = result.get("business_result", {})

        return {
            "answer": answer,
            "intent": result.get("router_intent", "unknown"),
            "business_result": business_result,
        }

    def stream(self, question: str, thread_id: str = "default"):
        """流式问答（生成器）。"""
        logger.info("流式提问: %s", question[:100])

        for event in self._graph.stream(
            {"messages": [HumanMessage(content=question)]},
            config={"configurable": {"thread_id": thread_id}},
        ):
            yield event

    def get_state(self, thread_id: str = "default") -> Optional[AgentState]:
        """获取指定会话的当前状态。"""
        config = {"configurable": {"thread_id": thread_id}}
        state = self._graph.get_state(config)
        return state.values if state else None
