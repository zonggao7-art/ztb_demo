# public_kb 目录收敛阶段 6B 最终物理收敛报告

> 记录时间：2026-08-29  
> 前一状态：`2c50185 refactor(public_kb): deprecate legacy compatibility modules`  
> 验证环境：本地 Milvus POC `http://localhost:19531`

## 1. 本次修正

前一阶段只将旧入口标记为废弃，未完成用户要求的物理收敛。本次按最终目标修正：

1. 删除 10 个包根旧兼容壳。
2. 当前 `agent/`、`scripts/`、测试和文档索引不再引用旧路径。
3. `PublicKnowledgeRAG` 直接使用 `generation.chain.build_chain()`，并显式注入 `SiliconFlowReranker`。
4. `README.md` 与 `AGENTS.md` 更新为收敛后的目录边界。
5. 结构守卫测试强制要求旧兼容文件不存在，且代码中不再出现旧 `public_kb.*` 导入路径。

已删除文件：

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

## 2. 收敛后的包根结构

```text
public_kb/
├── __init__.py
├── __main__.py
├── config.py
├── contracts.py
├── chunk_ids.py
├── rag_engine.py
├── services/
├── ingestion/
├── retrieval/
└── generation/
```

包根只保留公共门面、共享配置/契约/身份工具；具体能力全部按链路分层。

## 3. 回归验证

目标回归：

```text
80 passed in 9.34s
```

全量离线回归：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test -q --ignore=test/test_cloud_sync.py
```

```text
238 passed in 26.27s
```

数量低于上一阶段的原因是旧路径别名转发测试已随兼容壳一并删除。

## 4. POC 验证

混合检索 POC：

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

CSV 小批量入库：

```text
CSV-INGESTION-CHECK: PASS
```

| 项 | 结果 |
| --- | --- |
| 实验集合 | `public_kb_hybrid_poc_ingest_v1` |
| chunk count | `6` |
| inserted count | `6` |
| Milvus row count | `6` |
| metadata 缺失 | 无 |
| schema 字段 | `id`, `sparse_vector`, `text`, `vector` |
| BM25 Function | `text_bm25_emb` |
| 稠密索引 | `vector` |
| 稀疏索引 | `sparse_vector` |

## 5. CLI 冒烟

| 入口 | 结果 |
| --- | --- |
| `python -m public_kb --help` | PASS |
| `python -m public_kb.ingestion.cli --help` | PASS |

## 6. 结论

`public_kb` 目录收敛已完成。旧路径兼容壳已物理删除，当前只保留一套分层实现和 `qa_chain.py` 一个在线链路稳定适配层。后续协程改造应直接使用新的 `services/`、`ingestion/`、`retrieval/`、`generation/` 边界。

## 7. 追加修正

按协作约定恢复 `public_kb/qa_chain.py` 作为在线问答链稳定入口。该文件只保留 `build_qa_chain()` 原签名和少量旧符号转发，真实链路实现仍位于 `generation/chain.py` 与 `retrieval/` 分层模块中。
