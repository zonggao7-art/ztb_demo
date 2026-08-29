# public_kb 目录收敛阶段 6B 安全废弃验证报告

> 记录时间：2026-08-29  
> 阶段 5 基线提交：`2386be1 refactor(ingestion): unify csv cli entrypoints`  
> 验证环境：本地 Milvus POC `http://localhost:19531`

## 1. 执行决策

删除兼容层前的前置审计发现，`smart-tadpole` 协程工作树仍引用以下旧路径：

```text
public_kb.citations
public_kb.llm_factory
public_kb.embedding_service
public_kb.process_csv
```

因此阶段 6B 不执行物理删除，改为保守的安全废弃：

1. 当前 `agent/` 主链路全部切换到 `services/`、`generation/` 新路径。
2. 当前 POC 脚本全部切换到新路径。
3. `public_kb/qa_chain.py` 的旧入口用途改由 `generation.chain.build_chain` 直接承担，POC 显式注入 `SiliconFlowReranker`。
4. 10 个旧路径模块保留为兼容壳，导入时统一发出 `DeprecationWarning`。
5. 新增结构守卫测试，防止 `agent/` 和 POC 入口再次引入旧路径。

保留的兼容壳包括：

```text
public_kb/embedding_service.py
public_kb/llm_factory.py
public_kb/milvus_store.py
public_kb/mineru_parser.py
public_kb/chunker.py
public_kb/text_cleaner.py
public_kb/csv_loader.py
public_kb/citations.py
public_kb/qa_chain.py
public_kb/process_csv.py
```

## 2. 引用收敛

已切换的生产与 POC 引用：

| 原旧路径 | 新路径 |
| --- | --- |
| `public_kb.llm_factory` | `public_kb.services.llm` |
| `public_kb.citations` | `public_kb.generation.citations` |
| `public_kb.embedding_service` | `public_kb.services.embeddings` |
| `public_kb.milvus_store` | `public_kb.services.milvus_store` |
| `public_kb.chunker` | `public_kb.ingestion.transforms.chunker` |
| `public_kb.qa_chain.build_qa_chain` | `public_kb.generation.chain.build_chain` + 显式 `reranker_class` |

当前旧路径命中仅允许出现在三类位置：

1. 明确发出 `DeprecationWarning` 的兼容壳自身；
2. 暂时保留的兼容行为回归测试；
3. 历史 `docs/`、`archive/`、`test_report/`。

## 3. 回归验证

目标回归命令：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test/test_public_kb_layout.py test/test_citation_tracing.py test/test_qa_chain_offline.py test/test_ingestion_pipeline.py test/test_ingestion_cli.py test/test_milvus_store_offline.py test/test_public_kb_offline_gate.py -q
```

结果：

```text
87 passed, 10 warnings in 10.00s
```

10 个警告均为新增 `DeprecationWarning` 的预期输出。

全量离线回归命令：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test -q --ignore=test/test_cloud_sync.py
```

结果：

```text
245 passed, 10 warnings in 27.54s
```

相对阶段 5 的 `243 passed` 基线，新增 2 个结构守卫用例。

## 4. Milvus POC 验证

混合检索 POC 命令：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" scripts/poc_verify_hybrid.py
```

结果：

```text
OVERALL: PASS
```

八个用例全部通过：

| 用例 | 结果 |
| --- | --- |
| dense-only | PASS |
| bm25-only | PASS |
| hybrid-rrf(raw) | PASS |
| full-chain(reranker real) | PASS |
| reranker-failure fallback | PASS |
| irrelevant -> refusal | PASS |
| citation R1-R7 | PASS |
| strict-mode e2e | PASS |

`case 5` 中的 Reranker 连接失败日志是故障注入预期行为，最终回退到 RRF 原始排序。

## 5. CSV 小批量入库验证

先按原计划执行不带 `--refresh` 的校验时，脚本因 POC 集合已存在且默认 `initialize` 而拒绝覆盖：

```text
ConfigurationContractError: 实验 public_kb_hybrid_poc_ingest_v1 已存在，默认禁止覆盖
```

这是验证脚本重复执行的既有幂等限制，不是本次引用迁移造成的回归。为避免绕过契约保护，本次将执行计划修正为显式刷新实验集合：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" scripts/poc_validate_csv_ingestion.py --refresh
```

结果：

```text
CSV-INGESTION-CHECK: PASS
```

| 项 | 结果 |
| --- | --- |
| 实验集合 | `public_kb_hybrid_poc_ingest_v1` |
| chunk count | `6` |
| inserted count | `6` |
| Milvus row count | `6` |
| metadata 回查数量 | `6` |
| metadata 缺失 | 无 |
| schema 字段 | `id`, `sparse_vector`, `text`, `vector` |
| BM25 Function | `text_bm25_emb` |
| 稠密索引 | `vector` |
| 稀疏索引 | `sparse_vector` |
| Milvus sink 耗时 | `4324.476ms` |
| 入库流程耗时 | `4.334s` |

## 6. CLI 冒烟

以下命令均正常退出：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m public_kb.ingestion.cli --help
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m public_kb.process_csv --help
```

| 入口 | 结果 |
| --- | --- |
| `public_kb.ingestion.cli` | PASS |
| `public_kb.process_csv` | PASS |

## 7. 结论与删除条件

阶段 6B 的安全废弃目标完成：当前主链路和 POC 已不依赖旧路径，旧路径保留给 `smart-tadpole` 协程工作树过渡使用，并已具备显式废弃提示。

后续物理删除兼容层必须同时满足：

1. 协程并发改造分支合并完成，或其负责人明确确认不再依赖旧路径；
2. 剩余兼容测试迁移到新路径，或明确标记为废弃契约测试后删除；
3. 全仓库旧路径搜索只剩历史文档、归档和已删除文件；
4. 全量离线回归、混合检索 POC、CSV 小批量入库再次通过。
