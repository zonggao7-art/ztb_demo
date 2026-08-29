# public_kb 目录收敛阶段 4 端到端验证报告

> 记录时间：2026-08-29
> 阶段 3 基线提交：`6e9a34e refactor(public_kb): consolidate citations and reranker`
> 验证环境：本地 Milvus POC `http://localhost:19531`，服务端版本 `2.6.23`

## 1. 验证范围

阶段 4 不修改业务代码，仅对阶段 0–3 的目录收敛结果做端到端验证。

验证覆盖：

1. 全量离线回归。
2. Milvus 服务端 BM25 Function 与 sparse index 探针。
3. 混合检索 POC 八用例。
4. CSV 小批量入库与 metadata 回查。
5. 公共问答、单文件 CSV 入库、批量 CSV 处理三个 CLI 入口冒烟。

## 2. 离线全量回归

命令：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test -q --ignore=test/test_cloud_sync.py
```

结果：

```text
233 passed in 24.54s
```

说明：

- `test_cloud_sync.py` 仍因历史缺失 `cloud_sync` 模块而排除。
- 阶段 0–3 新增的结构守卫测试全部通过。
- `PublicKnowledgeRAG.query()` 与 `HybridRetriever.retrieve()` 均保持同步接口。

## 3. Milvus 功能探针

命令：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" scripts/poc_probe_function.py
```

结果：

```text
PROBE: PASS
```

| 项 | 结果 |
| --- | --- |
| Milvus URI | `http://localhost:19531` |
| 服务端版本 | `2.6.23` |
| sparse 字段 | `sparse_vector` |
| BM25 Function | `text_bm25_emb` |
| 耗时 | `3.77s` |

## 4. 混合检索 POC

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

总耗时约 `21.43s`。case 5 中的 Reranker 连接失败日志是故障注入预期行为；最终保留 RRF 原始排序，未注入假分。

## 5. CSV 小批量入库

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
| Milvus sink 耗时 | `4066.868ms` |
| 入库流程耗时 | `4.086s` |
| 进程总耗时 | `12.85s` |

## 6. CLI 冒烟验证

以下命令均正常退出，未触发初始化、清空集合或生产数据写入：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m public_kb --help
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m public_kb.ingestion.cli --help
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m public_kb.process_csv --help
```

| 入口 | 结果 | 说明 |
| --- | --- | --- |
| `public_kb` | PASS | 公共知识库问答 / PDF 初始化入口 |
| `public_kb.ingestion.cli` | PASS | 单文件 CSV 入库入口 |
| `public_kb.process_csv` | PASS | 历史 CSV 批量处理入口，待阶段 5 合并 |

终端帮助文本出现中文乱码属于当前 PowerShell 编码显示问题；命令均正常返回，不影响功能验证。

## 7. 当前结构状态

阶段 0–3 后，核心实现已收敛到以下边界：

```text
public_kb/
├── services/          # embedding、LLM、Milvus、MinerU
├── ingestion/         # Source / Transform / Sink / Pipeline / CLI
├── retrieval/         # hybrid retriever、search、fallback、reranker
└── generation/        # prompt、context、chain、citations
```

仍保留的兼容入口：

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

这些路径计划在阶段 5 CLI 合并完成、协程并发分支确认无影响后，于阶段 6B 删除或降级为废弃提示。

## 8. 阶段 4 结论

阶段 4 端到端验证通过。目录收敛后，离线入库、在线混合检索、引用校验、Reranker 故障降级、CSV metadata 溯源和 CLI 入口均保持可用。

下一阶段为阶段 5：将 `public_kb/process_csv.py` 的批量 CSV 能力合并到 `public_kb/ingestion/cli.py`，保持旧入口兼容，并用 mock 测试验证参数与统计逻辑。
