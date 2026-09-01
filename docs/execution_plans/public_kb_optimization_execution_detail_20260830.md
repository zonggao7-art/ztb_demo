# public_kb 优化执行方案（细化版）— 分模块可执行任务清单

> 计划日期：2026-08-30
> 上游总计划：`docs/execution_plans/public_kb_optimization_execution_plan_20260830.md`
> 上游审查证据：`docs/public_kb_architecture_review_20260830.md`、`docs/public_kb_data_pdf_dedup_review_20260830.md`
> 执行模型：**每个模块 = 一组编号任务卡片，独立实现 → 独立验证 → 独立提交；全部模块完成后做总体验证**
> 协作约束：不推送远端、不修改生产 `.env`、不改生产 Milvus 集合、不引入 async API（协程并发小组入口保持不动）

---

## 0. 三件前置决定（用户提问的直接回答）

### 0.1 任务卡片：产出，但内嵌在本方案中，不单独建文件

仓库约定执行计划是 Markdown 文档（`docs/execution_plans/`），尚无"每任务一个卡片文件"的先例。因此本方案每个模块内嵌一张 **任务卡片表**（编号 / 任务 / 涉及文件 / 预估改动 / 验收标准），既满足"编号任务+可执行"，又不增加 6 个散落文件的维护负担。若后续需要板式看板，可由本表一键导出，无需重写。

### 0.2 数据契约：必须冻结（多人协作的前提）

小组并行开发时，接口漂移是最大的集成风险。M0 阶段**冻结下表契约**，本方案所有模块改动都必须在不破坏这些契约的前提下进行（新增字段一律可空、向后兼容；新增方法一律纯追加）：

| 契约 | 冻结内容 | 触碰模块 |
| --- | --- | --- |
| `PublicKnowledgeRAG` | `init_knowledge_base/query/add_pdf/clear_kb` 签名与返回结构不变 | M1/M6 |
| `build_qa_chain()` | `qa_chain.py` 稳定入口 5 参数签名不变 | M5 |
| `Settings` | 只允许向后兼容新增字段，默认值保持现行为 | M1/M2/M3/M6 |
| `Document.metadata` | `doc_name/chapter/chunk_index/chunk_uid` 必填；其余透传不改名 | M1/M3 |
| `IngestionResult` | 新增字段必须给默认值；`inserted_count` 语义保持"实际写入数" | M2 |
| `RetrievalDiagnostics` | `retrieval_mode/reranker_status/fallback_reason` 兼容 | M3 |
| Milvus collection schema | 字段名/维度/analyzer/BM25 Function 不变；动态字段只增不改 | M2/M3 |
| `AgentState` / agent 节点返回 | 不受影响 | 全部 |
| `test_public_kb_layout.py` AST 守卫 | 禁止 legacy 导入路径、稳定入口签名回归 | M5 尤其 |

### 0.3 命名约定：沿用现有类名/文件名，只有新能力才新增文件

优先级：**产出工程化代码 > 名称可复用**。在此前提下：
- 现有类名（`SemanticChunker`/`TextCleaner`/`CsvLoader`/`MilvusStoreManager`/`CitationValidator`/`HybridRetriever`）**全部保留**，只改内部实现；
- 新增能力才新增文件（M1 的 `pdf_structure.py`、M5 拆分出的子模块），并保留包级 re-export，保证旧 import 路径不断；
- 所有新文件沿用仓库既有风格：`Protocol`/dataclass/显式函数边界，不引入抽象基类继承树。

### 0.4 MinerU 是否引入测试验证：**分层引入，不把重工具塞进单测**

MinerU（`magic-pdf`）是本地 GPU 重工具，不适合进 CI/单测。采用三层验证：

| 层 | 方式 | 是否引入 MinerU | 归属 |
| --- | --- | --- | --- |
| L1 单元测试 | 用**合成 Markdown 夹具**测 `pdf_structure.py`/`chunker.py`/`cleaner.py` 纯函数 | 否 | M1/M4 单测 |
| L2 冒烟核验 | `scripts/pdf_smoke_probe.py` 对 3 本 PDF 各抽 20-30 页跑通 `PdfSource`，人工核对表格/双栏/目录 3 类样张 | **是（可选手动）** | M1 验收关卡 |
| L3 批量 | 全量 PDF 向量化入库 | 是（大批量专项） | 总体验证之后 |

L2 的 MinerU 只在"验收该模块时手动跑一次"，产物缓存到 `DATA/raw_data/_pdf_adapter_samples/`，**不进版本库**；单测/CI 永不依赖 `magic-pdf`。这样既不牺牲验证质量，也不让重工具阻塞每次回归。

---

## 1. M0 — 基线冻结（无代码改动，0.5 人日）

**任务卡片**

| 编号 | 任务 | 涉及文件 | 预估 | 验收标准 |
| --- | --- | --- | --- | --- |
| M0-1 | 记录当前测试基线 | `docs/execution_plans/` | 0.1d | `239 passed`（`--ignore=test_cloud_sync.py`）存档 |
| M0-2 | 核对两环境代码一致 | `ztb_demo` vs `zhaotoubiao_demo 1` | 0.1d | diff 仅行尾差异 |
| M0-3 | 将 §0.2 契约清单写入总计划 | 总计划文档 | 0.1d | 全员可见 |

---

## 2. M1 — PDF 专用解析适配（最重，3–4 人日）

**背景**：三本电子书 PDF 存在双栏混排（book2 89%）、表格摊平（book3 表1-1）、点线目录/书脊页眉、Q&A 体例（book3"第N问…答："），当前链路无法妥善处理。

### 2.1 改动点

| 文件 | 改动 | 预估行数 |
| --- | --- | --- |
| `ingestion/transforms/pdf_structure.py` **新增** | ①表格块识别（MinerU `\|` 表格 Markdown → 不可拆原子块）；②双栏 reflow（优先消费 MinerU 读序；提供 x 坐标重排预检，可疑页打标）；③目录点线行 + 书脊页眉过滤 | ~180 |
| `ingestion/transforms/chunker.py` | 新增"原子块透传"分支：表格/公式块整体放行，不参与句子二次拆分 | ~25 |
| `ingestion/sources/pdf_source.py` | `load()` 在 clean→chunk 之间插入 `pdf_structure` 适配 | ~15 |
| `config.py` | 新增 `pdf_min_table_rows`(默认3)/`enable_pdf_structure`(默认True) 等开关，向后兼容 | ~10 |
| `test/test_pdf_structure.py` **新增** | 单测（合成夹具，不依赖 MinerU） | ~200 |
| `scripts/pdf_smoke_probe.py` **新增** | 冒烟脚本：对指定 PDF 页码区间跑 `PdfSource`，输出样张到 `DATA/raw_data/_pdf_adapter_samples/` | ~80 |

### 2.2 任务卡片

| 编号 | 任务 | 验收标准 |
| --- | --- | --- |
| M1-1 | 实现 `pdf_structure.py` 表格块识别 | 合成表格 Markdown → 产出单个 Document 且 `content_type=table`，不拆句 |
| M1-2 | 实现双栏 reflow 与可疑页打标 | 合成左右交错文本 → 读序正确；可疑页 metadata 打标 |
| M1-3 | 实现目录/书脊过滤 | 点线行（`……(82)`）、书脊页眉（book2"8 一、招标投标"）被剔除 |
| M1-4 | `chunker.py` 原子块透传 | 表格块不被句子拆分器切开；既有分块行为回归不变 |
| M1-5 | 接入 `PdfSource` + 配置开关 | `PdfSource.load()` 走通新链路；`enable_pdf_structure=False` 回退旧行为 |
| M1-6 | 单元测试 | `pytest test/test_pdf_structure.py` 全绿 |
| M1-7 | **L2 冒烟核验（手动，引入 MinerU）** | 3 本 PDF 各 20-30 页样张人工核对：表格/双栏/目录 3 类达标，样张入 `_pdf_adapter_samples/` |

### 2.3 契约影响
- `PdfSource.load()` 行为变化（产出质量提升），返回结构 `SourceResult` 不变；
- 对外 `PublicKnowledgeRAG.init_knowledge_base()` 无感知。

### 2.4 独立验证
`pytest test/test_pdf_structure.py test/test_ingestion_pipeline.py test/test_public_kb_layout.py -q` 全绿 + M1-7 样张通过 → 独立提交。

---

## 3. M2 — 文本块级去重 + 幂等导入（2–3 人日）

**背景**：`_batch_insert` 纯 insert，`chunk_uid` 只是标签；审计实测 55.57% 重复 chunk 污染检索/评估。

### 3.1 改动点

| 文件 | 改动 | 预估行数 |
| --- | --- | --- |
| `services/milvus_store.py` | `_batch_insert` 前按批次 `query(expr="chunk_uid in […]")` 判重，命中跳过；`initialize_collection`/`add_documents` 返回实际写入数；`add_documents` 幂等 | ~70 |
| `ingestion/sinks/milvus_sink.py` | `write()` 返回 manager 实际写入数（而非 `len(validated)`，修复去重后计数虚高） | ~10 |
| `config.py` | 新增 `enable_dedup: bool = True`（开关，默认开） | ~3 |
| `ingestion/models.py` | `IngestionResult` 增加 `skipped_duplicates: int = 0`（默认值保证向后兼容） | ~3 |
| `test/test_dedup_ingestion.py` **新增** | mock MilvusClient 单测 | ~180 |

### 3.2 任务卡片

| 编号 | 任务 | 验收标准 |
| --- | --- | --- |
| M2-1 | 批次内判重查询助手 | 按 `chunk_uid` 集合返回已存在 uid 列表 |
| M2-2 | `_batch_insert` 集成去重 | 同批重复 → 只写 1 条；`skipped_duplicates` 计数正确 |
| M2-3 | `add_documents` 幂等 | 同一批调用两次 → 第二次 `inserted=0` |
| M2-4 | `MilvusSink.write` 计数修正 | 返回实际写入数（去重后不为虚高） |
| M2-5 | 配置开关 | `enable_dedup=False` → 完全复现旧行为（全量写） |
| M2-6 | 单测 + 回归 | `test_dedup_ingestion.py` 全绿；`test_milvus_store_offline.py`/`test_ingestion_pipeline.py` 不破 |

### 3.3 契约影响
- `IngestionResult` 增 `skipped_duplicates`（默认 0）；
- `MilvusStoreManager.initialize_collection/add_documents` 返回值从 `None` 变为 int —— **内部契约，需同步更新 `milvus_sink.py`，并对现有调用方 `rag_engine.py` 做一次编译级核对**。

### 3.4 独立验证
M2 全套单测 + 回归 → 独立提交。

---

## 4. M3 — 法条时效性（1.5–2 人日）

**背景**：`release_time/imple_time` 入库即弃，检索不过滤；新旧法同时入库会混淆"现行/已废止"。

### 4.1 改动点

| 文件 | 改动 | 预估行数 |
| --- | --- | --- |
| `services/milvus_store.py` | `_build_records` 透传 `effective_date`/`status` 动态字段（默认空） | ~8 |
| `ingestion/sources/csv_loader.py` | `_process_row` 映射 `effective_date=imple_time`、`status=""` | ~5 |
| `contracts.py` | 新增 `build_effective_expr(today)` 纯函数（生成 `effective_date <= today` 过滤 expr） | ~20 |
| `retrieval/retriever.py` + `retrieval/milvus_search.py` | `search`/`hybrid_search` 增加可选 `expr=None` 参数（默认不过滤） | ~20 |
| `config.py` | 新增 `enable_effective_filter: bool = False`（默认关，不动现网行为） | ~3 |
| `test/test_effective_date_filter.py` **新增** | mock 单测 | ~150 |

### 4.2 任务卡片

| 编号 | 任务 | 验收标准 |
| --- | --- | --- |
| M3-1 | 元数据透传 | 入库行带 `effective_date/status`（旧数据为 NULL 不报错） |
| M3-2 | `build_effective_expr` 纯函数 | 边界单测：同日生效/已过期/未来生效 |
| M3-3 | 检索可选 `expr` 透传 | 开启开关后只召回现行版；关闭 → 行为与旧版逐字节一致（回归断言） |
| M3-4 | 单测 + 回归 | `test_effective_date_filter.py` 全绿；`test_retrieval_strategies.py`/`test_citation_tracing.py` 不破 |

### 4.3 契约影响
- `retrieve()`/`search*` 增加可选参数（默认 `None`/关，向后兼容）；
- 动态字段 +2 可空字段。

### 4.4 独立验证
M3 单测 + 回归 → 独立提交。

---

## 5. M4 — 清洗规则保护条款号（0.5–1 人日）

**背景**：`TextCleaner` 短行丢弃与页眉去重可能误删"第X条/第X章"；book2 章标题页眉"8 一、招标投标"会被当重复页眉删除。

### 5.1 改动点

| 文件 | 改动 | 预估行数 |
| --- | --- | --- |
| `ingestion/transforms/cleaner.py` | ①短行保留白名单（`^第[一二三四五六七八九十百千\d]+[章节条款项]` 等）；②页眉去重豁免合法章节标题；③页码正则保留 | ~45 |
| `test/test_cleaner_protection.py` **新增** | 单测 | ~120 |

### 5.2 任务卡片

| 编号 | 任务 | 验收标准 |
| --- | --- | --- |
| M4-1 | 短行白名单 | 含"第X条"短行保留；普通噪声短行仍删 |
| M4-2 | 章节标题页眉豁免 | 重复 ≥5 的"第X章/第X节"标题行不被删除 |
| M4-3 | 页码行回归 | 纯数字行仍被删除（原行为不变） |
| M4-4 | 单测 + 回归 | `test_cleaner_protection.py` 全绿；若 `test_ingestion_pipeline.py` 既有断言因清洗输出变化失败，逐条核对后更新并注明原因 |

### 5.3 独立验证
M4 单测 + 回归 → 独立提交。

---

## 6. M5 — 耦合拆分 + 契约复用（2 人日，分两步提交）

**背景**：`csv_loader.py`(535)/`citations.py`(430) 职责混合；`retriever.py` 硬编码字段名未复用 `MilvusCollectionContract`；`embeddings.py` 与 config 重复维护长度常量。

### 6.1 改动点（Step A 低风险 → Step B 中风险）

| 文件 | 改动 | 预估行数 |
| --- | --- | --- |
| `retrieval/retriever.py` | `anns_field`/`metric_type` 改用 `MilvusCollectionContract` 常量 | ~10 |
| `services/embeddings.py` | `_MAX_TEXT_CHARS` 改读 `config.chunk_max_chars`（消除双源） | ~5 |
| `ingestion/sources/csv_loader.py` | 拆出 `csv_loader_structure.py`（`structure_plain_text` + `save_chunks_to_markdown`），原文件保留 re-export | ~0（搬迁） |
| `generation/citations.py` | 拆出 `citations_models.py`（pydantic 模型）/`citations_build.py`（构建/解析/渲染）/`citations_validate.py`（Validator），`citations.py` 保留 re-export | ~0（搬迁） |
| `test/test_citations_split.py` **新增** | 从新模块路径导入验证 | ~60 |

### 6.2 任务卡片

| 编号 | 任务 | 验收标准 |
| --- | --- | --- |
| M5-1 | 契约常量复用 | `retriever.py` 无硬编码字段名/度量；行为不变（回归） |
| M5-2 | `embeddings` 常量统一 | 单源维护，行为不变 |
| M5-3 | `csv_loader` 拆分 + re-export | 旧 `from ...csv_loader import structure_plain_text, save_chunks_to_markdown` 仍可用 |
| M5-4 | `citations` 拆分 + re-export | 旧 `from ...citations import *` 等价；`test_citations_split.py` 全绿 |
| M5-5 | **AST 守卫 + 全量回归** | `test_public_kb_layout.py` 通过（防路径回归）；全量回归一次 |

### 6.3 契约影响
- 对外 import 路径全部经 re-export 保持不变；
- 新文件一律从 `ingestion/transforms/` 或 `generation/` 包内导入，不制造跨包新依赖。

### 6.4 独立验证
Step A 与 Step B 各独立提交；每步跑 AST 守卫 + 全量回归。

---

## 7. M6 — 工程化治理（0.5–1 人日）

### 7.1 改动点

| 文件 | 改动 |
| --- | --- |
| `requirements.txt` + 新增 `requirements.lock` | 修复 `openai` 冲突（`openai>=2.45,<4` 与源环境 3.0.0 对齐）；`uv pip freeze` 生成 lockfile |
| `rag_engine.py` + `__main__.py` | 增加公开 `load_existing()`，CLI 不再访问私有 `_store_manager` |
| `qa_chain.py` / `__init__.py` | 为下划线别名与懒加载门面补 docstring 说明意图（不重构） |
| `config.py` | `mineru_output_dir` 注释明确"解析中间产物目录，勿与 DATA 组织混放" |

### 7.2 任务卡片

| 编号 | 任务 | 验收标准 |
| --- | --- | --- |
| M6-1 | 依赖修复 + lockfile | `uv pip install -r requirements.txt` 可复现；新环境全量测试通过 |
| M6-2 | `load_existing()` 公开化 | `__main__.py` 无私有访问；`PublicKnowledgeRAG.load_existing()` 可调用 |
| M6-3 | 文档注释 | 别名/懒加载意图可读，无行为变化 |
| M6-4 | 全量回归 | `pytest test -q --ignore=test/test_cloud_sync.py` 全绿 |

---

## 8. 总体验证（跨模块集成，1 人日）

全部模块独立验收通过后执行：

| 步骤 | 内容 | 通过标准 |
| --- | --- | --- |
| V-1 | 全量测试 | `pytest test -q --ignore=test/test_cloud_sync.py` 全绿（含所有新增测试） |
| V-2 | 契约核对 | §0.2 冻结契约逐项核对（diff 无破坏） |
| V-3 | 小批量端到端（CSV，mock Milvus） | 重复行被去重（`skipped_duplicates>0`）、幂等二次导入 `inserted=0`、关闭开关回退旧行为 |
| V-4 | 小批量端到端（PDF，可选 MinerU） | 3 本 PDF 各抽 20-30 页 → 表格/双栏/目录样张达标（L2） |
| V-5 | 检索回归 | `retrieval_diagnostics`/引用 R1-R7/`build_qa_chain` 入口行为不变 |
| V-6 | 产出验证记录 | 结果写入 `docs/` 验证报告，附本次基线对比 |

---

## 9. 每模块执行循环（统一纪律）

```
实现 → 模块单测(新增测试) → 相关既有测试回归 → (L2 人工核验如适用)
→ 全量回归(239+ 且新增计入) → 独立提交(遵循可回退) → 下一模块
```

- 提交粒度：每张任务卡片至少一次可回退提交；
- 禁止在模块内混入其他模块改动（M5 的 Step A/B 也分开）；
- 全程不 push 远端、不动生产 `.env`/Milvus；
- 引入任何新外部依赖前（如 `magic-pdf` 仅 L2 手动，不进 requirements.txt）与小组确认。

---

*本细化方案仅文档产出，未改动任何代码。实施前建议将 §0.2 契约清单与各模块任务卡同步到小组，明确认领人与验收人。*

---

## 附录：模块执行状态（2026-08-30）

### M1 — PDF 专用解析适配 ✅（已实现，14 单测 + 全量回归通过）

**实际改动清单**
| 文件 | 状态 | 说明 |
| --- | --- | --- |
| `public_kb/ingestion/transforms/pdf_structure.py` | 新增 | 表格原子块 / 目录点线过滤 / 双栏乱序打标 / 标题行内联条文正文保护 |
| `test/test_pdf_structure.py` | 新增 | 14 用例 |
| `public_kb/config.py` | 修改 | `enable_pdf_structure`(默认true)/`pdf_min_table_rows`/`enable_pdf_toc_filter`/`enable_pdf_reflow_flag` |
| `public_kb/rag_engine.py` | 修改 | `_process_single_pdf` 增加结构适配分支；开关关时回退旧行为 |

**实现中发现并修复的隐藏缺陷**
- `SemanticChunker` 会把 `## 第X条 正文…` 的标题行整体丢弃（正文只进 heading_stack）。cleaned_v1 与电子书均为此格式 → 新增 `_split_heading_inline_articles` 把 条/款/项 标题行拆为「标题行 + 独立正文行」（章/节标题保留）。

**验证结果**
- `test/test_pdf_structure.py`：14 passed
- 回归：`test_ingestion_pipeline / test_public_kb_layout / test_ingestion_cli / test_kb_contracts`：38 passed
- 全量：`pytest test -q --ignore=test_cloud_sync.py` → **253 passed**

**M1-7 L2 冒烟（人工）结论 — 部分完成，表格项待 MinerU**
- `magic-pdf` 不在 PATH，无法直接跑 MinerU。用 PyMuPDF 文本层做了替代冒烟（`DATA/raw_data/law_pdf/_pdf_m1_smoke.txt`）：
  - 目录点线行过滤：book2 目录页触发（toc_removed_chars=1076/911）✅
  - 双栏乱序打标：book2 双栏正文页触发（suspect=1）✅
  - 表格原子段：**未触发** —— 因 PyMuPDF 文本层把表1-1 摊平成普通文本，`|` 表格 Markdown 是 MinerU 专有输出。**待 `magic-pdf` 可用后补跑** 3 本 PDF 各 20-30 页样张，核对表格原子块。
- 验收关卡 M1-7 的"表格项"标记为**待 MinerU**，其余项通过。

### 待办（M2 之前可选）
- 安装/配置 `magic-pdf`（GPU 版 MinerU）后补跑 L2 表格样张核验；也可推迟到 M2 之后统一执行，不阻塞模块推进。


---

## 附录 B：后续专项清单（M1 之外新增）

| 编号 | 专项 | 说明 | 状态 |
| --- | --- | --- | --- |
| P-1 | MinerU 接入专项 | ①Docker Desktop(WSL2)+`opendatalab/mineru` 镜像部署，GPU 透传（8G 显存够标准模型）；②MinerU 封装为 HTTP 服务（FastAPI）；③新增 `MinerUApiParser` 走 requests；④替换 `mineru_parser.py` 的 subprocess 直调；⑤补跑 M1-7 表格样张核验 | 待办 |
| P-2 | 大批量 PDF 并发化 | 需先清 reranker `last_status` 等共享可变状态（架构审查 §5） | 待办 |
| P-3 | 全量数据向量化入库 + 生产集合重建 | 所有模块验收后统一执行 | 待办 |

> P-1 不阻塞 M2/M3/M4（去重、时效性、清洗均不依赖 MinerU）。M1 表格项验收挂起至 P-1 完成。


---

## 附录 C：M2 执行状态（2026-08-30）✅ 已实现

**实际改动清单**
| 文件 | 状态 | 说明 |
| --- | --- | --- |
| `public_kb/config.py` | 修改 | 新增 `enable_dedup`（默认 true，可回退旧行为） |
| `public_kb/ingestion/models.py` | 修改 | `IngestionResult` 增加 `skipped_duplicates: int = 0` |
| `public_kb/services/milvus_store.py` | 修改 | `_batch_insert` 重构（批内 chunk_uid 去重 + 存量 `chunk_uid in [...]` 判重查询）；`initialize_collection`/`add_documents` 返回实际写入数 int；新增 `_query_existing_uids`（查询失败尽力而为退化为全量写） |
| `public_kb/ingestion/sinks/milvus_sink.py` | 修改 | `write()` 返回 manager 实际写入数（修复去重后计数虚高） |
| `public_kb/ingestion/pipeline.py` | 修改 | `IngestionResult.skipped_duplicates = len(documents) - inserted_count` |
| `test/test_dedup_ingestion.py` | 新增 | 9 用例（mock MilvusClient，不连真实库） |

**验证结果**
- `test/test_dedup_ingestion.py`：9 passed（批内重复只写 1 条 / 幂等二次导入 inserted=0 / 新内容追加 / 判重查询失败退化为全量写 / `enable_dedup=False` 完全复现旧行为 / pipeline skipped_duplicates 计数）
- 回归：`test_milvus_store_offline / test_ingestion_pipeline / test_ingestion_cli / test_public_kb_layout`：41 passed（同步更新了 `test_ingestion_pipeline.py` 的 FakeMilvusManager 返回类型以匹配新 int 返回值契约）
- 全量：`pytest test -q --ignore=test_cloud_sync.py` → **262 passed**

**契约影响（已核对）**
- `IngestionResult` 新增字段有默认值 → 向后兼容；
- `initialize_collection`/`add_documents` 返回值从 `None` 变 `int` → 内部契约，已同步 `milvus_sink.py`；`rag_engine.py` 编译级核对无受影响调用。

**遗留说明**
- 真实 Milvus 上的 `chunk_uid in [...]` 判重查询尚未实库验证（测试为 mock）——按计划在总体验证 V-3 小批量端到端时用真实 Milvus 补验。


---

## 附录 D：M3 执行状态（2026-08-30）✅ 已实现

**实际改动清单**
| 文件 | 状态 | 说明 |
| --- | --- | --- |
| `public_kb/config.py` | 修改 | 新增 `enable_effective_filter`（默认 false，不动现网行为） |
| `public_kb/contracts.py` | 修改 | 新增 `build_effective_expr(today)` 纯函数：`effective_date is null or effective_date <= "<today>"` |
| `public_kb/retrieval/milvus_search.py` | 修改 | `search_with_full_fields`/`hybrid_search_with_full_fields` 增加可选 `expr` 参数，透传为 Milvus `filter` |
| `public_kb/retrieval/fallback.py` | 修改 | `dense_only_retrieve` 增加可选 `expr` 参数并透传 |
| `public_kb/retrieval/retriever.py` | 修改 | 新增 `_effective_expr()`（按开关生成过滤 expr）；`retrieve`/`_hybrid_retrieve`/`_dense_fallback` 透传 expr |
| `public_kb/services/milvus_store.py` | 修改 | `_build_records` 透传 `effective_date`/`status` 动态字段（可空） |
| `public_kb/ingestion/sources/csv_loader.py` | 修改 | `_process_row` 映射 `effective_date=imple_time`、`status=""` |
| `test/test_effective_date_filter.py` | 新增 | 11 用例（expr 边界/透传/元数据/开关） |

**验证结果**
- 新增单测：`test_effective_date_filter.py` **11 passed**（expr 日期边界：同日生效/已过期/NULL 保留；search/hybrid 有/无 expr 时 filter 透传；`_build_records` 写 effective_date/status 及默认空；开关 on/off 行为）
- 回归：`test_retrieval_strategies / test_citation_tracing / test_recall_optimization / test_qa_chain_offline` **59 passed**
- 全量：`pytest test -q --ignore=test_cloud_sync.py` → **273 passed**

**契约影响（已核对）**
- `retrieve()`/`search*` 增加可选参数（默认 `None`/关，向后兼容）；
- Milvus 动态字段 +2 可空字段（`effective_date`/`status`）；
- `build_effective_expr` 保留 NULL 日期 → 旧数据不受影响。

**设计说明**
- `enable_effective_filter` 默认关 → 现网行为逐字节不变；
- 过滤策略 `is null or <= today`：新数据按施行日期生效，旧数据（无字段）不被误杀；
- CSV 侧把既有 `imple_time`（施行日期）映射为 `effective_date`，无需新字段来源。


---

## 附录 E：M4 执行状态（2026-08-30）✅ 已实现

**实际改动清单**
| 文件 | 状态 | 说明 |
| --- | --- | --- |
| `public_kb/ingestion/transforms/cleaner.py` | 修改 | 新增 `_LEGAL_HEADING_RE`（法律标题识别）；步骤3 短行白名单加入法律标题保留；`_remove_repeating_headers` 页眉去重豁免法律标题 |
| `test/test_cleaner_protection.py` | 新增 | 8 用例 |

**验证结果**
- 新增单测：`test_cleaner_protection.py` **8 passed**（第X条/第X章短行保留、噪声短行仍删、纯页码行仍删、页码行在标题后仍删；章节标题页眉重复≥5 不被删、普通页眉仍删）
- 回归：`test_ingestion_pipeline / test_pdf_structure / test_public_kb_layout` **28 passed**
- 全量：`pytest test -q --ignore=test_cloud_sync.py` → **281 passed**

**契约影响（已核对）**
- `TextCleaner.clean` 签名/返回不变，仅清洗规则增强；
- 既有`test_ingestion_pipeline` 断言不受影响（未改该文件，全部通过）。


---

## 附录 F：M5 执行状态（2026-08-30）✅ 已实现（Step A + Step B）

### Step A（低风险：契约复用）
| 文件 | 说明 |
| --- | --- |
| `public_kb/retrieval/retriever.py` | `_hybrid_retrieve` 的 `anns_field="vector"`/`"sparse_vector"`、`metric_type="COSINE"`/`"BM25"` 改用 `MilvusCollectionContract` 常量 |
| `public_kb/retrieval/fallback.py` | `dense_only_retrieve` 的 anns_field/metric_type 同样改用 contract 常量 |
| `public_kb/services/embeddings.py` | 删除硬编码 `_MAX_TEXT_CHARS=2000`，`_SafeEmbeddings` 改为实例化注入 `max_text_chars`，`create_embeddings` 传 `settings.chunk_max_chars`（单源对齐，消除双处维护） |

### Step B（中风险：职责拆分 + re-export）
| 文件 | 说明 |
| --- | --- |
| `public_kb/ingestion/sources/csv_loader_structure.py` **新增** | 承接 `structure_plain_text`（中文法律标题→MD）+ `save_chunks_to_markdown`（预览导出）；csv_loader.py 由 535→332 行 |
| `public_kb/ingestion/sources/csv_loader.py` | 删除拆出的两个函数块，改为从 csv_loader_structure re-export（旧导入路径不变） |
| `public_kb/generation/citations_models.py` **新增** | Citation/RuleResult/CitationValidationReport + parse_citation_markers + _compute_cited_sets |
| `public_kb/generation/citations_build.py` **新增** | build_citations + format_citations |
| `public_kb/generation/citations_validate.py` **新增** | CitationValidator（补 `_UNKNOWN_*` 常量导入） |
| `public_kb/generation/citations.py` | 变为门面（430→30 行），re-export 全部既有符号 |
| `test/test_citations_split.py` **新增** | 3 用例：门面等价 / 旧路径可用 / 子模块内部可用 |

### 验证结果
- Step A 回归：`test_retrieval_strategies/test_citation_tracing/test_public_kb_layout/test_effective_date_filter` 57 passed
- Step B 回归：`test_citation_tracing/test_public_kb_layout/test_qa_chain_offline/test_dedup_ingestion/test_effective_date_filter` 72 passed（拆分后修复 `citations_validate.py` 缺失 `_UNKNOWN_*` 导入的 NameError）
- 新增：`test_citations_split.py` 3 passed
- 全量：`pytest test -q --ignore=test_cloud_sync.py` → **284 passed**

### 契约影响（已核对）
- 所有既有 `from public_kb.generation.citations import ...` 与 `from public_kb.ingestion.sources.csv_loader import ...` 导入路径经 re-export 保持不变；
- `test_public_kb_layout.py` AST 守卫通过（无 legacy 路径回归）；
- 行为零变化（纯结构重构，逻辑逐字搬移）。


---

## 附录 G：M6 执行状态（2026-08-30）✅ 已实现

**实际改动清单**
| 文件 | 说明 |
| --- | --- |
| `public_kb/rag_engine.py` | 新增公开 `load_existing()`（加载+构建问答链，返回 bool）；`ensure_loaded` 保持不变 |
| `public_kb/__main__.py` | `cmd_query`/`cmd_interactive` 改走公开 `load_existing()`，移除私有 `_store_manager.load_existing()`/`_build_qa_chain()` 访问 |
| `requirements.txt` | 修复 `openai` 冲突：`openai>=2.45,<4`（与运行环境 3.0.0 对齐）；`langchain-core>=1.5.4,<2.0.0`、`langchain-openai>=1.5.0,<2.0.0` 等下限与运行环境一致 |
| `requirements.lock` **新增** | `uv pip compile` 生成（含依赖注释），关键版本与运行环境逐一致（numpy 2.4.6/openai 3.0.0/langchain-core 1.5.4/pymilvus 3.0.1/torch 2.12.1 等） |
| `public_kb/qa_chain.py` | 补下划线别名说明 docstring（解释用途：AST 守卫锚定 + 兼容） |
| `public_kb/__init__.py` | 补懒加载门面说明 docstring（为何用 __getattr__ 而非顶层导入） |
| `public_kb/config.py` | `mineru_output_dir` 注释明确"中间产物勿混入 DATA 组织" |

**验证结果**
- `uv pip compile requirements.txt` 成功（无版本冲突）；`uv pip install -r requirements.txt --dry-run` → "Would make no changes"（当前环境即满足）；
- `requirements.lock` 与运行环境关键版本逐一致；
- 懒加载门面 `PublicKnowledgeRAG is rag_engine.PublicKnowledgeRAG` OK；
- `__main__.py` 无残留私有成员访问；
- 全量：`pytest test -q --ignore=test_cloud_sync.py` → **284 passed**


---

## 附录 H：总体验证执行记录（2026-08-30）

| 步骤 | 状态 | 结果 |
| --- | --- | --- |
| V-1 全量测试 | ✅ | `284 passed` |
| V-2 契约核对 | ✅ | 公开入口签名逐项核对通过（`query/init_knowledge_base/add_pdf/clear_kb/load_existing`）；`build_qa_chain` 5 参数稳定入口不变；`Settings` 新增字段默认值正确；`IngestionResult.skipped_duplicates` 存在；`RetrievalDiagnostics.to_dict` 兼容 |
| V-3 小批量端到端（真实 Milvus） | ⏸️ 条件不具备 | 本机 `localhost:19530` 无 Milvus 服务运行（MilvusException 连接失败）。端到端补验需先启动基础设施（`docker compose -f milvus/docker-compose.yml up -d`） |
| V-4 PDF 小批量（MinerU） | ⏸️ 待 P-1 | `magic-pdf` 未装（P-1 专项）；M1 已用 PyMuPDF 替代冒烟双栏/目录，表格项挂起 |
| V-5 检索回归 | ✅ | `test_retrieval_strategies / test_citation_tracing / test_recall_optimization / test_qa_chain_offline / test_effective_date_filter` 全绿（检索诊断/R1-R7/降级/时效 expr 透传均覆盖） |
| V-6 验证记录 | ✅ | 本表 + 附录 A-G（M0–M6 各模块独立验证记录） |

**结论**：代码层 V-1/V-2/V-5 全绿；V-3（真实 Milvus 端到端）与 V-4（MinerU 表格）依赖外部基础设施（Milvus 服务 / magic-pdf），分别挂起至"基础设施启动"与"P-1 MinerU 专项"，不阻塞已交付的 7 个模块。


---

## 附录 I：V-3 真实 Milvus 端到端验证（2026-08-30）✅ 完成

**基础设施**：本机 Docker 已有 Milvus 2.6.23（`localhost:19531`，等报告显示是 2 天前启动、healthy），并非"无服务"——V-3 挂起的原因是**默认端口 19530 未开**，实际服务在 19531。用实验集合 `public_kb_hybrid_poc_e2e`（符合实验前缀，不触碰生产 `public_kb`）完成测试后已清理。

**验证结果（真实库，非 mock）**
| # | 场景 | 期望 | 实际 |
| --- | --- | --- | --- |
| 1 | 批内去重（同批 2 份相同 chunk） | 写 1 条 | ✅ 1，rows=1 |
| 2 | 幂等（同一批第二次 add_documents） | inserted=0 | ✅ 0 |
| 3 | 新增内容追加 | 写 2 条 | ✅ 2 |
| 4 | `enable_dedup=False` 开关（重复写入） | 写 1 条、总行数 4 | ✅ 1，rows=4 |
| 5 | 真实 `chunk_uid in [...]` 判重查询 | ≥1 命中 | ✅ 1 |

**结论**：M2 去重+幂等 在真实 Milvus 上行为与 mock 单测完全一致（批内去重/幂等/追加/开关回退/判重查询全通过）。实验集合已清理，无残留。

