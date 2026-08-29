# public_kb 目录收敛阶段 1 基线报告

> 记录时间：2026-08-29
> 阶段 0 基线提交：`0373819 test(public_kb): freeze consolidation baseline`
> 验证环境：本地 Milvus POC `http://localhost:19531`，服务端版本 `2.6.23`

## 1. 本阶段迁移范围

本阶段只迁移共享基础服务，不修改业务逻辑、Milvus schema、embedding 行为、LLM 构造行为或对外返回契约。

| 原路径 | 新路径 | 兼容路径 |
| --- | --- | --- |
| `public_kb/embedding_service.py` | `public_kb/services/embeddings.py` | 保留 |
| `public_kb/llm_factory.py` | `public_kb/services/llm.py` | 保留 |
| `public_kb/milvus_store.py` | `public_kb/services/milvus_store.py` | 保留 |
| `public_kb/mineru_parser.py` | `public_kb/services/mineru_parser.py` | 保留 |

新增：

```text
public_kb/services/__init__.py
```

旧路径已改为显式 re-export，继续保持以下符号可用：

- `public_kb.embedding_service._SafeEmbeddings`
- `public_kb.embedding_service.create_embeddings`
- `public_kb.llm_factory.create_llm`
- `public_kb.milvus_store.MilvusStoreManager`
- `public_kb.mineru_parser.MinerUParser`

注意：`MinerUParser` 为正确类型名；旧路径导出的就是 `services.mineru_parser.MinerUParser` 的同一对象。

## 2. 内部引用收敛

已从旧路径切换到新路径：

| 调用方 | 更新结果 |
| --- | --- |
| `public_kb/rag_engine.py` | 引用 `services.embeddings`、`services.llm`、`services.milvus_store`、`services.mineru_parser` |
| `public_kb/ingestion/cli.py` | 引用 `services.embeddings`、`services.milvus_store` |
| `public_kb/ingestion/sources/pdf_source.py` | 引用 `services.mineru_parser` |

`scripts/` 中的历史 POC 脚本暂时继续使用旧兼容路径，符合阶段 6A 策略；阶段 6B 删除兼容层前会统一清理。

## 3. 守卫测试增强

`test/test_public_kb_layout.py` 从 5 个测试扩展到 8 个测试，新增覆盖：

1. 四个共享服务可从 `public_kb/services/` 导入。
2. 旧路径 re-export 与新路径导出的是同一对象。
3. `rag_engine`、`generation`、`ingestion`、`retrieval`、`services` 内部不回引旧服务路径。

## 4. 离线回归

目标回归：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test/test_public_kb_layout.py test/test_milvus_store_offline.py test/test_public_kb_offline_gate.py test/test_ingestion_pipeline.py -q
```

结果：

```text
28 passed in 5.88s
```

全量离线回归：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test -q --ignore=test/test_cloud_sync.py
```

结果：

```text
229 passed in 23.32s
```

对比阶段 0：

| 阶段 | 测试数量 |
| --- | ---: |
| 阶段 0 | `226 passed` |
| 阶段 1 | `229 passed` |

新增 3 个服务收敛守卫测试全部通过。

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

运行耗时约 `20.21s`。case 5 的 Reranker 连接失败日志是故障注入预期行为，最终保持 RRF 原始排序。

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
| 入库流程耗时 | `4.166s` |

## 7. 阶段 1 结论

阶段 1 通过。共享基础服务已收敛到 `public_kb/services/`，旧路径兼容别名保持可用，内部主链路已切换到新路径。行为、外部接口、Milvus schema、检索降级语义、引用规则均未变化。

下一阶段为阶段 2：将 `chunker.py`、`text_cleaner.py`、`csv_loader.py` 的真实实现移动到 `ingestion/transforms/` 与 `ingestion/sources/`，旧路径暂保留 re-export，完成后暂停等待确认。
