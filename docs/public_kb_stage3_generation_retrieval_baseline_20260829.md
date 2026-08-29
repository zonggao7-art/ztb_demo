# public_kb 目录收敛阶段 3 基线报告

> 记录时间：2026-08-29
> 阶段 2 基线提交：`39fa440 refactor(ingestion): consolidate transforms and csv loader`
> 验证环境：本地 Milvus POC `http://localhost:19531`，服务端版本 `2.6.23`

## 1. 本阶段迁移范围

本阶段只迁移引用溯源模块和收敛 Reranker 模块，不修改引用规则、Reranker 重试策略、检索排序、降级语义或对外返回结构。

| 原路径 | 新路径 | 兼容策略 |
| --- | --- | --- |
| `public_kb/citations.py` | `public_kb/generation/citations.py` | 旧路径保留 re-export |
| `public_kb/retrieval/reranker/protocol.py` | `public_kb/retrieval/reranker.py` | 内部路径直接更新 |
| `public_kb/retrieval/reranker/siliconflow.py` | `public_kb/retrieval/reranker.py` | 内部路径直接更新 |

旧 `public_kb.citations` 路径继续导出：

- `Citation`
- `CitationValidator`
- `build_citations`
- `format_citations`
- `parse_citation_markers`

`retrieval/reranker/` 目录已删除，协议和 SiliconFlow 客户端合并为单文件 `public_kb/retrieval/reranker.py`。该目录只是内部实现边界，未承诺稳定对外 API。

## 2. 内部引用收敛

已更新以下内部引用：

| 调用方 | 更新结果 |
| --- | --- |
| `public_kb/generation/chain.py` | 引用 `generation/citations.py` 和 `retrieval/reranker.py` |
| `public_kb/retrieval/retriever.py` | 引用 `retrieval/reranker.py` |
| `public_kb/qa_chain.py` | 兼容门面引用 `retrieval/reranker.py` |
| `public_kb/__main__.py` | 引用 `generation/citations.format_citations` |

`test/test_citation_tracing.py` 仍通过旧 `public_kb.citations` 路径导入，用于验证兼容 re-export 生效。

## 3. 结构守卫测试

`test/test_public_kb_layout.py` 新增阶段 3 守卫：

1. `generation/citations.py` 与旧 `public_kb.citations` 导出同一 `CitationValidator`。
2. `retrieval/reranker.py` 同时提供 `Reranker` 协议和 `SiliconFlowReranker` 实现。
3. 旧的 `retrieval/reranker/protocol.py` 和 `retrieval/reranker/siliconflow.py` 不存在。
4. 内部主链路不再引用旧的 citations 包根路径和 reranker 子模块路径。

阶段 3 目标回归：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test/test_public_kb_layout.py test/test_citation_tracing.py test/test_qa_chain_offline.py test/test_kb_contracts.py -q
```

结果：

```text
70 passed in 6.39s
```

## 4. 全量离线回归

命令：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test -q --ignore=test/test_cloud_sync.py
```

结果：

```text
233 passed in 25.48s
```

对比阶段 2：

| 阶段 | 测试数量 |
| --- | ---: |
| 阶段 2 | `230 passed` |
| 阶段 3 | `233 passed` |

新增 3 个阶段 3 结构守卫测试全部通过。

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

运行耗时约 `21.09s`。case 5 的 Reranker 连接失败日志是故障注入预期行为，最终保持 RRF 原始排序。

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
| Milvus sink 耗时 | `4271.667ms` |
| 进程总耗时 | `13.02s` |

## 7. 阶段 3 结论

阶段 3 通过。引用溯源能力已归入 `generation/`，Reranker 协议与实现已收敛为 `retrieval/reranker.py`。行为、外部问答结构、R1-R7 校验、检索排序、Reranker 失败降级和 Milvus schema 均未变化。

当前 `public_kb` 为 50 个 Python 文件、约 3729 行。下一步是阶段 4 端到端结构验证；随后再评估阶段 5 的 CSV CLI 合并。
