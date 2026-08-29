"""
CSV 政策数据 → Milvus 知识库 全流程批量处理入口。

用法：
    # 场景 1：全量处理（组A优先 → 组C，切分 + 中间存储 + 入库）
    python -m public_kb.process_csv --csv-dir d:/DEMO/zhaotoubiao_demo/raw_policy

    # 场景 2：仅处理组 A
    python -m public_kb.process_csv --csv-dir d:/DEMO/zhaotoubiao_demo/raw_policy --group A

    # 场景 3：仅处理组 C
    python -m public_kb.process_csv --csv-dir d:/DEMO/zhaotoubiao_demo/raw_policy --group C

    # 场景 4：仅切分+中间存储（不入库）
    python -m public_kb.process_csv --csv-dir d:/DEMO/zhaotoubiao_demo/raw_policy --no-import

流程总览：
  Phase 1: CSV 批量扫描与按组分类（A 组优先）
  Phase 2: 逐文件清洗与标准化（CsvLoader）
  Phase 3: 内容结构增强（中文标题 → Markdown 标题）
  Phase 4: 语义切分（SemanticChunker）
  Phase 5: 中间存储（Markdown → DATA/raw_data/）
  Phase 6: 向量化 + Milvus 入库（_SafeEmbeddings + MilvusStoreManager）
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

# 将项目根目录加入 path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from public_kb import PublicKnowledgeRAG, Settings
from public_kb.csv_loader import CsvLoader
from public_kb.ingestion.pipeline import IngestionPipeline
from public_kb.ingestion.sinks.markdown_sink import MarkdownSink
from public_kb.ingestion.sinks.milvus_sink import MilvusSink
from public_kb.ingestion.sources.csv_source import CsvSource
from public_kb.ingestion.sources.document_source import DocumentSource

logger = logging.getLogger("public_kb.process_csv")

# 中间存储输出目录
DEFAULT_OUTPUT_DIR = os.path.join(str(_PROJECT_ROOT), "DATA", "raw_data")


def scan_csv_files(csv_dir: str) -> Tuple[List[str], List[str], List[str]]:
    """扫描 CSV 目录，按组分类。

    Args:
        csv_dir: CSV 文件所在目录。

    Returns:
        (group_a, group_c, unknown): 三个文件路径列表。
    """
    csv_path = Path(csv_dir)
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV 目录不存在: {csv_dir}")

    all_csv = sorted(csv_path.glob("*_data.csv"))
    if not all_csv:
        raise FileNotFoundError(f"目录中未找到 *_data.csv 文件: {csv_dir}")

    loader = CsvLoader()
    group_a: List[str] = []
    group_c: List[str] = []
    unknown: List[str] = []

    for f in all_csv:
        category = loader.classify_file(str(f))
        if category == "A":
            group_a.append(str(f))
        elif category == "C":
            group_c.append(str(f))
        else:
            unknown.append(str(f))

    return group_a, group_c, unknown


def process_group(
    files: List[str],
    group_label: str,
    loader: CsvLoader,
    rag: PublicKnowledgeRAG,
    output_dir: str,
    skip_import: bool = False,
) -> Dict[str, object]:
    """处理一组 CSV 文件：加载 → 清洗 → 切片 → 中间存储 → (入库)。

    Args:
        files: 待处理的文件路径列表。
        group_label: 组标签（如 "A"、"C"）。
        loader: CsvLoader 实例。
        rag: PublicKnowledgeRAG 实例（用于入库）。
        output_dir: 中间 Markdown 输出目录。
        skip_import: 是否跳过 Milvus 入库。

    Returns:
        处理统计 dict。
    """
    stats = {
        "group": group_label,
        "total_files": len(files),
        "processed": 0,
        "failed": 0,
        "total_rows": 0,
        "total_chunks": 0,
        "failed_files": [],  # type: List[str]
        "imported": False,
    }

    if not files:
        logger.info("组 %s 无待处理文件，跳过", group_label)
        return stats

    logger.info("=" * 60)
    logger.info("开始处理 组 %s（%d 个文件）", group_label, len(files))
    for f in files:
        logger.info("  - %s", os.path.basename(f))
    logger.info("=" * 60)

    all_docs: list = []
    total_rows = 0

    for file_path in files:
        file_name = os.path.basename(file_path)
        try:
            # Phase 2-4: CSV Source 加载 + 清洗 + 切片
            source_result = CsvSource(file_path, loader=loader).load()
            docs, rows = source_result.documents, source_result.records

            if not docs:
                logger.warning("  → %s 无有效内容，跳过", file_name)
                stats["processed"] += 1
                continue

            all_docs.extend(docs)
            total_rows += len(rows)
            stats["processed"] += 1
            stats["total_rows"] += len(rows)
            stats["total_chunks"] += len(docs)

            # Phase 5: 可选中间预览（Markdown）
            MarkdownSink(output_dir, source_file=file_name).write(
                docs,
                records=rows,
            )

        except Exception as e:
            logger.error("✗ %s 处理失败: %s", file_name, e)
            stats["failed"] += 1
            stats["failed_files"].append(file_name)

    logger.info(
        "组 %s 处理完成: %d/%d 成功, %d 行, %d chunks",
        group_label,
        stats["processed"],
        stats["total_files"],
        total_rows,
        len(all_docs),
    )

    # Phase 6: 入库（增量模式）
    if not skip_import and all_docs:
        logger.info("开始入库组 %s（%d 个文档块）...", group_label, len(all_docs))
        try:
            ingestion_result = IngestionPipeline([
                MilvusSink(rag._store_manager, mode="append"),
            ]).run(
                DocumentSource(
                    all_docs,
                    source_name=f"csv_group_{group_label}",
                )
            )
            stats["imported"] = True
            logger.info(
                "组 %s 入库完成: chunks=%d, inserted=%d",
                group_label,
                ingestion_result.chunk_count,
                ingestion_result.inserted_count,
            )
        except Exception as e:
            logger.error("组 %s 入库失败: %s", group_label, e)
            stats["imported"] = False
    elif skip_import:
        logger.info("已跳过入库（--no-import）")

    return stats


def validate_markdown_output(output_dir: str) -> Dict[str, int]:
    """校验中间存储目录中的所有 Markdown 文件。

    Returns:
        {文件名: chunk 数量} 的字典。
    """
    result: Dict[str, int] = {}
    md_files = sorted(Path(output_dir).glob("*_chunks.md"))

    for md_file in md_files:
        try:
            content = md_file.read_text(encoding="utf-8")
            # 统计 ``` 代码块数量（每个 chunk 一对 ```）
            chunk_count = content.count("```") // 2
            result[md_file.name] = chunk_count
        except Exception as e:
            logger.warning("校验 %s 失败: %s", md_file.name, e)
            result[md_file.name] = -1

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="CSV 政策数据 → Milvus 知识库 全流程处理",
    )
    parser.add_argument(
        "--csv-dir", type=str, required=True,
        help="CSV 文件所在目录",
    )
    parser.add_argument(
        "--output-dir", type=str, default=DEFAULT_OUTPUT_DIR,
        help=f"中间 Markdown 输出目录（默认: {DEFAULT_OUTPUT_DIR}）",
    )
    parser.add_argument(
        "--group", type=str, choices=["A", "C"], default=None,
        help="仅处理指定组别（默认: 先A后C全部处理）",
    )
    parser.add_argument(
        "--no-import", action="store_true",
        help="仅切分并存储中间 Markdown，跳过 Milvus 入库",
    )
    parser.add_argument(
        "--validate-only", action="store_true",
        help="仅校验已有中间 Markdown 文件，不执行处理",
    )

    args = parser.parse_args()

    csv_dir = args.csv_dir
    output_dir = args.output_dir
    skip_import = args.no_import

    # 仅校验模式
    if args.validate_only:
        logger.info("仅校验模式：检查 %s 中的中间文件...", output_dir)
        validation = validate_markdown_output(output_dir)
        total_chunks = sum(max(0, v) for v in validation.values())
        logger.info("校验完成: %d 个文件, 共 %d chunks", len(validation), total_chunks)
        for fname, count in sorted(validation.items()):
            status = "✓" if count >= 0 else "✗"
            logger.info("  %s %s: %d chunks", status, fname, count)
        return

    # 扫描分类
    logger.info("正在扫描 CSV 目录: %s", csv_dir)
    group_a, group_c, unknown = scan_csv_files(csv_dir)

    logger.info("=" * 60)
    logger.info("扫描结果:")
    logger.info("  组 A（完整政策文档）: %d 个文件", len(group_a))
    for f in group_a:
        logger.info("    - %s", os.path.basename(f))
    logger.info("  组 C（QA 问答对）: %d 个文件", len(group_c))
    for f in group_c:
        logger.info("    - %s", os.path.basename(f))
    if unknown:
        logger.info("  无法识别: %d 个文件", len(unknown))
        for f in unknown:
            logger.info("    - %s", os.path.basename(f))
    logger.info("=" * 60)

    # 初始化组件
    settings = Settings()
    rag = PublicKnowledgeRAG(settings=settings)
    loader = CsvLoader(
        max_chars=settings.chunk_max_chars,
        overlap_chars=settings.chunk_overlap_chars,
    )

    # 确保中间输出目录存在
    os.makedirs(output_dir, exist_ok=True)

    all_stats: List[Dict] = []

    # 按优先级处理：组 A 优先
    if args.group is None or args.group == "A":
        stats_a = process_group(group_a, "A", loader, rag, output_dir, skip_import)
        all_stats.append(stats_a)

    if args.group is None or args.group == "C":
        stats_c = process_group(group_c, "C", loader, rag, output_dir, skip_import)
        all_stats.append(stats_c)

    # ── 汇总报告 ──
    logger.info("")
    logger.info("=" * 60)
    logger.info("全流程处理完成 — 汇总报告")
    logger.info("=" * 60)

    grand_total_rows = 0
    grand_total_chunks = 0
    for s in all_stats:
        logger.info(
            "组 %s: %d/%d 成功, %d 行, %d chunks, 入库=%s",
            s["group"],
            s["processed"],
            s["total_files"],
            s["total_rows"],
            s["total_chunks"],
            "是" if s.get("imported") else "否",
        )
        grand_total_rows += s["total_rows"]
        grand_total_chunks += s["total_chunks"]
        if s["failed_files"]:
            logger.info("  失败文件: %s", ", ".join(s["failed_files"]))

    logger.info("合计: %d 行原始数据, %d 文档块", grand_total_rows, grand_total_chunks)

    # ── 中间存储校验 ──
    logger.info("")
    logger.info("正在校验中间 Markdown 文件...")
    validation = validate_markdown_output(output_dir)
    valid_count = sum(1 for v in validation.values() if v >= 0)
    total_validated_chunks = sum(max(0, v) for v in validation.values())
    logger.info(
        "中间存储校验完成: %d/%d 个文件有效, 共 %d chunks",
        valid_count, len(validation), total_validated_chunks,
    )

    logger.info("=" * 60)
    logger.info("✅ 全部处理完成！")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
