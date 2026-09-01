# 功能：集中加载和管理公共知识库所有配置参数。
"""
统一配置中心 — 集中管理所有可配置参数。

使用方式：
    from public_kb.config import Settings
    settings = Settings()                         # 从 .env 自动加载
    settings = Settings(milvus_host="192.168.1.1") # 或显式覆盖
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

# 自动加载项目根目录的 .env
_ENV_PATH = os.path.join(os.path.dirname(__file__), "..", ".env")
load_dotenv(_ENV_PATH)


@dataclass
class CitationRuleConfig:
    """引用来源校验规则开关 — 法规类专业场景溯源规则集。

    每条规则 fail-soft：只产出结构化校验报告，不阻断回答返回。
    测评系统依据 citation_validation 做自动化评估。
    """

    # R1: 每条引用必须有 Milvus 行级 chunk_id
    require_chunk_id: bool = True
    # R2: 每条引用必须有内容派生 chunk_uid
    require_chunk_uid: bool = True
    # R3: 数据源位置（doc_name / chapter）必须非空且可定位
    require_source_location: bool = True
    # R4: 原文片段必须完整（非空、未被截断）
    require_full_text: bool = True
    # R5: 无遗漏 — 所有进入 LLM 上下文的 chunk 必须全部出现在 citations 中
    check_context_completeness: bool = True
    # R6: 回答中的【来源N】标记必须全部解析到有效引用（无幻觉引用）
    check_marker_validity: bool = True
    # R7: 严格模式 — 上下文 chunk 必须全部被回答标记引用（默认关，
    #     开启后未标记即判失败；关闭时未标记 chunk 仅记入 uncited_chunks）
    enforce_all_context_cited: bool = False


@dataclass
class Settings:
    """公共知识库全局配置，所有字段均可通过构造参数覆盖。"""

    # ============================================================
    # Milvus 连接
    # ============================================================
    milvus_host: str = field(
        default_factory=lambda: os.getenv("MILVUS_HOST", "localhost")
    )
    milvus_port: str = field(
        default_factory=lambda: os.getenv("MILVUS_PORT", "19530")
    )
    milvus_uri: str = field(
        default_factory=lambda: os.getenv("MILVUS_URI", "")
    )
    milvus_token: str = field(
        default_factory=lambda: os.getenv("MILVUS_TOKEN", "")
    )
    milvus_timeout: int = field(
        default_factory=lambda: int(os.getenv("MILVUS_TIMEOUT", "30"))
    )
    collection_name: str = field(
        default_factory=lambda: os.getenv("MILVUS_COLLECTION", "public_kb")
    )
    collection_schema_version: str = "public_kb_v2"
    enable_bm25: bool = field(
        default_factory=lambda: os.getenv("ENABLE_MILVUS_BM25", "false").lower()
        in {"1", "true", "yes"}
    )
    milvus_experiment_prefix: str = "public_kb_hybrid_poc_"
    milvus_dense_index_type: str = "IVF_FLAT"
    milvus_sparse_index_type: str = "SPARSE_INVERTED_INDEX"
    bm25_k1: float = 1.2
    bm25_b: float = 0.75
    bm25_analyzer_type: str = "chinese"

    @property
    def resolved_milvus_uri(self) -> str:
        """返回显式 URI；未配置时兼容现有 host/port。"""
        return self.milvus_uri or f"http://{self.milvus_host}:{self.milvus_port}"

    # ============================================================
    # Embedding 模型
    # ============================================================
    embedding_model: str = field(
        default_factory=lambda: os.getenv(
            "EMBEDDING_MODEL", "BAAI/bge-m3"
        )
    )
    embedding_api_key: str = field(
        default_factory=lambda: os.getenv(
            "EMBEDDING_API_KEY",
            os.getenv(
                "SILICONFLOW_API_KEY",
                os.getenv("CLOSEAI_API_KEY", os.getenv("DEEPSEEK_API_KEY", "")),
            ),
        )
    )
    embedding_base_url: str = field(
        default_factory=lambda: os.getenv(
            "EMBEDDING_BASE_URL",
            os.getenv("SILICONFLOW_BASE_URL", os.getenv("CLOSEAI_BASE_URL", "")),
        )
    )
    # BAAI/bge-m3 中文专用，1024 维，8192 token 上限
    embedding_dim: int = 1024

    # ============================================================
    # LLM 问答模型
    # ============================================================
    llm_model: str = field(
        default_factory=lambda: os.getenv("LLM_MODEL", "deepseek-chat")
    )
    llm_api_key: str = field(
        default_factory=lambda: os.getenv(
            "LLM_API_KEY",
            os.getenv("DEEPSEEK_API_KEY", os.getenv("CLOSEAI_API_KEY", "")),
        )
    )
    llm_base_url: str = field(
        default_factory=lambda: os.getenv(
            "LLM_BASE_URL",
            os.getenv("DEEPSEEK_BASE_URL", os.getenv("CLOSEAI_BASE_URL", "")),
        )
    )
    llm_temperature: float = 0.0

    # ============================================================
    # MinerU 解析
    # ============================================================
    mineru_timeout: int = 3600  # OCR 版 PDF 需要较长超时
    # magic-pdf 输出目录（临时中间产物）
    # 注意（M6 治理）：此目录存放 MinerU 解析的中间 Markdown 缓存，仅用于
    # 断点续跑，勿与 DATA 的组织化目录（cleaned_v1 等）混放或提交入库。
    mineru_output_dir: str = field(
        default_factory=lambda: os.path.join(
            os.path.dirname(__file__), "..", "DATA", "raw_data"
        )
    )

    # ── MinerU 远程解析服务（三档路由 Tier C / 部署补充方案 §4）──
    # 本地侧只依赖 HTTP 协议，换部署位置只改 base_url（PDF_PARSE_BASE_URL 优先）。
    mineru_api_base_url: str = field(
        default_factory=lambda: os.getenv(
            "PDF_PARSE_BASE_URL", os.getenv("MINERU_API_BASE_URL", "")
        )
    )
    mineru_api_token: str = field(
        default_factory=lambda: os.getenv("MINERU_API_TOKEN", "")
    )
    mineru_api_timeout: int = field(
        default_factory=lambda: int(os.getenv("MINERU_API_TIMEOUT", "1800"))
    )

    # ── PDF 三档路由（三档路由计划 §7）──
    # 总开关：关闭时回退旧的 MinerUParser 全量链路（M1 行为不变）。
    pdf_tiered_routing_enabled: bool = field(
        default_factory=lambda: os.getenv(
            "PDF_TIERED_ROUTING_ENABLED", "false"
        ).lower() in {"1", "true", "yes"}
    )
    pdf_tiered_two_col_confidence: float = field(
        default_factory=lambda: float(os.getenv("PDF_TIERED_TWO_COL_CONFIDENCE", "0.80"))
    )
    pdf_tiered_min_text_chars: int = field(
        default_factory=lambda: int(os.getenv("PDF_TIERED_MIN_TEXT_CHARS", "80"))
    )
    pdf_tiered_table_min_rows: int = field(
        default_factory=lambda: int(os.getenv("PDF_TIERED_TABLE_MIN_ROWS", "2"))
    )
    pdf_tiered_table_max_empty_ratio: float = field(
        default_factory=lambda: float(
            os.getenv("PDF_TIERED_TABLE_MAX_EMPTY_RATIO", "0.30")
        )
    )
    pdf_tiered_image_area_ratio: float = field(
        default_factory=lambda: float(
            os.getenv("PDF_TIERED_IMAGE_AREA_RATIO", "0.35")
        )
    )
    pdf_tiered_expand_boundary_pages: int = field(
        default_factory=lambda: int(
            os.getenv("PDF_TIERED_EXPAND_BOUNDARY_PAGES", "1")
        )
    )
    pdf_tiered_fast_max_workers: int = field(
        default_factory=lambda: int(os.getenv("PDF_TIERED_FAST_MAX_WORKERS", "4"))
    )
    pdf_tiered_allow_partial: bool = field(
        default_factory=lambda: os.getenv(
            "PDF_TIERED_ALLOW_PARTIAL", "false"
        ).lower() in {"1", "true", "yes"}
    )
    pdf_tiered_manifest_dir: str = field(
        default_factory=lambda: os.getenv(
            "PDF_TIERED_MANIFEST_DIR",
            os.path.join(
                os.path.dirname(__file__), "..", "DATA", "raw_data", "_pdf_tiered_manifest"
            ),
        )
    )

    # ============================================================
    # PDF 结构适配（任务 M1）— 电子书类 PDF 的表格原子块/目录过滤/双栏打标
    # ============================================================
    enable_pdf_structure: bool = field(
        default_factory=lambda: os.getenv(
            "ENABLE_PDF_STRUCTURE", "true"
        ).lower() in {"1", "true", "yes"}
    )
    pdf_min_table_rows: int = 2  # 连续 | 表格行达到该行数才视为表格原子段
    enable_pdf_toc_filter: bool = True  # 剔除点线目录行（"……(82)"）
    enable_pdf_reflow_flag: bool = True  # 疑似双栏乱序块打标（供人工抽检）

    # ============================================================
    # 切片参数
    # ============================================================
    chunk_max_chars: int = 2000  # 单块最大字符数（bge-m3 限 8192 token，中文约 1 token/字，留足余量）
    chunk_overlap_chars: int = 100  # 句子切分时的重叠字符数

    # ============================================================
    # 检索参数（P1-3：扩大候选池并放宽阈值，降低长尾问题漏召）
    # ============================================================
    retrieval_top_k: int = 5
    similarity_threshold: float = 0.45  # 降级路径阈值（低于此值直接拒答），主路径使用自适应阈值

    # ── 混合检索参数 ──
    hybrid_dense_limit: int = 30   # 稠密向量检索候选数
    hybrid_sparse_limit: int = 30  # 稀疏向量检索候选数
    hybrid_fusion_limit: int = 30  # RRF 融合后取 Top-N
    nprobe: int = 32               # IVF 检索探测单元数（显式控制精度）
    rrf_k: int = 60                # RRF 融合参数 k
    reranker_model: str = "BAAI/bge-reranker-v2-m3"  # Cross-Encoder 重排序模型
    rerank_high_confidence_score: float = field(
        default_factory=lambda: float(os.getenv("RERANK_HIGH_CONFIDENCE_SCORE", "0.75"))
    )
    rerank_medium_confidence_score: float = field(
        default_factory=lambda: float(os.getenv("RERANK_MEDIUM_CONFIDENCE_SCORE", "0.50"))
    )
    rerank_high_confidence_threshold: float = field(
        default_factory=lambda: float(os.getenv("RERANK_HIGH_CONFIDENCE_THRESHOLD", "0.40"))
    )
    rerank_medium_confidence_threshold: float = field(
        default_factory=lambda: float(os.getenv("RERANK_MEDIUM_CONFIDENCE_THRESHOLD", "0.45"))
    )
    rerank_low_confidence_threshold: float = field(
        default_factory=lambda: float(os.getenv("RERANK_LOW_CONFIDENCE_THRESHOLD", "0.50"))
    )
    reranker_timeout: float = field(
        default_factory=lambda: float(os.getenv("RERANKER_TIMEOUT", "30"))
    )
    reranker_max_retries: int = field(
        default_factory=lambda: int(os.getenv("RERANKER_MAX_RETRIES", "2"))
    )
    reranker_retry_backoff_seconds: float = field(
        default_factory=lambda: float(os.getenv("RERANKER_RETRY_BACKOFF_SECONDS", "0.25"))
    )
    strict_hybrid_validation: bool = field(
        default_factory=lambda: os.getenv(
            "STRICT_HYBRID_VALIDATION", "false"
        ).lower() in {"1", "true", "yes"}
    )

    # ============================================================
    # 超时与重试参数（任务2）
    # ============================================================
    llm_timeout: int = field(
        default_factory=lambda: int(os.getenv("LLM_TIMEOUT", "60"))
    )
    llm_max_retries: int = field(
        default_factory=lambda: int(os.getenv("LLM_MAX_RETRIES", "1"))
    )
    embedding_timeout: int = field(
        default_factory=lambda: int(os.getenv("EMBEDDING_TIMEOUT", "30"))
    )
    embedding_max_retries: int = field(
        default_factory=lambda: int(os.getenv("EMBEDDING_MAX_RETRIES", "1"))
    )

    # ============================================================
    # SQL 查询超时（任务3）
    # ============================================================
    sql_query_timeout: int = field(
        default_factory=lambda: int(os.getenv("SQL_QUERY_TIMEOUT", "15"))
    )

    # ============================================================
    # 全局总超时（任务4）— 单次业务节点最大允许执行时间
    # ============================================================
    node_total_timeout: int = field(
        default_factory=lambda: int(os.getenv("NODE_TOTAL_TIMEOUT", "45"))
    )

    # ============================================================
    # 功能开关（任务1）
    # ============================================================
    enable_auto_semantic_bootstrap: bool = field(
        default_factory=lambda: os.getenv(
            "ENABLE_AUTO_SEMANTIC_BOOTSTRAP", "false"
        ).lower() in {"1", "true", "yes"}
    )

    # ============================================================
    # 入库去重（任务 M2）— 基于 chunk_uid 的文本块级去重与幂等
    # ============================================================
    enable_dedup: bool = field(
        default_factory=lambda: os.getenv("ENABLE_DEDUP", "true").lower()
        in {"1", "true", "yes"}
    )

    # ============================================================
    # 法条时效性（任务 M3）— 检索时按施行日期过滤（默认关，不动现网行为）
    # ============================================================
    enable_effective_filter: bool = field(
        default_factory=lambda: os.getenv(
            "ENABLE_EFFECTIVE_FILTER", "false"
        ).lower() in {"1", "true", "yes"}
    )

    # ============================================================
    # 系统提示词
    # ============================================================
    system_prompt: str = (
        "你是一个招投标领域的专业顾问，基于权威的公共知识库资料回答问题。\n"
        "请严格依据下方提供的参考资料作答，不要添加任何资料中没有的信息。\n"
        "如果参考资料不足以回答问题，请明确告知用户无法回答。"
    )

    # ============================================================
    # 引用溯源（任务：回答附带被引用 chunk 的完整来源信息）
    # ============================================================
    enable_inline_citations: bool = True  # LLM 回答内联标注【来源N】标记
    citation_rules: CitationRuleConfig = field(
        default_factory=CitationRuleConfig
    )
