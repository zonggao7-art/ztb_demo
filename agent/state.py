"""
AgentState — 通用 Agent 状态定义。

设计原则：
  - 所有分支共享同一套 State 字段，不加分支专用字段
  - messages 使用 add_messages reducer，支持 ID 去重和 Checkpointer 兼容
  - business_result 为泛型 dict，各节点自行定义内部结构
  - 不引入 is_complete / error 等冗余字段
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage


class AgentState(TypedDict, total=False):
    """通用 Agent 状态。新增业务分支不需要修改此定义。"""

    # ── 对话历史 ──
    # add_messages reducer: 自动 ID 去重、类型校验、与 Checkpointer 原生兼容
    messages: Annotated[list[BaseMessage], add_messages]

    # ── 路由意图 ──
    # 值域见 router.py 中的 RouterIntent 枚举
    router_intent: str

    # ── 业务负载 ──
    # 泛型字典，各节点自由定义内部结构，State 层不感知
    # knowledge_qa  → {"branch": "knowledge_qa", "answer": "...",
    #                  "data": {"sources": [...], "citations": [...],
    #                           "citation_validation": {...}}}   # 引用溯源标准化
    # price_inquiry → {"branch": "price_inquiry", "answer": "...", "data": {"records": [...]}}
    # general_chat  → {"branch": "general_chat",  "answer": "...", "data": None}
    # doc_qa        → {"branch": "doc_qa",        "answer": "...", "data": {"status": "placeholder"}}
    # fallback      → {"branch": "fallback",      "answer": "...", "data": None}
    business_result: dict
