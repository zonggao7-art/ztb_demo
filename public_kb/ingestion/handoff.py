# 功能：知识库交接产物——导出解析 markdown 与向量化前 document 数据；导入任一产物入库。
"""知识库交接产物（handoff artifacts）。

导出（本机执行，需 MinerU 可达）——``prepare_handoff(pdf_dir)`` 对每本 PDF：
  <stem>.assembled.md       三档路由解析产物（分块输入，人工可读）
  <stem>.documents.jsonl    分块后的 Document（page_content + metadata，含 chunk_uid）

导入（组员执行，需本地 Milvus + embedding 配置）：
  ingest_documents_jsonl(<file>)     直接消费导出的 document 数据
  ingest_markdown_dir(<dir>)         从 assembled markdown 重新分块

确定性契约：两条导入路径与导出产物逐字段一致（chunk 边界 / chapter /
chunk_index / chunk_uid / 向量），唯一差异是 Milvus 主键 ``id``（schema
auto_id=True，服务端自增）。分块口径与 ``rag_engine._process_single_pdf``
一致（cleaner → adapt_pdf_markdown / chunker）。

doc_name 约定：导出写 ``{pdf.stem}.assembled.md``，doc_name 取 ``pdf.stem``；
markdown 导入从文件名反向推导（去掉 ``.assembled`` 后缀）。两边文件名
不一致会导致 chunk_uid 不同（uid = md5(doc_name|chapter|chunk_index|text)）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from langchain_core.documents import Document

from ..chunk_ids import compute_chunk_uid
from ..config import Settings
from .pipeline import IngestionPipeline
from .sinks.milvus_sink import MilvusSink, MilvusSinkMode
from .sources.document_source import DocumentSource
from .transforms import SemanticChunker, TextCleaner
from .transforms.pdf_structure import adapt_pdf_markdown

logger = logging.getLogger(__name__)

_MARKDOWN_SUFFIX = ".assembled.md"


def chunk_markdown(
    markdown: str,
    doc_name: str,
    settings: Settings,
    *,
    cleaner: Optional[TextCleaner] = None,
    chunker: Optional[SemanticChunker] = None,
) -> List[Document]:
    """与 rag_engine._process_single_pdf 相同的分块口径（确定性）。

    ``enable_pdf_structure=true`` 时走 ``adapt_pdf_markdown``（表格原子块 /
    目录过滤 / 双栏打标），否则走纯 ``SemanticChunker``。
    """
    cleaner = cleaner or TextCleaner()
    chunker = chunker or SemanticChunker(
        max_chars=settings.chunk_max_chars,
        overlap_chars=settings.chunk_overlap_chars,
    )
    cleaned = cleaner.clean(markdown)
    if not settings.enable_pdf_structure:
        return chunker.chunk(cleaned, doc_name)
    return adapt_pdf_markdown(
        cleaned,
        doc_name=doc_name,
        chunker=chunker,
        min_table_rows=settings.pdf_min_table_rows,
        enable_toc_filter=settings.enable_pdf_toc_filter,
        enable_reflow_flag=settings.enable_pdf_reflow_flag,
    )


def _freeze_chunk_uid(documents: Sequence[Document]) -> List[Document]:
    """把 chunk_uid 固化进 metadata，避免两侧代码版本差异导致 uid 漂移。"""
    docs = list(documents)
    for doc in docs:
        doc.metadata["chunk_uid"] = str(
            doc.metadata.get("chunk_uid")
            or compute_chunk_uid(doc.page_content, doc.metadata)
        )
    return docs


# ── 导出 ───────────────────────────────────────────────────


def prepare_handoff(
    pdf_dir: str | Path,
    settings: Settings,
    *,
    out_dir: str | Path,
    force: bool = False,
) -> List[Dict[str, Any]]:
    """解析 pdf_dir 下所有 PDF，产出 assembled.md + documents.jsonl。

    只做解析 + 分块，不创建 embeddings / 不写 Milvus。
    每本 PDF 独立 try，单本失败不阻断其余。

    文件级缓存：``out_dir/{stem}.assembled.md`` 已存在且未传 ``force`` 时，
    跳过解析（Tier C 本就有内容缓存），直接读取 markdown 重新分块——幂等
    /增量重跑。路由规则或解析参数变更后，用 ``force=True`` 或删除该 md 触发
    重新解析。
    """
    from .parser_factory import build_pdf_parser

    pdf_dir = Path(pdf_dir)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not pdf_dir.exists():
        raise FileNotFoundError(f"PDF 目录不存在: {pdf_dir}")

    parser = build_pdf_parser(settings)
    summary: List[Dict[str, Any]] = []
    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    if not pdf_files:
        raise FileNotFoundError(f"目录中未找到 PDF 文件: {pdf_dir}")

    timeout_sec = max(1, settings.pdf_tiered_book_timeout_sec)
    for pdf_file in pdf_files:
        # 跳过下划线前缀的开发产物（如 _smoke_test_2p.pdf）
        if pdf_file.name.startswith("_"):
            logger.info("跳过开发产物: %s", pdf_file.name)
            continue
        entry: Dict[str, Any] = {"pdf": pdf_file.name}
        md_path = out_dir / f"{pdf_file.stem}{_MARKDOWN_SUFFIX}"
        jsonl_path = out_dir / f"{pdf_file.stem}.documents.jsonl"

        def _process_book() -> Dict[str, Any]:
            if md_path.exists() and not force:
                # 缓存命中：跳过解析，直接对已有 markdown 分块
                raw_markdown = md_path.read_text(encoding="utf-8")
                documents = _freeze_chunk_uid(
                    chunk_markdown(raw_markdown, pdf_file.stem, settings)
                )
                dump_documents_jsonl(documents, jsonl_path)
                return {
                    "chunks": len(documents),
                    "markdown": str(md_path),
                    "jsonl": str(jsonl_path),
                    "cached": True,
                }
            raw_markdown = parser.parse(pdf_file)
            documents = _freeze_chunk_uid(
                chunk_markdown(raw_markdown, pdf_file.stem, settings)
            )
            md_path.write_text(raw_markdown, encoding="utf-8")
            dump_documents_jsonl(documents, jsonl_path)
            return {
                "chunks": len(documents),
                "markdown": str(md_path),
                "jsonl": str(jsonl_path),
            }

        try:
            result, timeout_err = _run_with_timeout(_process_book, timeout_sec)
            if timeout_err is not None:
                raise timeout_err
            entry.update(result)
            status = "⏭ 命中缓存" if result.get("cached") else "✓"
            logger.info(
                "%s %s → %d 块 → %s / %s",
                status, pdf_file.name, result["chunks"],
                md_path.name, jsonl_path.name,
            )
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)
            logger.error(
                "✗ %s 处理失败（已跳过，继续下一本）: %s",
                pdf_file.name, exc,
            )
        summary.append(entry)
    return summary


def _run_with_timeout(fn, timeout_sec: int) -> tuple:
    """在守护线程中执行 fn；超时返回 (None, TimeoutError)。

    守护线程不会被进程退出阻塞：超时后主流程继续处理下一本，被跳过的
    线程在后台自行收尾（其内部 httpx 调用受 mineru_api_timeout 约束）。
    """
    import threading

    box: dict = {"done": False, "value": None, "exc": None}

    def _target() -> None:
        try:
            box["value"] = fn()
        except Exception as exc:  # noqa: BLE001
            box["exc"] = exc
        finally:
            box["done"] = True

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join(timeout_sec)
    if not box["done"]:
        return None, TimeoutError(
            f"超过 {timeout_sec}s 未完成，已跳过该本"
        )
    if box["exc"] is not None:
        raise box["exc"]
    return box["value"], None


def dump_documents_jsonl(
    documents: Sequence[Document],
    path: str | Path,
) -> int:
    """把 Document 序列化为 JSONL（page_content + metadata）。"""
    path = Path(path)
    with path.open("w", encoding="utf-8") as fh:
        for doc in documents:
            fh.write(json.dumps(
                {"page_content": doc.page_content, "metadata": doc.metadata},
                ensure_ascii=False,
            ) + "\n")
    return len(documents)


# ── 导入 ───────────────────────────────────────────────────


def load_documents_jsonl(path: str | Path) -> List[Document]:
    """读 JSONL → List[Document]（含固化 chunk_uid）。"""
    docs: List[Document] = []
    for line_no, line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines()
    ):
        if not line.strip():
            continue
        record = json.loads(line)
        docs.append(Document(
            page_content=record.get("page_content", ""),
            metadata=record.get("metadata") or {},
        ))
        if not docs[-1].page_content:
            raise ValueError(f"{path}: 第 {line_no + 1} 行 page_content 为空")
    return _freeze_chunk_uid(docs)


def ingest_documents(
    documents: Sequence[Document],
    settings: Settings,
    *,
    mode: MilvusSinkMode = "append",
    manager: Any = None,
) -> IngestionPipeline:
    """把 Document 列表写入 Milvus（走既有 MilvusSink，含去重/幂等）。"""
    if manager is None:
        from ..services.embeddings import create_embeddings
        from ..services.milvus_store import MilvusStoreManager

        manager = MilvusStoreManager(settings, create_embeddings(settings))
    return IngestionPipeline([MilvusSink(manager, mode=mode)]).run(
        DocumentSource(documents, source_name="handoff")
    )


def ingest_documents_jsonl(
    path: str | Path,
    settings: Settings,
    *,
    mode: MilvusSinkMode = "append",
    manager: Any = None,
) -> IngestionPipeline:
    """从导出的 documents.jsonl 直接入库（不重新分块）。"""
    documents = load_documents_jsonl(path)
    logger.info("读取 %s → %d 个 Document", Path(path).name, len(documents))
    return ingest_documents(documents, settings, mode=mode, manager=manager)


def ingest_markdown_dir(
    markdown_dir: str | Path,
    settings: Settings,
    *,
    mode: MilvusSinkMode = "append",
    manager: Any = None,
) -> IngestionPipeline:
    """从 ``<stem>.assembled.md`` 重新分块后入库（确定性等价于 jsonl 导入）。"""
    md_dir = Path(markdown_dir)
    files = sorted(md_dir.glob(f"*{_MARKDOWN_SUFFIX}"))
    if not files:
        raise FileNotFoundError(
            f"目录中未找到 *{_MARKDOWN_SUFFIX} 文件: {md_dir}"
        )
    all_docs: List[Document] = []
    for md_file in files:
        doc_name = md_file.name[: -len(_MARKDOWN_SUFFIX)]  # 去掉 .assembled.md
        raw = md_file.read_text(encoding="utf-8")
        all_docs.extend(
            _freeze_chunk_uid(chunk_markdown(raw, doc_name, settings))
        )
        logger.info("✓ %s → %s 块", md_file.name, len(all_docs))
    logger.info("markdown 目录共 %d 个 Document", len(all_docs))
    return ingest_documents(all_docs, settings, mode=mode, manager=manager)
