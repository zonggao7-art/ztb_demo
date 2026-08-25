"""
fallback — 兜底引导节点。

当意图不明或业务节点异常降级时，列出可用功能清单并引导用户。
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage

from ..state import AgentState

logger = logging.getLogger(__name__)


def node_fallback(state: AgentState) -> dict:
    """兜底引导节点。

    当路由无法判断意图或业务节点异常降级时触发。

    Args:
        state: AgentState

    Returns:
        {"business_result": {...}, "messages": [AIMessage]}
    """
    # 检查是否有异常降级信息
    biz = state.get("business_result", {})
    failed_branch = biz.get("data", {}).get("failed_branch", "") if isinstance(biz.get("data"), dict) else ""

    if failed_branch:
        # 来自 _with_fallback 异常降级
        answer = biz.get("answer", "")
        logger.warning("fallback: 异常降级，来源分支=%s", failed_branch)
        return {
            "business_result": {
                "branch": "fallback",
                "answer": answer,
                "data": {"failed_branch": failed_branch},
            },
            "messages": [AIMessage(content=answer)],
        }

    # 意图不明，列出可用功能
    answer = (
        "🤔 我不太确定您想做什么，以下是我可以帮您的：\n\n"
        "  📚 专业知识问答 — 例如：「招标方式有哪些？」「评标委员会怎么组成？」\n"
        "  💰 智能询价 — 例如：「查一下XX产品的历史中标价格」\n"
        "  💬 通用对话 — 例如：「你能做什么？」\n\n"
        "请告诉我您想使用哪个功能，或者直接提出问题。"
    )
    logger.info("fallback: 意图不明确，引导用户")

    return {
        "business_result": {
            "branch": "fallback",
            "answer": answer,
            "data": None,
        },
        "messages": [AIMessage(content=answer)],
    }
