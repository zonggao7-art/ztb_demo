# public_kb 目录收敛阶段 5 CLI 合并验证报告

> 记录时间：2026-08-29  
> 阶段 4 基线提交：`19385e1 docs: validate consolidated public_kb structure`  
> 验证环境：本地 Milvus POC `http://localhost:19531`

## 1. 修改范围

阶段 5 将历史批量 CSV 入口能力合并到统一实现：

1. `public_kb/ingestion/cli.py` 承载单文件入库与批量 CSV 入库能力。
2. 新增目录扫描、A/C 分组、批量统计、Markdown 预览校验和批量编排。
3. `public_kb/process_csv.py` 降级为纯兼容壳，只转发到新 CLI。
4. 批量模式中的 `initialize` 只作用于第一个成功入库的分组；后续分组自动切换为 `append`，避免重复创建集合。
5. 已注入 Milvus manager 时不再重复创建 embeddings。

对外保持的旧符号包括：

```python
main
scan_csv_files
process_group
run_batch_csv_ingestion
validate_markdown_output
DEFAULT_OUTPUT_DIR
```

## 2. 回归验证

目标回归命令：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test/test_ingestion_cli.py test/test_ingestion_pipeline.py test/test_public_kb_layout.py -q
```

结果：

```text
27 passed in 9.94s
```

全量离线回归命令：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test -q --ignore=test/test_cloud_sync.py
```

结果：

```text
243 passed in 28.09s
```

相对阶段 4 的 `233 passed` 基线，新增 10 个统一 CLI 回归用例。

## 3. Milvus POC 验证

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

`case 5` 中的 Reranker 连接失败日志是故障注入预期行为，最终回退到 RRF 原始排序。验证报告仍写入 `test_report/hybrid_poc_c3_results.json`。

## 4. CSV 小批量入库验证

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
| schema 字段 | `id`, `sparse_vector`, `text`, `vector` |
| BM25 Function | `text_bm25_emb` |
| 稠密索引 | `vector` |
| 稀疏索引 | `sparse_vector` |
| Milvus sink 耗时 | `4328.235ms` |
| 入库流程耗时 | `4.338s` |

## 5. CLI 帮助页冒烟

以下命令均正常退出：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m public_kb.ingestion.cli --help
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m public_kb.process_csv --help
```

| 入口 | 结果 |
| --- | --- |
| `public_kb.ingestion.cli` | PASS |
| `public_kb.process_csv` | PASS |

两个入口的参数集合保持一致：`--csv-path` / `--csv-dir` 互斥，`--group`、`--no-import`、`--validate-only`、`--output-dir`、`--mode` 均可正常解析。

## 6. 结论

阶段 5 完成。批量 CSV 能力已收敛到 `public_kb/ingestion/cli.py`，旧入口 `public_kb/process_csv.py` 保持兼容但不承载真实逻辑。离线回归、混合检索 POC、CSV 小批量入库和 CLI 冒烟全部通过。

下一阶段为阶段 6B：确认协程并发分支依赖后，评估删除或继续废弃提示化旧路径；本阶段不修改生产 `.env`，不写入生产 Milvus 集合。
