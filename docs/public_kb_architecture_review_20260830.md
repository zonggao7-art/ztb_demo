# public_kb 代码架构与工程化评估报告

> 日期：2026-08-30
> 审核范围：`D:\agent_project\ztb_demo\public_kb\`（40 个 Python 文件，约 4,364 行）
> 审核方式：静态代码审查（未改动任何代码）
> 参考文档：`docs/pipeline_refactor_baseline_20260829.md`、`docs/pipeline_refactor_final_validation_20260829.md`
>
> **环境说明**：用户提供的运行环境为 `D:\agent_project\zhaotoubiao_demo 1\.venv`，本次审查基于当前工作目录 `D:\agent_project\ztb_demo` 的代码副本。两份目录代码应核对一致性后再行采纳本报告中的行号结论。

---

## 0. TLDR 结论

1. **整体架构：合理，符合工程化规范**（8/10）。离线入库（`ingestion/`）与在线问答（`retrieval/` + `generation/`）清晰分层，`contracts.py` 集中定义跨模块契约，`config.py` 统一配置中心，引用溯源 R1–R7 规则集是亮点。存在若干契约复用不足与硬编码问题，但无结构性缺陷。
2. **数据链路：批量处理能力完备；去重能力缺失**。批量向量化入库（每批 100 条）与 CSV/PDF 批量导入均已实现；但**没有实际去重逻辑**——只设计了 `chunk_uid` 去重标识，入库时既不过滤也不去重，重复数据会原样入库。
3. **分块与索引：方案主流且合理**。标题层级感知 + 句子边界二次拆分 + 重叠，适合法规文本；索引为 dense IVF_FLAT/COSINE + 可选服务端 BM25 稀疏索引（Milvus 2.5+）。存在 `chunk_index` 标识退化（99.4% 为 0，文档自述）与 nlist 文档漂移两个小问题。
4. **在线检索：混合检索已实现、边界覆盖充分、整体可跑通**（9/10）。dense + BM25 → RRF(k=60) → Cross-Encoder rerank → 自适应阈值 → 拒答/降级路径完整。已由 2026-08-29 验证文档记录 221 测试通过 + 8/8 混合检索 POC 通过。
5. **大文件：行数超限本身不违规，但 4 个文件存在职责混合**，建议拆分 `csv_loader.py`(535) 与 `citations.py`(430)。
6. **多人协作：对外契约稳定（已有 AST 守卫测试防回归），但对"协程并发"接手方有明确冲突点**——`public_kb` 内全部为同步阻塞 IO 且存在实例级可变状态（reranker 的 `last_status`、store 管理器、RAG 单例），**非线程安全**，协作者并行化时必须先解决共享状态问题。

---

## 1. 整体架构评估（对应问题 1）

### 1.1 目录结构

```
public_kb/
├── __init__.py        # 懒加载门面（__getattr__ 延迟导入 PublicKnowledgeRAG）
├── __main__.py        # CLI：--init / --question / --interactive / --clear
├── config.py          # 统一配置中心（Settings + CitationRuleConfig）
├── contracts.py       # 跨模块契约 + 校验函数（推荐实践）
├── chunk_ids.py       # chunk_uid 稳定标识工具
├── rag_engine.py      # RAG 门面（离线入库 + 在线问答入口）
├── qa_chain.py        # 稳定兼容薄壳（转发到 generation.chain）
├── services/          # 基础设施：embeddings / llm / milvus_store / mineru_parser
├── ingestion/         # 离线链路：pipeline 编排 + sources + transforms + sinks + cli
├── retrieval/         # 在线检索：retriever + fallback + reranker + entities + strategies + milvus_search
└── generation/        # 生成层：chain(LCEL) + citations(溯源) + context + prompts
```

### 1.2 优点

- **清晰的单向依赖**：`ingestion`/`retrieval`/`generation` 通过 `contracts.py` 共享最小契约，不互相直接依赖；`services/` 作为底层基础设施被上层复用。
- **`contracts.py` 是工程化亮点**：定义异常层级、`RetrievalMode`/`RerankerStatus` 枚举、`MilvusCollectionContract`、`RetrievalDiagnostics` 及多个 `validate_*` 入口校验函数，跨模块边界有据可依。
- **`config.py` 统一配置**：所有参数集中，带 .env 自动加载与默认值，减少散落的魔法数字。
- **引用溯源设计完善**：`chunk_id` + `chunk_uid` 双标识、R1–R7 校验规则 fail-soft、结构化报告 `citation_validation`，支撑测评系统。
- **已有结构性守卫测试**：`test/test_public_kb_layout.py` 用 AST 静态扫描禁止 legacy 导入路径回归、锚定稳定入口签名，对多人协作是强保障。

### 1.3 问题

| # | 位置 | 问题 | 严重度 |
|---|------|------|--------|
| A1 | `retrieval/retriever.py:113,123` | 硬编码 `"vector"` / `"sparse_vector"` / `"COSINE"` / `"BM25"`，未复用 `MilvusCollectionContract` 中已声明的 `dense_field`/`sparse_field` 常量。契约集中声明了却不复用，是最典型的契约漂移源。 | 中 |
| A2 | `services/embeddings.py:26` | `_MAX_TEXT_CHARS = 2000` 硬编码，与 `config.chunk_max_chars`(2000) 重复维护。应读取 config。 | 低 |
| A3 | `qa_chain.py:18-19` | `_SiliconFlowReranker = SiliconFlowReranker`、`_dense_only_retrieve = dense_only_retrieve` 的下划线别名是为兼容签名/测试而设的"别名壳"，无文档解释意图，后续维护者易困惑。 | 低 |
| A4 | `rag_engine.py:59` | CLI 通过 `rag._store_manager.load_existing()` + `rag._build_qa_chain()` 访问私有方法（`__main__.py:59-60`），门面缺少公开的 `load_existing()` 等价方法。 | 低 |
| A5 | `__init__.py` 懒加载门面 | `from public_kb import PublicKnowledgeRAG` 依赖 `__getattr__` 动态导入，属于非常规模式，虽注释说明了动机（避免缺依赖时 import 失败），但会增加静态分析工具误判面。 | 低 |

---

## 2. 离线数据处理链路评估（对应问题 2）

### 2.1 批量处理能力 —— **已实现，完备**

- **PDF 批量**：`rag_engine.init_knowledge_base()` 遍历目录全部 `*.pdf`，逐本解析（MinerU→清洗→分块），**先全部收集再一次性批量入库**（`rag_engine.py:61-85`）。单本失败仅记录到 `failed` 清单，不中断整体（`rag_engine.py:68-70`）。
- **CSV 批量**：`ingestion/cli.py:run_batch_csv_ingestion()` 扫描 `*_data.csv`，按 Schema 分 A（政策全文）/C（QA 问答对）两组，逐文件解析收集后分组入库，支持 `--no-import` 预览与 `--group` 单组处理（`cli.py:203-260`）。
- **增量**：`rag_engine.add_pdf()` 与 CLI `--mode append` 支持向既有集合追加。
- **注意**：当前 `init_knowledge_base` 只递归 `pdf_path.glob("*.pdf")` 顶层目录，不支持子目录嵌套。

### 2.2 数据清洗 —— **已有基础能力，规则偏薄**

`TextCleaner`（`ingestion/transforms/cleaner.py`）提供 4 条规则：
1. 移除全文档出现 ≥5 次的重复短行（页眉/页脚）；
2. 移除纯数字行（页码）；
3. 移除 <10 字符的孤立行（保留标题/分隔线/空行）；
4. 压缩连续空行。

**评估**：对 MinerU 产出的 Markdown 属于够用的基础清洗，但明显偏薄——未处理 URL/HTML 残留、全角半角混排、特殊符号、表头表尾的跨页表格碎片、乱码/OCR 错字。对法规类 PDF 来说，第 3 条"短行丢弃"存在**误删风险**（如单行的"第一条""第三条"若不足 10 字符且出现次数 <5，会被保留；而出现 ≥5 次的条款号会被当作页眉删除——两条规则对法律条文号存在冲突风险，需实测数据确认）。

### 2.3 去重 —— **能力缺失（本次审查最值得关注的缺口）**

**结论：当前实现不具备入库去重能力，只具备"去重检测标识"。**

- 设计层面：`chunk_ids.py` 的 docstring 明确指出**实测 55.57% 的 chunk 内容重复**（同源多快照导入 + 法规条文跨文档引用），并据此设计了内容派生的 `chunk_uid`（`doc_name|chapter|chunk_index|md5(text)[:16]`），入库时写入动态字段固化。
- 执行层面：`milvus_store._batch_insert()` / `_build_records()` **只插入，不做任何基于 `chunk_uid` 的过滤、upsert、或删除**。schema 主键为 `auto_id` INT64，`chunk_uid` 不是唯一键（Milvus 2.4/2.5 亦不支持基于非主键的唯一约束）。
- 结果：重复 chunk 会**原样重复入库**，检索时同一内容命中多份，`chunk_uid` 仅供测评环节事后检测。
- 无幂等性设计：重复执行 `init` 会因"集合已存在"报错（`milvus_store.py:68-72`，默认禁止覆盖），这是保护机制但并非去重。

### 2.4 文本分块方式 —— **主流且合理，两处标识退化**

`SemanticChunker`（`ingestion/transforms/chunker.py`）：

- **主策略**：按 Markdown 标题层级（`#`–`######`）切分，维护 `heading_stack` 生成章节路径（如"第一章 总则 > 第一条"），章节语义完整。
- **补策略**：单块超过 `max_chars`(2000) 时按中文句子边界二次拆分，相邻块带 100 字符重叠。
- 叠加 `csv_loader.structure_plain_text()` 将中文法律文本"第X章/第X条"转为 Markdown 标题（含交叉引用否定后顾），使 CSV 纯文本也能按结构切分。

**评估**：标题感知 + 句子边界 + 重叠是法规 RAG 的标准做法，方案合理。问题：

| # | 位置 | 问题 |
|---|------|------|
| B1 | `chunker.py:72,80` | `chunk_index` 在遇到新标题时**重置为 0**，导致同文档绝大多数块 `chunk_index=0`（文档自述实测 99.4% 为 0），作为标识基本失效——团队已用 `chunk_uid` 弥补，但 `chunk_index` 参与 `chunk_uid` 派生，使其区分度依赖 `doc_name+chapter` 组合。 |
| B2 | `chunker.py:80` | `chunk_index = 0` 在分支内重复出现两次（72 行与 80 行），冗余代码。 |
| B3 | `chunker.py:193` | 二次拆分的重叠为**字符级**简单截断（`current[-overlap_chars:]`），可能从句子中间截断（容忍度内，属小瑕疵）。 |
| B4 | 类默认值 `max_chars=500/overlap=50` 与 `config` 的 2000/100 不一致 | 直接 `SemanticChunker()`（未传参）的调用方会拿到与配置不同的切片尺寸，存在口径不一致风险。 |

### 2.5 索引构建 —— **合理，一处文档漂移**

`schema`（`milvus_store.py:92-152`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | INT64 | 主键，auto_id |
| `text` | VARCHAR(65535) | 启用 analyzer（BM25 时） |
| `vector` | FLOAT_VECTOR(1024) | 稠密，bge-m3 |
| `sparse_vector` | SPARSE_FLOAT_VECTOR | 可选（enable_bm25） |
| 动态字段 | — | `enable_dynamic_field=True` 承载元数据 |

- dense 索引：`IVF_FLAT` + `COSINE`，`nlist=256`（`milvus_store.py:134` 硬编码）——**与 `CLAUDE.md` 记载的 nlist=128 不一致**（文档漂移）。
- sparse 索引：`SPARSE_INVERTED_INDEX` + `BM25`（`DAAT_MAXSCORE`），由服务端 BM25 Function `text_bm25_emb` 自动生成稀疏向量（需 Milvus 2.5+，验证环境为 2.6.23，POC 探测 PASS）。
- 创建后 `_validate_collection_contract()` 校验字段/Function/索引三方齐备才放行入库。

**评估**：IVF_FLAT 对法规库量级（数万条）合理；服务端 BM25 替代客户端 sparse embedding 是省算力且先进的做法。需注意 `ENABLE_MILVUS_BM25` 默认 `false`——**未开启时集合无稀疏向量，在线检索自动降级为纯稠密**。

### 2.6 向量化批量入库 —— **已实现**

`milvus_store._batch_insert()`（`milvus_store.py:189-211`）：
- 固定 `batch_size=100`，逐批 `embed_documents()`（OpenAI 兼容批量接口）→ `validate_embedding_batch()` 维度校验 → 批量 `insert` → 核对 `insert_count`，最后 `flush()`。
- 单批失败抛出 `IngestionContractError`，**无事务回滚**（批量导入场景可接受，但中途失败会留部分数据，需人工重跑清理）。

**评估**：批量能力完备，`batch_size` 硬编码 100 未配置化（低）。**全程同步串行，无并发**（与第 5 点协作者任务直接相关）。

### 2.7 元数据标签 —— **建立合理，但三处分散 + 约束偏弱**

元数据在三处建立，最终统一写入 Milvus 动态字段（`milvus_store._build_records()`，`milvus_store.py:213-242`）：

| 建立位置 | 字段 |
|----------|------|
| `chunker` | `doc_name` / `chapter` / `chunk_index` |
| `csv_loader._process_row` | + `title` / `source_file` / `source_url` / `publish_date` / `imple_time` / `_line_num` / `source_question` / `page_number` / `content_type` |
| `milvus_store` | + `chunk_uid` / `schema_version` / `embedding_model` |

**评估**：
- **合理点**：动态字段全透传（`_` 前缀与 None 跳过，`milvus_store.py:236-237`），`schema_version` 与 `embedding_model` 入每行记录，便于版本回溯；`chunk_uid` 双端同口径（入库写死 + 检索端即时重算）。
- **问题点**：
  - 契约 `validate_ingestion_documents()` 只强制校验 `doc_name/chapter/chunk_index` 三字段（`contracts.py:131`），其余元数据（如 `content_type`）无任何约束，检索端无法保证存在。
  - `_line_num` 以下划线开头，入库时被过滤（`milvus_store.py:236`），仅用于 Markdown 预览分组——该约定（下划线=仅内部）无文档说明。
  - 元数据字段名分散定义、无集中 schema，不同 source（PDF vs CSV）产出的字段集天然不一致（PDF 无 `source_url`/`content_type`），检索端 `citations` 透传时需容忍缺失。

---

## 3. 在线检索链路评估（对应问题 3）

### 3.1 链路结构

```
query → HybridRetriever.retrieve
      ├─ validate_question
      ├─ embed_query (dense)
      ├─ 探测集合是否有 sparse_vector（缓存）
      ├─ [混合] AnnSearch(dense COSINE) + AnnSearch(sparse BM25)
      │        → RRFRanker(k=60) 融合 → top-30
      │        → SiliconFlowReranker (bge-reranker-v2-m3) 重排 → top-5
      │        → adaptive_threshold 过滤
      ├─ [降级] 无 sparse → dense_only_retrieve（原生 pymilvus → langchain_milvus）
      └─ _decide_and_answer：空结果→拒答 / 否则 format_docs → prompt|LLM → citations 校验
```

### 3.2 混合检索实现程度 —— **已实现，但受开关控制**

- 混合检索**代码能力完整**：dense（COSINE/IVF）+ 服务端 BM25 稀疏 → RRF(k=60) → Cross-Encoder rerank → 自适应阈值 → top-5。
- 验证：`docs/pipeline_refactor_final_validation_20260829.md` 记录 POC 8/8 PASS（覆盖 dense-only / BM25-only / hybrid RRF / full-chain reranker / reranker 故障降级 / 拒答 / R1–R7 / strict mode）。
- **前提**：需 `ENABLE_MILVUS_BM25=true` 且 Milvus ≥2.5。默认配置下走纯稠密 + rerank 路径。POC 探测已确认本地 2.6.23 支持。

### 3.3 工程化与边界覆盖 —— **好**

| 边界场景 | 处理 | 位置 |
|----------|------|------|
| 无原生 collection/embeddings | 降级 `DENSE_LANGCHAIN` | `retriever.py:66-71` |
| 集合无 sparse 字段 | 降级 `DENSE_NATIVE`；strict 模式抛错 | `retriever.py:87-92` |
| embed_query 失败 | 捕获 → dense fallback | `retriever.py:95-105` |
| hybrid_search 输出字段异常 | `output_fields=['*']` 失败回退基础字段 | `milvus_search.py:30-48` |
| reranker API 失败 | 重试（指数退避）→ 保留 RRF 排序降级 | `reranker.py:80-127`、`retriever.py:156-176` |
| 检索为空 | 拒答 + 空引用校验报告 | `chain.py:77-89` |
| rerank 分数过低 | 自适应阈值过滤 → 空 → 拒答 | `strategies.py`、`chain.py` |
| 引用溯源 | R1–R7 全规则 fail-soft，结构化报告 | `citations.py` |
| 问题校验 | `validate_question` 空/非字符串拦截 | `contracts.py:115-122` |

**诊断体系是加分项**：`RetrievalDiagnostics` 记录 `retrieval_mode`/各阶段计数/`fallback_reason`/`threshold`，随问答结果返回，便于测评与排障。

**工程化问题**：

| # | 位置 | 问题 | 严重度 |
|---|------|------|--------|
| C1 | `reranker.py:56,57` | `last_status` / `retry_count` 为**实例可变状态**，且被 `retriever.py:156` 读取以决定是否降级——**非线程安全**。并发场景下多个请求共享实例会互相污染状态（见第 5 节）。 | 中高 |
| C2 | `retriever.py:60` | `_has_sparse_cache` 懒加载缓存（无锁），并发首次查询存在读时竞态（后果轻微，但属于共享状态）。 | 低 |
| C3 | `retriever.py:113-126` | `anns_field`/`metric_type` 硬编码，未复用 contract 常量（同 A1）。 | 中 |
| C4 | `fallback.py:70-72` | langchain_milvus 降级路径返回的 Document.metadata 是否含 `chunk_id` 取决于包装器字段映射，`chain.py:102-105` 用它做 R5 完整性校验，降级路径下 R1/R5 可能 fail-soft 记录缺失。 | 低 |
| C5 | `chain.py:95` | `prompt \| llm \| StrOutputParser()` 无流式/回调/中间观测点，生产级观测需另行加装。 | 低 |
| C6 | `strategies.py:20` | `configured = settings or Settings()` 写法（应 `is None` 判断），当前因 dataclass 恒 truthy 而无实际影响，但属可读性瑕疵。 | 低 |

**整体能否跑通**：是。除 2026-08-29 验证记录外，当前工作副本对 `public_kb/` 的未提交改动经 `git diff --stat` 核对**每文件仅 +1 行**（均为文件头"功能"注释增补），不触及逻辑；主风险是行尾 CRLF/LF 规范化噪音，不影响运行。

---

## 4. 大文件与耦合评估（对应问题 4）

超 300 行的 4 个文件：

| 文件 | 行数 | 承载职责 | 耦合度 | 结论 |
|------|------|----------|--------|------|
| `ingestion/sources/csv_loader.py` | 535 | ①CSV 解析 ②列名归一化 ③标题提取 ④中文法律标题→MD 转换（3 个正则阶段） ⑤逐行清洗/分块 ⑥Markdown 预览导出 | **高** | **建议拆分** |
| `generation/citations.py` | 430 | ①pydantic 数据模型 ②引用构建 ③标记解析 ④渲染（format_citations） ⑤R1–R7 校验器 | **高** | **建议拆分** |
| `services/milvus_store.py` | 344 | ①Schema 构建 ②索引构建 ③契约校验 ④批量插入 ⑤集合生命周期 | 中高 | 可接受，可拆 |
| `ingestion/cli.py` | 342 | ①单文件入口 ②批量入口 ③扫描分组 ④预览校验 ⑤argparse | 中 | 可接受 |

**工程化判断**：行数本身不是违规标准（工程规范关注的是**单一职责**而非行数阈值），但以上文件确实把多个关注点混在一个模块：

- `csv_loader.py` 是最该拆的一个——**预览导出（`save_chunks_to_markdown`）与数据加载是两件事**，且 `structure_plain_text` 的中文法律标题转换是一个独立、可测试的纯函数域，应独立成 `ingestion/transforms/` 下的工具。
- `citations.py` 建议按「模型层（`Citation`/`RuleResult`/`Report`）→ 构建解析层 → 渲染层 → 校验层」拆为 2–3 个模块。
- `milvus_store.py` 的 schema 构建（`_build_schema`/`_build_index_params`）与批插入（`_batch_insert`/`_build_records`）可拆，但规模尚可接受，**不拆也不构成技术债**。

> 注意：`csv_loader.py` 的拆分属于**有回归风险的重构**（被 `cli.py`/`csv_source.py`/`markdown_sink.py`/测试多处引用），应配合 `test_public_kb_layout.py` 的路径守卫测试推进，且需与其他协作者协调（见第 5 节）。

---

## 5. 多人协作影响评估（对应问题 5）

### 5.1 对外契约稳定性 —— **好（是加分项）**

`public_kb` 被 `agent/`、`test/`、`scripts/` 广泛依赖，但依赖面**收敛且稳定**：

```
agent/graph.py                → public_kb.config.Settings、public_kb.services.llm.create_llm
agent/nodes/knowledge_qa.py   → public_kb.PublicKnowledgeRAG
agent/nodes/price_inquiry/*   → public_kb.config / services.llm / services.embeddings
agent/__main__.py             → public_kb.generation.citations.format_citations
test/*, scripts/*             → 契约/门面/管道各模块
```

- `test_public_kb_layout.py` 用 **AST 静态扫描**禁止 legacy 导入路径回归，并锚定 `build_qa_chain` 签名——**这是对"迁移复用"最直接的保障**，协作者改文件名/类名会被测试拦住。
- `qa_chain.py` 作为稳定薄壳保留旧 `build_qa_chain` 签名，是刻意的兼容设计，方向正确。

### 5.2 对"协程并发"接手方的冲突点 —— **需重点提示**

`public_kb` 当前**完全同步、无任何并发原语**（无 asyncio、无锁、无线程池），且存在多处**实例级可变共享状态**。协作者要做 asyncio 并发化，以下位置会直接踩雷：

| # | 位置 | 共享状态 | 并发风险 |
|---|------|----------|----------|
| D1 | `retrieval/reranker.py:56-57` | `last_status` / `retry_count` | **最严重**。`retriever.py:156` 依赖 `last_status` 决定是否走 rerank 结果，并发请求互相覆盖状态 → 检索结果串路 |
| D2 | `services/milvus_store.py:46,299` | `_store` 包装器 / `_has_collection` | 并发 initialize/load 竞态 |
| D3 | `retrieval/retriever.py:60` | `_has_sparse_cache` 缓存 | 并发首查竞态（轻微） |
| D4 | `rag_engine.py:36-45` | 懒加载单例，`_qa_chain` 懒构建 | 并发首问时重复构建链 / 竞态 |
| D5 | 全链路 IO | embed_documents / MilvusClient / requests | 同步阻塞，asyncio 下需包线程池或改为异步客户端，否则阻塞事件循环 |

**建议**：协作者接手前需明确约定——(1) 将 `Reranker.last_status` 改为方法返回值携带状态（去掉实例状态）；(2) `PublicKnowledgeRAG.query()` 与 `HybridRetriever.retrieve()` 是**无状态纯函数**后才可安全并发；(3) asyncio 化要么在线程池里跑现有同步实现，要么对 `reranker`/`milvus_search`/`embeddings` 三个 IO 点做异步封装。

### 5.3 类名/文件名迁移复用

- 当前类名职责清晰（`Source`/`Sink`/`Pipeline`/`Retriever`/`Reranker`/`StoreManager`），抽象命名通用，迁移到其他项目/并行分支**阻力小**。
- `qa_chain.py` 的下划线别名（A3）与 `__init__.py` 懒加载（A5）是**非常规写法**，协作者首次阅读会困惑，建议补文档说明意图。

---

## 6. 问题清单（按严重度排序）

| 严重度 | 编号 | 位置 | 问题 |
|--------|------|------|------|
| 🔴 高 | D1 | `reranker.py:56-57` | Reranker 实例级可变状态非线程安全，直接阻碍协作者并发化 |
| 🔴 高 | 2.3 | `milvus_store.py:189-242` | **无入库去重逻辑**，55.57% 重复率下重复数据原样入库（仅有检测标识） |
| 🟠 中 | A1/C3 | `retriever.py:113-126` | 字段名/度量硬编码，未复用 `MilvusCollectionContract` |
| 🟠 中 | 4 | `csv_loader.py`(535) / `citations.py`(430) | 职责混合，建议拆分 |
| 🟠 中 | B4 | `chunker.py:27` | 类默认切片参数与 config 不一致（500/50 vs 2000/100） |
| 🟡 低 | 2.5 | `milvus_store.py:134` vs `CLAUDE.md` | nlist 256 与文档记载 128 漂移 |
| 🟡 低 | B1 | `chunker.py:72,80` | chunk_index 新标题重置导致 99.4% 为 0 |
| 🟡 低 | A2 | `embeddings.py:26` | _MAX_TEXT_CHARS 与 config 重复维护 |
| 🟡 低 | C4 | `fallback.py:70-72` | langchain 降级路径 chunk_id 缺失影响 R1/R5 |
| 🟡 低 | 2.2 | `cleaner.py` | 清洗规则偏薄，短行丢弃对条款号有误删风险 |
| 🟡 低 | A3/A4/A5 | `qa_chain.py`/`__main__.py`/`__init__.py` | 兼容别名、私有方法访问、懒加载非常规写法需补文档 |

---

## 7. 改进建议路线（不涉及本次改动，供排期参考）

1. **短期（低风险）**：补充 `chunk_uid` 入库去重的执行层（初始化与增量入库时基于 chunk_uid 查询过滤，或引入 upsert 语义）；将 `reranker.last_status` 重构为返回值携带状态，消除线程安全缺陷；统一 `chunker` 默认参数与 config。
2. **中期（中风险，需协作协调）**：`csv_loader.py` 与 `citations.py` 按职责拆分，并以现有 AST 守卫测试为回归锚点；检索层复用 `MilvusCollectionContract` 常量，消除硬编码。
3. **长期**：评估 asyncio 化路径（线程池封装 vs 异步客户端）时，先完成共享状态清理；为 `public_kb` 建立基准性能/并发生理（当前无任何并发基准）。
4. **文档**：修正 nlist 漂移；为 `qa_chain.py` 别名、懒加载门面、`_` 前缀内部字段约定补充说明，降低协作者理解成本。

---

*本报告为静态审查结论，未运行测试、未改动代码。测试通过性引用 2026-08-29 验证文档（221 passed + 8/8 混合检索 POC）。*
