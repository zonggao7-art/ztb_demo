# public_kb 目录收敛阶段 0 基线报告

> 记录时间：2026-08-29
> 计划基线提交：`2aa62e2 docs: plan public_kb directory consolidation`
> 验证环境：本地 Milvus POC `http://localhost:19531`，服务端版本 `2.6.23`

## 1. 结构冻结范围

阶段 0 未移动业务代码，只冻结当前结构并增加守卫测试。

| 项 | 状态 |
| --- | --- |
| `public_kb` Python 文件数 | `46` |
| `public_kb` Python 行数 | `3686` |
| 对外问答入口 | `PublicKnowledgeRAG.query()` 保持同步 |
| 检索入口 | `HybridRetriever.retrieve()` 保持同步 |
| Agent 隔离 | `agent.nodes.knowledge_qa` 不直接 import `public_kb` 内部实现 |
| 兼容门面 | `public_kb.qa_chain` 保留 `_SiliconFlowReranker`、`_dense_only_retrieve`、`build_qa_chain` |

新增测试：`test/test_public_kb_layout.py`，覆盖：

1. 稳定公共入口可导入。
2. `qa_chain` 兼容门面继续转发到拆分后的检索管线。
3. ingestion 管线边界与当前旧别名保持一致。
4. `chunk_ids`、`config`、`contracts` 等共享契约仍在包根可用。
5. Agent 知识问答节点只依赖 `public_kb` 稳定门面，不穿透内部模块。

## 2. 离线测试基线

命令：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test -q --ignore=test/test_cloud_sync.py
```

结果：

```text
226 passed in 23.57s
```

说明：

- 阶段 5 最终基线为 `221 passed`。
- 阶段 0 新增 5 个结构守卫测试后，基线变为 `226 passed`。
- `test_cloud_sync.py` 仍因历史缺失 `cloud_sync` 模块而排除。

## 3. 混合检索 POC 基线

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

运行耗时约 `21.57s`。过程中出现的 Reranker 连接失败日志来自 case 5 的故障降级注入，最终保留 RRF 原始排序，属于预期路径。

## 4. CSV 小批量入库基线

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
| 流程耗时 | `4.282s` |

阶段 0 修复了验证脚本的重复执行问题：

1. 默认仍禁止覆盖已存在集合。
2. 新增 `--refresh` 显式刷新实验集合。
3. 刷新前校验集合必须以 `public_kb_hybrid_poc_` 前缀开头，避免误删非实验集合。

## 5. 阶段 0 结论

阶段 0 通过。当前行为、外部接口、Milvus schema、检索降级语义和引用校验均已冻结。

下一阶段为阶段 1：将 `embedding_service.py`、`llm_factory.py`、`milvus_store.py`、`mineru_parser.py` 收敛到 `public_kb/services/`，旧路径暂保留 re-export，完成后暂停等待确认。
