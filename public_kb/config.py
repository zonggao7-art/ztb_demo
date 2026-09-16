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
    # Embedding 模型 — 身份配置（.env 必填、无默认值、无别名兜底；
    # 缺失/空值在 __post_init__ 统一抛错。口径见
    # design_docs/嵌入模型与混合检索整改工作计划_20260901.md §2.1）
    # ============================================================
    embedding_model: str = field(
        default_factory=lambda: os.getenv("EMBEDDING_MODEL") or ""
    )
    embedding_api_key: str = field(
        default_factory=lambda: os.getenv("EMBEDDING_API_KEY") or ""
    )
    embedding_base_url: str = field(
        default_factory=lambda: os.getenv("EMBEDDING_BASE_URL") or ""
    )
    # 向量维度随模型而定（bge-m3 = 1024）；.env 更换模型时必须同步确认
    # 维度（EMBEDDING_DIM 可覆盖），且需全量重建集合
    embedding_dim: int = field(
        default_factory=lambda: int(os.getenv("EMBEDDING_DIM", "1024"))
    )

    # ============================================================
    # LLM 问答模型 — 身份配置（.env 必填、无默认值、无别名兜底）。
    # 历史：曾按 LLM_* → API_KEY/BASE_URL/MODEL_NAME → DEEPSEEK_* 别名链
    # 解析并带代码默认值；2026-09 整改后仅认规范名，缺失即抛错
    # ============================================================
    llm_model: str = field(
        default_factory=lambda: os.getenv("LLM_MODEL") or ""
    )
    llm_api_key: str = field(
        default_factory=lambda: os.getenv("LLM_API_KEY") or ""
    )
    llm_base_url: str = field(
        default_factory=lambda: os.getenv("LLM_BASE_URL") or ""
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

    # ── 混合检索参数 ──
    hybrid_dense_limit: int = 30   # 稠密向量检索候选数
    hybrid_sparse_limit: int = 30  # 稀疏向量检索候选数
    hybrid_fusion_limit: int = 30  # RRF 融合后取 Top-N
    nprobe: int = 32               # IVF 检索探测单元数（显式控制精度）
    rrf_k: int = 60                # RRF 融合参数 k
    # Reranker 精排模型名 — 身份配置（.env 必填；凭据/端点复用 Embedding
    # 提供方配置，属有意设计：Reranker 与 Embedding 由同一服务方提供）
    reranker_model: str = field(
        default_factory=lambda: os.getenv("RERANKER_MODEL") or ""
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
    # 系统提示词
    # ============================================================
    system_prompt: str = (
        "你是一个招投标领域的专业顾问，基于权威的公共知识库资料回答问题。\n"
        "请严格依据下方提供的参考资料作答，不要添加任何资料中没有的信息。\n"
        "围绕用户问题提取核心内容，不要逐段复述参考资料。普通单问题正文尽量控制在200字以内，引用来源单独计算。\n"
        "准确简洁的原句可以保留，否则归纳表达；先删重复、背景和非必要例子，不得省略关键适用条件。"
        "200字是软目标，必要时允许超长，不要截断答案。多项问题应分别回答，不能为字数遗漏任务。\n"
        "资料只支持部分回答时，提供有依据的部分并简短说明具体缺口；不要擅自替换问题中的概念。"
        "没有必要时，不添加免责声明、主动追问或建议补充材料等套话。"
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
    # 入库批量向量化的每批条数：默认 32（每批约 6 万字符，规避 SiliconFlow TPM 限流）
    milvus_insert_batch: int = int(os.getenv("MILVUS_INSERT_BATCH", "32"))
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

    # The default path is one top-level Agent loop. legacy/hybrid remain explicit rollback modes.
    agent_execution_mode: str = field(default_factory=lambda: os.getenv("AGENT_EXECUTION_MODE", "unified"))
    agent_react_rollout_percent: int = field(default_factory=lambda: int(os.getenv("AGENT_REACT_ROLLOUT_PERCENT", "100")))
    agent_react_thread_allowlist: str = field(default_factory=lambda: os.getenv("AGENT_REACT_THREAD_ALLOWLIST", ""))
    agent_request_timeout_s: float = field(default_factory=lambda: float(os.getenv("AGENT_REQUEST_TIMEOUT_S", "90")))
    agent_router_timeout_s: float = field(default_factory=lambda: float(os.getenv("AGENT_ROUTER_TIMEOUT_S", "10")))
    agent_react_timeout_s: float = field(default_factory=lambda: float(os.getenv("AGENT_REACT_TIMEOUT_S", "65")))
    agent_finalize_reserve_s: float = field(default_factory=lambda: float(os.getenv("AGENT_FINALIZE_RESERVE_S", "15")))
    agent_max_model_calls: int = field(default_factory=lambda: int(os.getenv("AGENT_MAX_MODEL_CALLS", "8")))
    agent_max_tool_calls: int = field(default_factory=lambda: int(os.getenv("AGENT_MAX_TOOL_CALLS", "6")))
    agent_max_input_bytes: int = field(default_factory=lambda: int(os.getenv("AGENT_MAX_INPUT_BYTES", "16000")))
    # 完整 RAG 需要读取原文；不与外层调度共用较小的输入额度。
    agent_rag_max_input_bytes: int = field(default_factory=lambda: int(os.getenv("AGENT_RAG_MAX_INPUT_BYTES", "48000")))
    agent_model_max_output_tokens: int = field(default_factory=lambda: int(os.getenv("AGENT_MODEL_MAX_OUTPUT_TOKENS", "4096")))
    agent_amount_unit: str = field(default_factory=lambda: os.getenv("AGENT_AMOUNT_UNIT", ""))

    # ============================================================
    # 身份配置校验（D4/D6：.env 单一事实源 — 未配置即抛错，无默认值、
    # 无别名兜底；模型名/凭据/端点缺一不可）
    # ============================================================
    # 注意：不能加类型注解，否则会被 dataclass 误注册为字段
    _IDENTITY_VARS = (
        ("EMBEDDING_MODEL", "embedding_model"),
        ("EMBEDDING_API_KEY", "embedding_api_key"),
        ("EMBEDDING_BASE_URL", "embedding_base_url"),
        ("LLM_MODEL", "llm_model"),
        ("LLM_API_KEY", "llm_api_key"),
        ("LLM_BASE_URL", "llm_base_url"),
        ("RERANKER_MODEL", "reranker_model"),
    )

    def __post_init__(self) -> None:
        """校验模型身份配置 — 缺一即拒启。

        实际调用模型必须与 .env 配置严格同步（design_docs/嵌入模型与混合检索
        整改工作计划_20260901.md D4/D6）：缺失或空串直接抛 ValueError，
        不做任何默认值/别名兜底。测试环境请在夹具中显式注入这些变量
        （测试显式配置等同于 .env 配置）。
        """
        if self.agent_execution_mode not in {"legacy", "hybrid", "unified"}:
            raise ValueError("AGENT_EXECUTION_MODE must be legacy, hybrid or unified")
        if not 0 <= self.agent_react_rollout_percent <= 100:
            raise ValueError("AGENT_REACT_ROLLOUT_PERCENT must be between 0 and 100")
        for name in ("agent_request_timeout_s", "agent_router_timeout_s", "agent_react_timeout_s",
                     "agent_finalize_reserve_s", "agent_max_model_calls", "agent_max_tool_calls",
                     "agent_max_input_bytes", "agent_rag_max_input_bytes",
                     "agent_tool_timeout_s", "agent_model_max_output_tokens"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")
        if self.agent_tool_max_content_chars < 256:
            raise ValueError("AGENT_TOOL_MAX_CONTENT_CHARS must be at least 256")
        if self.agent_amount_unit not in {"", "元", "万元"}:
            raise ValueError("AGENT_AMOUNT_UNIT must be empty, 元 or 万元")
        missing = [
            env_name
            for env_name, attr_name in self._IDENTITY_VARS
            if not str(getattr(self, attr_name) or "").strip()
        ]
        if missing:
            raise ValueError(
                "模型身份配置缺失，拒绝启动（.env 单一事实源：必填且唯一，"
                "无默认值、无别名兜底）——缺："
                + "、".join(missing)
                + "。请在项目根目录 .env 中补配后重试。"
            )
