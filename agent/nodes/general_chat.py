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

# 所有入口共享同一份六业务线说明，避免能力边界随入口漂移。
GENERAL_GUIDANCE = (
    "👋 您好！我是「招投标智能助手」，很高兴为您服务！\n\n"
    "   我可以帮您处理以下事务：\n\n"
    "   1️⃣  法律法规问答\n"
    "       招投标法律法规、招标方式、评标规则、采购流程等专业问题\n"
    "   2️⃣  企业工商信息\n"
    "       按主体名称查询统一社会信用代码、法定代表人、注册资本等工商信息\n"
    "   3️⃣  企业经营范围\n"
    "       按主体名称查询已收录的经营范围\n"
    "   4️⃣  项目中标情况\n"
    "       按项目编号查询项目名称、采购人、中标供应商、中标金额和中标日期\n"
    "   5️⃣  企业中标历史\n"
    "       按中标企业或供应商名称查询历史中标项目\n"
    "   6️⃣  企业违法/处罚信息\n"
    "       按主体名称查询已收录的违法行为与行政处罚记录\n\n"
    "   请问有什么可以帮您的？"
)


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
    answer = GENERAL_GUIDANCE

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
