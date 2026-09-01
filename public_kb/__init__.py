# 功能：公共知识库包入口，懒加载对外门面 PublicKnowledgeRAG。
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
# 仅 config 模块在任何情况下都可安全导入。
# 说明（M6 治理）：__getattr__ 是"有意的非常规"设计——public_kb 包在部分
# 只读脚本/扫描器中被 import，若顶层直接 import rag_engine 会连带拉起
# langchain/pymilvus 等重依赖；懒加载只在真正需要 PublicKnowledgeRAG 时
# 才初始化它们。不要改为顶层直接导入。


def __getattr__(name: str):
    if name == "PublicKnowledgeRAG":
        from .rag_engine import PublicKnowledgeRAG as _RAG
        return _RAG
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["PublicKnowledgeRAG", "Settings"]
