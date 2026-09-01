# 功能：定义入库、检索、问答和 Milvus 数据的跨模块契约。
"""公共知识库建库与检索的最小数据契约。

本模块只定义跨组件共享的稳定边界，不访问 Milvus、Embedding 或 LLM。
后续存储与检索实现应复用这些校验和诊断类型，避免各自重复约定。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from langchain_core.documents import Document


class KnowledgeBaseContractError(ValueError):
    """公共知识库输入或组件契约不满足。"""


class ConfigurationContractError(KnowledgeBaseContractError):
    """配置或集合结构契约不满足。"""


class IngestionContractError(KnowledgeBaseContractError):
    """入库文档或向量批次契约不满足。"""


class RetrievalContractError(KnowledgeBaseContractError):
    """检索输入或检索阶段契约不满足。"""


class RetrievalMode(str, Enum):
    """一次检索最终实际采用的路径。"""

    HYBRID_RERANK = "hybrid_rerank"
    HYBRID_RRF = "hybrid_rrf"
    DENSE_RERANK = "dense_rerank"
    DENSE_NATIVE = "dense_native"
    DENSE_LANGCHAIN = "dense_langchain"
    REFUSAL = "refusal"


class RerankerStatus(str, Enum):
    """Reranker 的可观察状态。"""

    NOT_REQUESTED = "not_requested"
    SUCCESS = "success"
    FAILED = "failed"
    DISABLED = "disabled"


@dataclass(frozen=True)
class MilvusCollectionContract:
    """目标 Milvus 集合的稳定字段与检索契约。"""

    primary_field: str = "id"
    text_field: str = "text"
    dense_field: str = "vector"
    sparse_field: str = "sparse_vector"
    bm25_function_name: str = "text_bm25_emb"
    dense_metric: str = "COSINE"
    sparse_metric: str = "BM25"

    def validate(self) -> None:
        names = (
            self.primary_field,
            self.text_field,
            self.dense_field,
            self.sparse_field,
            self.bm25_function_name,
        )
        if any(not name.strip() for name in names):
            raise ConfigurationContractError("Milvus 字段名和 Function 名不能为空")
        fields = {
            self.primary_field,
            self.text_field,
            self.dense_field,
            self.sparse_field,
        }
        if len(fields) != 4:
            raise ConfigurationContractError("Milvus 主键、文本、稠密和稀疏字段名必须互不相同")
        if self.dense_metric != "COSINE":
            raise ConfigurationContractError("当前 dense 检索契约要求 metric=COSINE")
        if self.sparse_metric != "BM25":
            raise ConfigurationContractError("服务端 BM25 检索契约要求 metric=BM25")


@dataclass
class RetrievalDiagnostics:
    """检索链路诊断信息，不改变现有问答业务字段。"""

    retrieval_mode: RetrievalMode
    dense_count: int = 0
    sparse_count: int = 0
    fusion_count: int = 0
    reranker_status: RerankerStatus = RerankerStatus.NOT_REQUESTED
    threshold: Optional[float] = None
    fallback_reason: Optional[str] = None

    def __post_init__(self) -> None:
        for name in ("dense_count", "sparse_count", "fusion_count"):
            if getattr(self, name) < 0:
                raise RetrievalContractError(f"{name} 不能为负数")
        if self.threshold is not None and not 0.0 <= self.threshold <= 1.0:
            raise RetrievalContractError("threshold 必须处于 [0, 1]")

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["retrieval_mode"] = self.retrieval_mode.value
        data["reranker_status"] = self.reranker_status.value
        return data


def validate_question(question: str) -> str:
    """校验并规范化检索问题。"""
    if not isinstance(question, str):
        raise RetrievalContractError("question 必须是字符串")
    normalized = question.strip()
    if not normalized:
        raise RetrievalContractError("question 不能为空")
    return normalized


def validate_ingestion_documents(documents: Sequence[Document]) -> List[Document]:
    """校验入库 Document 最小契约并返回列表副本。"""
    if not documents:
        raise IngestionContractError("documents 不能为空")

    validated = list(documents)
    required_metadata = ("doc_name", "chapter", "chunk_index")
    for index, document in enumerate(validated):
        if not isinstance(document, Document):
            raise IngestionContractError(f"documents[{index}] 必须是 Document")
        if not document.page_content.strip():
            raise IngestionContractError(f"documents[{index}].page_content 不能为空")
        metadata = document.metadata or {}
        missing = [key for key in required_metadata if key not in metadata]
        if missing:
            raise IngestionContractError(
                f"documents[{index}] 缺少 metadata: {', '.join(missing)}"
            )
        if not str(metadata["doc_name"]).strip():
            raise IngestionContractError(f"documents[{index}].metadata.doc_name 不能为空")
        if not str(metadata["chapter"]).strip():
            raise IngestionContractError(f"documents[{index}].metadata.chapter 不能为空")
        if not isinstance(metadata["chunk_index"], int) or metadata["chunk_index"] < 0:
            raise IngestionContractError(
                f"documents[{index}].metadata.chunk_index 必须是非负整数"
            )
    return validated


def validate_embedding_batch(
    documents: Sequence[Document],
    vectors: Sequence[Sequence[float]],
    expected_dim: int,
) -> None:
    """校验文档与 dense embedding 批次的一一对应和维度。"""
    if expected_dim <= 0:
        raise IngestionContractError("expected_dim 必须为正整数")
    if len(documents) != len(vectors):
        raise IngestionContractError(
            f"文档数 {len(documents)} 与向量数 {len(vectors)} 不一致"
        )
    for index, vector in enumerate(vectors):
        if len(vector) != expected_dim:
            raise IngestionContractError(
                f"vectors[{index}] 维度为 {len(vector)}，期望 {expected_dim}"
            )


def validate_qa_result(result: Dict[str, Any]) -> None:
    """校验 PublicKnowledgeRAG.query() 的既有外部返回契约。"""
    if not isinstance(result, dict):
        raise RetrievalContractError("问答结果必须是 dict")
    required = ("answer", "sources", "citations", "citation_validation")
    missing = [key for key in required if key not in result]
    if missing:
        raise RetrievalContractError(f"问答结果缺少字段: {', '.join(missing)}")
    if not isinstance(result["answer"], str):
        raise RetrievalContractError("answer 必须是字符串")
    if not isinstance(result["sources"], list):
        raise RetrievalContractError("sources 必须是列表")
    if not isinstance(result["citations"], list):
        raise RetrievalContractError("citations 必须是列表")
    if not isinstance(result["citation_validation"], dict):
        raise RetrievalContractError("citation_validation 必须是字典")
    diagnostics = result.get("retrieval_diagnostics")
    if diagnostics is not None and not isinstance(diagnostics, dict):
        raise RetrievalContractError("retrieval_diagnostics 必须是字典")


# ============================================================
# 法条时效性（任务 M3）— 检索过滤表达式生成
# ============================================================

def build_effective_expr(today: date) -> str:
    """生成"仅召回已施行法条"的 Milvus 标量过滤表达式。

    动态字段 ``effective_date`` 为 YYYY-MM-DD 字符串（缺失为 NULL）：
    ``effective_date is null or effective_date <= <today>``。
    这样旧数据（无该字段）不会被过滤掉，新数据按施行日期生效。
    """
    return f'effective_date is null or effective_date <= "{today.isoformat()}"'
