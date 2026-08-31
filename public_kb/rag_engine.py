# 功能：PublicKnowledgeRAG 对外门面，串联离线入库与在线问答。
"""Public knowledge RAG facade for ingestion and question answering."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_openai import OpenAIEmbeddings

from .config import Settings
from .services.embeddings import create_embeddings
from .ingestion.pipeline import IngestionPipeline
from .ingestion.sinks.milvus_sink import MilvusSink
from .ingestion.sources.document_source import DocumentSource
from .ingestion.sources.pdf_source import PdfSource
from .ingestion.transforms import SemanticChunker, TextCleaner
from .ingestion.transforms.pdf_structure import adapt_pdf_markdown
from .generation.chain import build_chain
from .retrieval.reranker import SiliconFlowReranker
from .services.llm import create_llm
from .services.milvus_store import MilvusStoreManager
from .services.milvus_store import MilvusStoreManager


logger = logging.getLogger(__name__)


class PublicKnowledgeRAG:
    """Public entry point for knowledge-base initialization and querying."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or Settings()
        self._embeddings: OpenAIEmbeddings = create_embeddings(self._settings)
        self._llm: BaseChatModel = create_llm(self._settings)
        self._store_manager = MilvusStoreManager(self._settings, self._embeddings)
        self._qa_chain: Optional[Any] = None
        from .ingestion.parser_factory import build_pdf_parser
        self._parser = build_pdf_parser(self._settings)
        self._cleaner = TextCleaner()
        self._chunker = SemanticChunker(
            max_chars=self._settings.chunk_max_chars,
            overlap_chars=self._settings.chunk_overlap_chars,
        )
        logger.info("PublicKnowledgeRAG 初始化完成")

    def init_knowledge_base(self, pdf_dir: str) -> None:
        """Parse all PDFs, validate chunks, and initialize the collection."""
        pdf_path = Path(pdf_dir).resolve()
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF 目录不存在: {pdf_dir}")
        if not pdf_path.is_dir():
            raise NotADirectoryError(f"路径不是目录: {pdf_dir}")

        pdf_files = sorted(pdf_path.glob("*.pdf"))
        if not pdf_files:
            raise FileNotFoundError(f"目录中未找到 PDF 文件: {pdf_dir}")

        logger.info("开始公共知识库初始化，共发现 %d 个 PDF 文件", len(pdf_files))
        all_docs: List[Document] = []
        failed: List[str] = []
        for pdf_file in pdf_files:
            try:
                docs = self._process_single_pdf(pdf_file)
                all_docs.extend(docs)
                logger.info("✓ %s → %d 个文档块", pdf_file.name, len(docs))
            except Exception as exc:
                logger.error("✗ %s 处理失败: %s", pdf_file.name, exc)
                failed.append(pdf_file.name)

        if not all_docs:
            raise RuntimeError(
                f"所有 PDF 处理均失败，无法初始化知识库。失败清单: {failed}"
            )

        logger.info("开始向量化并入库 %d 个文档块...", len(all_docs))
        ingestion_result = IngestionPipeline([
            MilvusSink(self._store_manager, mode="initialize"),
        ]).run(DocumentSource(all_docs, source_name="pdf_directory"))
        logger.info(
            "PDF 初始化入库完成: chunks=%d, inserted=%d",
            ingestion_result.chunk_count,
            ingestion_result.inserted_count,
        )

        self._build_qa_chain()
        logger.info(
            "公共知识库初始化完成：成功 %d 本，失败 %d 本，总块数 %d",
            len(pdf_files) - len(failed),
            len(failed),
            len(all_docs),
        )

    def query(self, question: str) -> Dict[str, Any]:
        """Answer a question and return the existing QA result contract."""
        if self._qa_chain is None:
            raise RuntimeError(
                "知识库尚未初始化，请先调用 init_knowledge_base() 入库。"
            )

        logger.info("用户提问: %s", question[:100])
        result: Dict[str, Any] = self._qa_chain.invoke(question)
        logger.info("回答完成，来源数: %d", len(result.get("sources", [])))
        return result

    def clear_kb(self) -> None:
        """Clear the configured experimental collection."""
        logger.warning("正在清空 public_kb 集合...")
        self._store_manager.clear_collection()
        self._qa_chain = None
        logger.warning("public_kb 集合已清空。")

    def ensure_loaded(self) -> None:
        """Load an existing collection and build its QA chain."""
        self._store_manager.load_existing()
        self._build_qa_chain()

    def load_existing(self) -> bool:
        """公开入口：加载并校验已存在的集合，成功后构建问答链。

        返回是否加载成功（集合不存在时为 False）。此前 CLI 直接访问私有
        ``_store_manager.load_existing()``（M6 治理），现收敛到公开方法。
        """
        loaded = self._store_manager.load_existing()
        if loaded:
            self._build_qa_chain()
        return loaded

    def add_pdf(self, pdf_path: str) -> int:
        """Parse one PDF and append its chunks to the existing collection."""
        source = self._pdf_source(pdf_path)
        if not source.pdf_path.exists():
            raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")
        if source.pdf_path.suffix.lower() != ".pdf":
            raise ValueError(f"文件不是 PDF: {pdf_path}")

        logger.info("增量导入 PDF: %s", source.pdf_path.name)
        ingestion_result = IngestionPipeline([
            MilvusSink(self._store_manager, mode="append"),
        ]).run(source)
        if self._qa_chain is None:
            self._build_qa_chain()

        logger.info(
            "增量导入完成: %s → %d 块",
            source.pdf_path.name,
            ingestion_result.chunk_count,
        )
        return ingestion_result.chunk_count

    def _pdf_source(self, pdf_path: str | Path) -> PdfSource:
        return PdfSource(
            pdf_path,
            parser=self._parser,
            cleaner=self._cleaner,
            chunker=self._chunker,
        )

    def _process_single_pdf(self, pdf_path: Path) -> List[Document]:
        """Process one PDF through the PDF Source transform boundary."""
        if not self._settings.enable_pdf_structure:
            documents = self._pdf_source(pdf_path).load().documents
        else:
            raw_markdown = self._parser.parse(pdf_path)
            cleaned_markdown = self._cleaner.clean(raw_markdown)
            documents = adapt_pdf_markdown(
                cleaned_markdown,
                doc_name=pdf_path.name,
                chunker=self._chunker,
                min_table_rows=self._settings.pdf_min_table_rows,
                enable_toc_filter=self._settings.enable_pdf_toc_filter,
                enable_reflow_flag=self._settings.enable_pdf_reflow_flag,
            )
        if not documents:
            logger.warning("%s 切片后无有效内容，跳过", pdf_path.name)
        return documents

    def _build_qa_chain(self) -> None:
        """Build the LCEL chain with native Milvus hybrid retrieval when available."""
        try:
            collection = self._store_manager.collection
        except RuntimeError:
            collection = None
            logger.info("MilvusClient 不可用，问答链将使用纯稠密检索")

        self._qa_chain = build_chain(
            vector_store=self._store_manager.store,
            llm=self._llm,
            settings=self._settings,
            collection=collection,
            embeddings=self._embeddings,
            reranker_class=SiliconFlowReranker,
        )
