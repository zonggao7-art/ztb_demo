"""
Milvus 向量库重建脚本 — bge-m3 双向量模式
阶段一：删除旧 collection → 重新解析 PDF → 入库 → 验证问答
"""
import logging
import sys
import os

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from public_kb.rag_engine import PublicKnowledgeRAG

# PDF 源目录
PDF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw_pdfs")

# 验证问题集
VERIFY_QUESTIONS = [
    "招标方式有哪些？",
    "公开招标和邀请招标有什么区别？",
    "履约保证金的比例是多少？",
    "投标保证金什么时候退还？",
    "废标的情形有哪些？",
]


def main():
    print("=" * 60)
    print("Milvus 向量库重建（bge-m3 双向量模式）")
    print("=" * 60)

    # 1. 初始化 RAG 引擎
    print("\n>>> 初始化 PublicKnowledgeRAG...")
    rag = PublicKnowledgeRAG()

    # 2. 先清空旧 collection
    print("\n>>> 清空旧 public_kb collection...")
    try:
        rag.clear_kb()
        print("   旧 collection 已删除")
    except Exception as e:
        print(f"   清空跳过（可能不存在）: {e}")

    # 3. 重建全量知识库
    print(f"\n>>> 开始全量重建知识库（源目录: {PDF_DIR}）...")
    try:
        rag.init_knowledge_base(PDF_DIR)
        print("   知识库重建完成！")
    except Exception as e:
        print(f"   ❌ 重建失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # 4. 验证问答
    print("\n" + "=" * 60)
    print("验证问答（bge-m3 混合检索模式）")
    print("=" * 60)

    for i, q in enumerate(VERIFY_QUESTIONS, 1):
        print(f"\n>>> 问题 {i}: {q}")
        try:
            result = rag.query(q)
            answer = result.get("answer", "")
            sources = result.get("sources", [])
            print(f"    回答: {answer[:200]}{'...' if len(answer) > 200 else ''}")
            print(f"    来源数: {len(sources)}")
            for src in sources:
                print(f"      - [{src.get('doc', '?')}] {src.get('chapter', '?')} (score={src.get('score', 0):.4f})")
        except Exception as e:
            print(f"    ❌ 查询失败: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 60)
    print("重建与验证完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
