# Pipeline 重构最终验证报告

> 记录时间: 2026-08-29
> 验证环境: 本地 Milvus POC `http://localhost:19531`，服务端版本 `2.6.23`
> 生产配置: 未修改 `.env`，未触碰生产 Milvus 集合

## 结论

`public_kb` 在线检索、生成层、离线入库的 Pipeline 重构已完成固定回归验证和小批量入库验证。现有代码结构可以支撑 Milvus 2.5+/2.6 升级后的数据向量化入库与混合检索复核；在完成全量数据入库和生产级 citation eval 之前，不应宣称生产检索质量达标。

## 回归锚点

| 检查项 | 结果 | 耗时 |
| --- | --- | ---: |
| `pytest test -q --ignore=test/test_cloud_sync.py` | `221 passed` | `23.43s`（进程总耗时 `26.19s`） |
| `scripts/poc_verify_hybrid.py` | `8/8 PASS` | `42.79s` |
| `scripts/poc_probe_function.py` | `PASS` | `4.12s` |

POC 覆盖 dense-only、BM25-only、hybrid RRF、full-chain reranker、reranker 故障降级、无关问题拒答、citation R1-R7、strict mode e2e。

## CLI 入口冒烟

| 入口 | 结果 |
| --- | --- |
| `python -m public_kb --help` | PASS |
| `python -m public_kb.ingestion.cli --help` | PASS |

本次仅验证入口加载和参数解析，未执行会修改生产集合的交互式问答或初始化操作。

## CSV 小批量入库验证

验证脚本：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" scripts/poc_validate_csv_ingestion.py
```

结果：

| 项 | 结果 |
| --- | --- |
| 实验集合 | `public_kb_hybrid_poc_ingest_v1` |
| 源数据 | 3 行临时 CSV |
| 生成 chunks | `6` |
| inserted count | `6` |
| Milvus row count | `6` |
| metadata 回查数量 | `6` |
| metadata 缺失 | 无 |
| 入库流程耗时 | `4.842s` |
| 进程总耗时 | `12.88s` |

阶段耗时：

| 阶段 | 输出数量 | 耗时 |
| --- | ---: | ---: |
| `source` | `6` | `16.974ms` |
| `validate` | `6` | `0.007ms` |
| `sink:MilvusSink` | `6` | `4757.030ms` |

回查 metadata 字段均存在且非空：

- `title`
- `publish_date`
- `source_url`
- `source_file`
- `doc_name`
- `chapter`
- `chunk_index`
- `chunk_uid`

## Milvus schema 与索引

实验集合确认包含：

| 项 | 结果 |
| --- | --- |
| 字段 | `id`、`text`、`vector`、`sparse_vector` |
| BM25 Function | `text_bm25_emb` |
| 索引 | `vector`、`sparse_vector` |
| 写入确认 | `inserted_count == row_count == queried_count == 6` |

该结果确认离线入库阶段已经建立 dense 与 sparse 双索引，在线检索阶段可以在 Milvus 2.5+/2.6 上继续走 dense + BM25 → RRF → reranker / RRF fallback 链路。

## 验证产物

以下产物位于本地 `test_report/`，当前被 `.gitignore` 排除：

- `test_report/hybrid_poc_c3_results.json`
- `test_report/csv_ingestion_validation_results.json`

可复跑脚本：

- `scripts/poc_verify_hybrid.py`
- `scripts/poc_probe_function.py`
- `scripts/poc_validate_csv_ingestion.py`

## 已知限制与未执行项

1. `test/test_cloud_sync.py` 依赖仓库中不存在的 `cloud_sync` 模块，因此全量测试命令需要继续使用 `--ignore=test/test_cloud_sync.py`。
2. `testset_knowledge.jsonl` 缺失，未能执行完整 citation eval，不能宣称 Hit@5、MRR@10 和引用完整率达标。
3. 本次未执行全量向量化入库。
4. 本次未修改生产 `.env`，未连接生产 Milvus 集合。
5. `DATA/raw_data/*.md` 是中间产物或调试数据，不是正式入库源；正式路径是 PDF / CSV 直通 Source。
6. Markdown Sink 仅作为预览和调试输出，不是正式入库通道。

## 升级后的下一步

1. 部署目标 Milvus 版本后，确认服务端支持 dense index、sparse index 与 BM25 Function。
2. 使用新的 `.env` 或显式 `Settings` 指向目标集合。
3. 先执行小批量 PDF / CSV 入库，运行 `scripts/poc_validate_csv_ingestion.py` 或等价校验。
4. 通过后执行全量向量化入库。
5. 补齐或恢复 `testset_knowledge.jsonl`，执行 `scripts/run_knowledge_citation_eval.py`。
6. 根据 Hit@5、MRR@10、引用完整率、拒答准确率和耗时结果进一步调优检索参数。
