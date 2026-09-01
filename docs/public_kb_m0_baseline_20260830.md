# public_kb 优化 — M0 基线冻结记录（2026-08-30）

> 归属：`docs/execution_plans/public_kb_optimization_execution_plan_20260830.md` 之 M0
> 执行人/日期：2026-08-30（本会话）
> 目的：冻结代码基线与对外契约，作为 M1–M6 各模块独立验证的回归基准。

---

## 1. 基线命令与结果

```powershell
cd D:\agent_project\ztb_demo
.\\.venv\\Scripts\\python.exe -m pytest test -q --ignore=test/test_cloud_sync.py
```

结果：

```text
239 passed in 28.22s
```

- 排除 `test/test_cloud_sync.py`：依赖仓库中不存在的 `cloud_sync` 模块（历史问题，与本次优化无关，见 `docs/pipeline_refactor_baseline_20260829.md`）。
- 环境：`D:\agent_project\ztb_demo\.venv`（uv 管理，Python 3.11.15，89 个包与源环境逐版本一致）。

## 2. 两环境代码一致性核对结论

- `D:\agent_project\zhaotoubiao_demo 1` 是**只有工作区文件、git 尚无任何提交的历史快照**（旧扁平结构：`chunker.py`/`csv_loader.py`/`embedding_service.py` 等，无 `ingestion/`、`retrieval/`、`generation/`）。
- `D:\agent_project\ztb_demo` 是**唯一优化目标**：已含完整 pipeline 重构提交链，功能已全部迁移自旧快照。
- 结论：**冻结的是 `ztb_demo` 这份代码 + 239 测试基线**；旧快照仅作参考，绝不覆盖回 `ztb_demo`。

## 3. 冻结的对外契约清单（M0-3）

见总计划 §0.2 与细化方案 §0.2，核心 9 项：

| 契约 | 冻结内容 |
| --- | --- |
| `PublicKnowledgeRAG` | `init_knowledge_base/query/add_pdf/clear_kb` 签名与返回结构不变 |
| `build_qa_chain()` | `qa_chain.py` 稳定入口 5 参数签名不变 |
| `Settings` | 只允许向后兼容新增字段，默认值保持现行为 |
| `Document.metadata` | `doc_name/chapter/chunk_index/chunk_uid` 必填；其余透传不改名 |
| `IngestionResult` | 新增字段必须给默认值；`inserted_count` 语义保持"实际写入数" |
| `RetrievalDiagnostics` | `retrieval_mode/reranker_status/fallback_reason` 兼容 |
| Milvus collection schema | 字段名/维度/analyzer/BM25 Function 不变；动态字段只增不改 |
| `AgentState` / agent 节点返回 | 不受影响 |
| `test_public_kb_layout.py` AST 守卫 | 禁止 legacy 导入路径、稳定入口签名回归 |

## 4. 后续回归基准（随模块递增）

| 阶段 | 全量测试 | 对应模块完成 |
| --- | --- | --- |
| M0 基线 | 239 | — |
| M1 | 253 | PDF 解析适配 |
| M2 | 262 | 去重+幂等 |
| M3 | 273 | 法条时效性 |
| M4 | 281 | 清洗保护 |
| M5 | 284 | 拆分+契约复用 |
| M6 | 284 | 工程化治理 |

*记录补充说明：本文件于 2026-08-30 阶段总结时补落盘（M0 当时未实际写入，特此补齐以保证基线可回溯）。*
