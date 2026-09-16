"""知识库业务异常，与网络、schema 和事件循环运行时故障区分。"""


class KnowledgeBaseNotReadyError(RuntimeError):
    """尚未建立可查询的知识库。"""
