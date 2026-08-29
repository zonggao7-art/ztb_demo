# Pipeline 重构基线报告

> 基线提交: `f058408 docs: establish pipeline refactor baseline`  
> 记录时间: 2026-08-29  
> Python: `D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe`  
> Milvus POC: `http://localhost:19531`，服务端版本 `2.6.23`

---

## 1. 离线测试基线

### 1.1 全量测试收集结果

命令：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test -q
```

结果：

```text
ImportError: No module named 'cloud_sync'
ERROR collecting test/test_cloud_sync.py
1 error in 30.12s
```

结论：

- `test/test_cloud_sync.py` 依赖的 `cloud_sync` 模块当前不在仓库中。
- 该问题与本次 `public_kb` Pipeline 重构无关。
- 后续全量基线使用 `--ignore=test/test_cloud_sync.py`，并在重构完成后单独提示该历史问题。

### 1.2 排除历史缺模块后的测试结果

命令：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test -q --ignore=test/test_cloud_sync.py
```

结果：

```text
211 passed in 37.55s
```

结论：

- 当前可运行测试全部通过。
- 该结果作为重构前基线。

---

## 2. Milvus POC 基线

### 2.1 BM25 Function 服务端探测

命令：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" scripts\poc_probe_function.py
```

结果：

```text
探测目标: http://localhost:19531 | 服务端版本: 2.6.23
fields: ['id', 'text', 'sparse_vector']
functions: ['text_bm25_emb']
PROBE: PASS
临时集合已清理
```

### 2.2 混合检索 8 用例

命令：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" scripts\poc_verify_hybrid.py
```

结果：

| 用例 | 名称 | 结果 | 关键证据 |
| --- | --- | --- | --- |
| 1 | dense-only | PASS | 5 hits，top score `0.6730` |
| 2 | bm25-only | PASS | 5 hits，top score `3.985332` |
| 3 | hybrid-rrf(raw) | PASS | dense 5 + sparse 5，fusion 10 |
| 4 | full-chain(reranker real) | PASS | mode `hybrid_rerank`，Reranker `success`，sources 1 |
| 5 | reranker-failure fallback | PASS | mode `hybrid_rrf`，status `failed`，保留 5 个 sources |
| 6 | irrelevant -> refusal | PASS | 无引用，返回拒答 |
| 7 | citation R1-R7 | PASS | `all_passed: true` |
| 8 | strict-mode e2e | PASS | strict 下仍进入 hybrid，无静默异常 |

总体：

```text
OVERALL: PASS
```

运行产物：

```text
test_report/hybrid_poc_c3_results.json
```

说明：

- 该报告文件当前位于本地测试产物目录，未纳入 Git。
- POC 验证过程中出现过一次外部服务的瞬时连接日志，但最终 case4 记录 `reranker_status: success`，case8 严格模式通过。

---

## 3. 重构兼容性检查

静态检索显示，当前测试直接依赖以下 `qa_chain` 私有符号：

```text
public_kb.qa_chain._SiliconFlowReranker
public_kb.qa_chain._dense_only_retrieve
public_kb.qa_chain.build_qa_chain
```

相关测试：

```text
test/test_citation_tracing.py
test/test_qa_chain_offline.py
```

结论：

- 阶段 1 和阶段 2 拆分时，必须在 `qa_chain.py` 中保留这些兼容导出。
- `agent/` 未直接依赖 `qa_chain.py` 私有符号，协程并发改造的主要外部入口不受影响。

---

## 4. 阶段 0 结论

| 项 | 状态 |
| --- | --- |
| 文档与计划基线 | 已提交 |
| 可运行离线测试 | 211 passed |
| Milvus 2.6 POC | 8/8 PASS |
| BM25 Function | PASS |
| Reranker 降级语义 | PASS |
| 引用规则 | PASS |
| 严格模式 | PASS |
| 历史阻断项 | `cloud_sync` 模块缺失，需另行处理 |
| 协作影响 | 未发现 `agent` 依赖 `qa_chain` 私有实现；测试依赖需保留兼容壳 |

**阶段 0 基线冻结完成，等待用户确认后进入阶段 1 / R1.1。**
