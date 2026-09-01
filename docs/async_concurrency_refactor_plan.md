# 招投标智能助手协程并发改造方案

> 版本：v1.0  
> 日期：2026-08-25  
> 范围：Agent 主链路、智能询价链路、公共知识库 RAG、基础设施层  
> 目标：在不改变现有业务语义和对外返回结构的前提下，将同步阻塞链路改造为可编排的异步并发链路，降低多路召回与 LLM 等待时间，并提升服务端并发吞吐。

## 1. 结论摘要

- 采用“**外层 asyncio + 边界线程池桥接**”的渐进式方案。优先将 LangGraph 入口、Router、业务节点、RAG 链路改为 `async`；MySQL `pymysql`、Milvus 同步客户端、HTTP Reranker 等暂不改驱动，先通过统一的异步边界执行器隔离阻塞调用。
- 第一阶段不做全量重写。先建立 `agent/runtime/async_bridge.py` 和统一资源池规范，再按 Router → Price Inquiry → Knowledge QA 的顺序迁移。
- MySQL 不使用单条长事务连接承载并发查询。当前 `_pool_connections` 是自定义列表池，且 `_query_tables()` 在一个连接上串行执行多表查询；应替换为具备容量上限的连接池或为每个并发任务获取独立连接。
- 所有外部 I/O 设置显式超时、并发上限和取消策略，避免线程池占满、MySQL 连接耗尽以及任务取消后 SQL 继续执行。
- 新增异步性能基线、回归测试和压测脚本。每阶段保留同步入口 `AgentGraph.invoke()` 作为兼容层，内部可委托 `asyncio.run()` 或独立事件循环适配器。

## 2. 现状诊断

### 2.1 当前关键链路

```text
CLI / AgentGraph.invoke()
  → CompiledStateGraph.invoke()
    → router（LLM 同步 invoke）
      → knowledge_qa / price_inquiry / general_chat / doc_qa / fallback

knowledge_qa:
  PublicKnowledgeRAG.query()
    → qa_chain.invoke()
      → Embedding
      → Milvus dense + sparse hybrid_search
      → Reranker HTTP requests.post()
      → LLM answer_chain.invoke()

price_inquiry:
  node_price_inquiry()
    → _parse_unified_intent()          # LLM 同步调用
    → _sql_executor.submit(query_fn)   # ThreadPoolExecutor 超时包装
      → _query_tables()
        → Milvus semantic recall
        → 多表 FULLTEXT/LIKE 召回
        → 二次回表补齐字段
```

### 2.2 主要阻塞点

| 位置 | 现状 | 问题 | 并发改造价值 |
| --- | --- | --- | --- |
| `agent/graph.py` | `CompiledStateGraph.invoke()` 与 `AgentGraph.invoke()` 均为同步 | 单请求内无法并行等待多个 I/O；服务化场景下每个请求容易占用工作线程 | 高 |
| `agent/router.py` | structured output / tool calling 使用 `.invoke()` | Router 是所有请求的前置 LLM 等待点 | 中 |
| `public_kb/rag_engine.py` | `PublicKnowledgeRAG.query()` 使用 `.invoke()` | RAG 全流程串行等待 Embedding、Milvus、Reranker、LLM | 高 |
| `public_kb/qa_chain.py` | `RunnableLambda` 包装同步检索；Reranker 用 `requests.post()` | 无法原生释放事件循环；精排阶段会阻塞 worker | 高 |
| `agent/nodes/price_inquiry/node.py` | 先等 LLM 意图解析，再提交 SQL 查询 | 两段强依赖不能并行，但后续可提前准备资源池与表 schema | 中 |
| `agent/nodes/price_inquiry/recall.py` | `_sql_executor = ThreadPoolExecutor(max_workers=4)`；SQL 超时后 future 不取消 | 高并发下线程池易饱和；超时任务仍占用 SQL 连接和 CPU/DB 时间 | 高 |
| `agent/nodes/price_inquiry/db.py` | 自定义 `list[Connection]` + threading lock | 无最大连接数声明；不适合同一进程内多协程并发取用 | 高 |
| `agent/nodes/price_inquiry/recall.py::_query_tables()` | 一个 MySQL 连接内逐表执行语义召回、FULLTEXT/LIKE、回表补齐 | 多表之间天然可并行；单连接串行放大总耗时 | 高 |
| `agent/nodes/price_inquiry/semantic.py` | Milvus 客户端同步 search，Embedding 同步生成 | 与 MySQL 回表存在依赖，但向量生成和集合检查可并行预热 | 中 |
| `public_kb/embedding_service.py` | `_SafeEmbeddings` 使用同步 `embed_query()` | 批量入库和多路查询时阻塞事件循环 | 中 |

## 3. 总体技术路线

### 3.1 架构原则

1. **业务状态结构不变**：继续沿用 `AgentState` 的 `messages`、`router_intent`、`business_result` 三字段契约，不因并发改造增加分支字段。
2. **节点签名逐步双轨**：LangGraph 支持异步节点。新增 `node_xxx_async(state) -> dict`，旧函数暂时委托新函数并使用 `asyncio.run()`，避免一次性破坏诊断脚本和测试。
3. **阻塞调用必须过边界**：禁止在 coroutine 内直接调用 pymysql、pymilvus 同步 SDK、`requests.post()` 或 CPU 密集型解析逻辑；统一通过 `AsyncBoundary` 执行。
4. **并发必须受控**：LLM、Embedding、Reranker、Milvus、MySQL 分别设置 Semaphore 和队列深度，防止上游一次请求触发指数级下游压力。
5. **失败快速降级**：并发分支采用 `return_exceptions=True` 或结构化结果；任一辅助路径失败不吞掉主结果，超时路径仍输出当前已有的友好提示和部分数据。
6. **取消要传导到底**：Python `Future.cancel()` 对已运行任务无效。SQL 层需要结合语句级 `MAX_EXECUTION_TIME`、连接关闭/归还前校验和池健康检查处理超时。

### 3.2 目标架构

```text
FastAPI / CLI / Batch Runner
  ↓ await
AsyncDispatcher / AgentGraph.ainvoke()
  ↓ await
LangGraph AsyncStateGraph
  ├─ router_async                    # LLM ainvoke
  ├─ knowledge_qa_async              # async RAG chain
  │    ├─ embedding                  # async boundary / native aembed_query
  │    ├─ milvus hybrid_search       # async boundary
  │    ├─ rerank HTTP                # aiohttp/httpx AsyncClient
  │    └─ llm answer                 # ainvoke
  ├─ price_inquiry_async
  │    ├─ unified intent             # LLM ainvoke
  │    └─ controlled parallel recall
  │         ├─ table A: semantic + fulltext/like + enrich
  │         ├─ table B: semantic + fulltext/like + enrich
  │         └─ table C: semantic + fulltext/like + enrich
  └─ general_chat_async / doc_qa_async / fallback_async

Shared Infrastructure:
  - AsyncBoundary（to_thread / executor）
  - MySQL async pool or bounded per-task connection factory
  - Milvus sync-client bridge
  - HTTP connection pool
  - per-dependency semaphore / timeout / circuit breaker
```

## 4. 分阶段实施计划

### Phase 0 — 建立基线与安全网（0.5～1 天）

**目标**：确认当前性能、正确性与并发上限，避免优化不可度量。

1. 运行现有 pytest：

   ```bash
   python -m pytest test/ -v
   ```

2. 记录以下基线指标：
   - Router P50/P95/P99；
   - Knowledge QA 端到端耗时、引用校验通过率；
   - Price Inquiry 意图解析耗时、SQL 总耗时、命中数、引导率；
   - MySQL 活跃连接数、慢查询数、FULLTEXT 命中分布；
   - Milvus 检索耗时、Reranker 耗时、LLM 首 token/完成耗时；
   - 并发 1/5/10/20/50 下的错误率和吞吐。

3. 固化代表性数据集：
   - 继续使用 `testset_knowledge.jsonl` 做 RAG 引用回归；
   - 从 `testset_company_info.jsonl`、`testset_company_penalty.jsonl`、`testset_bid_project.jsonl` 抽样固定 seed 的询价用例；
   - 补充并发重复问题、无结果问题、慢查询问题三类异常样本。

4. 新增配置项：

   ```env
   ASYNC_ENABLED=true
   ASYNC_IO_THREADS=16
   MYSQL_MAX_POOL_SIZE=16
   MYSQL_ACQUIRE_TIMEOUT=3
   PRICE_RECALL_CONCURRENCY=3
   MILVUS_MAX_CONCURRENCY=8
   LLM_MAX_CONCURRENCY=8
   EMBEDDING_MAX_CONCURRENCY=8
   RERANK_MAX_CONCURRENCY=4
   REQUEST_TOTAL_TIMEOUT=30
   ```

**退出条件**：形成 baseline JSON 报告，现有测试全部通过。

### Phase 1 — 异步骨架与阻塞桥接层（1～2 天）

#### 1.1 新增运行时模块

建议新增目录：

```text
agent/runtime/
  __init__.py
  async_bridge.py       # run_io / run_cpu / bounded gather
  concurrency.py        # named semaphore registry and limits
  timeouts.py           # deadline propagation helpers
```

核心能力：

```python
# agent/runtime/async_bridge.py 示例
import asyncio
from concurrent.futures import Executor, ThreadPoolExecutor

_default_executor: Executor | None = None

def configure_executor(max_workers: int) -> None:
    global _default_executor
    _default_executor = ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="blocking-io",
    )

async def run_blocking(function, /, *args, executor=None, **kwargs):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        executor or _default_executor,
        lambda: function(*args, **kwargs),
    )

async def gather_limited(coros_factory, limit: int):
    semaphore = asyncio.Semaphore(limit)

    async def run_one(factory):
        async with semaphore:
            return await factory()

    tasks = [run_one(factory) for factory in coros_factory]
    return await asyncio.gather(*tasks, return_exceptions=True)
```

注意事项：

- 不要把 `ThreadPoolExecutor.submit().result(timeout=...)` 直接搬进 coroutine；应使用 `asyncio.wait_for()` 控制等待时间。
- `loop.run_in_executor()` 只能让当前协程不再阻塞事件循环，不会自动终止线程中的阻塞函数。

#### 1.2 AgentGraph 双轨入口

保持现有 API 不破坏：

```python
class AgentGraph:
    def invoke(self, question, thread_id="default"):
        return asyncio.run(self.ainvoke(question, thread_id))

    async def ainvoke(self, question, thread_id="default"):
        result = await self._graph.ainvoke(
            {"messages": [HumanMessage(content=question)]},
            config={"configurable": {"thread_id": thread_id}},
        )
        ...
```

若未来嵌入已有事件循环的服务，不要在 `invoke()` 内硬编码 `asyncio.run()`，而是提供：

```python
def invoke_sync_safe(self, question, thread_id="default"):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(self.ainvoke(question, thread_id))
    raise RuntimeError("请在 running loop 内使用 await agent_graph.ainvoke()")
```

#### 1.3 Router 异步化

改造点：

- `_route_via_structured_output()` → `_route_via_structured_output_async()`;
- `_route_via_tool_calling()` → `_route_via_tool_calling_async()`;
- `.invoke([...])` 改为 `await ... .ainvoke([...])`;
- `build_router_node()` 增加 `build_router_node_async()`；
- 启动期 structured-output 能力探测不得放在首个用户请求的关键路径中，建议在应用 startup 中预热并缓存结果。

### Phase 2 — 智能询价并发改造（2～4 天）

这是收益最高、风险也最高的阶段，必须先改资源池和超时模型。

#### 2.1 数据库资源层

推荐顺序：

1. **保守方案**：继续使用 pymysql，但替换自定义列表池。
   - 引入 `DBUtils.PooledDB` 或 SQLAlchemy Engine；
   - 显式配置 `maxconnections=MYSQL_MAX_POOL_SIZE`、`blocking_timeout`、`ping=1`；
   - 每个并发 SQL 子任务从池中独立取连接；
   - 连接只允许在一个子任务生命周期内使用，禁止跨协程共享未加保护的 cursor。

2. **中期方案**：切换到 `asyncmy` 或 `aiomysql`。
   - SQL builder、schema、意图模型不变；
   - 将 `_execute_sql_fetch_rows()` 改为 async 版本；
   - 保留 pymysql 适配器用于诊断脚本和离线工具。

不建议直接给每个协程新建裸连接；高并发下会放大握手成本和 MySQL `max_connections` 压力。

#### 2.2 SQL 超时治理

当前 `_execute_sql_with_timeout()` 的线程池 future 超时后只是上层放弃等待，任务可能仍在执行。改造为三层防护：

```sql
SELECT /*+ MAX_EXECUTION_TIME(8000) */ ...
```

应用层：

```python
rows = await asyncio.wait_for(run_blocking(fetch_rows, conn, sql, params), timeout)
```

资源层：

- 超时后标记连接可疑，不立即复用；
- 关闭 cursor/connection 或执行 `KILL QUERY` 由专用管理器决定；
- 池归还前检查 `conn.open`，必要时重建连接；
- 区分“上层等待超时”和“SQL 服务端超时”两个指标。

#### 2.3 多表并行召回

将 `_query_tables(tables, intent)` 拆成两层：

```python
async def query_tables_async(tables, intent):
    results = await asyncio.gather(
        *(query_table_async(table, intent) for table in tables),
        return_exceptions=True,
    )
    merged = merge_recall_results(results)
    ranked = await run_blocking(rank_records, merged.records, intent, top_k=20)
    return build_query_result(ranked, merged.stats)
```

每个表任务内部：

1. 独立获取 MySQL 连接；
2. `await` Milvus semantic recall；
3. `await` FULLTEXT OR / LIKE fallback；
4. 必要时执行关键词拆分重试；
5. `await` 二次回表补齐字段；
6. 输出带统计信息的结构化结果。

并发约束：

- `PRICE_RECALL_CONCURRENCY` 初始值设为 3，对应当前三张核心表；
- 每个表任务最多占用 1 个 MySQL 连接；
- 语义候选 ID 过多时，回表拆批但不要无限拆分；
- 排序、去重、记录清洗如耗时明显，放入线程池或单独 CPU executor。

#### 2.4 意图解析与查询阶段衔接

LLM 意图解析仍是后续 SQL 的前置依赖，不能强行与查询并行。可做的低风险优化：

- 应用启动或首次进入询价分支时预热 `_get_classification()`；
- 在等待 LLM 结果期间预创建/预热 MySQL pool、Milvus collection load；
- 对高频模板问题建立确定性路由缓存，例如明确项目编号、明确公司名加常见动词的组合；
- 缓存 key 只使用规范化后的用户问题和最近一轮上下文摘要，避免缓存穿透和错误上下文污染。

### Phase 3 — 公共知识库 RAG 异步化（2～3 天）

#### 3.1 查询链路

改造目标链路：

```python
async def aquery(self, question: str):
    if self._aqa_chain is None:
        raise RuntimeError("知识库尚未初始化")
    result = await self._aqa_chain.ainvoke(question)
    return normalize_rag_result(result)
```

LCEL 改造方向：

- `RunnableLambda(_retrieve)` → `RunnableLambda(_retrieve_async)`，并在内部返回 coroutine；
- 或直接使用 LangChain 提供的 async Runnable 组合方式；
- LLM prompt chain 改用 `ainvoke()`；
- Embedding 优先使用 OpenAIEmbeddings 的 `aembed_query()`；
- Reranker 从 `requests.post()` 改为共享 `httpx.AsyncClient` 或 `aiohttp.ClientSession`。

示例：

```python
async def arerank(query, documents, top_k):
    async with semaphores["rerank"]:
        response = await http_client.post(
            f"{base_url}/rerank",
            headers=headers,
            json={
                "model": model,
                "query": query,
                "documents": documents,
                "top_n": min(top_k, len(documents)),
            },
            timeout=request_timeout,
        )
        response.raise_for_status()
        return response.json()["results"]
```

#### 3.2 混合检索策略

dense 向量生成、collection schema 检查可以合并到一次异步预处理步骤。当前 sparse request 直接传原始文本并由 BM25 Function tokenize，因此不需要为 sparse 再额外生成向量。

不建议盲目并行 dense/sparse 两次 search 后本地融合；当前 pymilvus `hybrid_search()` 已在服务端完成 RRF，客户端拆开反而增加网络往返和一致性风险。

#### 3.3 批量入库并发

`initialize_collection()` 的 PDF 解析、清洗、切分、向量化、插入可以流水线化：

```text
PDF parse (bounded threads)
  → clean/chunk (bounded CPU pool)
    → embed batches (bounded async)
      → Milvus insert (bounded bridge)
```

但第一阶段不要追求最大化并发：

- MinerU API 和 Embedding API 有限流；
- Milvus flush 频率过高会影响稳定性；
- 初始 batch 并发建议不超过 2～4；
- 保留失败清单和断点续跑能力。

### Phase 4 — 服务化与全局治理（1～2 天）

如果项目仍以 CLI 为主，此阶段可延后；如果计划部署为在线服务，建议引入 FastAPI：

```python
@app.post("/chat")
async def chat(payload: ChatRequest):
    return await agent_graph.ainvoke(
        payload.question,
        thread_id=payload.thread_id,
    )
```

全局要求：

1. 每个请求携带 deadline，并向下传播到 LLM、SQL、Milvus、Reranker；
2. 外部依赖设置熔断阈值，连续失败时进入短时降级；
3. `/health/readiness` 检查 MySQL、Milvus、LLM 配置和知识库加载状态；
4. 结构化日志加入 `request_id`、`thread_id`、intent、sub_route、各阶段耗时；
5. Prometheus/OpenTelemetry 至少采集：
   - active coroutines；
   - blocking executor queue size；
   - MySQL pool checked-out/idle/waiting；
   - Milvus search latency；
   - LLM/Reranker latency；
   - timeout/cancel/fallback count。

## 5. 关键代码改造映射

| 文件 | 当前实现 | 改造动作 | 优先级 |
| --- | --- | --- | --- |
| `agent/graph.py` | `graph.invoke()`、`_with_fallback()` 同步包装 | 增加 `ainvoke()` 和 async fallback wrapper；同步入口保留兼容 | P0 |
| `agent/router.py` | LLM `.invoke()` | 增加 async router 和 `.ainvoke()`；缓存能力探测 | P0 |
| `agent/checkpointer.py` | MemorySaver 默认 | 在线服务使用支持 async checkpoint 的持久化后端；验证 sqlite/postgres/redis saver 是否支持当前 LangGraph 异步图 | P1 |
| `agent/nodes/knowledge_qa.py` | `rag.query()` 同步调用 | 增加 `node_knowledge_qa_async()` 并调用 `rag.aquery()` | P0 |
| `public_kb/rag_engine.py` | `_qa_chain.invoke(question)` | 增加 `_aqa_chain` 与 `aquery()`；懒加载共享 AsyncClient | P0 |
| `public_kb/qa_chain.py` | RunnableLambda + 同步检索/精排 | 增加 async retrieve/rerank/answer 链 | P0 |
| `public_kb/embedding_service.py` | 同步 `embed_query()` | 查询侧使用 `aembed_query()`；批量入库使用受控 `aembed_documents()` | P1 |
| `agent/nodes/price_inquiry/node.py` | LLM 后 submit 到 `_sql_executor` | 改成 async 意图解析 + `wait_for(query_tables_async())` | P0 |
| `agent/nodes/price_inquiry/recall.py` | 单连接逐表查询；ThreadPoolExecutor 超时 | 表级并发；async wait_for；SQL 服务端超时；连接可疑回收 | P0 |
| `agent/nodes/price_inquiry/db.py` | 自定义 list pool | 替换为有界成熟连接池，或抽象出 async pool adapter | P0 |
| `agent/nodes/price_inquiry/semantic.py` | 同步 Milvus search/embedding | 通过 async bridge 调用；短期可保留同步客户端 | P1 |
| `test/profile_node_price.py` | 端到端同步 profile | 增加并发 profile 和阶段耗时对比 | P1 |

## 6. 并发控制参数建议

| 参数 | 初始值 | 说明 |
| --- | ---: | --- |
| `ASYNC_IO_THREADS` | 8～16 | 默认阻塞桥接线程数，不宜超过 MySQL/Milvus 实际承受能力 |
| `MYSQL_MAX_POOL_SIZE` | 8～16 | 必须小于 MySQL `max_connections` 减去其他系统预留 |
| `PRICE_RECALL_CONCURRENCY` | 3 | 先按三张表并行，稳定后再评估表内策略并行 |
| `MILVUS_MAX_CONCURRENCY` | 4～8 | 避免 search 打满 query node |
| `LLM_MAX_CONCURRENCY` | 按 API quota | 全局信号量，Router 与业务 LLM 共享或分类配额 |
| `EMBEDDING_MAX_CONCURRENCY` | 按 API quota | 批量入库与在线查询分离限额更佳 |
| `RERANK_MAX_CONCURRENCY` | 2～4 | Reranker 通常是最脆弱的外部依赖之一 |
| `REQUEST_TOTAL_TIMEOUT` | 30～45s | 用户可感知总 deadline |
| `SQL_STATEMENT_TIMEOUT` | 5～8s | 写入 SQL hint 或 session 变量 |
| `SHUTDOWN_GRACE_SECONDS` | 10～30s | 等待进行中请求完成，随后取消剩余任务 |

## 7. 错误处理、超时与降级矩阵

| 场景 | 行为 | 用户结果 |
| --- | --- | --- |
| Router LLM 失败/超时 | 进入 `fallback` | 保持现有兜底回答 |
| Intent LLM 失败/超时 | 使用规则降级或返回统一引导 | 不执行高风险宽泛 SQL |
| 某张表召回失败 | `gather(return_exceptions=True)` 收集异常，其他表继续 | 可返回其他表命中结果，并在 data 中标注 partial_error |
| SQL 上层等待超时 | 取消等待，标记连接可疑，触发 SQL kill/关闭策略 | 输出现有简化条件建议 |
| Milvus semantic recall 失败 | 跳过语义路，继续 FULLTEXT/LIKE | 保留传统召回能力 |
| Reranker 失败 | 回退 dense-only 或跳过精排，保留 RRF 顺序 | 降低排序质量但不拒答 |
| LLM 回答失败 | 返回检索摘要/来源列表或现有降级话术 | 明确告知回答生成失败 |
| 整体 deadline 超时 | `asyncio.timeout()` 触发取消，等待 executor 安全收尾 | 输出友好超时提示 |
| Checkpointer 写入失败 | fail-open 或 fail-closed 按业务定义 | 会话记忆可能丢失，但主答案不应被覆盖 |

## 8. 测试与验收标准

### 8.1 功能测试

1. 现有 `test/test_bug_repairs.py`、`test/test_p0_*.py`、`test/test_sub_route.py`、`test/test_recall_optimization.py`、`test/test_citation_tracing.py` 全部通过；
2. 为下列行为补 mock 单测：
   - async router 成功、structured output 失败后 tool calling 回退；
   - async fallback wrapper 捕获业务节点异常；
   - 多表并发部分失败仍能合并成功结果；
   - MySQL acquire timeout；
   - SQL statement timeout；
   - RAG reranker 失败回退；
   - event loop 内误用同步入口的保护逻辑。

### 8.2 性能测试

新增：

```text
scripts/benchmark_async.py
scripts/benchmark_price_parallel_recall.py
scripts/benchmark_knowledge_rag.py
```

至少覆盖：

| 场景 | 并发 | 关注指标 |
| --- | ---: | --- |
| Knowledge QA | 1 / 10 / 30 | P50/P95、引用完整率、拒答率 |
| Price Inquiry company_query | 1 / 10 / 30 | 意图耗时、SQL 耗时、命中率 |
| Price Inquiry all 兜底 | 1 / 10 / 20 | 三表并行收益、partial error 率 |
| Mixed traffic | 30 | 相互干扰、连接池排队、整体吞吐 |
| Slow SQL | 5 | 超时是否释放请求、DB 连接是否恢复 |

### 8.3 验收门槛

- 功能结果与同步基线一致，引用溯源校验不退化；
- Price Inquiry all 兜底模式 P50 至少下降 25%（以实际三表耗时为准）；
- 并发 20 下错误率不高于同步基线；
- MySQL pool waiting 为零或在毫秒级；
- 无线程池 queue 持续增长；
- 超时请求结束后 MySQL 活跃连接回落；
- 所有新增异步代码可通过 `pytest` 与静态检查。

## 9. 风险清单与缓解措施

| 风险 | 影响 | 缓解措施 |
| --- | --- | --- |
| 在 coroutine 中直接调用 pymysql/pymilvus/requests | 事件循环被阻塞，异步吞吐失效 | 强制经过 `run_blocking()`；code review checklist 明确禁止裸调用 |
| Future timeout 后 SQL 继续执行 | DB 资源被慢查询拖垮 | SQL hint/session timeout + 可疑连接隔离 + KILL QUERY 管理器 |
| 共享单个 MySQL connection | cursor 状态互相污染、协议错误 | 一任务一连接；禁止跨 task 共享 connection/cursor |
| 连接池过大 | MySQL 连接耗尽 | 有界池、acquire timeout、监控 waiting/active |
| LLM 并发触发供应商限流 | 大量 429/timeout | 全局 semaphore、退避重试、按 intent 分类限额 |
| asyncio.gather 未隔离异常 | 一个分支异常导致整组失败或结果缺失 | `return_exceptions=True` + 结构化合并 |
| 任务取消后后台线程仍在运行 | 资源泄漏、结果写入竞争 | 明确取消语义；executor 线程只做幂等读取，不写业务状态 |
| LangGraph checkpoint 异步兼容性差异 | 状态保存失败或死锁 | 每个后端做 async smoke test；默认先用 MemorySaver 验证 |
| RAG 并发改变引用顺序 | 来源编号不稳定，测评回归失败 | 合并结果前按 score/chunk_id 稳定排序，快照对比 |
| 缓存上下文污染 | 多轮对话答非所问 | cache key 包含 thread 上下文摘要和版本号；敏感会话禁用共享缓存 |

## 10. 推荐里程碑

```text
第 1 步：Phase 0 基线 + runtime bridge + 单测
第 2 步：Graph/Router/Knowledge QA 最小异步闭环
第 3 步：MySQL 有界池 + SQL 超时治理
第 4 步：Price Inquiry 三表并行
第 5 步：RAG reranker/native async 链路完善
第 6 步：FastAPI readiness、metrics、graceful shutdown
第 7 步：压测报告、容量参数定版、文档更新
```

预计净开发量：**7～12 个工作日**；若包含完整压测调优和服务化观测体系，建议预留 **2 周**。

## 11. 不建议本次一起做的事项

- 不建议一次性把所有模块改成 asyncmy/aiomysql；先让链路结构和资源池稳定；
- 不建议自行实现复杂分布式锁或跨进程任务队列；当前瓶颈主要在单请求 I/O 编排；
- 不建议为了并发而放宽 SQL 索引原则；FULLTEXT/精确过滤仍是第一优先级；
- 不建议对 public_kb 初始化做无上限并发抓取和向量化；
- 不建议在缺少压测数据的情况下把所有外部依赖并发上限调大。
