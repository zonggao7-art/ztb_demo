"""
Router — 意图路由节点。

核心设计：
  - 优先尝试 with_structured_output；若 API 不支持则回退到 Tool Calling
  - 两种方案均强制枚举输出，不解新自由文本
  - 携带最近 3 轮对话历史（6 条消息），避免指代分类失准
  - 所有异常兜底 → fallback
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import tool

from .state import AgentState

logger = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════
# 路由枚举 — 新增分支只需加一个 Literal 值
# ═════════════════════════════════════════════════════════

RouterIntent = Literal[
    "knowledge_qa",
    "price_inquiry",
    "general_chat",
    "doc_qa",
    "fallback",
]


class RouterDecision(BaseModel):
    """路由决策 — LLM 强制按此 schema 输出。"""
    intent: RouterIntent = Field(description="用户意图分类")
    reason: str = Field(description="分类理由，4~8 字")


# ═════════════════════════════════════════════════════════
# Tool Calling 回退方案 — 5 个虚拟 tool，tool 名称即意图
# ═════════════════════════════════════════════════════════

@tool
def route_knowledge_qa(reason: str) -> str:
    """招投标专业知识问答：法律法规、招标方式、评标规则、投标流程、履约验收等。

    触发：用户询问招投标法规、程序、资格、责任等专业知识。"""
    return "knowledge_qa"


@tool
def route_price_inquiry(reason: str) -> str:
    """结构化业务数据查询（三大核心能力）：企业工商信息、风控黑名单、招投标中标情报。

    触发场景：
    - 查企业详情/工商信息/资质/经营状态/行业/注册资本 → 企业工商情报查询
    - 查企业不良记录/处罚/违法/黑名单/信用 → 企业风控黑名单查询
    - 查企业中标记录/采购项目/招标历史 → 招投标中标情报查询
    - 查某个项目的中标情况/中标供应商/中标金额 → 招投标中标情报查询
    - 查询供应商推荐/找几个公司/哪些公司 → 企业推荐"""
    return "price_inquiry"


@tool
def route_general_chat(reason: str) -> str:
    """通用对话：问候、自我介绍、功能咨询、操作引导等非业务聊天。

    触发：打招呼、问能力、闲聊。"""
    return "general_chat"


@tool
def route_doc_qa(reason: str) -> str:
    """文档问答：上传文件后要求分析、解读、对比、提取文档内容。

    触发：提到上传文件、分析文档、解读合同、对比标书。"""
    return "doc_qa"


@tool
def route_fallback(reason: str) -> str:
    """兜底引导：意图无法确定、模棱两可时使用。"""
    return "fallback"


ROUTER_TOOLS = [
    route_knowledge_qa,
    route_price_inquiry,
    route_general_chat,
    route_doc_qa,
    route_fallback,
]

_TOOL_INTENT_MAP: dict[str, str] = {
    "route_knowledge_qa":  "knowledge_qa",
    "route_price_inquiry": "price_inquiry",
    "route_general_chat":  "general_chat",
    "route_doc_qa":        "doc_qa",
    "route_fallback":      "fallback",
}


# ═════════════════════════════════════════════════════════
# 路由提示词
# ═════════════════════════════════════════════════════════

ROUTER_SYSTEM_PROMPT = """你是招投标智能助手的意图路由器。请将用户输入分类到以下意图之一：

- knowledge_qa: 招投标专业知识问答（法律法规、招标方式、评标规则、投标流程、履约验收等）
- price_inquiry: 结构化业务数据查询，包括以下场景：
  · 企业不良记录/处罚/违法/黑名单/信用查询
  · 企业中标记录/采购项目/招标历史查询
  · 供应商推荐/企业详情/资质查询
- general_chat: 通用对话（问候、自我介绍、功能咨询等非业务聊天）
- doc_qa: 文档问答（上传文件后要求分析、解读、对比文档内容）
- fallback: 意图无法确定时使用

=== 路由示例 ===
用户: "评标委员会怎么组成？" → knowledge_qa
用户: "XX公司有没有不良记录？" → price_inquiry
用户: "XX公司被处罚过吗？" → price_inquiry
用户: "XX公司中标了哪些项目？" → price_inquiry
用户: "你能做什么？" → general_chat
用户: "帮我分析这个文档" → doc_qa
"""


ROUTER_USER_TEMPLATE = """【对话历史】
{history}

【当前用户输入】
{user_input}

请调用对应的路由工具。结合历史判断真实意图，不确定时用 route_fallback。"""


# ═════════════════════════════════════════════════════════
# Router 节点
# ═════════════════════════════════════════════════════════

def _format_history(messages: list, max_turns: int = 3) -> str:
    """格式化最近 N 轮对话为文本。"""
    recent = messages[-(max_turns * 2):]
    if not recent:
        return "（无历史对话）"
    lines: list[str] = []
    for msg in recent:
        role = "用户" if isinstance(msg, HumanMessage) else "助手"
        lines.append(f"{role}: {str(msg.content)[:150]}")
    return "\n".join(lines)


def _route_via_tool_calling(
    llm: BaseChatModel, history_str: str, user_input: str
) -> str:
    """通过 Tool Calling 实现路由。"""
    llm_with_tools = llm.bind_tools(ROUTER_TOOLS, tool_choice="required")
    response = llm_with_tools.invoke([
        SystemMessage(content=ROUTER_SYSTEM_PROMPT),
        HumanMessage(content=ROUTER_USER_TEMPLATE.format(history=history_str, user_input=user_input)),
    ])
    tool_calls = getattr(response, "tool_calls", None) or []
    if tool_calls:
        tool_name = tool_calls[0].get("name", "")
        intent = _TOOL_INTENT_MAP.get(tool_name, "fallback")
        logger.info("路由(tool): intent=%s", intent)
        return intent
    logger.warning("Tool Calling 无结果，降级 fallback")
    return "fallback"


def _route_via_structured_output(
    llm: BaseChatModel, history_str: str, user_input: str
) -> str:
    """通过 with_structured_output 实现路由。"""
    structured_llm = llm.with_structured_output(RouterDecision)
    decision: RouterDecision = structured_llm.invoke([
        SystemMessage(content=ROUTER_SYSTEM_PROMPT),
        HumanMessage(content=ROUTER_USER_TEMPLATE.format(history=history_str, user_input=user_input)),
    ])
    logger.info("路由(structured): intent=%s", decision.intent)
    return decision.intent


def build_router_node(llm: BaseChatModel):
    """构建路由节点。

    策略：预检测 structured_output 支持性，不支持则用 Tool Calling。

    Args:
        llm: 用于路由判断的 LLM（temperature=0）

    Returns:
        router_node 函数: (AgentState) → dict
    """
    # 预检测 structured_output 支持性
    tool_fallback = False
    try:
        test_llm = llm.with_structured_output(RouterDecision)
        test_llm.invoke("测试")
    except Exception as e:
        err = str(e).lower()
        if any(k in err for k in ("response_format", "unavailable", "not supported")):
            logger.info("Router: 使用 Tool Calling 回退")
            tool_fallback = True

    # 用 dict 包装可变状态（避免 nonlocal 问题）
    state = {"tool_fallback": tool_fallback}

    def router_node(agent_state: AgentState) -> dict:
        messages = agent_state.get("messages", [])
        if not messages:
            return {"router_intent": "fallback"}

        history_str = _format_history(messages, max_turns=3)
        user_input = str(messages[-1].content)

        try:
            if state["tool_fallback"]:
                intent = _route_via_tool_calling(llm, history_str, user_input)
            else:
                try:
                    intent = _route_via_structured_output(llm, history_str, user_input)
                except Exception as e:
                    err = str(e).lower()
                    if any(k in err for k in ("response_format", "unavailable", "not supported")):
                        state["tool_fallback"] = True
                        logger.info("Router: 运行时切换到 Tool Calling")
                        intent = _route_via_tool_calling(llm, history_str, user_input)
                    else:
                        raise
            return {"router_intent": intent}
        except Exception as e:
            logger.error("路由失败 → fallback: %s", e)
            return {"router_intent": "fallback"}

    return router_node
