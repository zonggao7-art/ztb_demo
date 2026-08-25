"""
公共知识库 CLI 入口 + 测试示例。

用法：
    # 场景 1：初始化入库 + 测试问答
    python -m public_kb --init --pdf-dir d:/DEMO/zhaotoubiao_demo/raw_pdfs

    # 场景 2：仅测试问答（假设已入库）
    python -m public_kb --question "招标方式有哪些？"

    # 场景 3：清空知识库
    python -m public_kb --clear

    # 场景 4：交互问答模式
    python -m public_kb --interactive
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 将项目根目录加入 path（确保可独立运行）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from public_kb import PublicKnowledgeRAG
from public_kb.citations import format_citations

# 配置日志输出
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("public_kb")


def _get_default_pdf_dir() -> str:
    """获取默认 PDF 目录路径。"""
    return str(_PROJECT_ROOT / "raw_pdfs")


def cmd_init(pdf_dir: str) -> None:
    """执行初始化入库。"""
    logger.info("初始化公共知识库，PDF 目录: %s", pdf_dir)
    rag = PublicKnowledgeRAG()
    rag.init_knowledge_base(pdf_dir)
    logger.info("初始化完成！")


def cmd_query(question: str) -> None:
    """执行单次问答。"""
    rag = PublicKnowledgeRAG()
    # 尝试加载已有集合
    rag._store_manager.load_existing()
    rag._build_qa_chain()

    result = rag.query(question)
    print("\n" + "=" * 60)
    print(f"问题: {question}")
    print("-" * 60)
    print(f"回答: {result['answer']}")
    _print_citations(result)
    print("=" * 60 + "\n")


def _print_citations(result: dict) -> None:
    """打印标准化引用（chunk 唯一标识 + 数据源位置 + 原文片段 + 元数据）。

    复用 citations.format_citations 渲染，保证与 agent CLI 呈现一致。
    """
    citations = result.get("citations")
    if not citations:
        return
    print("-" * 60)
    print(format_citations(citations))
    print()
    validation = result.get("citation_validation") or {}
    rules = validation.get("rules") or []
    failed = [r for r in rules if r.get("enabled") and not r.get("passed")]
    print(
        f"  校验: {'✅ 全部通过' if validation.get('all_passed') else '⚠️ 存在失败规则'}"
        + (f" (失败: {', '.join(r['rule_id'] for r in failed)})" if failed else "")
    )


def cmd_interactive() -> None:
    """交互式问答模式。"""
    rag = PublicKnowledgeRAG()
    # 尝试加载已有集合
    if not rag._store_manager.load_existing():
        logger.error(
            "未找到 public_kb 集合，请先运行: python -m public_kb --init"
        )
        return
    rag._build_qa_chain()

    print("\n" + "=" * 60)
    print("  招投标公共知识库 — 交互问答模式")
    print("  输入问题后按回车，输入 quit / exit 退出")
    print("=" * 60 + "\n")

    while True:
        try:
            question = input("🧑 你的问题: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if question.lower() in ("quit", "exit", "q", "退出"):
            print("再见！")
            break
        if not question:
            continue

        result = rag.query(question)
        print(f"\n🤖 回答: {result['answer']}")
        _print_citations(result)
        print()


def cmd_clear() -> None:
    """清空知识库。"""
    confirm = input("⚠️  确认清空 public_kb 集合? (yes/no): ").strip().lower()
    if confirm == "yes":
        rag = PublicKnowledgeRAG()
        rag.clear_kb()
        print("✅ public_kb 集合已清空。")
    else:
        print("❌ 已取消。")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="招投标公共知识库 RAG 系统",
    )
    parser.add_argument(
        "--init", action="store_true",
        help="初始化知识库（批量解析 PDF 并入库）",
    )
    parser.add_argument(
        "--pdf-dir", type=str, default=None,
        help="PDF 文件目录（默认: raw_pdfs/）",
    )
    parser.add_argument(
        "--question", "-q", type=str, default=None,
        help="单次问答",
    )
    parser.add_argument(
        "--interactive", "-i", action="store_true",
        help="交互问答模式",
    )
    parser.add_argument(
        "--clear", action="store_true",
        help="清空知识库",
    )

    args = parser.parse_args()

    pdf_dir = args.pdf_dir or _get_default_pdf_dir()

    if args.init:
        cmd_init(pdf_dir)
    elif args.clear:
        cmd_clear()
    elif args.interactive:
        cmd_interactive()
    elif args.question:
        cmd_query(args.question)
    else:
        # 默认：交互模式
        print("未指定操作，进入交互问答模式。")
        print("提示: 使用 --init 初始化入库，--question 单次问答，--help 查看帮助。\n")
        cmd_interactive()


if __name__ == "__main__":
    main()
