# 工作报告 — 阶段2：异步 Knowledge QA 节点改造

> **项目**：招投标智能助手（zhaotoubiao_demo）
> **分支**：`feat/async-memory-streaming`
> **日期**：2026-08-26
> **依据文档**：
> - `docs/implementation_handbook_async_memory_streaming.md` §阶段2
> - `docs/project_refactoring_master_plan.md`
> - `docs/async_concurrency_refactor_plan.md`
> **前置状态**：阶段0（基线与依赖）✅、阶段1（异步骨架与 AgentGraph 双轨入口）✅ 已完成

---

## 1. 阶段目标回顾

按实施手册 §阶段2 要求，完成知识问答链路的异步化改造：

| 目标项 | 手册要求 | 达成情况 |
| --- | --- | --- |
| 异步 RAG 链路 | `node_knowledge_qa_async()` 走 async RAG | ✅ 完成 |
| Reranker 异步化 | 改用 `httpx.AsyncClient` | ✅ 完成 |
| Embedding 异步化 | 使用 `aembed_query()` | ✅ 完成 |
| 核心交付 API | `PublicKnowledgeRAG.aquery()` / `build_async_qa_chain()` | ✅ 完成 |
| 流式预留 | `rag_engine.astream()` 产出 token + final citations | ✅ 完成（超额，含引用帧） |

---

## 2. 交付物清单

### 2.1 新增文件（5 个）

| 文件 | 说明 |
| --- | --- |
| `public_kb/reranker.py` | `AsyncSiliconFlowReranker`：httpx.AsyncClient 版重排序客户端 |
| `public_kb/qa_chain_async.py` | `AsyncRAGPipeline` + `build_async_qa_chain()` 异步 LCEL 问答链 |
| `agent/nodes/knowledge_qa_async.py` | 异步知识问答节点（业务节点层） |
| `test/test_reranker_async.py` | Reranker 离线测试（9 用例） |
| `test/test_rag_async.py` | 异步 RAG 全链路测试（17 用例 + 1 门控集成测试） |

### 2.2 修改文件（6 个）

| 文件 | 变更内容 |
| --- | --- |
| `public_kb/embedding_service.py` | `_SafeEmbeddings` 新增原生 `aembed_query` / `aembed_documents`（保留超长文本截断保护） |
| `public_kb/rag_engine.py` | 新增 `aquery()`、`astream()`；异步流水线懒建；`clear_kb()` 时同步重置 |
| `agent/graph.py` | 抽取共用 `_fallback_result()`；新增 `_with_fallback_async`；`async_nodes=True` 时 knowledge_qa 注册异步节点 |
| `agent/streaming/events.py` | 补充 `STAGE` / `RETRIEVAL` 事件类型（对齐手册 §3.5 envelope 契约） |
| `agent/nodes/__init__.py` | 懒加载导出 `node_knowledge_qa_async` |
| `agent/runtime/concurrency.py` | **缺陷修复**：并发信号量按事件循环分桶（见 §4.4） |

---

## 3. 关键技术实现

### 3.1 异步 RAG 流水线（`AsyncRAGPipeline`）

检索 → 回答 → 结果组装三段式设计，各步骤可独立调用：

```
question
  ├─ embed_query_async ──┐
  │                      ├─ gather_limited(limit=2) 并行执行
  ├─ describe_collection ┘   （压缩首字延迟）
  │
  ├─ hybrid_search（稠密 COSINE + 稀疏 BM25 → RRFRanker 融合）
  │     └─ run_blocking 桥接线程池 + "milvus_search" 信号量限流
  │
  ├─ Reranker 精排（AsyncSiliconFlowReranker，"rerank" 信号量限流）
  │
  └─ 动态阈值过滤（复用同步 _adaptive_threshold 纯函数）
        ↓
   prompt | llm | StrOutputParser（ainvoke，"llm" 信号量限流）
        ↓
   引用溯源校验（CitationValidator R1-R7，fail-soft）
```

### 3.2 降级路径完整保留（与同步版一一对应）

| 场景 | 同步行为 | 异步实现 |
| --- | --- | --- |
| 无 collection/embeddings | 纯稠密检索 | `_dense_only_retrieve_async` 方案 A/B 两级回退 |
| 旧 schema 无稀疏字段 | 稠密+Reranker 模式 | 相同分支判断 |
| hybrid_search 抛错 | 回退稠密降级检索 | try/except 后走同一降级函数 |
| Reranker API 失败 | 原始顺序 + relevance_score=0.5 | 逐字一致（先指数退避重试再降级） |
| 检索为空 | 固定拒答文案 + is_refusal 校验报告 | 文案与报告结构逐字段一致 |

### 3.3 重试策略（新增能力）

`tenacity.AsyncRetrying` 仅对瞬时错误重试，避免浪费尝试次数：

- **可重试**：`TimeoutException`、`TransportError`、HTTP 429/500/502/503/504
- **不重试**：业务性 4xx（如 400 参数错误）——直接走降级
- 退避参数：`multiplier=0.5, min=0.5s, max=4s`；默认最多尝试 2 次

### 3.4 缺陷修复：并发信号量跨事件循环失效

**问题**：Python 3.12 的 `asyncio.Semaphore` 一旦出现排队竞争会绑定首个事件循环。CLI 交互模式每轮问答都是独立的 `asyncio.run()`（新循环），第二轮一旦触发限流竞争即抛 `RuntimeError: ... is bound to a different event loop`。

**修复**：`agent/runtime/concurrency.py` 注册表按 `(资源名, 当前循环id)` 分桶，同一循环内共享并发配额，跨循环各自独立。

**兼容性**：`register / acquire / get_or_register / list_registered` 接口不变；`test_runtime_smoke.py` 直接操作 `_REGISTRY` 的用法不受影响；显式 `register()` 保持阶段1的覆盖语义。

### 3.5 流式输出预留（为阶段5铺路）

`rag_engine.astream()` 产出统一 envelope 事件序列：

```
stage(retrieval_start) → retrieval(候选摘要) → token* → citations → final
```

- 引用帧严格晚于正文 token 生成完毕后发出（规避手册风险登记册 R-07「引用帧引用未生成正文」）
- 拒答场景跳过 retrieval/token，直接 final
- 单次流内所有事件共享同一 `request_id`

---

## 4. 业务语义零退化保障措施

1. **纯函数不复制**：prompt 构建、文档格式化、entity→Document 转换、动态阈值、citations 构建与校验器全部从 `qa_chain.py` 导入复用；
2. **单例共享**：异步节点通过 `knowledge_qa._get_rag()` 复用同一 RAG 实例，不重复建立 Milvus 连接；
3. **结果结构契约**：`{"answer", "sources", "citations", "citation_validation"}` 四字段与同步版完全一致，测评系统无感知；
4. **对照测试**：`test_refusal_semantics_identical_to_sync` 用相同 mock 组件分别跑同步/异步链，断言 answer/sources/citation_validation 逐字段相等。

---

## 5. 测试与验证结果

### 5.1 新增测试（27 项）

| 测试文件 | 数量 | 覆盖点 |
| --- | --- | --- |
| `test/test_reranker_async.py` | 9 通过 | 分数降序、空文档短路、超时降级、500 重试后成功、400 不重试、并发信号量上限、工厂方法、请求体形状 |
| `test/test_rag_async.py` | 17 通过 + 1 跳过 | 混合检索 happy path、拒答、稀疏字段缺失降级、hybrid 异常降级、低分拒答、token 流式、embedding/schema 并行、aquery 结构、astream 事件序列、节点成功/RuntimeError/空输入、图节点注册双模式、同步语义对照；live 集成测试默认门控跳过 |

**结果：26 passed, 1 skipped**（live 集成需 `RUN_LIVE_RAG_ASYNC=1` + 本地 Milvus）

### 5.2 全量回归

```
239 passed, 2 skipped, 4 failed（34.34s）
```

4 个失败均为**预存环境问题**，与本次改动无关：

- 失败位置：`test/test_sub_route.py`（询价子路由分类，4 例）
- 根因：测试需调用真实 DeepSeek LLM，当前账户返回 `402 Insufficient Balance`，回退关键词提取导致断言失败
- 佐证：失败发生在 `price_inquiry/intent.py` 路径，该模块本次改动未触碰

### 5.3 离线冒烟验证

- `AgentGraph(async_enabled=False)` 图构建 ✅
- `AgentGraph(async_enabled=True)` 图构建（含异步 router + 异步 knowledge_qa 注册）✅

---

## 6. 验收对照表（手册 §阶段2）

| # | 验收项 | 状态 | 说明 |
| --- | --- | --- | --- |
| 1 | 单请求 P50/P95 ≤ 同步基线 +10% | ⏸ 待环境 | 需可用 Milvus + 有余额 LLM Key，环境就绪后运行 `scripts/benchmark_async.py` 对照 `docs/baseline/baseline_async_pre.json` |
| 2 | 并发 10 错误率 ≤ 同步基线 | ⏸ 待环境 | 同上 |
| 3 | 引用 R1-R7 校验无退化 | ✅ 单测已验证 | happy path `all_passed=true`；拒答路径校验报告与同步版逐字段相等 |

---

## 7. 回滚方案

按手册约定，无需代码回滚：

```bash
# 方式一：环境变量总开关（生产推荐）
ASYNCHRONOUS_BACKEND_ENABLED=false

# 方式二：代码级显式指定
AgentGraph(async_enabled=False)
```

两种方式均使 `build_graph(async_nodes=False)` 注册同步 knowledge_qa 节点，回到阶段1状态。异步代码路径完全不被触达。

---

## 8. 遗留事项与下一步计划

### 8.1 遗留事项

| 事项 | 优先级 | 说明 |
| --- | --- | --- |
| 性能验收（P50/P95、并发错误率） | 中 | 依赖 Milvus 可达 + LLM Key 充值，就绪后补测并入档 |
| live 集成测试执行 | 中 | `RUN_LIVE_RAG_ASYNC=1 python -m pytest test/test_rag_async.py::test_live_rag_async_integration` |

### 8.2 下一步：阶段3 — MySQL 有界连接池与询价并发（预计 2~3 天）

1. `db_async.py`：DBUtils.PooledDB 有界连接池 + 异步 acquire 上下文管理器 + `SET SESSION MAX_EXECUTION_TIME` 服务端超时
2. `recall_async.py`：拆分 `_query_table_async`，`gather_limited(limit=PRICE_RECALL_CONCURRENCY)` 三表并行召回
3. `safe_execute` SQL 客户端封装：语句超时 + 连接回收验证
4. `node_price_inquiry_async()`：LLM 意图解析 ainvoke + 并行召回 + P0-11/P0-12 后置校验保留
5. 测试：池耗尽行为、多表合并顺序、慢 SQL 注入连接不泄漏

---

## 附录：变更统计

```
 agent/graph.py                    | 166 +++++++++++++++++++++++++++++----------
 agent/nodes/__init__.py           |   9 +++
 public_kb/embedding_service.py    |  23 ++++++
 public_kb/rag_engine.py           | 116 +++++++++++++++++++++++++++-
 public_kb/reranker.py             | 200 行（新增）
 public_kb/qa_chain_async.py       | 445 行（新增）
 agent/nodes/knowledge_qa_async.py |  87 行（新增）
 agent/streaming/events.py         |   +2 事件类型
 agent/runtime/concurrency.py      | 重构（循环分桶）
 test/test_rag_async.py            | 554 行（新增，18 用例）
 test/test_reranker_async.py       | 212 行（新增，9 用例）
```

> 报告人：ox-alpha（Claude Code）
> 审核人：待定
