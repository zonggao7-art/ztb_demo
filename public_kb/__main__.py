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


def cmd_prepare_handoff(data_dir: str, output_dir: str) -> None:
    """从三本书 content_list 生成本地、已校验的交接产物。"""
    from public_kb.book_pipeline import prepare_handoff

    manifest = prepare_handoff(data_dir, output_dir)
    print(
        f"交接产物已生成: {output_dir}\n"
        f"三本书总块数: {manifest['chunk_count']}\n"
        f"UID digest: {manifest['chunk_uid_digest']}\n"
        "Milvus 未修改。"
    )


def _initialize_from_documents(documents: list) -> None:
    from public_kb.handoff_validate import validate_documents

    report = validate_documents(documents)
    if not report.ok:
        codes = ", ".join(issue.code for issue in report.issues)
        raise ValueError(f"入库数据未通过质量门禁: {codes}")
    rag = PublicKnowledgeRAG()
    rag._store_manager.initialize_collection(documents)
    rag._build_qa_chain()
    print(f"已重建 public_kb，共导入 {len(documents)} 个切块。")


def cmd_ingest_jsonl(path: str) -> None:
    """校验并用 documents.jsonl 重建 public_kb。"""
    from public_kb.book_pipeline import load_documents_jsonl

    _initialize_from_documents(load_documents_jsonl(path))


def cmd_ingest_markdown(markdown_path: str, metadata_jsonl: str) -> None:
    """校验 Markdown/页码侧车一致性并重建 public_kb。"""
    from public_kb.book_pipeline import load_markdown_with_sidecar

    _initialize_from_documents(
        load_markdown_with_sidecar(markdown_path, metadata_jsonl)
    )


def cmd_ingest_handoff(handoff_dir: str, replace_doc: str | None, rebuild: bool) -> None:
    """校验交接产物（digest + 门禁 + 联合唯一）后按 chunk_uid 主键写入。"""
    from public_kb.book_pipeline import load_handoff_bundle

    documents = load_handoff_bundle(handoff_dir)
    rag = PublicKnowledgeRAG()
    if rebuild:
        rag._store_manager.initialize_collection(documents)
    else:
        rag._store_manager.upsert_documents(documents, replace_doc=replace_doc)
    rag._store_manager.load_existing()
    rag._build_qa_chain()
    if rebuild:
        mode = "全量重建（drop + 重建集合）"
    elif replace_doc:
        mode = f"文档级替换 doc_name={replace_doc!r}（先删旧行再 upsert）"
    else:
        mode = "upsert 幂等写入（同 chunk_uid 覆盖，不产生重复行）"
    print(
        f"交接产物已入库: {len(documents)} 块（{mode}）\n"
        f"集合当前总数: {rag._store_manager.row_count()}"
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="招投标公共知识库 RAG 系统",
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--init", action="store_true",
        help="初始化知识库（批量解析 PDF 并入库）",
    )
    parser.add_argument(
        "--pdf-dir", type=str, default=None,
        help="PDF 文件目录（默认: raw_pdfs/）",
    )
    actions.add_argument(
        "--question", "-q", type=str, default=None,
        help="单次问答",
    )
    actions.add_argument(
        "--interactive", "-i", action="store_true",
        help="交互问答模式",
    )
    actions.add_argument(
        "--clear", action="store_true",
        help="清空知识库",
    )
    actions.add_argument(
        "--prepare-handoff", action="store_true",
        help="从 DATA 三本书 content_list 重建并导出已校验交接产物",
    )
    actions.add_argument(
        "--ingest-jsonl", type=str, default=None, metavar="PATH",
        help="校验 documents.jsonl 后重建 public_kb",
    )
    actions.add_argument(
        "--ingest-markdown", type=str, default=None, metavar="PATH",
        help="校验 normalized Markdown 及页码侧车后重建 public_kb",
    )
    actions.add_argument(
        "--ingest-handoff", type=str, default=None, metavar="DIR",
        help="校验交接产物（digest+门禁+联合唯一）后按 chunk_uid 主键写入 public_kb",
    )
    parser.add_argument(
        "--replace-doc", type=str, default=None, metavar="NAME",
        help="与 --ingest-handoff 连用：先删除指定 doc_name 的旧行再写入（文档级替换）",
    )
    parser.add_argument(
        "--rebuild", action="store_true",
        help="与 --ingest-handoff 连用：drop 后按混合 schema 全量重建集合",
    )
    parser.add_argument(
        "--data-dir", type=str, default=str(_PROJECT_ROOT / "DATA" / "raw_data"),
        help="三本书 MinerU 数据根目录",
    )
    parser.add_argument(
        "--output-dir", type=str,
        default=str(_PROJECT_ROOT / "DATA" / "repaired_knowledge"),
        help="交接产物输出目录（必须尚不存在）",
    )
    parser.add_argument(
        "--metadata-jsonl", type=str, default=None,
        help="--ingest-markdown 必需的 normalized.blocks.jsonl",
    )
    return parser


def main() -> None:
    parser = _build_parser()

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
    elif args.prepare_handoff:
        cmd_prepare_handoff(args.data_dir, args.output_dir)
    elif args.ingest_jsonl:
        cmd_ingest_jsonl(args.ingest_jsonl)
    elif args.ingest_markdown:
        if not args.metadata_jsonl:
            parser.error("--ingest-markdown 必须同时提供 --metadata-jsonl")
        cmd_ingest_markdown(args.ingest_markdown, args.metadata_jsonl)
    elif args.ingest_handoff:
        if args.replace_doc and args.rebuild:
            parser.error("--replace-doc 与 --rebuild 不能同时使用")
        cmd_ingest_handoff(args.ingest_handoff, args.replace_doc, args.rebuild)
    else:
        # 默认：交互模式
        print("未指定操作，进入交互问答模式。")
        print("提示: 使用 --init 初始化入库，--question 单次问答，--help 查看帮助。\n")
        cmd_interactive()


if __name__ == "__main__":
    main()
