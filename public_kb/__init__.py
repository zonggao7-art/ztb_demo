"""
公共知识库 RAG 包 — 招投标领域通用权威知识库。

对外统一入口：
    from public_kb import PublicKnowledgeRAG

    rag = PublicKnowledgeRAG()
    rag.init_knowledge_base("d:/DEMO/zhaotoubiao_demo/raw_pdfs")
    result = rag.query("什么是公开招标？")
"""

from .config import Settings

# PublicKnowledgeRAG 使用懒加载，避免未安装 langchain 依赖时 import 失败
# 仅 config 模块在任何情况下都可安全导入


def __getattr__(name: str):
    if name == "PublicKnowledgeRAG":
        from .rag_engine import PublicKnowledgeRAG as _RAG
        return _RAG
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["PublicKnowledgeRAG", "Settings"]
