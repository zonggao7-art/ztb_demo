# 异步 + 记忆 + 流式改造 — 改造前基线

> 生成日期：2026-08-25
> 对应分支：`feat/async-memory-streaming`
> 对应手册：`docs/implementation_handbook_async_memory_streaming.md` §阶段 0

本目录归档 **改造实施前** 的正确性与性能基线，作为阶段 1~6 改造完成后的对照基准。
所有数字必须**禁止退化**（性能不退化，正确性指标不得下降）。

---

## 1. 文件清单

| 文件 | 来源 | 说明 |
|---|---|---|
| `baseline_async_pre.json` | `scripts/benchmark_async.py --force-historical` | 性能基线：LLM/Embedding/Milvus/MySQL 各阶段 P50/P95 |
| `pytest_summary.txt` | `python -m pytest test/` | 单元测试快照（184 通过 / 4 预存失败 / 1 跳过） |
| `knowledge_citation_summary.md` | `test_report/knowledge_citation_report.md` | 引用溯源评估快照（106 题，101/101 通过） |
| `price_inquiry_summary.json` | `test_report/metrics.json` 关键字段 | 询价评测快照（1500 题，accuracy 99.533%，field_recall 99.464%） |

> 历史明细文件保留在仓库根的 `test_report/`：
> `knowledge_citation_results.jsonl`、`raw_results.jsonl`、`case_details.csv`、`evaluation_report.md` 等。

---

## 2. 性能基线（`baseline_async_pre.json`）

> 数据源：阶段 0 当天 MySQL/Milvus/DeepSeek **均不可达**（本地无 Docker，远端 192.168.10.120 离线），
> 因此走 `--force-historical` 回退路径，汇总 `test_report/` 中的历史评测时延。

| 组件 | 样本数 | P50 (ms) | P95 (ms) | Mean (ms) | 数据源 |
|---|---|---|---|---|---|
| **rag_e2e**（LLM+Embedding+Milvus+Reranker 端到端） | 106 | **2351.5** | **3889.5** | 2523.0 | `knowledge_citation_results.jsonl` |
| **mysql_sql**（询价 SQL 平均耗时） | 1495 | 27.4 | 482.0* | 27.4 | `metrics.json` |
| **price_node_total**（price_inquiry 节点总耗时） | 1495 | 1760.8 | 3253.0* | 1760.8 | `metrics.json` |
| **price_e2e**（询价端到端总耗时） | 1500 | 2694.0 | **3344.0** | 2760.0 | `metrics.json` |

\* `mysql_sql` 与 `price_node_total` 的 P95 是用 `max` 退化的近似值（历史数据未保存分位数）；
**`rag_e2e` 与 `price_e2e` 的 P95 是真实分位数**，作为后续阶段的主对照指标。

### 2.1 阶段 1+ 在线补正项

阶段 1 完成后，必须在 Milvus/MySQL/DeepSeek 在线时再跑一次 `scripts/benchmark_async.py`（默认即可），
产出 `baseline_async_post_stage1.json`，比较以下指标：

- rag_e2e P95: 改造前 3889.5 ms → 改造后应**下降或持平**
- price_e2e P95: 改造前 3344.0 ms → 改造后应**下降或持平**
- mysql_sql P95: 改造前 482 ms → 改造后应**显著下降**（异步连接池 + 超时控制的目标）

---

## 3. 正确性基线

### 3.1 单元测试

```
$ python -m pytest test/ --tb=line -q --ignore=test/legacy
...
4 failed, 184 passed, 1 skipped in 31.90s
```

**4 个失败为预存问题，与阶段 0 无关，禁止在阶段 1~6 中被"修复"掉再冒充通过**：

| 测试 | 期望 | 实际 | 备注 |
|---|---|---|---|
| `test_sub_route.py::test_company_query_supplier_recommend` | `company_query` | `all` | sub-route 分类边界 |
| `test_sub_route.py::test_company_query_penalty_check` | `company_query` | `all` | 同上 |
| `test_sub_route.py::test_bidding_query_purchaser` | `bidding_query` | `all` | 同上 |
| `test_sub_route.py::test_bidding_query_bidding_aggregation` | `bidding_query` | `all` | 同上 |

### 3.2 引用溯源评估（106 题）

| 指标 | 值 |
|---|---|
| 全部规则通过（all_passed） | **101 / 101** |
| R1~R6 通过率 | **100.0%** |
| 引用总条数 | 505 |
| 关联校验失败 | **0** |
| 拒答正确率（负样本 10 题） | 5 正确 + 5 路由兜底（无漏拒答） |

详见 `test_report/knowledge_citation_report.md`。

### 3.3 询价评测（1500 题）

| 指标 | 值 |
|---|---|
| 总样本数 | 1500 |
| 字段召回率（field_recall） | **99.464%** |
| 答案正确率（answer_accuracy） | **99.533%** |
| 端到端 P95 | **3.344 s** |

---

## 4. 阶段 0 偏差登记

| 项 | 手册要求 | 实际做法 | 原因 |
|---|---|---|---|
| `pip-compile` | `pip-compile requirements.in -o requirements.txt` | **手工同步** | 当前环境无法安装 pip-tools（pip 24.2 + 离线受限） |
| 在线基准 | LLM/Embedding/Milvus/MySQL 各采 N 次 | 走历史快照 | MySQL 192.168.10.120:3306 / Milvus 127.0.0.1:19530 均不可达 |
| 知识库引用评估重跑 | `python scripts/run_knowledge_citation_eval.py` | **未重跑**，沿用 `test_report/` 现有快照 | 同上，依赖 Milvus |

以上偏差**不影响阶段 0 验收**：基线报告存在、P50/P95 已记录、可重复执行（脚本已落地）。

---

## 5. 复现方法

```bash
# 1. 切换到基线分支
git checkout feat/async-memory-streaming

# 2. 在线时（Milvus/MySQL/DeepSeek 均可达）：
python scripts/benchmark_async.py --n 30
#    → 覆盖 test_report/baseline_async_pre.json

# 3. 离线时（任何依赖不可达）：
python scripts/benchmark_async.py --force-historical

# 4. 正确性快照：
python -m pytest test/ --tb=line -q --ignore=test/legacy > docs/baseline/pytest_summary.txt 2>&1

# 5. 引用溯源评估（需 Milvus+LLM 在线）：
python scripts/run_knowledge_citation_eval.py
#    → 覆盖 test_report/knowledge_citation_*
```

---

## 6. 阶段 1+ 必须维持的底线

- ✅ 单元测试 `184 passed / 1 skipped` 不得减少；4 个预存 sub_route 失败**可修复但需独立 PR**
- ✅ 引用溯源 `101/101 all_passed, 0 association failures` 不得退化
- ✅ 询价 `accuracy ≥ 99.533%, field_recall ≥ 99.464%` 不得退化
- ⚠️ 性能基线：阶段 1 的目标是把 `rag_e2e P95` 与 `price_e2e P95` **持平或更优**
