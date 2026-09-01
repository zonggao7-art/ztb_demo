# 功能：公共知识库命令行入口，用于初始化、问答和交互测试。
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
from public_kb.generation.citations import format_citations

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
    # 尝试加载已有集合（公开入口，M6 治理后不再访问私有成员）
    rag.load_existing()

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
    # 尝试加载已有集合（公开入口，M6 治理后不再访问私有成员）
    if not rag.load_existing():
        logger.error(
            "未找到 public_kb 集合，请先运行: python -m public_kb --init"
        )
        return

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


def cmd_prepare_handoff(pdf_dir: str, out_dir: str, force: bool) -> None:
    """只解析 + 分块，产出 assembled.md 与 documents.jsonl（不向量化、不入库）。"""
    from public_kb.config import Settings
    from public_kb.ingestion.handoff import prepare_handoff

    summary = prepare_handoff(pdf_dir, Settings(), out_dir=out_dir, force=force)
    ok = [e for e in summary if "error" not in e]
    failed = [e for e in summary if "error" in e]
    cached = sum(1 for e in ok if e.get("cached"))
    total_chunks = sum(int(e.get("chunks", 0)) for e in ok)
    logger.info(
        "交接产物导出完成: 成功 %d 本 / 失败 %d 本 / 缓存 %d 本 / 共 %d 块 → %s",
        len(ok), len(failed), cached, total_chunks, out_dir,
    )
    for entry in failed:
        logger.error("  失败: %s → %s", entry["pdf"], entry["error"])


def cmd_ingest_handoff(kind: str, path: str, mode: str) -> None:
    """从交接产物入库（jsonl 或 assembled markdown 目录）。"""
    from public_kb.config import Settings

    if kind == "jsonl":
        from public_kb.ingestion.handoff import ingest_documents_jsonl
        result = ingest_documents_jsonl(path, Settings(), mode=mode)
    else:
        from public_kb.ingestion.handoff import ingest_markdown_dir
        result = ingest_markdown_dir(path, Settings(), mode=mode)
    logger.info(
        "交接产物入库完成: source=%s chunks=%d inserted=%d",
        result.source, result.chunk_count, result.inserted_count,
    )


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
    parser.add_argument(
        "--prepare-handoff", action="store_true",
        help="导出交接产物：解析 PDF 并产出 assembled.md + documents.jsonl（不向量化、不入库）",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="配合 --prepare-handoff：忽略文件级缓存，强制重新解析",
    )
    parser.add_argument(
        "--out-dir", type=str, default=None,
        help="交接产物输出目录（配合 --prepare-handoff / --ingest-markdown）",
    )
    parser.add_argument(
        "--ingest-jsonl", type=str, default=None, metavar="FILE",
        help="从交接产物 documents.jsonl 直接入库",
    )
    parser.add_argument(
        "--ingest-markdown", type=str, default=None, metavar="DIR",
        help="从 <stem>.assembled.md 目录重新分块入库",
    )
    parser.add_argument(
        "--mode", choices=("initialize", "append"), default="append",
        help="入库模式（默认 append；集合不存在时自动初始化）",
    )

    args = parser.parse_args()

    pdf_dir = args.pdf_dir or _get_default_pdf_dir()

    if args.prepare_handoff:
        out_dir = args.out_dir or str(
            _PROJECT_ROOT / "DATA" / "raw_data" / "handoff"
        )
        cmd_prepare_handoff(pdf_dir, out_dir, args.force)
    elif args.ingest_jsonl:
        cmd_ingest_handoff("jsonl", args.ingest_jsonl, args.mode)
    elif args.ingest_markdown:
        cmd_ingest_handoff("markdown", args.ingest_markdown, args.mode)
    elif args.init:
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
