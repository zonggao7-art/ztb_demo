# public_kb 优化 — 阶段性交付总结（2026-08-30 里程碑）

> 状态：✅ M0–M6 全部实现并通过分模块独立验证；整体验证 V-1/V-2/V-3/V-5/V-6 完成
> 上游：`docs/execution_plans/public_kb_optimization_execution_plan_20260830.md`（总计划）
> 细则：`docs/execution_plans/public_kb_optimization_execution_detail_20260830.md`（任务卡 + 附录 A-I 各模块执行状态）
> 审查证据：`docs/public_kb_architecture_review_20260830.md`、`docs/public_kb_data_pdf_dedup_review_20260830.md`

---

## 1. 模块交付总览

全量测试从基线 **239 → 284**（新增 45 用例），对外契约零破坏（AST 守卫全程护航）。

| 模块 | 内容 | 全量测试 |
| --- | --- | --- |
| M0 | 基线冻结 + 契约清单 | 239 |
| M1 | PDF 解析适配（表格原子块/目录点线过滤/双栏乱序打标/修复标题行正文丢失缺陷） | 253 |
| M2 | 块级去重 + 幂等导入（`chunk_uid` 判重 + `enable_dedup` 开关） | 262 |
| M3 | 法条时效性（`build_effective_expr` + 检索 expr 过滤 + `effective_date/status` 字段） | 273 |
| M4 | 清洗保护条款号（短行白名单 + 页眉去重豁免） | 281 |
| M5 | 契约常量复用 + embeddings 单源 + csv_loader/citations 拆分（re-export 保兼容） | 284 |
| M6 | 依赖冲突修复 + `requirements.lock` + `load_existing()` 公开化 + 注释治理 | 284 |

## 2. 新增/改动文件

**新增（7 源码 + 5 测试 + 1 lock）**
- `public_kb/ingestion/transforms/pdf_structure.py`（M1）
- `public_kb/ingestion/sources/csv_loader_structure.py`、`public_kb/generation/citations_{models,build,validate}.py`（M5 拆分）
- `test/test_{pdf_structure,dedup_ingestion,effective_date_filter,cleaner_protection,citations_split}.py`
- `requirements.lock`

**改动（源码）**：`config.py`、`rag_engine.py`、`services/milvus_store.py`、`ingestion/sinks/milvus_sink.py`、`ingestion/pipeline.py`、`ingestion/models.py`、`ingestion/sources/csv_loader.py`、`ingestion/transforms/cleaner.py`、`contracts.py`、`retrieval/{retriever,fallback,milvus_search}.py`、`services/embeddings.py`、`generation/citations.py`（门面）、`qa_chain.py`、`__init__.py`、`__main__.py`、`requirements.txt`。

> 注：`git status` 中 `public_kb/` 的大量 `M` 是会话开始前就存在的未提交 WIP（上一重构批次），本轮只改了上列文件，未触碰其余。

## 3. 计划外发现并修复的关键缺陷

1. **M1 隐藏缺陷**：`SemanticChunker` 会把 `## 第X条 正文…` 的标题行整体丢弃（正文只进标题栈）——cleaned_v1 与电子书大量此格式，等于"无标题分行时本就会整段丢条文正文"。已由 `_split_heading_inline_articles` 修复并有测试锁定。
2. **M2 内部契约变更**：`initialize_collection`/`add_documents` 返回值从 `None` 改 `int`（实际写入数），已同步 `milvus_sink.py` 与测试。

## 4. 整体验证记录（详见细则附录 A-I）

- **V-1** 全量测试 284 passed；**V-2** 契约逐项核对通过；**V-5** 检索/引用回归全绿。
- **V-3 真实 Milvus 端到端**（2026-08-30 完成）：本机 19531 端口实为 Milvus 2.6.23（此前误判"无服务"，因默认端口 19530 未开）。用实验集合 `public_kb_hybrid_poc_e2e` 验证：批内去重/幂等（同批二次 inserted=0）/新增追加/`enable_dedup=False` 开关回退/真实 `chunk_uid in [...]` 判重查询——全部与 mock 单测一致，实验集合已清理。
- **V-4 挂起**：MinerU 表格样张待 P-1 专项（`magic-pdf` 未装）。

## 5. 待办（后续专项，不阻塞已交付）

| 项 | 内容 | 前置 |
| --- | --- | --- |
| V-4 | 3 本 PDF 表格样张核验 | P-1 MinerU |
| P-1 | MinerU 接入（Docker Desktop/HTTP 服务封装/`MinerUApiParser`） | Docker + 镜像 |
| P-2 | 大批量 PDF 并发化 | 先清 reranker `last_status` 等共享可变状态 |
| P-3 | 全量向量化入库 + 生产集合重建（含去重、时效字段迁移） | P-1 + P-2 |

## 6. 文档产物（审查 → 计划 → 验证 → 里程碑 四层闭环）

| 层 | 文件 |
| --- | --- |
| 审查 | `docs/public_kb_architecture_review_20260830.md`、`docs/public_kb_data_pdf_dedup_review_20260830.md` |
| 总计划 | `docs/execution_plans/public_kb_optimization_execution_plan_20260830.md` |
| 细化执行 | `docs/execution_plans/public_kb_optimization_execution_detail_20260830.md`（任务卡 + 附录 A-I） |
| 基线 | `docs/public_kb_m0_baseline_20260830.md` |
| 里程碑 | 本文件 |

**交付状态**：所有模块改动未提交（遵循"不推送远端、每步可回退"约束），代码库处于可继续推进状态。
