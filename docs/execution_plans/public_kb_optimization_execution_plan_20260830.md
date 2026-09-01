# public_kb 优化执行计划（整合两份审查结论）

> 计划日期：2026-08-30
> 上游审查：
> - `docs/public_kb_architecture_review_20260830.md`（架构/检索/耦合/协作）
> - `docs/public_kb_data_pdf_dedup_review_20260830.md`（PDF 数据链路/去重/时效性）
> 执行原则：接口先冻结、每模块独立提交、每步可验证、每步可回退
> 协作约束：不推送远端、不修改生产 `.env`、不改生产 Milvus 集合、不影响协程并发改造入口（本轮只维护同步入口）

---

## 0. 两报告重叠评估与整合策略

### 0.1 重叠矩阵

| 主题 | 架构审查 | PDF/去重审查 | 重叠度 | 说明 |
| --- | --- | --- | --- | --- |
| 入库去重缺失 | §2.3 能力缺失，chunk_uid 仅检测 | §3.2 执行层无，附 cleaned_v1 文档级去重证据 | **高** | 同一主题；report2 补充了数据侧证据 |
| 清洗规则误删条款号 | §2.2 短行丢弃风险 | §2.3 PDF 场景同问题 | 中 | report2 落到 PDF 具体场景 |
| 多人协作/并发前提 | §5 共享可变状态、非线程安全 | §4 PDF 大批量前提含并发 | 中低 | 视角不同：代码 vs 数据处理 |
| 批量处理能力 | §2.1 已实现 | §4 大批量可行性 | 中低 | 互补 |
| 在线检索链路 | §3 完整评估 | 未深入 | 低 | 无重叠 |
| 大文件/耦合拆分 | §4 csv_loader/citations | 未涉及 | 低 | 无重叠 |
| 环境迁移 | 未涉及 | §1 已完成 | 低 | 无重叠 |
| PDF 双栏/表格剖析 | 未涉及 | §2 详细 | 低 | 无重叠 |
| 法条时效性 | 未涉及 | §5 未实现 | 低 | 无重叠 |

### 0.2 整合策略：**不合并审查报告，新增统一执行计划**

理由：
1. **两份审查是证据，不是待办**。报告的价值在于可追溯的事实与行号引用；合并成一份会稀释"架构评审"与"数据源评估"两个不同受众的阅读路径。
2. **仓库已有文档分工约定**（`docs/execution_plans/README.md`）：审查报告留在 `docs/`，执行计划进 `docs/execution_plans/`，不混放。新增一份执行计划即完成"整合落地"。
3. 重叠主题（去重、清洗、并发）在下方执行方案中以**单一模块**承载，交叉引用两份报告的对应小节即可，无需物理合并。
4. 只在两报告结论冲突时才需要更新——核对后**无冲突**，互为补充。

---

## 1. 目标与非目标

### 1.1 目标

1. 让新上传的 3 本电子书 PDF（双栏、表格、Q&A 体例）能走通入库链路且数据可用。
2. 实现文本块级去重与幂等导入，消除实测 55.57% 的重复 chunk 对检索/评估的污染。
3. 建立法条时效性元数据与检索过滤，支撑"新法生效、旧法失效"场景。
4. 消除清洗规则对条款号的误删风险。
5. 落地既有契约复用（`MilvusCollectionContract` 常量）与 2 个高耦合文件的拆分。
6. 修复 `requirements.txt` 版本冲突，固化可复现依赖。
7. 每个模块自带测试验证，保证回归安全与多人协作下的行为稳定。

### 1.2 非目标

1. 本轮不改生产 Milvus 集合、不跑全量入库、不修改 `.env`。
2. 本轮不引入 async API（留给协程并发改造小组）；只确保改造不破坏其入口。
3. 本轮不重写 `MinerUParser` 的 subprocess 调用方式（大批量并发留给后续并发专项）。
4. 本轮不引入复杂继承树，沿用 Protocol + 数据类 + 显式函数边界。

---

## 2. 必须冻结的对外契约

| 契约 | 说明 |
| --- | --- |
| `PublicKnowledgeRAG` | `init_knowledge_base()` / `query()` / `add_pdf()` / `clear_kb()` 签名与返回不变 |
| `build_qa_chain()` | `qa_chain.py` 稳定入口签名不变 |
| `Settings` | 只允许向后兼容新增字段，默认值保持当前行为 |
| `Document.metadata` | 离线入库与在线检索的字段不丢失、不改名；新增字段一律可空、向后兼容 |
| `RetrievalDiagnostics` | 检索模式 / Reranker 状态 / 降级原因兼容 |
| Milvus collection schema | 字段名、维度、analyzer、BM25 Function 不变；新增动态字段不影响既有查询 |
| `AgentState` / agent 节点返回 | 不因 public_kb 改动受影响 |

---

## 3. 模块划分与执行顺序

依赖关系：
```
M0（冻结契约/基线） → M1（PDF 解析适配，最重）
                    → M2（块级去重+幂等） → M3（法条时效性）
                    → M4（清洗规则保护）  → M5（耦合拆分+契约复用） → M6（工程化治理）
```
M2 依赖 M1 产出（PDF 块也需去重）；M3 依赖 M2 的元数据写入点；M4/M5/M6 相互独立、可并行。

---

## 4. 模块设计（每模块含：改动点 / 契约影响 / 测试验证）

### M0 冻结基线（前置，无代码改动）

- 在 `docs/execution_plans/` 记录当前测试基线：`239 passed`（2026-08-30 实测）。
- 确认两个环境（`ztb_demo` 与 `zhaotoubiao_demo 1`）代码一致（上次审查基于 `ztb_demo`）。
- **验证**：`pytest test -q --ignore=test/test_cloud_sync.py` 通过即冻结。

### M1 PDF 专用解析适配（对应 PDF 审查 §2.3/§4，架构审查 §2.2）

**问题**：MinerU 输出 Markdown 的表格与双栏读序未被 `TextCleaner`/`SemanticChunker` 利用；Q&A 体例退化成长度切块；书脊页眉被误删。

**改动点**：
- `ingestion/sources/pdf_source.py`：`load()` 后追加"PDF 结构适配"步骤（新模块）。
- 新增 `ingestion/transforms/pdf_structure.py`（纯函数）：
  1. 表格块识别：将 MinerU 的表格 Markdown 块（`|` 分隔）作为**不可切分的原子块**保留，`SemanticChunker` 遇到表格块整体放行；
  2. 双栏 reflow：依赖 MinerU 版面分析输出的段落顺序（若 MinerU 输出有序则直接消费；无序时提供基于 `x` 坐标的重排预检，标记可疑页）；
  3. 目录/书脊噪声过滤：识别点线目录行（`……(页码)`）与书脊页眉，从正文流剔除。
- `ingestion/transforms/chunker.py`：新增"原子块透传"分支（表格/公式块不参与句子拆分）。

**契约影响**：无对外契约变化；`PdfSource` 内部行为变化，产出块质量提升。

**验证（新增测试）**：
- `test/test_pdf_structure.py`：
  - 表格 Markdown → 原子块不拆（单测）；
  - 双栏样例（人工构造左右交错文本）→ 读序正确；
  - 目录点线行 / 书脊重复行 → 被过滤；
  - Q&A 体例（`N.问题…答：`）→ 保留 `content_type=qa_pair` 提示、按问题聚合切块。
- **实测抽检**：对 3 本 PDF 各取 20-30 页跑通 `PdfSource`，人工核对表格/双栏/目录 3 类样张的切块质量（输出到 `DATA/raw_data/_pdf_adapter_samples/`，不入库）。

### M2 文本块级去重 + 幂等导入（对应两报告去重主题，架构 §2.3、PDF §3.3）

**问题**：`_batch_insert` 纯 insert，无 chunk_uid 判重；`add_documents` 重复导入即重复写。

**改动点**：
- `services/milvus_store.py`：
  1. `_batch_insert` 前按批次 `query(expr="chunk_uid in […]", output_fields=["chunk_uid"])` 收集已存在 uid；
  2. 命中即跳过（初始化时同批内部也要先算好 uid 去重）；记录 `skipped_duplicates` 计数；
  3. `add_documents` 同路径，幂等：重复导入同一批 → 全部跳过；
  4. `config.py` 新增 `enable_dedup: bool = True`（开关，默认开，可回退旧行为）。
- `chunk_ids.py`：确认 `compute_chunk_uid` 的 `(doc_name, chapter, chunk_index, text_hash)` 口径不变（保持跨集合稳定）。

**契约影响**：`IngestionResult` 增加可选 `skipped_duplicates` 字段（向后兼容）；`MilvusSink.write` 返回值语义不变（返回实际写入数）。

**验证（新增测试）**：
- `test/test_dedup_ingestion.py`（mock MilvusClient）：
  - 同批内重复 chunk → 只写 1 条；
  - 跨批重复（第二批含第一批已写 uid）→ 全部跳过、计数正确；
  - `enable_dedup=False` → 回到旧行为（全量写）；
  - 幂等：同一批调用两次 `add_documents` → 第二次 `inserted=0`。
- **回归**：`test/test_milvus_store_offline.py`、`test/test_ingestion_pipeline.py` 保持通过。

### M3 法条时效性（对应 PDF 审查 §5）

**问题**：`release_time`/`imple_time` 入库即弃，检索不过滤，新旧法混淆。

**改动点**：
- `services/milvus_store.py::_build_records`：从 metadata 透传 `effective_date` / `status`（新字段，默认空）。
- `csv_loader._process_row`：已有 `publish_date`/`imple_time`，映射为 `effective_date=imple_time`、`status=""`。
- `retrieval/retriever.py` / `retrieval/milvus_search.py`：`search`/`hybrid_search` 增加可选 `expr`（默认 `None` = 不过滤，行为不变）；`Settings` 新增 `enable_effective_filter: bool = False`（默认关，避免改变现网行为）。
- 提供工具函数 `contracts.py` 或 `config.py`：`now` 日期与 `effective_date` 比较，生成过滤 expr。

**契约影响**：`retrieve()` 增加可选参数（默认不过滤）；动态字段新增 2 个可空字段。

**验证（新增测试）**：
- `test/test_effective_date_filter.py`（mock MilvusClient）：
  - 构造新旧两版同标题法规 → 开启过滤后只召回现行版；
  - 关闭开关 → 行为与旧版一致（回归断言）；
  - `expr` 生成逻辑单测（日期比较边界：同日生效/已过期）。
- **回归**：`test/test_retrieval_strategies.py`、`test/test_citation_tracing.py` 保持通过。

### M4 清洗规则保护条款号（对应架构 §2.2、PDF §2.3）

**问题**：`TextCleaner` 短行丢弃与页眉去重可能误删"第X条/第X章"。

**改动点**：
- `ingestion/transforms/cleaner.py`：
  1. 短行保留白名单：`^第[一二三四五六七八九十百千\d]+[章节条款项]$` 及其带标题形式；
  2. 页眉去重豁免：连续出现的"章节标题页眉"（如 book2 的"8 一、招标投标"）不再被当作普通重复行删除——改为仅当同时满足"重复 ≥5 次 + 非合法章节标题"才删；
  3. 页码行正则保留原逻辑。
- 全部规则保持为静态方法、纯函数，便于单测。

**契约影响**：无。仅清洗输出变化（减少误删）。

**验证（新增测试）**：
- `test/test_cleaner_protection.py`：
  - 含"第X条"的短行保留；
  - 章节标题页眉（重复 ≥5）不被删除；
  - 纯页码行仍被删除；
  - 既有 `test_ingestion_pipeline.py` 中基于旧清洗行为的断言若受影响，需同步更新并说明原因。

### M5 耦合拆分 + 契约复用（对应架构 §4、§1.3 A1/A2）

**改动点（分两步，每步独立提交）**：
1. **契约复用（低风险）**：
   - `retrieval/retriever.py:113-126` 的 `anns_field`/`metric_type` 改用 `MilvusCollectionContract` 常量；
   - `services/embeddings.py:26` 的 `_MAX_TEXT_CHARS` 改读 `config.chunk_max_chars`（消除重复维护）。
2. **文件拆分（中风险，需 AST 守卫测试护航）**：
   - `ingestion/sources/csv_loader.py`(535) 拆为：`csv_loader.py`（解析/归一化/标题提取）+ `csv_loader_md.py`（`structure_plain_text` 中文法律标题转换 + `save_chunks_to_markdown` 预览导出），或迁入 `ingestion/transforms/`；
   - `generation/citations.py`(430) 拆为：`citations_models.py`（pydantic 模型）+ `citations_build.py`（构建/解析/渲染）+ `citations_validate.py`（Validator）。
   - 保留 `public_kb` 包级 re-export，确保 `test_public_kb_layout.py` 的导入守卫通过。

**契约影响**：无对外变化（包级 re-export 兜底）。

**验证（新增 + 回归）**：
- 沿用 `test/test_public_kb_layout.py`（AST 守卫会拦截任何路径回归）；
- 新增 `test/test_citations_split.py`：从拆分后的新模块路径导入 `build_citations`/`CitationValidator`/`format_citations` 均可用；
- 全量回归一次。

### M6 工程化治理（对应架构 §1.3 A3-A5、PDF §1/§6）

**改动点**：
1. `requirements.txt` 冲突修复：`openai>=1.50.0,<2.0.0` 与 `langchain-openai`(需 openai≥2.45) 互斥 → 改为 `openai>=2.45,<4`（与源环境 openai 3.0.0 对齐），并锁定 `requirements.lock`（`uv pip freeze` 生成）。
2. `rag_engine.py` 增加公开 `load_existing()`，`__main__.py` 不再访问私有 `_store_manager`。
3. `qa_chain.py` 下划线别名与 `__init__.py` 懒加载门面补文档注释说明意图（不重构）。
4. `config.py` 增加 `mineru_output_dir` 覆盖说明，避免解析中间产物混入 DATA。

**契约影响**：`requirements.lock` 新增；`PublicKnowledgeRAG.load_existing()` 为纯新增方法。

**验证**：
- 新环境 `uv pip install -r requirements.txt -r requirements.lock` 可复现；
- `pytest test -q --ignore=test/test_cloud_sync.py` 全绿。

---

## 5. 测试验证策略（统一）

### 5.1 每模块红线

| 模块 | 新增测试文件 | 必过既有测试 |
| --- | --- | --- |
| M1 | test_pdf_structure.py | test_ingestion_pipeline.py |
| M2 | test_dedup_ingestion.py | test_milvus_store_offline.py, test_ingestion_pipeline.py |
| M3 | test_effective_date_filter.py | test_retrieval_strategies.py, test_citation_tracing.py |
| M4 | test_cleaner_protection.py | test_ingestion_pipeline.py |
| M5 | test_citations_split.py | test_public_kb_layout.py（AST 守卫） |
| M6 | （无新增，回归） | 全量 |

### 5.2 全量回归门禁

每完成 1 个模块提交前，必须：
```powershell
pytest test -q --ignore=test/test_cloud_sync.py   # 必须 239+ 全绿（新增测试计入）
```
M1 附加：3 本 PDF 抽 20-30 页人工样张核验（表格/双栏/目录 3 类）。

### 5.3 不做但留给后续专项的事项

- 大批量 PDF 并发化（M1 之后单独专项，须先清 reranker `last_status` 等共享可变状态——架构审查 §5）。
- 全量数据向量化入库（所有模块验收后统一执行）。
- 生产 Milvus 集合重建（含去重后重建、时效字段迁移）。

---

## 6. 提交与里程碑建议

| 里程碑 | 内容 | 预计验证 |
| --- | --- | --- |
| M0 | 基线冻结 | 239 passed |
| M1 | PDF 解析适配 + 样张核验 | 新测试 + 人工样张 |
| M2 | 块级去重 + 幂等 | 新测试 + 回归 |
| M3 | 法条时效性 | 新测试 + 回归 |
| M4 | 清洗保护 | 新测试 + 回归 |
| M5 | 拆分 + 契约复用 | AST 守卫 + 新测试 |
| M6 | 工程化治理 | 全量回归 |

每里程碑独立提交（遵循"每步可回退"），不推送远端。

---

*本执行计划由两份审查报告整合而来，仅文档产出，未改动任何代码。*
