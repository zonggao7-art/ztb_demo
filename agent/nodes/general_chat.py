"""
general_chat — 通用对话节点。

返回静态欢迎语与功能引导，不调用 LLM、不连接数据库。
用于问候、功能介绍、闲聊等非业务对话。
"""

from __future__ import annotations

import asyncio
import logging

from langchain_core.messages import AIMessage

from ..streaming import EventType
from ..streaming.context import emit
from ..state import AgentState

logger = logging.getLogger(__name__)


def node_general_chat(state: AgentState) -> dict:
    """通用对话节点。

    纯静态回答，不连接 Milvus 和 MySQL。

    Args:
        state: AgentState

    Returns:
        {"business_result": {...}, "messages": [AIMessage]}
    """
    messages = state.get("messages", [])
    if not messages:
        return {
            "business_result": {
                "branch": "general_chat",
                "answer": "您好！我是招投标智能助手，请问有什么可以帮助您的？",
                "data": None,
            },
        }

    question = str(messages[-1].content)
    logger.info("general_chat: %s", question[:80])
    emit(EventType.STAGE, {"stage": "general_compose"})

    # 通用对话为静态欢迎语，不使用 LLM
    answer = (
        "👋 您好！我是「招投标智能助手」，很高兴为您服务！\n\n"
        "   我可以帮您处理以下事务：\n\n"
        "   1️⃣  专业知识问答\n"
        "       招投标法律法规、招标方式、评标规则、采购流程等专业问题\n"
        "   2️⃣  中标情报获取\n"
        "       查询历史中标项目、产品中标价格、中标公司等市场情报\n"
        "   3️⃣  企业工商信息查询\n"
        "       查询公司基本信息、经营范围、注册资本等工商数据\n"
        "   4️⃣  企业风险排查\n"
        "       查询公司不良记录、行政处罚、经营异常等风险信息\n\n"
        "   请问有什么可以帮您的？"
    )

    return {
        "business_result": {
            "branch": "general_chat",
            "answer": answer,
            "data": None,
        },
        "messages": [AIMessage(content=answer)],
    }


async def node_general_chat_async(state: AgentState) -> dict:
    """通用对话流式适配（当前为静态引导文案，按 UTF-8 字符分段）。"""
    messages = state.get("messages", [])
    question = str(messages[-1].content)
    logger.info("general_chat(async): %s", question[:80])
    sync_result = await asyncio.to_thread(node_general_chat, state)
    answer = str(sync_result["business_result"]["answer"])
    step = max(1, len(answer) // 12)
    for start in range(0, len(answer), step):
        delta = answer[start:start + step]
        emit(EventType.TOKEN, {"delta": delta, "synthetic": True})
        await asyncio.sleep(0)
    emit(EventType.FINAL, {"answer": answer, "business_result": {"branch": "general_chat"}})
    return sync_result
