"""
PublicKnowledgeRAG — 招投标公共知识库对外统一入口。

对外暴露三个核心方法：
  - init_knowledge_base(pdf_dir)  → 批量解析入库（仅初始化调用）
  - query(question)               → 问答，返回 {"answer": str, "sources": list,
                                             "citations": list, "citation_validation": dict}
  - clear_kb()                    → 清空公共库（仅管理员）

设计原则：
  - public_kb 为只读权威库，仅支持批量初始化入库，不提供日常写入接口
  - 所有问答链基于 LCEL + Runnable 范式
  - 后续可作为 LangGraph Tool 节点无缝接入
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_openai import OpenAIEmbeddings

from .chunker import SemanticChunker
from .config import Settings
from .embedding_service import create_embeddings
from .llm_factory import create_llm
from .milvus_store import MilvusStoreManager
from .mineru_parser import MinerUParser
from .qa_chain import build_qa_chain
from .text_cleaner import TextCleaner

logger = logging.getLogger(__name__)


class PublicKnowledgeRAG:
    """招投标公共知识库 RAG 引擎。

    使用示例：
        >>> rag = PublicKnowledgeRAG()
        >>> rag.init_knowledge_base("d:/DEMO/zhaotoubiao_demo/raw_pdfs")
        >>> result = rag.query("招标方式有哪些？")
        >>> print(result["answer"])
        >>> for src in result["sources"]:
        ...     print(src["doc"], src["chapter"])
        >>> rag.clear_kb()
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        """初始化公共知识库引擎。

        Args:
            settings: 全局配置。若为 None 则从 .env 自动加载。
        """
        self._settings = settings or Settings()
        self._embeddings: OpenAIEmbeddings = create_embeddings(self._settings)
        self._llm: BaseChatModel = self._create_llm()
        self._store_manager: MilvusStoreManager = MilvusStoreManager(
            self._settings, self._embeddings
        )
        self._qa_chain: Optional[Any] = None
        self._async_pipeline: Optional[Any] = None  # 阶段 2：异步 RAG 流水线（懒建）
        self._parser: MinerUParser = MinerUParser(self._settings)
        self._cleaner: TextCleaner = TextCleaner()
        self._chunker: SemanticChunker = SemanticChunker(
            max_chars=self._settings.chunk_max_chars,
            overlap_chars=self._settings.chunk_overlap_chars,
        )

        logger.info("PublicKnowledgeRAG 初始化完成")

    # ==========================================================
    #  公开核心方法
    # ==========================================================

    def init_knowledge_base(self, pdf_dir: str) -> None:
        """批量解析指定目录下的所有 PDF，完成清洗→切片→入库全流程。

        仅初始化调用。会先清空已有集合再全量重建（幂等操作）。

        Args:
            pdf_dir: 包含 PDF 文件的目录绝对路径。

        Raises:
            FileNotFoundError: 目录不存在或目录中无 PDF 文件。
        """
        pdf_path = Path(pdf_dir).resolve()
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF 目录不存在: {pdf_dir}")
        if not pdf_path.is_dir():
            raise NotADirectoryError(f"路径不是目录: {pdf_dir}")

        pdf_files = sorted(pdf_path.glob("*.pdf"))
        if not pdf_files:
            raise FileNotFoundError(f"目录中未找到 PDF 文件: {pdf_dir}")

        logger.info("=" * 60)
        logger.info("开始公共知识库初始化，共发现 %d 个 PDF 文件", len(pdf_files))
        for f in pdf_files:
            logger.info("  - %s (%.1f MB)", f.name, f.stat().st_size / 1024 / 1024)
        logger.info("=" * 60)

        all_docs: List[Document] = []
        failed: List[str] = []

        for pdf_file in pdf_files:
            try:
                docs = self._process_single_pdf(pdf_file)
                all_docs.extend(docs)
                logger.info(
                    "✓ %s → %d 个文档块", pdf_file.name, len(docs)
                )
            except Exception as e:
                logger.error("✗ %s 处理失败: %s", pdf_file.name, e)
                failed.append(pdf_file.name)

        if not all_docs:
            raise RuntimeError(
                f"所有 PDF 处理均失败，无法初始化知识库。失败清单: {failed}"
            )

        # 入库
        logger.info("开始向量化并入库 %d 个文档块...", len(all_docs))
        self._store_manager.initialize_collection(all_docs)

        # 构建问答链
        self._build_qa_chain()

        summary = (
            f"\n{'=' * 60}\n"
            f"公共知识库初始化完成！\n"
            f"  成功: {len(pdf_files) - len(failed)} 本\n"
            f"  失败: {len(failed)} 本\n"
            f"  总块数: {len(all_docs)}\n"
        )
        if failed:
            summary += f"  失败清单: {', '.join(failed)}\n"
        summary += f"{'=' * 60}"
        logger.info(summary)

    def query(self, question: str) -> Dict[str, Any]:
        """基于公共知识库回答用户问题。

        Args:
            question: 用户自然语言问题。

        Returns:
            {
                "answer": str,              # LLM 生成的回答 / 拒答提示（含【来源N】内联标记）
                "sources": [                # legacy 来源列表（向后兼容）
                    {
                        "doc": str,
                        "chapter": str,
                        "chunk_index": int,
                        "content_snippet": str,
                        "score": float,
                    },
                    ...
                ],
                "citations": [              # 标准化引用（测评系统直接读取格式）
                    {
                        "context_index": int,   # 对应回答中【来源N】的 N
                        "chunk_id": int,        # Milvus 主键 id（行级唯一）
                        "chunk_uid": str,       # 内容派生稳定标识
                        "doc_name": str,        # 所属文档（数据源位置）
                        "chapter": str,         # 章节路径（数据源位置）
                        "chunk_index": int,
                        "text": str,            # 完整原文片段
                        "score": float,         # 相关度
                        "metadata": dict,       # 全部附加元数据
                    },
                    ...
                ],
                "citation_validation": dict,    # 引用校验规则结构化报告（R1-R7）
            }

        Raises:
            RuntimeError: 知识库尚未初始化。
        """
        if self._qa_chain is None:
            raise RuntimeError(
                "知识库尚未初始化，请先调用 init_knowledge_base() 入库。"
            )

        logger.info("用户提问: %s", question[:100])
        result: Dict[str, Any] = self._qa_chain.invoke(question)
        logger.info(
            "回答完成，来源数: %d", len(result.get("sources", []))
        )
        return result

    async def aquery(self, question: str) -> Dict[str, Any]:
        """query() 的异步镜像 — 返回结构与 query() 完全一致。

        差异仅在 I/O：Embedding 原生异步、Milvus 走线程池桥接、
        Reranker 走 httpx.AsyncClient、LLM 走 ainvoke（详见 qa_chain_async）。

        Raises:
            RuntimeError: 知识库尚未初始化。
        """
        if self._qa_chain is None:
            raise RuntimeError(
                "知识库尚未初始化，请先调用 init_knowledge_base() 入库。"
            )

        pipeline = self._ensure_async_pipeline()
        logger.info("用户提问(async): %s", question[:100])
        result: Dict[str, Any] = await pipeline.decide_and_answer_async({
            "docs": await pipeline.retrieve_async(question),
            "question": question,
        })
        logger.info(
            "回答完成(async)，来源数: %d", len(result.get("sources", []))
        )
        return result

    async def astream(self, question: str) -> AsyncIterator[Any]:
        """流式问答（异步）— 产出统一 StreamEvent 序列。

        事件序列：
            stage(retrieval_start) → retrieval(候选摘要) → token*
            → citations(标准化引用) → final(完整结果)

        拒答场景跳过 retrieval/token，直接 final（answer 为拒答文案）。

        Raises:
            RuntimeError: 知识库尚未初始化。
        """
        import time as _time
        from uuid import uuid4

        from agent.streaming.events import EventType, StreamEvent

        if self._qa_chain is None:
            raise RuntimeError(
                "知识库尚未初始化，请先调用 init_knowledge_base() 入库。"
            )

        pipeline = self._ensure_async_pipeline()
        request_id = uuid4().hex

        def _event(event_type: EventType, payload: Dict[str, Any]) -> Any:
            return StreamEvent(
                type=event_type, request_id=request_id,
                payload=payload, ts=_time.time(),
            )

        logger.info("流式提问(async): %s", question[:100])
        yield _event(EventType.STAGE, {"stage": "retrieval_start"})

        docs_with_scores = await pipeline.retrieve_async(question)

        if not docs_with_scores:
            result = pipeline.build_refusal_result()
            yield _event(EventType.FINAL, {"result": result})
            return

        yield _event(EventType.RETRIEVAL, {
            "candidates": [
                {
                    "doc": doc.metadata.get("doc_name", ""),
                    "chapter": doc.metadata.get("chapter", ""),
                    "score": round(score, 4),
                }
                for doc, score in docs_with_scores
            ],
        })

        # 逐 token 推流；引用必须晚于正文生成（风险 R-07）
        parts: List[str] = []
        async for delta in pipeline.stream_answer(docs_with_scores, question):
            parts.append(delta)
            yield _event(EventType.TOKEN, {"delta": delta})

        answer = "".join(parts).strip()
        result = pipeline.build_answer_result(docs_with_scores, question, answer)

        yield _event(EventType.CITATIONS, {"citations": result["citations"]})
        yield _event(EventType.FINAL, {"result": result})

    def clear_kb(self) -> None:
        """清空公共知识库所有数据（仅管理员使用）。"""
        logger.warning("正在清空 public_kb 集合...")
        self._store_manager.clear_collection()
        self._qa_chain = None
        self._async_pipeline = None
        logger.warning("public_kb 集合已清空。")

    def ensure_loaded(self) -> None:
        """确保已加载现有集合并构建问答链（不重新入库）。

        供外部节点（如 agent knowledge_qa）在知识库已初始化时安全激活引擎，
        避免直接触碰 _store_manager / _build_qa_chain 私有成员。
        """
        self._store_manager.load_existing()
        self._build_qa_chain()

    def add_pdf(self, pdf_path: str) -> int:
        """解析并增量导入单个 PDF（无需停服重建）。

        Args:
            pdf_path: 单个 PDF 文件的绝对路径。

        Returns:
            本次导入的文档块数量。

        Raises:
            FileNotFoundError: PDF 文件不存在。
        """
        pdf_file = Path(pdf_path).resolve()
        if not pdf_file.exists():
            raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")
        if not pdf_file.suffix.lower() == ".pdf":
            raise ValueError(f"文件不是 PDF: {pdf_path}")

        logger.info("增量导入 PDF: %s", pdf_file.name)

        # 解析 → 清洗 → 切片
        docs = self._process_single_pdf(pdf_file)
        if not docs:
            logger.warning("%s 处理后无有效内容，跳过", pdf_file.name)
            return 0

        # 增量入库（不 drop 旧集合）
        self._store_manager.add_documents(docs)

        # 若 QA 链未初始化，则构建
        if self._qa_chain is None:
            self._build_qa_chain()

        logger.info("增量导入完成: %s → %d 块", pdf_file.name, len(docs))
        return len(docs)

    # ==========================================================
    #  内部方法
    # ==========================================================

    def _process_single_pdf(self, pdf_path: Path) -> List[Document]:
        """处理单个 PDF：解析 → 清洗 → 切片。"""
        # 步骤 1: MinerU 解析 → Markdown
        raw_md = self._parser.parse(pdf_path)

        # 步骤 2: 文本清洗
        cleaned_md = self._cleaner.clean(raw_md)

        # 步骤 3: 语义切片
        documents = self._chunker.chunk(cleaned_md, pdf_path.name)
        if not documents:
            logger.warning("%s 切片后无有效内容，跳过", pdf_path.name)

        return documents

    def _create_llm(self) -> BaseChatModel:
        """根据配置创建 ChatOpenAI 实例（统一走 llm_factory）。"""
        return create_llm(self._settings)

    def _build_qa_chain(self) -> None:
        """基于当前 vector_store 构建 LCEL 问答链。"""
        # 尝试获取 MilvusClient（用于混合检索）
        try:
            collection = self._store_manager.collection
        except RuntimeError:
            collection = None
            logger.info("MilvusClient 不可用，问答链将使用纯稠密检索")

        self._qa_chain = build_qa_chain(
            vector_store=self._store_manager.store,
            llm=self._llm,
            settings=self._settings,
            collection=collection,
            embeddings=self._embeddings,
        )
        # 同步链重建后，异步流水线一并失效（下次使用时懒建）
        self._async_pipeline = None

    def _ensure_async_pipeline(self) -> Any:
        """懒建异步 RAG 流水线（与同步链共享 vector_store/llm/collection/embeddings）。"""
        if self._async_pipeline is not None:
            return self._async_pipeline
        from .qa_chain_async import AsyncRAGPipeline

        try:
            collection = self._store_manager.collection
        except RuntimeError:
            collection = None
            logger.info("MilvusClient 不可用，异步问答链将使用纯稠密检索")

        self._async_pipeline = AsyncRAGPipeline(
            vector_store=self._store_manager.store,
            llm=self._llm,
            settings=self._settings,
            collection=collection,
            embeddings=self._embeddings,
        )
        return self._async_pipeline
