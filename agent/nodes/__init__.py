"""业务节点包 — 每个节点遵循统一接口约定。"""

from .knowledge_qa import node_knowledge_qa
from .price_inquiry import node_price_inquiry
from .general_chat import node_general_chat
from .doc_qa import node_doc_qa
from .fallback import node_fallback

__all__ = [
    "node_knowledge_qa",
    "node_knowledge_qa_async",  # 阶段 2：异步 RAG 节点（懒加载，避免循环导入）
    "node_price_inquiry_async",  # 阶段 3：异步询价节点（懒加载，避免循环导入）
    "node_price_inquiry",
    "node_general_chat",
    "node_doc_qa",
    "node_fallback",
    "node_doc_qa_async",
    "node_general_chat_async",
    "node_fallback_async",
]


def __getattr__(name: str):
    # 异步节点延迟加载：其模块依赖 agent.runtime 与 public_kb 异步链路
    if name == "node_knowledge_qa_async":
        from .knowledge_qa_async import node_knowledge_qa_async as _n
        return _n
    if name == "node_price_inquiry_async":
        from .price_inquiry.node_async import node_price_inquiry_async as _n
        return _n
    if name == "node_doc_qa_async":
        from .doc_qa import node_doc_qa_async as _n
        return _n
    if name == "node_general_chat_async":
        from .general_chat import node_general_chat_async as _n
        return _n
    if name == "node_fallback_async":
        from .fallback import node_fallback_async as _n
        return _n
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
