"""
doc_qa — 文档问答预留节点。

Demo 阶段仅返回功能待上线提示，不实现真实解析与检索逻辑。
未来正式接入时，替换此节点函数体即可，不改 State 和 Graph。
"""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage

from ..state import AgentState

logger = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════
# doc_qa 正式版接口契约（未来实现参考）
# ═════════════════════════════════════════════════════════
#
# 输入约定（从 State 读取）：
#   - state["messages"][-1].content  → 用户问题（含文档引用）
#   - state["messages"]              → 完整对话历史
#   - 通过依赖注入获取: doc_vector_store, doc_parser, doc_embedder
#
# 输出约定（business_result）：
#   {
#       "branch": "doc_qa",
#       "answer": str,
#       "data": {
#           "status": "success" | "no_doc_attached" | "irrelevant",
#           "sources": [                    ← 与 knowledge_qa 同构
#               {"doc": str, "chapter": str, "chunk_index": int,
#                "content_snippet": str, "score": float}
#           ],
#           "attached_docs": [str],         ← 用户上传的文件名
#       }
#   }
#
# 依赖注入清单：
#   - doc_vector_store:  MilvusVectorStore   (用户私有 doc_kb 集合)
#   - doc_parser:        DocumentParser       (PDF/Word/Excel 解析)
#   - doc_embedder:      OpenAIEmbeddings     (与公共库共用实例)
#
# 正式上线改动清单（6 步）：
#   1. 初始化依赖: 在 AgentGraph.__init__ 中创建 doc_vector_store
#   2. 替换节点:   用真实 node_doc_qa 替换当前占位
#   3. 调优 prompt: Router system prompt 增加文档问答触发描述
#   4. 不改 State   ❌ 不需要
#   5. 不改 Graph   ❌ 不需要
#   6. 不改其他节点 ❌ 不需要
# ═════════════════════════════════════════════════════════


def node_doc_qa(state: AgentState) -> dict:
    """文档问答占位节点。

    返回功能待上线提示，引导用户使用已支持的功能。

    Args:
        state: AgentState

    Returns:
        {"business_result": {...}, "messages": [AIMessage]}
    """
    logger.info("doc_qa: 占位节点被调用（功能待上线）")

    answer = (
        "📄 文档问答功能正在开发中，敬请期待！\n\n"
        "当前已支持的功能：\n"
        "  1️⃣ 专业知识问答 — 查询招投标法规、招标方式、评标规则等\n"
        "  2️⃣ 智能询价 — 查询历史中标价格、产品报价、公司中标记录\n"
        "  3️⃣ 通用对话 — 功能引导和操作咨询\n\n"
        "您可以尝试问我：\n"
        "  • 「招标方式有哪些？」\n"
        "  • 「公开招标和邀请招标有什么区别？」\n"
        "  • 「帮我查一下智慧交通项目的中标价格」"
    )

    return {
        "business_result": {
            "branch": "doc_qa",
            "answer": answer,
            "data": {
                "status": "placeholder",
                "available_since": None,
            },
        },
        "messages": [AIMessage(content=answer)],
    }
