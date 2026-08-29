# public_kb 目录收敛阶段 2 基线报告

> 记录时间：2026-08-29
> 阶段 1 基线提交：`815f6bf refactor(public_kb): consolidate shared services package`
> 验证环境：本地 Milvus POC `http://localhost:19531`，服务端版本 `2.6.23`

## 1. 本阶段迁移范围

本阶段只迁移离线入库的文本处理与 CSV 解析实现，不修改清洗规则、分块规则、CSV metadata 规则或 Milvus schema。

| 原路径 | 新路径 | 兼容路径 |
| --- | --- | --- |
| `public_kb/chunker.py` | `public_kb/ingestion/transforms/chunker.py` | 保留 |
| `public_kb/text_cleaner.py` | `public_kb/ingestion/transforms/cleaner.py` | 保留 |
| `public_kb/csv_loader.py` | `public_kb/ingestion/sources/csv_loader.py` | 保留 |

旧路径 `public_kb/chunker.py`、`public_kb/text_cleaner.py`、`public_kb/csv_loader.py` 均改为 4 行兼容转发壳，分别 re-export 新实现中的 `SemanticChunker`、`TextCleaner`、`CsvLoader`、`save_chunks_to_markdown`。

同时删除：

```text
public_kb/ingestion/transforms/chunk_ids.py
```

原因：`chunk_ids` 是 ingestion、retrieval、generation/citations 共用的身份契约，继续保留在 `public_kb/chunk_ids.py`，不再通过 ingestion transforms 间接转发。

## 2. 内部引用收敛

已从旧路径或转发壳切换到新路径：

| 调用方 | 更新结果 |
| --- | --- |
| `public_kb/ingestion/sources/csv_loader.py` | 引用 `ingestion/transforms/chunker.py` 和 `ingestion/transforms/cleaner.py` |
| `public_kb/ingestion/sources/csv_source.py` | 引用同包内 `sources/csv_loader.py` |
| `public_kb/ingestion/sinks/markdown_sink.py` | 引用 `sources/csv_loader.save_chunks_to_markdown` |
| `public_kb/ingestion/cli.py` | 引用 `sources/csv_loader.CsvLoader` |

`public_kb/rag_engine.py` 继续通过 `ingestion.transforms` 聚合入口引用，不直接依赖旧包根实现。
`scripts/` 和 `public_kb/process_csv.py` 暂时继续使用旧兼容路径，符合阶段 6A 策略；阶段 6B 删除兼容层前统一清理。

## 3. 守卫测试增强

新增阶段 2 守卫测试：

1. 确认旧 `chunker`、`text_cleaner`、`csv_loader` 路径与新实现导出同一对象。
2. 确认 `ingestion/`、`generation/`、`retrieval/`、`services/` 和 `rag_engine.py` 不回引包根实现。
3. 确认 `ingestion/transforms/chunk_ids.py` 已删除。

阶段 2 目标回归：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test/test_public_kb_layout.py test/test_ingestion_pipeline.py test/test_citation_tracing.py -q
```

结果：

```text
48 passed in 5.90s
```

## 4. 全量离线回归

命令：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test -q --ignore=test/test_cloud_sync.py
```

结果：

```text
230 passed in 24.67s
```

对比阶段 1：

| 阶段 | 测试数量 |
| --- | ---: |
| 阶段 1 | `229 passed` |
| 阶段 2 | `230 passed` |

新增 1 个阶段 2 结构守卫测试全部通过。

## 5. 混合检索 POC

命令：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" scripts/poc_verify_hybrid.py
```

结果：

```text
OVERALL: PASS
```

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

运行耗时约 `22.16s`。case 5 的 Reranker 连接失败日志是故障注入预期行为，最终保持 RRF 原始排序。

## 6. CSV 小批量入库

命令：

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
| 稠密索引 | `vector` |
| 稀疏索引 | `sparse_vector` |
| BM25 Function | `text_bm25_emb` |
| Milvus sink 耗时 | `4064.420ms` |
| 进程总耗时 | `13.05s` |

## 7. 阶段 2 结论

阶段 2 通过。离线入库的文本清洗、语义分块和 CSV 解析真实实现已收敛到 `ingestion/`，旧路径仅保留轻量兼容壳。清洗规则、分块行为、CSV 行级 metadata、Milvus schema、检索降级语义和引用规则均未变化。

下一阶段为阶段 3：将 `citations.py` 移动到 `generation/citations.py`，并将 `retrieval/reranker/` 合并为 `retrieval/reranker.py`，完成后暂停等待确认。
