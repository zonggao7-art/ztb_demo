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
    collection_name: str = "public_kb"

    # ============================================================
    # MySQL 连接（询价链路结构化库；agent.nodes.price_inquiry.db 消费）
    # ============================================================
    mysql_host: str = field(
        default_factory=lambda: os.getenv("MYSQL_HOST", "127.0.0.1")
    )
    mysql_port: int = field(
        default_factory=lambda: int(os.getenv("MYSQL_PORT", "3306"))
    )
    mysql_user: str = field(
        default_factory=lambda: os.getenv("MYSQL_USER", "root")
    )
    mysql_password: str = field(
        default_factory=lambda: os.getenv("MYSQL_PASSWORD", "")
    )
    mysql_clean_db: str = field(
        default_factory=lambda: os.getenv("MYSQL_CLEAN_DB", "ztb_clean")
    )

    # ============================================================
    # Embedding 模型
    # ============================================================
    embedding_model: str = field(
        default_factory=lambda: os.getenv(
            "EMBEDDING_MODEL", "BAAI/bge-large-zh-v1.5"
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
    # 读取优先级：LLM_*（规范名）→ OpenRouter 直配（API_KEY/BASE_URL/MODEL_NAME）
    #            → DEEPSEEK_*（历史遗留兜底；2026-08 起对话模型默认切换为 OpenRouter）
    llm_model: str = field(
        default_factory=lambda: os.getenv(
            "LLM_MODEL", os.getenv("MODEL_NAME", "z-ai/glm-5.3-flash")
        )
    )
    llm_api_key: str = field(
        default_factory=lambda: os.getenv(
            "LLM_API_KEY",
            os.getenv("API_KEY", os.getenv("DEEPSEEK_API_KEY", "")),
        )
    )
    llm_base_url: str = field(
        default_factory=lambda: os.getenv(
            "LLM_BASE_URL",
            os.getenv("BASE_URL", os.getenv("DEEPSEEK_BASE_URL", "")),
        )
    )
    llm_temperature: float = 0.0

    # ============================================================
    # MinerU 解析
    # ============================================================
    mineru_timeout: int = 3600  # OCR 版 PDF 需要较长超时
    # magic-pdf 输出目录（临时中间产物）
    mineru_output_dir: str = field(
        default_factory=lambda: os.path.join(
            os.path.dirname(__file__), "..", "DATA", "raw_data"
        )
    )

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

    # ============================================================
    # 询价语义检索（Milvus mysql_price_semantic 集合）
    # ============================================================
    mysql_semantic_collection: str = field(
        default_factory=lambda: os.getenv("MYSQL_SEMANTIC_COLLECTION", "mysql_price_semantic")
    )
    mysql_semantic_batch_size: int = int(os.getenv("MYSQL_SEMANTIC_BATCH_SIZE", "100"))
    mysql_semantic_top_k: int = int(os.getenv("MYSQL_SEMANTIC_TOP_K", "64"))
    mysql_semantic_per_table_limit: int = int(os.getenv("MYSQL_SEMANTIC_PER_TABLE_LIMIT", "24"))
    mysql_semantic_text_truncate: int = int(os.getenv("MYSQL_SEMANTIC_TEXT_TRUNCATE", "120"))
    mysql_semantic_threshold: float = float(os.getenv("MYSQL_SEMANTIC_THRESHOLD", "0.30"))

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


    # ============================================================
    # 异步 + 记忆 + 流式改造新增字段（手册 §8.2；阶段 1 起生效）
    # ============================================================

    # ── 异步执行开关 ──
    async_backend_enabled: bool = field(
        default_factory=lambda: os.getenv("ASYNC_BACKEND_ENABLED", "false").lower() in {"1", "true", "yes"}
    )
    async_io_workers: int = int(os.getenv("ASYNC_IO_WORKERS", "16"))
    async_cpu_workers: int = int(os.getenv("ASYNC_CPU_WORKERS", "4"))
    llm_max_concurrency: int = int(os.getenv("LLM_MAX_CONCURRENCY", "8"))
    embedding_max_concurrency: int = int(os.getenv("EMBEDDING_MAX_CONCURRENCY", "8"))
    rerank_max_concurrency: int = int(os.getenv("RERANK_MAX_CONCURRENCY", "4"))
    milvus_max_concurrency: int = int(os.getenv("MILVUS_MAX_CONCURRENCY", "8"))
    price_recall_concurrency: int = int(os.getenv("PRICE_RECALL_CONCURRENCY", "3"))

    # ── MySQL 池（阶段 3 真正用，阶段 1 先占位） ──
    mysql_max_pool_size: int = int(os.getenv("MYSQL_MAX_POOL_SIZE", "16"))
    mysql_acquire_timeout_s: int = int(os.getenv("MYSQL_ACQUIRE_TIMEOUT", "3"))
    sql_stmt_timeout_s: int = int(os.getenv("SQL_STMT_TIMEOUT_S", "8"))

    # ── Checkpointer ──
    checkpointer_backend: str = os.getenv("CHECKPOINTER_BACKEND", "memory")
    checkpointer_sqlite_path: str = os.getenv("CHECKPOINTER_SQLITE_PATH", "checkpoints.db")
    checkpointer_postgres_dsn: str = os.getenv("CHECKPOINTER_POSTGRES_DSN", "")

    # ── 长期记忆（阶段 4 启用） ──
    memory_enabled: bool = field(
        default_factory=lambda: os.getenv("MEMORY_ENABLED", "false").lower() in {"1", "true", "yes"}
    )
    memory_store_backend: str = os.getenv("MEMORY_STORE_BACKEND", "sqlite")
    memory_pg_dsn: str = os.getenv("MEMORY_PG_DSN", "")
    memory_sqlite_path: str = os.getenv("MEMORY_SQLITE_PATH", "memory.db")
    memory_max_injection_tokens: int = int(os.getenv("MEMORY_MAX_INJECTION_TOKENS", "400"))
    memory_min_confidence: float = float(os.getenv("MEMORY_MIN_CONFIDENCE", "0.7"))
    memory_allow_extracted: bool = field(
        default_factory=lambda: os.getenv("MEMORY_ALLOW_EXTRACTED", "false").lower() in {"1", "true", "yes"}
    )

    # ── 流式输出（阶段 5 启用） ──
    stream_enabled: bool = field(
        default_factory=lambda: os.getenv("STREAM_ENABLED", "false").lower() in {"1", "true", "yes"}
    )
    stream_heartbeat_s: int = int(os.getenv("STREAM_HEARTBEAT_S", "15"))
    stream_cancel_grace_s: int = int(os.getenv("STREAM_CANCEL_GRACE_S", "5"))

    # ── Reranker 超时 ──
    rerank_timeout_s: int = int(os.getenv("RERANK_TIMEOUT_S", "5"))

    # ============================================================
    # 工具化（Tool Registry / Agent 平台化 P1）
    # ============================================================
    # Agent 自助调用总开关；false 时 --agent-mode 拒绝启动（--list-tools 不受限）
    agent_tools_enabled: bool = field(
        default_factory=lambda: os.getenv("AGENT_TOOLS_ENABLED", "false").lower() in {"1", "true", "yes"}
    )
    # 工具白名单（逗号分隔工具名；空 = 全部注册的工具可用）
    agent_tools_whitelist: str = field(
        default_factory=lambda: os.getenv("AGENT_TOOLS_WHITELIST", "")
    )
    # 单工具执行超时兜底（秒）
    agent_tool_timeout_s: float = field(
        default_factory=lambda: float(os.getenv("AGENT_TOOL_TIMEOUT_S", "20"))
    )
    # 检索类工具默认 top_k
    agent_tool_default_top_k: int = field(
        default_factory=lambda: int(os.getenv("AGENT_TOOL_DEFAULT_TOP_K", "20"))
    )
    # LLM 可见工具返回内容的字符截断上限（防 prompt 膨胀）
    agent_tool_max_content_chars: int = field(
        default_factory=lambda: int(os.getenv("AGENT_TOOL_MAX_CONTENT_CHARS", "4000"))
    )
    # Agent 原型单次任务最大步数（recursion_limit）
    agent_loop_max_steps: int = field(
        default_factory=lambda: int(os.getenv("AGENT_LOOP_MAX_STEPS", "6"))
    )