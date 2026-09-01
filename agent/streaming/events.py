"""流式事件 envelope —— 阶段 5 完整接入，阶段 1 先定契约。"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict

from pydantic import BaseModel, Field
from pydantic.config import ConfigDict


class EventType(str, Enum):
    """流式事件类型枚举。"""

    # 阶段 5 规范事件；新代码只允许产出这些值。
    META = "meta"
    STAGE = "stage"
    TOKEN = "token"
    RETRIEVAL = "retrieval"
    CITATIONS = "citations"
    TABLE = "table"
    PARTIAL = "partial"
    FINAL = "final"
    ERROR = "error"
    CANCELLED = "cancelled"
    HEARTBEAT = "heartbeat"

    # 兼容旧客户端/旧测试的过渡别名；新代码不要继续产出。
    ROUTER = "router"
    MESSAGE = "message"


class StreamEvent(BaseModel):
    """统一的流式事件 envelope。

    业务节点、CLI、SSE、FastAPI 共用此结构。
    """

    type: EventType
    request_id: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    ts: float = 0.0

    model_config = ConfigDict(populate_by_name=True)
