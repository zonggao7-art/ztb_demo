# 招投标智能助手 — 代码结构全面审计报告

- **审计日期**：2026-08-15
- **P0 清理执行状态**：✅ 已于 2026-08-15 完成，见附录 D 执行记录
- **P1 结构重构执行状态**：✅ 已于 2026-08-15 完成，见附录 E 执行记录
- **P2 收尾整理执行状态**：✅ 已于 2026-08-15 完成，见附录 F 执行记录
- **审计范围**：`agent/`（7 文件 + 5 节点）、`public_kb/`（14 文件）、`cloud_sync/`（9 文件）、`scripts/`（16 文件）、`test/`（28 文件）、根目录 3 个独立脚本，共 **80 个 Python 源文件 / 15,059 有效代码行**
- **审计重点**：① 代码冗余（重复逻辑 / 死代码 / 重复工具函数与常量 / 重复文件）；② 单体模块臃肿（>800 有效行 / 核心职责 >3 / 依赖层级 >5）
- **审计方法**：
  1. 自研 AST 代码量统计器（有效行 = 总行 − 空行 − 注释行 − 文档字符串行）
  2. 静态分析：`vulture 2.16`（死代码，置信度 ≥60%）、`pyflakes`（未用导入/变量/未定义名）、自研 token 级克隆检测器（函数级 ≥85% 相似、文件级 ≥60% 相似）
  3. 对全部 80 个源文件逐一人工复核，所有死代码候选经全仓 `grep` 交叉验证
- **工具局限说明**：pylint 的 duplicate-code 检查因环境 astroid 版本过旧崩溃，已用自研克隆检测器替代；vulture 无法解析 `public_kb/process_csv.py`（Python 3.12 语法），其引用关系已人工补验。

---

## 1. 代码量统计报告

### 1.1 全项目规模

| 范围 | 文件数 | 总行数 | 有效代码行 |
| --- | --- | --- | --- |
| agent/ | 12 | 5,373 | 4,026 |
| public_kb/ | 14 | 4,372 | 3,274 |
| cloud_sync/ | 9 | 1,320 | 1,066 |
| scripts/ | 16 | 4,736 | 3,741 |
| test/ | 28 | 6,656 | 4,977 |
| 根目录脚本 | 3 | 661 | 507 |
| **合计** | **80** | **21,118** | **15,059** |

### 1.2 超过 800 有效行阈值的文件

| 文件 | 总行 | 有效行 | 顶层定义数 | 判定 |
| --- | --- | --- | --- | --- |
| **`agent/nodes/price_inquiry.py`** | 3,152 | **2,513**（阈值 3.1×） | 81（类 3 + 函数 78） | ⛔ **严重超标** |

### 1.3 接近阈值（600~800 有效行）

| 文件 | 有效行 | 备注 |
| --- | --- | --- |
| scripts/generate_three_core_report.py | 755 | 职责 6+ 项，见 §4.2 |
| public_kb/qa_chain.py | 480 | 职责 3 项以内，结构尚可 |
| test/db_explorer.py | 458 | 诊断工具 |
| scripts/export_policy_tables.py | 449 | 一次性导出工具 |
| test/test_citation_tracing.py | 449 | 测试文件 |

完整逐文件统计见附录 A。

### 1.4 依赖层级分析（判定标准：>5 层）

对 `agent/`、`public_kb/`、`cloud_sync/` 构建本地模块导入图并计算最长依赖链：

- **最长链 5 层**（含自身）：`agent.nodes.knowledge_qa → public_kb.rag_engine → public_kb.qa_chain → public_kb.citations → public_kb.chunk_ids`（共 5 个本地模块）——**未超过 5 层阈值**，但已到边界。
- 直接本地依赖最多的模块：`public_kb.rag_engine`（7 个，门面模式合理）、`agent.graph` / `agent.nodes.price_inquiry`（各 5 个）。
- 结论：**本项目依赖层级整体健康**，无模块违反 >5 层标准；体积问题集中在单文件内聚而非横向依赖。

---

## 2. 静态分析结果汇总

| 工具 | 扫描结果 | 复核后确认 |
| --- | --- | --- |
| vulture（≥60% 置信） | 49 处候选 | **生产代码 14 处真死代码**、6 处误报（见附录 B）、测试/脚本 8 处 |
| pyflakes | ~40 处 | 未用导入 20 处、未定义名 1 处（`Any`）、无占位符 f-string ~30 处 |
| 克隆检测（函数级 ≥85%） | 142 对 | 生产代码 9 组真实重复（其余为测试模板化用例，属正常） |
| 克隆检测（文件级 ≥60%） | 5 对 | 3 对为真实重复文件组，1 对为误报（闭包），1 对为低相似度 |

---

## 3. 问题清单（均经人工复核，区分业务必要逻辑与真实冗余）

### A. 冗余问题 · 死代码 / 无效代码（已确认零引用或行为失效）

| # | 位置 | 问题 | 类型 | 影响范围 |
| --- | --- | --- | --- | --- |
| A1 | `agent/nodes/price_inquiry.py:1930` | `SqlTimeoutError` 异常类：定义后从未被 raise 或 except（超时路径实际捕获 `FutureTimeoutError` 并返回空结果） | 死代码 | 无（仅占行） |
| A2 | `agent/nodes/price_inquiry.py:2663` | `_build_project_detail_guidance()`：无任何调用点（节点入口对 project_detail 缺编号走 `_build_unified_guidance`，见 2832 行） | 死代码 | 无 |
| A3 | `agent/nodes/price_inquiry.py:2472` | `_query_product_data()`：自标注 `[DEPRECATED]`，函数体仅返回空字典；不在 `_SUB_ROUTE_MAP` 中；入口已确定性拦截 product_query（2827 行）。仅 `test/test_sub_route.py:40` 以"验证兼容性"名义导入（从未调用） | 死代码（业务已下线） | 无；删除需同步清理测试导入 |
| A4 | `agent/nodes/price_inquiry.py:3124` | `_format_records()`：仅被一次性验证脚本 `scripts/verify_price_inquiry_p1.py` 引用 | 半死代码 | 仅调试工具 |
| A5 | `agent/nodes/price_inquiry.py:40` | `_FIELD_REGISTRY` 导入未使用（`_apply_output_template` 内部已自取默认值） | 未用导入 | 无 |
| A6 | `agent/nodes/price_inquiry.py:148-158` | `HardFilters` 字段 `company_type` / `product_name` / `supplier_name` / `project_name` 写入后从未被 SQL 构建读取（`project_name` 为 P0-11 有意屏蔽；产品类字段随产品线砍除彻底失效；但 LLM prompt 仍要求输出这些字段，属遗留契约） | 死字段 | 意图解析 prompt 与数据结构漂移 |
| A7 | `agent/nodes/price_inquiry.py` | `_build_search_term` 的 `mode` 参数与 `_build_candidate_sql` 的 `search_mode` 参数：docstring 自述"行为已统一为 OR"，参数无任何行为差异；导致 `_execute_recall_chain_core` 的 **Stage 2「FULLTEXT_AND」与 Stage 1「FULLTEXT_OR」生成完全相同的 SQL**——Stage 1 零行时 Stage 2 必然重复执行同一查询再零行 | 死参数 + 无效阶段 | 多关键词查询在零召回路径多执行 1 次无用 SQL（轻微性能浪费） |
| A8 | `agent/nodes/answer_templates.py:44` | `empty_result_guidance()`：无任何调用（`render_answer` 走各模板的 `empty_template`；test_bug_repairs.py:332 仅为测试名包含该词） | 死代码 | 无 |
| A9 | `agent/nodes/general_chat.py:21` | `GENERAL_CHAT_PROMPT`：定义后从未使用（节点返回硬编码欢迎语）；连带 `BaseChatModel`、`StrOutputParser` 两个导入变为无用（pyflakes 已标记）。注释"由 graph.py 注入 LLM"与实现不符 | 死代码 + 注释漂移 | 无；清理后该文件仅剩一个常量回答函数 |
| A10 | `agent/nodes/output_templates.py:26` | `FieldDescriptor.default_priority`：注册时传入（如 `default_priority="required"`）但运行时筛选只读 `required/conditional/optional` 三个列表，该字段从未被读取 | 死字段（只写不读） | 配置语义误导 |
| A11 | `agent/nodes/output_templates.py:340` | `_merge_templates()`：无任何调用（mixed 模板为手写） | 死代码 | 无 |
| A12 | `agent/router.py:42` | `RouterDecision.sub_intent`：标注"预留扩展"，从未读取 | 死字段（预留） | 无；如近期无规划建议删除 |
| A13 | `public_kb/embedding_service.py:93` | `embed_texts()`：全仓无调用（批量向量化各处直接调 `embeddings.embed_documents`） | 死代码 | 无 |
| A14 | `public_kb/config.py` | `embedding_provider`、`llm_provider`、`mysql_pool_size`、`mysql_pool_max_overflow`、`system_prompt` 五个配置字段全仓无读取者；其中 `mysql_pool_size`（默认 5）与 `price_inquiry.py` 硬编码 `_pool_max_size = 8` 形成"配置与实现脱节" | 死配置 | 维护者易被误导 |
| A15 | `scripts/build_report_pdf.py:13,44` | `PAGE_H`、`display_width()` 未使用（`footer` 为 fpdf2 页面钩子，vulture 误报） | 死代码 | 无 |
| A16 | `test/quality_check.py:54,62` | `check_url_leak()`、`check_truncated()` 定义后未调用；脚本读取的 `sample_export/` 目录已不存在 → 整个脚本为历史遗留 | 死代码 + 遗留脚本 | 无 |
| A17 | `test/test_bug_repairs.py:122` | `_get_bidder_query_template_keys()` 未调用（仅 `_get_project_detail_template_keys` 在 269 行被用） | 死代码 | 无 |
| A18 | 多处未用导入（pyflakes 全表） | `answer_templates.field`、`chunker.Dict/Optional`、`csv_loader.Path`、`mineru_parser.os`、`process_csv.time`、`public_kb/__main__.Settings`、`export_policy_tables.os`、`generate_three_core_report.subprocess`、`run_evaluation.traceback`、`smoke_test.json`、`create_fulltext_indexes.Any`、`db_explorer.sys`、`explain_sql.defaultdict`、`preview_candidates.json/datetime`、`general_chat.BaseChatModel/StrOutputParser` | 未用导入 | 无 |
| A19 | `agent/__main__.py:85` | `Any` 未定义即用于注解（`from __future__ import annotations` 使其不崩溃，但属真实缺陷） | 缺陷 | CLI 无运行时影响 |
| A20 | `public_kb/process_csv.py:90` | `Dict[str, any]` 误用内建 `any` 作类型（应为 `Any`） | 缺陷 | 无运行时影响（注解字符串化） |
| A21 | ~30 处无占位符 f-string | 如 `price_inquiry.py:2670/2907`、`csv_loader.py:471-528` 等 `f"纯文本"` | 风格冗余 | 无 |

### B. 冗余问题 · 重复实现的功能逻辑

| # | 位置 | 问题 | 克隆相似度 | 影响范围 |
| --- | --- | --- | --- | --- |
| B1 | `agent/nodes/price_inquiry.py` | **数据库行清洗循环重复 4 次**（bytes 解码 + isoformat 序列化 + str 兜底）：`_clean_result_row`(1860)、`_query_penalty_by_company_name`(2341-2352)、`_query_company_data` 信用代码联查(2428-2438)、`_query_bidding_aggregation`(2560-2570)。后 3 处与 `_clean_result_row` 逻辑一致 | 手工比对 | 行清洗口径若改动需改 4 处，极易漏改 |
| B2 | 三处 LLM 构造逻辑重复 | `price_inquiry._build_llm`(231) / `graph.build_graph`(124-133) / `rag_engine._create_llm`(255) 构造 ChatOpenAI 的 kwargs 几乎一致（model/api_key/temperature/timeout/max_retries/base_url） | 手工比对 | LLM 初始化参数口径三处漂移风险 |
| B3 | `public_kb/config.py:197` ↔ `public_kb/qa_chain.py:39` | `Settings.system_prompt` 与 `qa_chain.SYSTEM_TEMPLATE` 内容几乎逐字相同，前者无任何读取者 | 手工比对 | 提示词维护者不知道改哪份 |
| B4 | `public_kb/citations.py:322/334/362` | `_check_chunk_id` / `_check_chunk_uid` / `_check_full_text` 三个校验方法结构完全相同（列表推导找坏引用 + 构造 RuleResult）；`validate()`(304-307) 中 uncited/unknown 的计算与 R6/R7 规则内部(417-445) 重复计算 | 0.91~0.93 | 规则引擎新增 R8 时需复制第四份 |
| B5 | `agent/nodes/price_inquiry.py:472,538` | `_COMPANY_NAME_SUFFIXES`（21 项）与 `_ENTITY_SUFFIXES`（11 项）后缀常量列表重叠定义 | 手工比对 | 新增机构后缀需改两处 |
| B6 | `scripts/run_evaluation.py` ↔ `scripts/run_three_core_evaluation.py` | 两脚本 91% 相似：`load_done_ids` 100% 相同、`load_cases` 98%、`extract_result` 88%、主循环/进度/断点续跑骨架全同。**两者写入同一个 `test_report/raw_results.jsonl`**，旧脚本若再运行将以旧 schema 追加行，污染三大核心评测报告 | 文件级 0.91 | ⚠️ 评测数据一致性风险 |
| B7 | `scripts/run_knowledge_citation_eval.py` | 与前两者共享 `load_cases`(100%)、`load_done_ids`(100%)、AgentGraph 初始化与进度循环骨架 | 函数级 1.00 | 三处维护 |
| B8 | `scripts/generate_report.py` ↔ `scripts/generate_three_core_report.py` | 后者已复用前者 4 个函数，但又重写：`pct`（100% 相同）、`append_extreme_cases`（97%）、逐条统计主循环骨架、Markdown 骨架。**两者写同一批输出文件**（metrics.json / case_details.csv / evaluation_report.md），先后运行互相覆盖 | 函数级 1.00/0.97 | ⚠️ 报告产物被覆盖风险 |
| B9 | `scripts/export_policy_tables.py` ↔ `test/scan_export_csv.py` | `_cell_value` 100% 相同；`get_connection` / `list_user_databases` / SSCursor 分批导出流程近同。且两文件硬编码了**不同的 MySQL 密码**（`123456` vs `.19900504tT`） | 函数级 1.00 | 一次性工具双份维护 |
| B10 | `test/db_explorer.py:29,145` ↔ `test/scan_export_csv.py:53` ↔ `test/inspect_price_dbs.py:35,42` ↔ `test/scan_tables.py:22` ↔ `test/create_fulltext_indexes.py:49` ↔ `test/export_samples.py:38` | `get_connection`（含重试参数）与 `json_serializer`/`serialize`（datetime 序列化）在 6 个诊断脚本中重复实现 | 0.85~1.00 | 诊断工具公共层缺失 |
| B11 | `test/test_citation_tracing.py:350,358` | `search` 与 `hybrid_search` 两个 mock 方法 93% 相似（测试桩，可参数化） | 0.93 | 低 |
| B12 | `generate_test_sets.py:157,208` | `_bid_b_line` / `_penalty_line` 行模板 86% 相似（字段集不同，可合并为参数化函数） | 0.86 | 低 |
| B13 | `test/test_bug_repairs.py:23-110` | **测试文件内冻结了生产代码副本**：本地重定义 `HardFilters` / `SearchIntent` dataclass（字段集已与生产漂移——含生产不存在的 `credit_rating`、`winning_amount_min/max` 等）、`_normalize_token`、`_build_constraint_conditions_fixed`（"修复后的版本"），且 Bug1/Bug2 用例不 import 生产模块，全部针对副本断言。**生产代码若回归，这些"回归测试"不会失败**。同文件 285 行起的集成用例则正常导入生产模块（answer_templates/output_templates） | 手工比对 | ⚠️ 伪回归测试，掩盖生产缺陷 |

### C. 冗余问题 · 重复 / 遗留文件

| # | 位置 | 问题 | 判定 | 影响范围 |
| --- | --- | --- | --- | --- |
| C1 | `Irrelevant files/`（25 个文件） | 旧 `.pyc` 备份目录，其中 `test___pycache__/parse_and_ingest.pyc`、`scan_all_tables.pyc` 对应的源码已不存在 | 死文件 | 无；整目录可删 |
| C2 | `test/profile_current_price.py`、`test/profile_new_price.py` | 两者均导入 `_query_price_data`——**该函数在 price_inquiry 重构后已不存在，脚本运行即 ImportError** | 破损死脚本 | 无 |
| C3 | `migrate_milvus_cloud.py` | 已迁移完成的"一次性全量迁移"脚本，功能被 `cloud_sync` 包完全覆盖（`python -m cloud_sync full/verify` 等价） | 重复文件 | 无；归档可删 |
| C4 | `rebuild_and_verify.py` | 知识库重建 + 验证问答，核心被 `python -m public_kb --init` 覆盖 | 重复文件 | 无；归档可删 |
| C5 | `test/_step1_csv_check.py` ~ `test/_final_verify.py`（6 个文件） | CSV→MySQL 数据迁移过程的一次性手工步骤脚本，无任何模块引用，含硬编码 root 口令 | 历史遗留 | 无；归档可删 |
| C6 | `scripts/csv_to_mysql.py` | 数据导入一次性脚本（导入已完成），含硬编码 root 口令 `123456` | 历史遗留 | 无；归档 |
| C7 | `test/scan_tables.py`、`test/inspect_price_dbs.py`、`test/preview_candidates.py`、`test/print_schema_summary.py`、`test/export_samples.py`、`test/diagnose_pdf.py`、`test/scan_export_csv.py` | 数据准备阶段诊断工具，未在 CLAUDE.md 注册，依赖的数据目录（raw_tables/sample_export/price_dbs_schema.json）多已不再是工作流输入 | 历史遗留 | 无；归档 |
| C8 | `test/profile_current_price.py` / `profile_new_price.py` / `profile_node_price.py` | 三份 profile 脚本 67%~81% 相似，前两份已破损（见 C2） | 重复+破损 | 无 |

### D. 单体模块臃肿判定

#### D1. `agent/nodes/price_inquiry.py` — **三项标准全部超标** ⛔

| 判定标准 | 阈值 | 实际 | 结论 |
| --- | --- | --- | --- |
| 有效代码行数 | ≤800 | **2,513** | ⛔ 超标 3.1× |
| 核心职责数 | ≤3 | **10 项**（见下） | ⛔ 超标 |
| 依赖层级 | ≤5 | 直接本地依赖 5 个模块、第三方 11 个包（层级本身 3 层，未超） | ✓ |

**职责清单（按代码分区实测）**：
1. MySQL 连接池与参数（49-123、656-714 行）
2. 数据模型 HardFilters / SearchIntent（125-225）
3. LLM 初始化 + 统一意图解析 Prompt（228-329）
4. 关键词提取 / 实体校验 / 项目编号确定性提取（332-653）
5. Milvus 语义集合 bootstrap：建集合、全量入库、自动重建、召回（763-1210，约 400 行）
6. 枚举值归一化（1213-1340）
7. SQL 构建器族（7 个 builder，1343-1793）
8. 检索执行链：超时、五级降级、重排序、回表、漏斗日志（1796-2314）
9. 三张表的专用查询函数（2317-2599）
10. 路由表 + 三层查询守卫 + 引导话术 + 节点入口（2602-3121）

**影响**：任何一处修改都需在本文件 3,152 行中定位；P0/P1/P2 多轮修复叠加（文件内 P0-12、P1-3、P2 等大量历史注释）使得新旧逻辑交织；单文件 81 个顶层定义远超可读性上限。

#### D2. `scripts/generate_three_core_report.py` — 职责超标、行数接近阈值 ⚠️

- 有效行 755（接近 800 阈值）；职责 6 项：用例加载、逐条校验统计、Markdown 报告、HTML 报告、内联 SVG 图表、环境信息采集。
- 且与 `generate_report.py` 共享输出文件（见 B8）。

#### D3. 依赖层级结论

最长本地依赖链 5 层（见 §1.4），**未违反 >5 层标准**；无需拆分依赖链。

---

## 4. 优化方案（按优先级）

### P0 · 立即清理（零风险，纯删除/合并，可一次提交完成）

| 项 | 动作 | 预计删行 |
| --- | --- | --- |
| A1-A3, A5, A7 | 删除 `SqlTimeoutError`、`_build_project_detail_guidance`、`_query_product_data`、未用导入 `_FIELD_REGISTRY`、`mode`/`search_mode` 死参数与 FULLTEXT_AND 无效阶段（保留 `_RECALL_STAGE_WEIGHTS` 键位以防未来恢复） | ~120 行 |
| A4 | `_format_records` 下沉到 `scripts/verify_price_inquiry_p1.py` 内部 | 30 行（净迁） |
| A8-A13 | 删除 `empty_result_guidance`、`GENERAL_CHAT_PROMPT`（及连带未用导入）、`default_priority`、`_merge_templates`、`sub_intent`、`embed_texts` | ~90 行 |
| A14 | 删除 5 个无读者配置字段；`_pool_max_size` 改读 `settings.mysql_pool_size`（默认值同步为 8） | 净减 20 行 |
| B1 | 4 处行清洗循环统一调用 `_clean_result_row`（该函数补 `_source_db/_source_table` 参数即可复用） | ~50 行 |
| B2 | 提取 `public_kb/llm_factory.py::create_llm(settings, temperature=None)`，三处调用点统一（agent 已依赖 public_kb，无新增依赖方向） | 净减 ~25 行 |
| B3 | `qa_chain` 改用 `settings.system_prompt`，删除重复常量 `SYSTEM_TEMPLATE` | 净减 3 行 |
| B5 | 删除 `_ENTITY_SUFFIXES`，统一用 `_COMPANY_NAME_SUFFIXES` | 4 行 |
| A15-A21 | 删除 `PAGE_H`/`display_width`、`check_url_leak`/`check_truncated`、`_get_bidder_query_template_keys`、20 处未用导入、修复 `Any` 未定义与 `Dict[str, any]`、清理无占位符 f-string | ~60 行 |
| C1, C2 | 删除 `Irrelevant files/` 目录与两份破损 profile 脚本（保留 `profile_node_price.py`） | 25 文件 + 2 文件 |
| **附带安全修复** | `price_inquiry.py:63`、`scripts/export_policy_tables.py:36`、`scripts/csv_to_mysql.py`、`test/_final_verify.py` 等硬编码数据库口令全部改读环境变量 | — |

P0 合计：净删约 400 行、删 27 个遗留文件，业务行为零变化。

### P1 · 结构重构（分两步，每步独立可回滚）

**P1-1：`price_inquiry.py` 按职责拆包**（D1）

目标结构（每文件 150~450 行、职责 ≤2）：

```
agent/nodes/price_inquiry/
├── __init__.py        # 兼容层：重导出全部历史符号（见 §5 兼容性验证清单）
├── schema.py          # _HARDCODED_SCHEMA / _get_classification / _semantic_columns
├── models.py          # HardFilters / SearchIntent（清理 A6 死字段后）
├── db.py              # 连接池（_mysql_base_kwargs / _get_connection / _release_connection）
├── intent.py          # 意图 Prompt、_parse_unified_intent、关键词提取、实体校验、项目编号提取
├── enum_norm.py       # 枚举归一化（_load_enum_values / _normalize_intent_enums）
├── semantic.py        # Milvus 语义集合 bootstrap 与召回（~400 行）
├── sql_builders.py    # 7 个 SQL 构建器 + 硬过滤 + 放宽策略
├── recall.py          # 执行链 / 超时 / 重排序 / 回表 / 漏斗
├── queries.py         # 四张表的专用查询函数
└── node.py            # 守卫 + 引导话术 + node_price_inquiry 入口
```

依赖方向单向无环：`node → queries → recall → sql_builders → schema/models`；`intent`、`enum_norm`、`semantic` 平行；`db` 被 recall/queries 依赖。`__init__.py` 仅做重导出（厚度 ≈30 行），`agent/nodes/__init__.py`、`agent/graph.py` 的导入路径 `from .price_inquiry import node_price_inquiry` **完全不变**。

**P1-2：评测脚本族去重**（B6/B7/B8）

```
scripts/
├── eval_common.py          # load_cases / load_done_ids / extract_result / AgentGraph 初始化 / 进度循环
├── eval_report_common.py   # norm / value_recalled / build_corpus / resolve_gt_values / _percentile / pct
├── run_three_core_evaluation.py   # 仅保留测试集定义 + 特有字段提取
├── run_knowledge_citation_eval.py # 仅保留关联校验 + 拒答统计
├── generate_three_core_report.py  # 仅保留报告渲染
└── archive/run_evaluation.py      # 归档（text2sql 1000 例评测已被三大核心评测取代，且共用输出文件存在 schema 污染风险）
```

**P1-3：`generate_three_core_report.py` 瘦身**（D2）
HTML/SVG 渲染（约 250 行）抽至 `scripts/report_html.py`；Markdown 骨架抽至 `scripts/report_markdown.py`。拆分后主脚本 300 行左右、职责单一。

**P1-4：`test/test_bug_repairs.py` 伪回归测试修复**（B13）
Bug1/Bug2 用例改为从 `agent.nodes.price_inquiry` 真实导入 `_build_constraint_conditions` / `_normalize_token` / `HardFilters` / `SearchIntent`，删除本地冻结副本（"修复后版本"即生产实现本身——这才是回归测试应有的形态）。若需保留修复过程存档，将副本单独抽出为注释或文档，不作为测试执行。

### P2 · 归档与低风险整理

| 项 | 动作 |
| --- | --- |
| C3-C8 | 建立 `archive/`（或 `scripts/legacy/` + `test/legacy/`）目录，将 migrate_milvus_cloud.py、rebuild_and_verify.py、_step* 6 件套、csv_to_mysql.py 及 7 个数据准备诊断脚本移入（保留历史参考，不直接删除） |
| B4 | `citations.py` 规则引擎参数化：提取 `_rule(rule_id, name, description, bad_list)` 辅助函数与 `_compute_cited_sets(citations, answer)`，R1-R4 与 R6/R7 复用，`validate()` 不再重复计算 |
| B9-B11 | 提取 `test/_diag_common.py`（get_connection / json_serializer / _cell_value），6 个诊断脚本统一引用；`scan_export_csv.py` 归档（功能已被 export_policy_tables.py 覆盖） |
| A6 | 清理 HardFilters 产品类死字段（product_name/supplier_name 等）并同步精简 `_UNIFIED_INTENT_SYSTEM` prompt 中对应字段说明 |
| A12 | `sub_intent` 预留字段：若近期无二级路由规划则删除 |
| B12 | `generate_test_sets.py` 行模板参数化（低优先级，生成器脚本可不动） |
| 补充 | `agent/nodes/knowledge_qa.py:29-30` 直接访问 `PublicKnowledgeRAG._store_manager.load_existing()` / `_build_qa_chain()` 私有成员——建议在 rag_engine 增加公开 `ensure_loaded()` 方法，消除跨包私有穿透 |

---

## 5. 优化方案可行性验证

### 5.1 死代码清理的引用交叉验证（已全部执行）

- A1~A17 每项均经全仓 `grep` 验证零引用（含 `test/`、`scripts/`、`agent/`、`public_kb/`、`cloud_sync/`）；
- 例外与处置：`_query_product_data` 被 `test/test_sub_route.py:40` 导入（未调用）→ 方案已含同步删除测试导入；`_format_records` 被 `scripts/verify_price_inquiry_p1.py` 引用 → 方案已含下沉；
- vulture 误报清单（不清理）：`public_kb/__init__.py::__getattr__`（PEP 562 懒加载钩子）、`footer`（fpdf2 页面钩子）、`state.router_intent`（TypedDict 字段经 `state.get` 读取）、`csv_loader.load_file/classify_file/save_chunks_to_markdown`（被 vulture 无法解析的 process_csv.py 引用）、测试 mock 辅助方法、dict 字面量键名。

### 5.2 拆分重构的兼容性验证

**对外符号契约**（`agent.nodes.price_inquiry` 当前被以下 11 个文件导入，拆分后 `__init__.py` 需重导出的符号已逐一枚举）：

| 导入方 | 使用的符号 |
| --- | --- |
| agent/nodes/__init__.py、agent/graph.py | `node_price_inquiry` |
| test/test_sub_route.py | `HardFilters`、`SearchIntent`、`_HARDCODED_SCHEMA`、`_parse_unified_intent`、`_query_product_data`(随 A3 删除) |
| test/test_recall_optimization.py | `HardFilters`、`SearchIntent`、`_strip_preference_filters`、`_query_tables` 等 |
| test/test_p0_11_guard.py | `_is_valid_company_name`、`_has_valid_query_entity`、`_looks_like_code` |
| test/test_p0_12_project_number_detection.py | `_extract_project_number_candidate`、`_looks_like_code`、`_is_valid_company_name` |
| test/test_p0_11_full_recall_fix.py | `HardFilters`、`SearchIntent`、`_build_llm`、`_query_tables` 等 |
| scripts/rebuild_mysql_semantic_collection.py、scripts/verify_price_inquiry_p1.py | `_build_llm`、`_format_records`(下沉) 等 |
| test/test_bug_repairs.py | 不导入 price_inquiry（其 Bug1/Bug2 用例针对本地冻结副本，见 B13；集成用例仅导入 answer_templates / output_templates，拆分不受影响） |

上述符号分散在拆分后的 `models.py / intent.py / sql_builders.py / recall.py / queries.py / db.py / node.py` 中，`__init__.py` 一次性重导出即可保证 **11 个导入方零改动**。`from .price_inquiry import X` 的包导入语义在 Python 中由 `__init__.py` 承接，路径不变。

### 5.3 回归验证方案

1. **单元回归**：`python -m pytest test/ -v`（现有 8 个测试文件全部为 mock 驱动、无需真实 DB/LLM，覆盖意图解析、硬过滤、守卫、引用溯源等核心路径）——P0 清理与 P1-1 拆分完成后必须全绿；
2. **静态回归**：`python -m pyflakes agent public_kb cloud_sync scripts` 应仅剩既有告警，无新增未定义名；
3. **行为冒烟**：`python -m agent --question "招标方式有哪些？"`（知识问答链路）与 `python -m agent --question "查询XX公司的工商信息"`（价格查询链路）人工比对拆分前后输出；
4. **评测管线回归**：P1-2 合并后运行 `python scripts/run_three_core_evaluation.py --limit 5` + `python scripts/generate_three_core_report.py`，确认 raw_results.jsonl schema 与报告产物不变；
5. **FULLTEXT_AND 阶段删除验证**：该阶段 SQL 与 Stage 1 逐字节一致（mode 参数无效），删除后零召回路径少执行 1 次相同 SQL，召回结果集合不变——由 `test/test_recall_optimization.py` 中的降级链用例回归覆盖。

### 5.4 拆分后职责与依赖断言

| 断言 | 验证方式 |
| --- | --- |
| 每个新文件 ≤450 有效行、职责 ≤2 | 复用本次审计的 LOC 统计器复测 |
| 无环依赖：node → queries → recall → sql_builders → schema/models | 复用本次审计的依赖图脚本复测 |
| 外部 11 个导入方零改动 | grep 导入路径不变 + pytest 全绿 |
| 清理后无功能异常 | §5.3 四步回归 |

---

## 附录 A · 完整代码量统计（有效行降序，节选前 20）

| 文件 | 总行 | 有效行 | 顶层定义 |
| --- | --- | --- | --- |
| agent/nodes/price_inquiry.py | 3152 | 2513 | 81 |
| scripts/generate_three_core_report.py | 879 | 755 | 12 |
| public_kb/qa_chain.py | 617 | 480 | 11 |
| test/db_explorer.py | 559 | 458 | 13 |
| scripts/export_policy_tables.py | 632 | 449 | 15 |
| test/test_citation_tracing.py | 590 | 449 | 41 |
| scripts/generate_report.py | 522 | 440 | 10 |
| generate_test_sets.py | 512 | 397 | 26 |
| test/test_sub_route.py | 556 | 387 | 8 |
| public_kb/csv_loader.py | 535 | 371 | 2 |
| public_kb/citations.py | 445 | 359 | 7 |
| scripts/run_knowledge_citation_eval.py | 378 | 323 | 8 |
| agent/nodes/output_templates.py | 387 | 322 | 7 |
| cloud_sync/connection.py | 402 | 328 | 5 |
| test/test_p0_11_full_recall_fix.py | 431 | 316 | 5 |
| test/test_bug_repairs.py | 397 | 281 | 10 |
| test/test_cloud_sync.py | 316 | 254 | 8 |
| agent/nodes/answer_templates.py | 442 | 253 | 11 |
| public_kb/process_csv.py | 324 | 253 | 4 |
| test/test_p0_11_guard.py | 355 | 245 | 6 |

## 附录 B · vulture 误报清单（不列为问题）

| 位置 | 误报原因 |
| --- | --- |
| public_kb/__init__.py:18 `__getattr__` | PEP 562 模块级懒加载钩子，由解释器隐式调用 |
| public_kb/csv_loader.py `load_file`/`classify_file`/`save_chunks_to_markdown` | vulture 解析 process_csv.py 失败，实际被其引用 |
| scripts/build_report_pdf.py:37 `footer` | fpdf2 每页自动调用的框架钩子 |
| agent/state.py:28 `router_intent` | TypedDict 字段，经 `state.get("router_intent")` 字符串键读取 |
| public_kb/rag_engine.py `add_pdf` | 公开 API（CLAUDE.md 文档化的对外接口） |
| test/ 各 dict 字面量键名、mock 辅助方法 | 测试代码模式性误报 |
| agent/graph.py `_with_fallback`↔`wrapped`（克隆检测 0.97） | 闭包内部函数，非复制粘贴 |

## 附录 C · 附带安全发现（不属于两类审计目标，但影响面大）

数据库口令以明文硬编码于 6 处源码：`agent/nodes/price_inquiry.py:63`（默认值）、`scripts/export_policy_tables.py:36`、`test/scan_export_csv.py:24`、`test/_final_verify.py`、`scripts/csv_to_mysql.py:39`（root/123456）、`test/_step5_fulltext.py` 等。已纳入 P0 清理清单，统一改读 `.env` 环境变量。

## 附录 D · P0 清理执行记录（2026-08-15）

### 执行内容

| 报告项 | 执行结果 |
| --- | --- |
| A1-A3, A5, A7 | ✅ 删除 `SqlTimeoutError`、`_build_project_detail_guidance`、`_query_product_data`、`_FIELD_REGISTRY` 未用导入、`mode`/`search_mode` 死参数与 FULLTEXT_AND 无效阶段（`_RECALL_STAGE_WEIGHTS` 键位保留）；降级链文档与阶段编号（1/3/4/5）同步更新 |
| A4 | ✅ `_format_records` 下沉至 `scripts/verify_price_inquiry_p1.py` 内部 |
| A8-A13 | ✅ 删除 `empty_result_guidance`、`GENERAL_CHAT_PROMPT`（及 3 处连带未用导入）、`FieldDescriptor.default_priority`（13 处注册参数同步移除）、`_merge_templates`、`RouterDecision.sub_intent`、`embed_texts` |
| A14 | ✅ 删除 `embedding_provider`、`llm_provider`、`mysql_pool_size`、`mysql_pool_max_overflow` 四个无读者配置字段；`system_prompt` 保留并经 B3 接入 qa_chain；死常量 `_pool_max_size` 一并删除（CLAUDE.md 连接池描述同步修正） |
| B1 | ✅ 4 处行清洗循环统一为 `_clean_result_row`（3 处直调 + 原实现保留） |
| B2 | ✅ 新建 `public_kb/llm_factory.py::create_llm()`，`graph.build_graph` / `rag_engine._create_llm` / `price_inquiry._build_llm`（保留薄包装兼容测试导入）三处统一，两处冗余 `ChatOpenAI` 导入移除 |
| B3 | ✅ `qa_chain` 改用 `settings.system_prompt`，删除重复常量 `SYSTEM_TEMPLATE` |
| B5 | ⚠️ 复核修正：`_ENTITY_SUFFIXES` 与 `_COMPANY_NAME_SUFFIXES` 属**有意差异**（前者为强实体词尾子集含“股份公司”，后者为工商主体校验全集；直接合并会误截“处罚”的“处”等普通词）——不合并，仅补充注释说明差异原因 |
| A15-A21 | ✅ 删除 `PAGE_H`、`display_width`、`check_url_leak`、`check_truncated`、`_get_bidder_query_template_keys`；清理 20+ 处未用导入（含 `Any` 未定义修复、`Dict[str, any]`→`Dict[str, object]`）；剥离 35 处无占位符 f-string；`scan_export_csv` 死参数 `estimated_rows` 移除；`test_cloud_sync` 未用形参改名 |
| C1, C2 | ✅ 删除 `Irrelevant files/`（25 个旧 .pyc）与两份破损 profile 脚本（保留 `profile_node_price.py`） |
| 安全修复 | ✅ 15 处硬编码数据库口令全部改为 `os.getenv("MYSQL_PASSWORD", "")`（`.env` 已含 MYSQL_PASSWORD，本地行为不变） |
| 连带修复 | `test_sub_route.py` 的 `test_query_product_data` 由“调用已删除函数”改写为断言能力边界引导（复用 `_build_capability_boundary_answer`，同时消除其未用导入告警）；`test_recall_optimization.py` 的 mode 参数测试随签名删除改写；`test_bug_repairs.py` 循环变量遮蔽导入问题修复 |

### 执行后指标

| 指标 | 执行前 | 执行后 | 变化 |
| --- | --- | --- | --- |
| 源码文件数 | 80 | 79（删 2、新增 llm_factory.py 1） | -1 |
| 有效代码行（扫描范围内） | 15,059 | 14,791 | **-268 行**（净） |
| price_inquiry.py 有效行 | 2,513 | 2,400 | -113 |
| pyflakes 告警 | ~40 | **0** | ✅ 归零 |
| vulture 命中（≥60%） | 49 | ~21 | 剩余全部为已知误报（PEP 562 钩子、TypedDict 字段、fpdf 钩子、process_csv 引用链、公开 API）或已列入 P1/P2 的计划项 |
| pytest | — | **188 passed, 1 skipped** | ✅ 零回归 |

### 遗留（已列入 P1/P2 计划，本次未动）

- P1-1：`price_inquiry.py` 拆包（剩余 2,400 有效行仍超阈值）
- P1-2/P1-3：评测脚本族去重与报告脚本瘦身
- P1-4：`test_bug_repairs.py` 伪回归测试修复（本地冻结副本）
- P2：A6 HardFilters 死字段清理、citations 校验器参数化、遗留脚本归档等

## 附录 E · P1 结构重构执行记录（2026-08-15）

### 执行内容

| 报告项 | 执行结果 |
| --- | --- |
| P1-1 `price_inquiry.py` 拆包 | ✅ 原 2,400 有效行单文件拆为 11 个模块的包 `agent/nodes/price_inquiry/`：`node.py`(396 eff) / `queries.py`(215) / `recall.py`(441) / `sql_builders.py`(351) / `intent.py`(343) / `semantic.py`(414) / `enum_norm.py`(112) / `db.py`(75) / `schema.py`(39) / `models.py`(84) / `__init__.py`(兼容层，114 个符号全量重导出)。AST 精确切片迁移，函数体逐字节保留；`_get_query_fn` 从 `sys.modules[__name__]` 动态查找改为显式引用 queries 模块 |
| P1-2 评测脚本去重 | ✅ 新建 `scripts/eval_common.py`（load_cases / load_done_ids / extract_result，三个评测脚本共用）；`run_three_core_evaluation.py`、`run_knowledge_citation_eval.py` 改为引用共用模块；`run_evaluation.py`（text2sql 1000 例，与三大核心共用输出文件存在 schema 污染风险）归档至 `scripts/archive/` |
| P1-3 报告脚本瘦身 | ✅ 新建 `scripts/eval_report_common.py`（norm / value_recalled / build_corpus / resolve_gt_values / _percentile / pct，6 个共享 helper）；`generate_three_core_report.py` 由 755 有效行瘦身为 341 行主脚本（仅统计骨架 + 环境采集），Markdown 渲染（build_markdown / append_extreme_cases / bottleneck_lines / optimize_lines / classify_failure，约 250 行）拆分至 `scripts/report_markdown.py`，HTML/SVG 渲染（svg_hbar / build_html，约 200 行）拆分至 `scripts/report_html.py`；`generate_report.py`（旧 text2sql 报告）归档至 `scripts/archive/` |
| P1-4 伪回归测试修复 | ✅ `test/test_bug_repairs.py` 的 Bug1 五用例（A~E）从「本地冻结副本」改为直接导入生产模块 `_build_constraint_conditions` / `_normalize_token` / `HardFilters` / `SearchIntent` 并全部通过——生产代码回归时该测试套件现在会真实失败 |

### 执行后指标

| 指标 | P0 后 | P1 后 | 变化 |
| --- | --- | --- | --- |
| 超过 800 有效行的文件数 | 1（price_inquiry.py 2,400） | **0** | ✅ 阈值清零 |
| 最大单文件有效行 | 2,400 | 441（recall.py） | -81.6% |
| 源码文件数 | 79 | 84（拆包 +11，归档 -2，新增共用模块 +4） | +5 |
| 有效代码行 | 14,791 | 14,080 | -711（净） |
| pyflakes 告警（不含归档目录） | 0 | 0 | ✅ 保持零 |
| pytest | 188 passed, 1 skipped | **188 passed, 1 skipped** | ✅ 零回归 |

### 验证方式与结果

1. **行为等价**：重新运行 `generate_three_core_report.py`，产出的 metrics.json 与重构前快照逐字段对比——除 `env`（解释器/依赖版本元数据，与重构无关）外**完全一致**（field_recall=99.464%、answer_accuracy=99.533%、correct=1493/1500 不变）；
2. **兼容性**：11 个外部导入方（nodes/__init__、graph、6 个测试文件、2 个 scripts、1 个 profile 脚本）零改动，通过包 `__init__.py` 重导出全部 114 个符号；
3. **依赖方向**：`node → queries → recall → sql_builders → intent → db` 单向直线链（含自身 6 层），`enum_norm`/`semantic`/`schema`/`models` 平行，无环；
4. **回归**：全量 pytest 188 passed、pyflakes 0 告警。

### 与计划的偏差说明

- `_query_product_data` 相关测试（test_sub_route）在 P0 已改为断言能力边界引导，P1 无额外偏差；
- 归档目录 `scripts/archive/` 内为冻结的历史脚本，不计入 pyflakes 扫描范围。

## 附录 F · P2 收尾整理执行记录（2026-08-15）

### 执行内容

| 报告项 | 执行结果 |
| --- | --- |
| C3-C8 遗留脚本归档 | ✅ 建立三个归档区：`archive/`（migrate_milvus_cloud.py、rebuild_and_verify.py）、`scripts/archive/`（追加 csv_to_mysql.py）、`test/legacy/`（_step* 7 件套、scan_tables / inspect_price_dbs / preview_candidates / print_schema_summary / export_samples / diagnose_pdf / scan_export_csv / quality_check 及 price_dbs_schema.json，共 16 个文件）。test/ 目录现仅剩 4 个活跃诊断工具（db_explorer / explain_sql / create_fulltext_indexes / profile_node_price）与 8 个测试文件 |
| A6 HardFilters 死字段清理 | ✅ 从 `models.py` 移除从未被 SQL 构建读取的 4 个字段：`company_type`、`product_name`、`supplier_name`、`project_name`（`from_dict` 映射与 `_UNIFIED_INTENT_SYSTEM` prompt 中的 company_type 行同步清理；prompt 保留 project_name 的"禁止用于检索"行为指令，LLM 输出该键时由 from_dict 直接忽略）；`category`/`price_range` 保留（仍被通用引擎读取） |
| A12 sub_intent | ✅ 已于 P0 提前完成（无需重复处理） |
| B4 citations 校验器参数化 | ✅ `validate()` 重写：新增模块级 `_compute_cited_sets()`（cited/uncited/unknown 三集合只算一次，规则判定与报告字段共用），新增 `CitationValidator._make_rule()`（R1-R4、R6-R7 共用的规则构造器）；删除 5 个重复的 staticmethod（`_check_chunk_id` / `_check_chunk_uid` / `_check_full_text` / `_check_markers` / `_check_all_context_marked`），R5 逻辑复杂保留独立实现。文件由 445 行降至约 370 行 |
| B9-B11 诊断脚本去重 | ✅ 新建 `test/_diag_common.py`（共享 `get_connection`）；db_explorer 与 create_fulltext_indexes 改为引用共享实现并删除本地副本（各自保留 DB_CONFIG——超时参数按用途不同，属有意差异）；scan_export_csv.py 随归档消除 `_cell_value` 重复 |
| 补充项（knowledge_qa 私有穿透） | ✅ `PublicKnowledgeRAG` 新增公开方法 `ensure_loaded()`（内部封装 `_store_manager.load_existing()` + `_build_qa_chain()`），`knowledge_qa._get_rag()` 不再触碰私有成员 |
| B12 generate_test_sets 行模板合并 | ⚠️ 评估后**不做**：`_bid_b_line` 与 `_penalty_line` 虽相似，但字段语义与格式结构（「」包裹、前缀标签）不同，强行参数化对一次性生成器脚本增加无谓间接层。已记录为有意保留 |

### 连带修复

- `test_p0_12_project_number_detection.py`：A6 删除 `project_name` 字段导致 2 个用例 TypeError；修复同时发现该文件同样存在**冻结副本**问题（本地简化版 `_build_constraint_conditions_strict`），按 P1-4 同一原则改为直接测试生产 `_build_constraint_conditions`，删除本地副本——生产回归时该套件现在真实有效。

### 执行后指标（P2 完成后全流程汇总）

| 指标 | 审计前 | **P0+P1+P2 后** |
| --- | --- | --- |
| 活跃源码文件数 | 80 | **74**（归档 16、删除 3、新增 7） |
| 活跃有效代码行 | 15,059 | **13,071**（净减 1,988 行） |
| 超 800 行文件数 | 1（2,513 行） | **0** |
| 最大单文件 | 2,513 行 | 441 行 |
| pyflakes 告警 | ~40 | **0** |
| vulture 命中 | 49 | ~18（剩余均为已确认误报或有意保留） |
| pytest | 188 passed, 1 skipped | **188 passed, 1 skipped**（且 test_bug_repairs / test_p0_12 两套"伪回归测试"已改造为真实回归测试） |
| 硬编码数据库口令 | 15 处 | **0** |

### 审计结论

两类目标问题已全部处置：冗余问题（死代码 14 处、重复实现 9 组、重复/遗留文件 27 个）经 P0/P2 清零或归档；单体模块臃肿（唯一超标文件 price_inquiry.py 2,513 有效行 / 10 项职责）经 P1 拆分为 11 个职责单一的子模块。三项判定标准（>800 行 / 职责 >3 / 依赖层级 >5）现全部满足，回归验证（pytest 188 通过、pyflakes 归零、评测管线产物逐字段一致）确认无功能异常。

