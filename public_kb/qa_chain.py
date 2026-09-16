"""
LCEL 问答链 — 基于 LangChain 1.0+ Runnable 接口的 RAG 问答流水线。

严格遵守：
  - 使用 | 运算符拼接 Runnable（LCEL 语法）
  - 禁止使用任何 langchain.chains 下的旧版 Chain
  - 检索 + 拒答判断 + LLM 调用 + 格式化输出 一体化

检索契约（2026-09 整改，D3 零降级）：
  - 混合检索 = 稠密 COSINE + 稀疏 BM25（服务端 Function 生成）→ RRF 融合
    → Reranker 精排 → 动态阈值过滤；任何环节失败一律上抛，不做静默降级
  - 集合 schema 必须含 sparse_vector 字段——构建链时 fail-fast 校验，
    缺失即抛错（拒绝以降级模式运行）
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple, Optional

import requests
from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda, RunnablePassthrough
from langchain_milvus import Milvus as MilvusVectorStore
from pymilvus import AnnSearchRequest, RRFRanker

from .chunk_ids import compute_chunk_uid
from .citations import CitationValidator, build_citations
from .config import Settings

logger = logging.getLogger(__name__)


# ============================================================
#  提示词模板（system 部分统一取自 config.Settings.system_prompt）
# ============================================================

# 内联引用指令（enable_inline_citations 开启时追加到 System Prompt）
INLINE_CITATION_INSTRUCTION = (
    "回答时必须在相关结论句末标注引用来源编号，格式如【来源1】【来源2】。\n"
    "未使用的参考资料不要标注其编号。"
)

USER_TEMPLATE = """参考资料：
{context}

用户问题：{question}"""


def _build_prompt(system_text: str, enable_inline_citations: bool = True) -> ChatPromptTemplate:
    """构建问答提示词模板。

    Args:
        system_text: system 提示词正文（来自 Settings.system_prompt）。
        enable_inline_citations: 是否要求 LLM 在回答中内联标注【来源N】。
    """
    system = system_text
    if enable_inline_citations:
        system += "\n\n" + INLINE_CITATION_INSTRUCTION
    return ChatPromptTemplate.from_messages([
        ("system", system),
        ("user",   USER_TEMPLATE),
    ])


# ============================================================
#  格式化工具函数
# ============================================================

def _format_docs(docs_with_scores: List[Tuple[Document, float]]) -> str:
    """将检索到的文档列表格式化为拼接上下文字符串。

    Args:
        docs_with_scores: (Document, similarity_score) 列表。

    Returns:
        带来源标注的拼接文本。
    """
    parts: List[str] = []
    for i, (doc, score) in enumerate(docs_with_scores, 1):
        doc_name = doc.metadata.get("doc_name", "未知文档")
        chapter = doc.metadata.get("chapter", "未知章节")
        parts.append(
            f"[来源{i}] 文档: {doc_name} | 章节: {chapter} | 相关度: {score:.2%}\n"
            f"{doc.page_content}"
        )
    return "\n\n---\n\n".join(parts)


def _build_sources(
    docs_with_scores: List[Tuple[Document, float]],
) -> List[Dict[str, Any]]:
    """从检索结果构建引用来源列表（legacy 视图，保持向后兼容）。

    Args:
        docs_with_scores: (Document, similarity_score) 列表。

    Returns:
        结构化来源信息列表。
    """
    return [
        {
            "doc": doc.metadata.get("doc_name", "未知文档"),
            "chapter": doc.metadata.get("chapter", "未知章节"),
            "chunk_index": doc.metadata.get("chunk_index", -1),
            "content_snippet": doc.page_content[:200] + "..." if len(doc.page_content) > 200 else doc.page_content,
            "score": round(score, 4),
        }
        for doc, score in docs_with_scores
    ]


# ============================================================
#  Milvus 实体 → Document 溯源元数据透传
# ============================================================

# 检索输出字段：优先全字段（含动态元数据），失败回退基础字段
_OUTPUT_FIELDS_ALL = ["*"]
_OUTPUT_FIELDS_FALLBACK = ["text", "id", "doc_name", "chapter", "chunk_index"]

# Document metadata 中不属于业务溯源字段的键（来自 Milvus 实体）
_EXCLUDED_META_KEYS = ("text", "vector", "sparse_vector", "id", "distance", "entity")


def _normalize_hit_entity(entity: Any) -> Dict[str, Any]:
    """归一化 pymilvus 3.x 检索命中实体。

    MilvusClient.search/hybrid_search 的 Hit.entity 为嵌套结构:
        {"id": int, "distance": float, "entity": {实际标量字段...}}
    实际字段在内层 entity 中；get() 查询返回的行则是平铺结构。
    本函数统一为平铺 dict（内层字段 + distance 兜底）。
    """
    if isinstance(entity, dict) and isinstance(entity.get("entity"), dict):
        merged = dict(entity["entity"])
        merged.setdefault("distance", entity.get("distance"))
        merged.setdefault("id", entity.get("id"))
        return merged
    return dict(entity) if isinstance(entity, dict) else {}


def _entity_to_doc(entity: Any, score: float) -> Document:
    """将 Milvus 检索命中实体转换为携带完整溯源元数据的 Document。

    元数据写入约定：
      - chunk_id   : Milvus 主键值（行级唯一；主键字段为 chunk_uid，
        Hit 顶层 id 即主键取值，回表验证"错误关联"用）
      - chunk_uid  : 内容派生稳定标识（主键化后与 chunk_id 同值）
      - doc_name / chapter / chunk_index : 数据源位置
      - 其余动态字段（source_file / source_url / publish_date 等）原样透传

    Args:
        entity: Milvus 命中实体（pymilvus 3.x Hit.entity 嵌套结构或平铺 dict）。
        score: 检索相关度分数。

    Returns:
        带完整溯源元数据的 Document。
    """
    entity = _normalize_hit_entity(entity)
    text = str(entity.get("text", "") or "")

    meta: Dict[str, Any] = {}
    for key, value in entity.items():
        if key in _EXCLUDED_META_KEYS:
            continue
        if value is None:
            continue
        meta[key] = value

    # chunk_id = Milvus 主键取值（主键字段 chunk_uid；Hit 顶层 id 即主键值，
    # _normalize_hit_entity 已将其合并进 entity["id"]；平铺行则回退主键字段）
    meta["chunk_id"] = entity.get("id") or entity.get("chunk_uid")
    meta.setdefault("doc_name", "未知文档")
    meta.setdefault("chapter", "未知章节")
    meta.setdefault("chunk_index", -1)

    # 存量数据无 chunk_uid → 即时计算（与入库侧 compute_chunk_uid 同口径）
    if not meta.get("chunk_uid"):
        meta["chunk_uid"] = compute_chunk_uid(text, meta)

    return Document(page_content=text, metadata=meta)


def _search_with_full_fields(
    collection: Any,
    settings: Settings,
    *,
    data: List[Any],
    anns_field: str,
    search_params: Dict[str, Any],
    limit: int,
) -> List[Any]:
    """pymilvus search，优先 output_fields=['*'] 全字段（含动态元数据），
    服务端不支持时回退基础字段列表。
    """
    try:
        return collection.search(
            settings.collection_name,
            data=data,
            anns_field=anns_field,
            search_params=search_params,
            limit=limit,
            output_fields=_OUTPUT_FIELDS_ALL,
        )[0]
    except Exception as e:
        logger.warning("output_fields=['*'] 检索失败 (%s)，回退基础字段", e)
        return collection.search(
            settings.collection_name,
            data=data,
            anns_field=anns_field,
            search_params=search_params,
            limit=limit,
            output_fields=_OUTPUT_FIELDS_FALLBACK,
        )[0]


def _hybrid_search_with_full_fields(
    collection: Any,
    settings: Settings,
    *,
    reqs: List[Any],
    ranker: Any,
    limit: int,
) -> List[Any]:
    """pymilvus hybrid_search，output_fields=['*'] 优先，失败回退基础字段。"""
    try:
        return collection.hybrid_search(
            settings.collection_name,
            reqs=reqs,
            ranker=ranker,
            limit=limit,
            output_fields=_OUTPUT_FIELDS_ALL,
        )[0]
    except Exception as e:
        logger.warning("hybrid_search output_fields=['*'] 失败 (%s)，回退基础字段", e)
        return collection.hybrid_search(
            settings.collection_name,
            reqs=reqs,
            ranker=ranker,
            limit=limit,
            output_fields=_OUTPUT_FIELDS_FALLBACK,
        )[0]


# ============================================================
#  LCEL 问答链构建
# ============================================================

def _validate_mixed_schema(collection: Any, settings: Settings) -> None:
    """构建问答链前一次性校验集合 schema（D3 fail-fast；结果随链缓存）。

    集合缺 sparse_vector 字段（旧版纯稠密 schema）时直接抛 RuntimeError，
    不做任何降级；同时将"每查询一次 describe_collection"收敛为构建期一次。
    """
    info = collection.describe_collection(settings.collection_name)
    field_names = [f.get("name", "") for f in info.get("fields", [])]
    if "sparse_vector" not in field_names:
        raise RuntimeError(
            f"Milvus 集合 '{settings.collection_name}' 缺少 sparse_vector 字段"
            "（旧版纯稠密 schema，混合检索无法工作）。按零降级口径拒绝以降级"
            "模式运行，请先完成知识库重建（文档重切分 + init_knowledge_base）。"
        )
    logger.info(
        "Schema 校验通过: 集合=%s 含 sparse_vector 字段（混合检索就绪）",
        settings.collection_name,
    )


def build_qa_chain(
    vector_store: MilvusVectorStore,
    llm: BaseChatModel,
    settings: Settings,
    collection: Any,
    embeddings: Any,
) -> Any:
    """构建完整的 LCEL RAG 问答链（混合检索版）。

    链结构：
      question
        │
        ├─→ _retrieve(question) ─→ docs_with_scores
        │     │
        │     ├─ 稠密向量检索 (COSINE, k=30, nprobe=32)
        │     ├─ 稀疏向量检索 (BM25 Function, k=30)
        │     ├─ RRF 融合 (k=60, 取 Top-30)
        │     ├─ Reranker 精排 (settings.reranker_model 实配模型)
        │     └─ 动态阈值过滤
        │
        └─→ _decide_and_answer(docs_with_scores, question)
              │
              ├── 无相关结果 → 直接返回拒答
              └── 有结果 → prompt | llm | StrOutputParser → 返回回答 + 来源

    零降级契约（D3）：collection 与 embeddings 必须提供，构建期校验 schema；
    检索/精排任何失败一律上抛（由上层图节点的 _with_fallback 统一转为
    友好提示），本链内部不存在任何降级路径。

    Args:
        vector_store: Milvus 向量存储包装器（保留集合管理职责；检索不再经由它）。
        llm: LangChain 兼容的 ChatModel。
        settings: 全局配置。
        collection: MilvusClient 实例（用于 hybrid_search）。
        embeddings: Embedding 模型实例（用于生成稠密查询向量）。

    Returns:
        可调用的 LCEL Runnable 链，invoke(question) → dict。
    """
    if collection is None or embeddings is None:
        raise ValueError(
            "build_qa_chain 需要 collection(MilvusClient) 与 embeddings 非空"
            "（零降级口径：已移除纯稠密降级检索路径）"
        )
    _validate_mixed_schema(collection, settings)
    prompt = _build_prompt(
        settings.system_prompt,
        enable_inline_citations=settings.enable_inline_citations,
    )

    # 引用溯源校验器（fail-soft：只产出结构化报告，不阻断回答）
    validator = CitationValidator(settings.citation_rules)

    # ── Reranker 客户端（延迟初始化）──
    _reranker: Optional[_SiliconFlowReranker] = None

    def _get_reranker() -> "_SiliconFlowReranker":
        nonlocal _reranker
        if _reranker is None:
            _reranker = _SiliconFlowReranker(
                model=settings.reranker_model,
                api_key=settings.embedding_api_key,
                base_url=settings.embedding_base_url,
            )
        return _reranker

    # ── 阶段 1: 混合检索（RunnableLambda 包装）──
    def _retrieve(question: str) -> List[Tuple[Document, float]]:
        """混合检索：稠密 COSINE + 稀疏 BM25 → RRF 融合 → Reranker 精排 → 动态阈值。

        零降级（D3）：schema 已在构建期经 _validate_mixed_schema 校验；
        向量生成、检索、精排任何环节失败一律上抛，不做静默降级。
        """
        # 1. 生成稠密 query 向量
        dense_vec = embeddings.embed_query(question)

        # 2. 构造双路检索请求
        dense_req = AnnSearchRequest(
            data=[dense_vec],
            anns_field="vector",
            param={
                "metric_type": "COSINE",
                "params": {"nprobe": settings.nprobe},
            },
            limit=settings.hybrid_dense_limit,
        )
        # 稀疏路查询传原始文本，服务端 BM25 Function 自动 tokenize；
        # 度量必须与稀疏索引一致（BM25 Function 场景下不允许写 IP/COSINE）
        sparse_req = AnnSearchRequest(
            data=[question],
            anns_field="sparse_vector",
            param={"metric_type": "BM25"},
            limit=settings.hybrid_sparse_limit,
        )

        # 3. RRF 融合（全字段输出，含动态溯源元数据）
        rrf = RRFRanker(k=settings.rrf_k)
        raw_hits = _hybrid_search_with_full_fields(
            collection, settings,
            reqs=[dense_req, sparse_req],
            ranker=rrf,
            limit=settings.hybrid_fusion_limit,
        )

        if not raw_hits:
            logger.debug("混合检索: 无命中结果")
            return []

        # 4. 转换为 (doc_content, rrf_score, entity) 列表
        candidates: List[Tuple[str, float, dict]] = []
        for hit in raw_hits:
            entity = _normalize_hit_entity(hit.entity)
            candidates.append((
                entity.get("text", ""),
                hit.score,
                entity,
            ))

        logger.info(
            "混合检索: 稠密=%d路, 稀疏=%d路, RRF融合后=%d条",
            settings.hybrid_dense_limit, settings.hybrid_sparse_limit,
            len(candidates),
        )

        # 5. Reranker 精排（失败上抛，零降级）
        reranker = _get_reranker()
        docs_text = [c[0] for c in candidates]
        reranked = reranker.rerank(
            query=question,
            documents=docs_text,
            top_k=settings.retrieval_top_k,
        )

        if not reranked:
            logger.debug("Reranker 精排: 无有效结果")
            return []

        # 6. 动态阈值过滤
        top_score = reranked[0]["relevance_score"]
        threshold = _adaptive_threshold(top_score)

        results: List[Tuple[Document, float]] = []
        for item in reranked:
            if item["relevance_score"] >= threshold:
                idx = item["index"]
                if idx < len(candidates):
                    _, rrf_score, entity = candidates[idx]
                    doc = _entity_to_doc(entity, item["relevance_score"])
                    doc.metadata["rrf_score"] = round(rrf_score, 4)
                    results.append((doc, item["relevance_score"]))

        logger.info(
            "检索完成: RRF=%d条, 精排top1=%.3f, 过滤后=%d条 (threshold=%.2f)",
            len(candidates), top_score, len(results), threshold,
        )
        return results

    # ── 阶段 2: 判断 + 回答 ──
    def _decide_and_answer(inputs: Dict[str, Any]) -> Dict[str, Any]:
        """核心决策节点：检索为空则拒答，有结果则走 LLM。

        返回结构（引用溯源标准化）：
            {
                "answer": str,                    # 回答 / 拒答提示（含【来源N】内联标记）
                "sources": list,                  # legacy 视图（向后兼容）
                "citations": list,                # 标准化引用（chunk_id/chunk_uid/数据源位置/完整原文/元数据）
                "citation_validation": dict,      # 校验规则结构化报告（R1-R7，fail-soft）
            }
        """
        docs_with_scores: List[Tuple[Document, float]] = inputs["docs"]
        question: str = inputs["question"]

        if not docs_with_scores:
            logger.info("检索结果不足，触发拒答")
            refusal_answer = "抱歉，公共知识库中暂无相关内容，无法提供可靠回答。"
            refusal_report = validator.validate(
                [], refusal_answer, [], is_refusal=True,
            )
            return {
                "answer": refusal_answer,
                "sources": [],
                "citations": [],
                "citation_validation": refusal_report.to_dict(),
            }

        context = _format_docs(docs_with_scores)
        sources = _build_sources(docs_with_scores)
        citations = build_citations(docs_with_scores)

        answer_chain = prompt | llm | StrOutputParser()
        raw_answer: str = answer_chain.invoke({
            "context": context,
            "question": question,
        })
        answer = raw_answer.strip()

        # 引用溯源校验（无遗漏 / 无错误关联，fail-soft）
        context_ids = [
            doc.metadata.get("chunk_id") for doc, _ in docs_with_scores
        ]
        report = validator.validate(citations, answer, context_ids)

        logger.info(
            "引用校验: 上下文=%d块, 引用=%d条, 标记=%s, 全部通过=%s",
            len(context_ids), len(citations),
            report.cited_markers, report.all_passed,
        )

        return {
            "answer": answer,
            "sources": sources,
            "citations": [c.to_dict() for c in citations],
            "citation_validation": report.to_dict(),
        }

    # ── 用 LCEL 管道符号 | 拼接 ──
    chain = (
        {
            "docs": RunnableLambda(_retrieve),
            "question": RunnablePassthrough(),
        }
        | RunnableLambda(_decide_and_answer)
    )

    logger.info(
        "LCEL 问答链构建完成（混合检索: dense+RRF+Reranker; embedding=%s, reranker=%s）",
        settings.embedding_model, settings.reranker_model,
    )
    return chain


# ============================================================
#  重排序 & 动态阈值
# ============================================================

class _SiliconFlowReranker:
    """SiliconFlow Reranker API 客户端（OpenAI 兼容 /rerank 端点）。

    零降级契约（D3/D6）：凭据/端点复用 Embedding 提供方配置（有意设计），
    base_url 缺失即抛错（无默认兜底）；调用失败异常上抛，不返回假分数。
    """

    def __init__(self, model: str, api_key: str, base_url: str) -> None:
        if not base_url:
            raise ValueError(
                "Reranker base_url 缺失（复用 EMBEDDING_BASE_URL，.env 必填，无兜底）"
            )
        self._model = model
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def rerank(
        self, query: str, documents: List[str], top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """调用 SiliconFlow Reranker API 精排。

        Args:
            query: 用户问题。
            documents: 候选文档文本列表。
            top_k: 返回 Top-N 结果。

        Returns:
            [{"index": int, "relevance_score": float}, ...] 按分数降序。

        Raises:
            任何网络/HTTP 异常原样上抛（零降级：不回退假分数，见 WP3.6）。
        """
        if not documents:
            return []
        resp = requests.post(
            f"{self._base_url}/rerank",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._model,
                "query": query,
                "documents": documents,
                "top_n": min(top_k, len(documents)),
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        return sorted(results, key=lambda x: x.get("relevance_score", 0), reverse=True)


def _adaptive_threshold(top_score: float) -> float:
    """基于 Reranker 最高分动态决定过滤阈值。

    策略：
    - top1 ≥ 0.75 → 高置信，放宽至 0.40，允许低分 chunk 补充上下文
    - top1 ≥ 0.50 → 中等置信，阈值 0.45
    - top1 < 0.50 → 直接使用 0.50（实际由 _decide_and_answer 判定是否拒答）
    """
    if top_score >= 0.75:
        return 0.40
    if top_score >= 0.50:
        return 0.45
    return 0.50
