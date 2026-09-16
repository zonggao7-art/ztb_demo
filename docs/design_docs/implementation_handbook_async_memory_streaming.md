# 异步 + 记忆 + 流式改造实施手册

> 版本：v1.1  
> 日期：2026-08-25  
> 范围：Agent 主链路、Public KB RAG、MySQL 资源层、Checkpointer 记忆、CLI/Web 流式输出  
> 目标：把现有同步阻塞架构改造成"事件循环 + 受控并发 + 分帧流式"的生产级骨架，且业务语义零退化。  
> 2026-08-27 状态修订：原“阶段 4 — 长期记忆”及持久 Checkpointer 改造**无限期延后**，本文件相关章节保留为历史设计存档；当前不应按其排期开发。原因与替代方案见 §4.A。

本手册是执行级文档，假设你已通读并认可：

- `docs/project_refactoring_master_plan.md`（项目改造总纲）
- `docs/async_concurrency_refactor_plan.md`（异步改造详细方案）

如果你尚未读这两份文档，请先完成阅读再开始编码。

## 0. 阅读指引

- 第 1 章：技术栈与版本；先调整 `requirements.txt` 与 `.env.example`。
- 第 2 章：模块布局；先落目录与文件骨架，再填代码。
- 第 3 章：通用契约；所有阶段共用，先实现。
- 第 4 章：阶段拆解，每个阶段都包含"目标/前置/输入/输出/步骤/测试/验收/回滚"。
- 第 5 章：测试与验收。
- 第 6 章：风险登记册与回滚手册。
- 第 7 章：任务清单与里程碑。
- 第 8 章：完整 `requirements.txt`、`Settings` 字段与 `.env.example` 增量。
- 附录：关键模块代码示例，可直接复制后改造。

## 1. 技术栈与版本

### 1.1 当前依赖（来自 `requirements.txt`）

```text
langchain-core>=0.3.37,<0.5.0
langchain-openai>=1.0.0,<2.0.0
langchain-milvus>=0.4.0
langgraph>=1.2.0
langgraph-checkpoint>=2.0.0
pymilvus>=2.4.5
pymysql>=1.1.0
openai>=1.50.0,<2.0.0
tiktoken>=0.7.0,<0.9.0
sentence-transformers>=3.0
markdown>=3.6,<4.0
python-dotenv>=1.0.0,<2.0.0
setuptools>=65,<70
```

### 1.2 新增依赖（精确范围，避免大版本冲击）

| 包 | 版本 | 用途 |
| --- | --- | --- |
| `httpx` | `>=0.27,<0.29` | Reranker / 外部 HTTP 异步客户端，替换 `requests` |
| `aiohttp` | `>=3.9,<4.0` | 备用异步 HTTP（SSE 上游推送场景） |
| `uvloop` | `>=0.19`（Linux/macOS） | 加速 asyncio 事件循环；Windows 跳过 |
| `orjson` | `>=3.10` | SSE/JSON envelope 高效序列化 |
| `DBUtils` | `>=3.1,<4.0` | `PooledDB`：跨平台 MySQL 有界连接池 |
| `SQLAlchemy` | `>=2.0,<2.1` | 备用连接池方案（与 `DBUtils` 二选一） |
| `psycopg[binary]` | `>=3.1,<4.0` | PostgreSQL Checkpointer 驱动 |
| `asyncpg` | `>=0.29` | PostgresSaver 异步连接（按 LangGraph 版本要求） |
| `aiosqlite` | `>=0.20` | SqliteSaver 异步依赖 |
| `pydantic` | `>=2.6,<3.0` | envelope / memory schema 强类型 |
| `tenacity` | `>=8.2` | 退避重试统一封装（替换散落的 try/except） |
| `prometheus-client` | `>=0.20` | 指标导出（可选，但建议有） |

> 备注：`asyncmy/aiomysql` 暂不引入，先用 `DBUtils.PooledDB + loop.run_in_executor` 桥接，保持改动面最小。

### 1.3 Checkpointer 后端依赖矩阵

| Backend | Python 包 | LangGraph 接口 | 用途 |
| --- | --- | --- | --- |
| memory | 自带 | `MemorySaver` | 本地开发 / 单测 |
| sqlite | `langgraph-checkpoint-sqlite` + `aiosqlite` | `SqliteSaver` | Demo / 单机生产 |
| postgres | `langgraph-checkpoint-postgres` + `asyncpg` | `AsyncPostgresSaver` | 联机生产（推荐） |
| redis | `langgraph-checkpoint-redis` + `redis>=5` | `AsyncRedisSaver` | 高频会话（第二阶段再评估） |

## 2. 模块布局与文件命名

建议保持外部路径稳定，仅在内部新增/重构。新增/调整模块：

```text
agent/
  graph.py                 # 增加 ainvoke / astream
  router.py                # 增加 build_router_node_async
  checkpointer.py          # 新增 AsyncCheckpointerFactory
  state.py                 # 不变（保持三字段契约）
  runtime/                 # 新增
    __init__.py
    async_bridge.py        # run_blocking / gather_limited / configure_executor
    concurrency.py         # SemaphoreRegistry + 全局信号量
    deadlines.py           # Deadline / deadline_propagation 装饰器
    cancellation.py        # 结构化取消 + 资源清理
  memory/                  # 新增
    __init__.py
    models.py              # MemoryItem / MemoryCategory / MemorySource
    store.py               # AsyncMemoryStore 接口
    postgres_store.py      # PostgreSQL 实现
    sqlite_store.py        # SQLite 实现（fallback）
    extractor.py           # 候选记忆抽取 LLM 包装
    prompt_injection.py    # memory -> prompt 注入器
    routes.py              # /memories CRUD 接口
  streaming/               # 新增
    __init__.py
    events.py              # 统一 envelope (pydantic model)
    protocol.py            # SSE/WebSocket 序列化
    tokens.py              # token -> envelope 适配
    service.py             # FastAPI/CLI 通用流式服务
  nodes/
    router_async.py        # 异步 router 节点
    knowledge_qa_async.py  # async RAG 节点
    price_inquiry_async.py # async 询价节点（拆分 _query_tables）
    fallback_async.py      # 异步 fallback 包装器
    fallback.py            # 保留同步 fallback，向 ainvoke 兼容
    general_chat.py        # 改为静态 node + async wrapper
    doc_qa.py              # 占位 + async wrapper
    price_inquiry/
      recall_async.py      # 多表并行召回
      db_async.py          # 异步连接池封装
public_kb/
  llm_factory.py           # 增加 acreate_llm 异步实例
  embedding_service.py     # aembed_query / aembed_documents
  qa_chain_async.py        # 异步 LCEL 链
  rag_engine.py            # 增加 aquery / astream
  reranker.py              # 新增：httpx.AsyncClient + AsyncSiliconFlowReranker
scripts/
  benchmark_async.py       # 并发基线
  benchmark_stream.py      # 流式 TTFT 与端到端
test/
  test_async_bridge.py
  test_router_async.py
  test_rag_async.py
  test_price_inquiry_async.py
  test_memory_store.py
  test_memory_prompt.py
  test_streaming_protocol.py
  test_streaming_endpoints.py
  test_checkpointer_backends.py
```

## 3. 通用契约

> 所有阶段都必须先定义并在 review 时锁定的契约。

### 3.1 AgentState 不变

保持 `messages / router_intent / business_result` 三字段。新增节点不允许向 `AgentState` 加字段，避免 Checkpointer schema 升级和分支污染。

### 3.2 节点签名

| 阶段 | 同步入口 | 异步入口 |
| --- | --- | --- |
| Router | `(AgentState) -> dict` | `async (AgentState) -> dict` |
| Knowledge QA | `(AgentState) -> dict` | `async (AgentState) -> dict` |
| Price Inquiry | `(AgentState) -> dict` | `async (AgentState) -> dict` |
| General Chat | `(AgentState) -> dict` | `async (AgentState) -> dict` |
| Doc QA | `(AgentState) -> dict` | `async (AgentState) -> dict` |
| Fallback | `(AgentState) -> dict` | `async (AgentState) -> dict` |

同步入口继续存在，内部委托异步入口，保证 `agent/__main__.py`、诊断脚本与 pytest 兼容。

### 3.3 AgentGraph 对外 API

```python
class AgentGraph:
    def invoke(self, question, thread_id="default", *, deadline_s: float | None = None) -> dict
    async def ainvoke(self, question, thread_id="default", *, deadline_s: float | None = None) -> dict
    def stream(self, question, thread_id="default", *, deadline_s: float | None = None) -> Iterator[StreamEvent]
    async def astream(self, question, thread_id="default", *, deadline_s: float | None = None) -> AsyncIterator[StreamEvent]
```

要点：

1. 同步 `invoke()` 在无 running loop 时调用 `asyncio.run(self.ainvoke(...))`；否则抛 `RuntimeError` 提示调用 `ainvoke()`。
2. `deadline_s` 沿调用链向下传播到 SQL/LLM/Milvus。
3. `StreamEvent` 必须是统一 envelope（见 3.5）。


### 3.4 异步运行时基础设施

`agent/runtime/async_bridge.py`：

```python
import asyncio
from concurrent.futures import Executor, ThreadPoolExecutor
from typing import Any, Awaitable, Callable, Iterable, Optional

_IO_EXECUTOR: Optional[Executor] = None
_CPU_EXECUTOR: Optional[Executor] = None

def configure_executors(*, io_workers: int, cpu_workers: int) -> None:
    global _IO_EXECUTOR, _CPU_EXECUTOR
    _IO_EXECUTOR = ThreadPoolExecutor(max_workers=io_workers, thread_name_prefix="blocking-io")
    _CPU_EXECUTOR = ThreadPoolExecutor(max_workers=cpu_workers, thread_name_prefix="blocking-cpu")

async def run_blocking(function: Callable[..., Any], /, *args, executor: Optional[Executor] = None, **kwargs) -> Any:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor or _IO_EXECUTOR, lambda: function(*args, **kwargs))

async def gather_limited(
    coros: Iterable[Awaitable[Any]],
    *,
    limit: int,
    return_exceptions: bool = True,
) -> list[Any]:
    semaphore = asyncio.Semaphore(limit)

    async def _wrap(coro: Awaitable[Any]) -> Any:
        async with semaphore:
            return await coro

    return await asyncio.gather(*(_wrap(c) for c in coros), return_exceptions=return_exceptions)
```

`agent/runtime/concurrency.py`：

```python
import asyncio
from typing import Dict

_REGISTRY: Dict[str, asyncio.Semaphore] = {}

def register(name: str, limit: int) -> asyncio.Semaphore:
    _REGISTRY[name] = asyncio.Semaphore(limit)
    return _REGISTRY[name]

def acquire(name: str) -> asyncio.Semaphore:
    if name not in _REGISTRY:
        raise KeyError(f"Semaphore {name!r} 未注册")
    return _REGISTRY[name]
```

`agent/runtime/deadlines.py`：

```python
import asyncio, time
from contextlib import asynccontextmanager
from dataclasses import dataclass

@dataclass
class Deadline:
    started_at: float
    timeout_s: float

    def remaining(self) -> float:
        return max(0.0, self.timeout_s - (time.monotonic() - self.started_at))

@asynccontextmanager
async def deadline(timeout_s: float):
    d = Deadline(time.monotonic(), timeout_s)
    try:
        yield d
    finally:
        pass

async def wait_for_with_deadline(coro, deadline_obj: Deadline, *, label: str):
    remaining = deadline_obj.remaining()
    if remaining <= 0:
        raise asyncio.TimeoutError(f"{label} deadline already expired")
    return await asyncio.wait_for(coro, timeout=remaining)
```

注册默认信号量（在 `agent/runtime/__init__.py` 中）：

```python
register("llm", int(os.getenv("LLM_MAX_CONCURRENCY", "8")))
register("embedding", int(os.getenv("EMBEDDING_MAX_CONCURRENCY", "8")))
register("rerank", int(os.getenv("RERANK_MAX_CONCURRENCY", "4")))
register("milvus_search", int(os.getenv("MILVUS_MAX_CONCURRENCY", "8")))
register("mysql_acquire", int(os.getenv("MYSQL_MAX_CONCURRENCY", "16")))
register("price_recall", int(os.getenv("PRICE_RECALL_CONCURRENCY", "3")))
```

### 3.5 流式 envelope 契约

`agent/streaming/events.py`：

```python
from enum import Enum
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field

class EventType(str, Enum):
    META = "meta"
    STAGE = "stage"
    TOKEN = "token"
    RETRIEVAL = "retrieval"
    CITATION = "citation"
    TABLE = "table"
    PARTIAL = "partial"
    FINAL = "final"
    ERROR = "error"
    CANCELLED = "cancelled"

class StreamEvent(BaseModel):
    request_id: str
    thread_id: Optional[str] = None
    type: EventType
    data: dict[str, Any] = Field(default_factory=dict)
    ts: float = 0.0
```

SSE 输出格式（`text/event-stream`）：

```text
id: <request_id>
event: token
data: {"request_id":"...","type":"token","data":{"delta":"招标"},"ts":...}

```

错误事件：

```json
{"type":"error","data":{"code":"rag_timeout","message":"...","retryable":true}}
```

### 3.6 长期记忆数据契约

`agent/memory/models.py`：

```python
class MemoryCategory(str, Enum):
    USER_PROFILE = "user_profile"
    EPISODIC = "episodic"
    SEMANTIC = "semantic"

class MemorySource(str, Enum):
    USER_EXPLICIT = "user_explicit"
    LLM_EXTRACTED = "llm_extracted"
    INFERRED = "inferred"

class MemoryItem(BaseModel):
    id: Optional[str] = None
    user_id: str
    category: MemoryCategory
    source: MemorySource
    content: str
    confidence: float = Field(ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)
    valid_from: Optional[float] = None
    valid_until: Optional[float] = None
    created_at: float
    updated_at: float
    version: int = 1
```

接口（`agent/memory/store.py`）：

```python
class AsyncMemoryStore(Protocol):
    async def upsert(self, item: MemoryItem) -> MemoryItem: ...
    async def list(self, user_id: str, *, category: MemoryCategory | None = None) -> list[MemoryItem]: ...
    async def delete(self, user_id: str, memory_id: str) -> bool: ...
    async def search(self, user_id: str, query: str, *, top_k: int = 5) -> list[MemoryItem]: ...
    async def get(self, user_id: str, memory_id: str) -> MemoryItem | None: ...
```

第一阶段实现 `PostgresStore` + `SqliteStore`（演示）。Semantic 类别使用向量存储时必须带 `user_id` 强过滤。


## 4. 分阶段实施

> 每个阶段都按"目标 / 前置 / 输入 / 输出 / 步骤 / 测试 / 验收 / 回滚"展开。

### 阶段 0 — 准备与基线（0.5 天）

**目标**：锁依赖、固化配置、记录性能与正确性基线。

**前置**：已有 `docs/project_refactoring_master_plan.md`、`docs/async_concurrency_refactor_plan.md`。

**输入**：现有 pytest、`testset_knowledge.jsonl`、各 `testset_*.jsonl`。

**输出**：基线报告 `test_report/baseline_async_pre.json`，更新的 `requirements.txt` 与 `.env.example`。

**步骤**：

1. 按 1.2 更新 `requirements.txt`，使用本地 venv 锁定版本：`pip-compile requirements.in -o requirements.txt`。
2. 复制本手册 8.3 中的配置项追加到 `.env.example`。
3. 运行：

   ```bash
   python -m pytest test/ -v
   python scripts/run_knowledge_citation_eval.py
   ```

4. 用 `scripts/profile_node_price.py` 与新增的 `scripts/benchmark_async.py` 记录 baseline。
5. 在 `docs/baseline/` 提交基线报告。

**测试**：pytest 全绿；基线脚本可重复执行。

**验收**：存在 `baseline_async_pre.json`，包含 LLM/Embedding/Milvus/MySQL 各阶段 P50/P95。

**回滚**：所有改动在 `feat/async-memory-streaming` 分支上，未合入主线。

### 阶段 1 — 异步骨架与 AgentGraph ainvoke（1~2 天）

**目标**：建立 `runtime/` 三件套，新增 `AgentGraph.ainvoke()` 与 `astream()` 占位。Router 异步化。

**前置**：阶段 0。

**输入**：`agent/graph.py`、`agent/router.py`、`agent/checkpointer.py`、`agent/state.py`。

**输出**：可在 `await agent_graph.ainvoke(...)` 下端到端运行的最小闭环（业务节点仍走同步实现）。

**步骤**：

1. 新建 `agent/runtime/{async_bridge,concurrency,deadlines,cancellation}.py`，实现 3.4 中的全部契约。
2. 在 `agent/runtime/__init__.py` 完成默认 executor 与 semaphore 注册，提供 `init_runtime_from_settings(settings)`。
3. 新增 `agent/streaming/events.py`、`protocol.py`，实现 3.5 envelope，但不接入业务。
4. `agent/router.py` 增加：
   - `_route_via_tool_calling_async()`
   - `_route_via_structured_output_async()`
   - `build_router_node_async(llm)`，并在 `build_router_node()` 内委托。
5. `agent/graph.py`：
   - `build_graph()` 增加参数 `async_nodes: bool = False`；
   - 增加 `acompile(checkpointer=...)` 入口；
   - `AgentGraph.__init__` 同时持有同步图和异步图；
   - 新增 `ainvoke`/`astream`，遵循 3.3 契约；
   - 同步 `invoke`/`stream` 内部委托 `asyncio.run(self.ainvoke(...))`，并捕获已运行 loop 异常。
6. 配置 `ASYNCHRONOUS_BACKEND_ENABLED=true` 时使用异步图，否则保持同步。
7. CLI `agent/__main__.py` 增加 `--async/--sync` 参数；默认同步，调试时用 `--async` 验证。

**测试**：

- `test/test_async_bridge.py`：mock `loop.run_in_executor`，验证桥接不阻塞；
- `test/test_router_async.py`：mock LLM ainvoke；
- `test/test_graph_ainvoke.py`：端到端 mock 各业务节点；
- 现有 pytest 全部通过。

**验收**：

1. 同步路径与基线一致；
2. `await agent.ainvoke(...)` 与 `agent.invoke(...)` 返回值一致；
3. 现有 CLI、诊断脚本行为不变。

**回滚**：将 `ASYNCHRONOUS_BACKEND_ENABLED=false` 即可退回同步路径，无需代码回滚。


### 阶段 2 — 异步 Knowledge QA 节点（1~2 天）

**目标**：`node_knowledge_qa_async()` 走 async RAG；Reranker 改 `httpx.AsyncClient`；Embedding 使用 `aembed_query()`。

**前置**：阶段 1。

**输入**：`public_kb/rag_engine.py`、`public_kb/qa_chain.py`、`public_kb/embedding_service.py`。

**输出**：`agent.nodes.knowledge_qa_async.node_knowledge_qa_async()`、`public_kb.rag_engine.PublicKnowledgeRAG.aquery()`、`public_kb.qa_chain_async.build_async_qa_chain()`。

**步骤**：

1. `public_kb/embedding_service.py`：
   - `_SafeEmbeddings` 增加 `async aembed_query(text, **kwargs)`、`aembed_documents(texts, **kwargs)`；
   - 默认调用 `super().aembed_query/aembed_documents`。
2. 新增 `public_kb/reranker.py`：
   - `AsyncSiliconFlowReranker` 使用 `httpx.AsyncClient`（带超时、连接池与重试）；
   - 在 `async_bridge.acquire("rerank")` 限制并发；
   - 提供 `def from_settings(settings)` 工厂方法。
3. `public_kb/qa_chain_async.py`：
   - `_retrieve_async()`、`_decide_and_answer_async()`、`_adaptive_threshold()` 复用 `qa_chain.py` 中的纯函数；
   - LLM answer chain 使用 `prompt | llm | StrOutputParser()` 的异步版本（LangChain `ainvoke`）；
   - Milvus 同步 `hybrid_search` 走 `await run_blocking(_hybrid_search_with_full_fields, ...)`；
   - 用 `gather_limited([embedding, milvus_search], limit=2)` 替代串行调用，节约首字延迟。
4. `public_kb/rag_engine.py`：
   - 懒加载 `AsyncClient` 与 `AsyncSiliconFlowReranker`；
   - 增加 `async def aquery(question: str) -> dict`；
   - 增加 `async def astream(question: str) -> AsyncIterator[StreamEvent]`（产出 token + final citations）。
5. `agent/nodes/knowledge_qa_async.py`：
   - 复用现有 `node_knowledge_qa` 的结果结构；
   - 业务异常依然落到 `_with_fallback_async()`；
   - 在异步图中注册为 `knowledge_qa_async` 节点。

**测试**：

- `test/test_rag_async.py`：mock Embedding/Milvus/Reranker/LLM，验证 token + citations；
- `test/test_reranker_async.py`：mock `httpx.AsyncClient`，验证超时/重试/限流；
- 集成测试：使用本地 Milvus（standalone），跑 `testset_knowledge.jsonl` 子集，记录延迟与引用回查成功率。

**验收**：

1. 单请求 P50/P95 不高于同步基线 + 10%；
2. 并发 10 下错误率 不高于同步基线；
3. 引用 R1-R7 校验不发生退化。

**回滚**：异步节点从图中摘除，回到阶段 1 状态。

### 阶段 3 — MySQL 有界连接池与询价并发（2~3 天）

**目标**：MySQL 替换为有界池；询价节点实现三表并行召回；SQL 增加服务端超时控制。

**前置**：阶段 2。

**输入**：`agent/nodes/price_inquiry/db.py`、`recall.py`、`queries.py`、`semantic.py`、`node.py`。

**输出**：新增 `db_async.py`、`recall_async.py`；`node_price_inquiry_async()`。

**步骤**：

1. `agent/nodes/price_inquiry/db_async.py`：
   - 使用 `DBUtils.PooledDB(pymysql, mincached=2, maxcached=5, maxconnections=MYSQL_MAX_POOL_SIZE, blocking=True, host=..., ping=4)`；
   - 暴露 `acquire()` 异步上下文管理器，内部 `await run_blocking(pool.connection)`；
   - 提供 `health_check()` 在启动时验证；
   - `MYSQL_STMT_TIMEOUT_S` 通过 `SET SESSION MAX_EXECUTION_TIME=...` 在 acquire 钩子中下发。
2. `recall_async.py`：
   - 拆分 `_query_table_async(table, intent)`：semantic recall + FULLTEXT/LIKE + enrich 三步走；
   - `query_tables_async(tables, intent)` 用 `gather_limited(..., limit=PRICE_RECALL_CONCURRENCY)`；
   - 合并逻辑（rank / dedup / clean）放入 CPU executor。
3. SQL 客户端封装 `safe_execute`：
   - `cur.execute("SET SESSION MAX_EXECUTION_TIME=%d" % int(stmt_timeout * 1000))`；
   - `await asyncio.wait_for(run_blocking(fetch, ...), timeout=stmt_timeout + 0.5)`；
   - 超时后调用 `await run_blocking(conn.close)` 并将连接归还池时验证。
4. `node_price_inquiry_async()`：
   - LLM 意图解析走 `await llm.ainvoke(...)`；
   - `await query_tables_async(tables, intent, deadline=...)`；
   - 保留 P0-11/P0-12 后置校验；
   - 复用 `node_price_inquiry` 的 `business_result` 结构。
5. 同步 `node_price_inquiry` 内部委托异步实现：检测到 running loop 报错，否则用 `asyncio.run()`。

**测试**：

- `test/test_db_async_pool.py`：mock `PooledDB.connection`，验证池耗尽行为；
- `test/test_price_inquiry_async.py`：mock 多表召回，验证合并顺序、partial 异常；
- `test/test_sql_timeout.py`：注入慢 SQL，验证 timeout 路径不会泄漏连接；
- `scripts/benchmark_async.py` 新增询价并发场景。

**验收**：

1. 并发 10/20/50 下 MySQL `Connections` 不超 `MYSQL_MAX_POOL_SIZE`；
2. SQL 超时后连接池能恢复可用；
3. all 兜底模式 P50 下降 不低于 25%；
4. 现有 P0 测试与回归测试全部通过。

**回滚**：将 `node_price_inquiry_async` 从图中摘除，回到阶段 2。


### 阶段 4 — 长期记忆（2~3 天）

### 4.A 状态修订：无限期延后（2026-08-27）

**状态**：不进入当前实施计划；本阶段原有步骤仅作技术储备存档。当前配置应固定为：

```env
CHECKPOINTER_BACKEND=memory
MEMORY_ENABLED=false
MEMORY_ALLOW_EXTRACTED=false
```

**延后范围**

- 延后长期记忆模型、`memory_items` 存储、CRUD 和 Prompt 注入；
- 延后自动记忆抽取和用户画像建设；
- 暂缓 SQLite/PostgreSQL 持久 Checkpointer。

**核心原因**

1. **现有业务链路没有稳定消费点。** 三轮历史只在 Router 层用于一级意图路由；路由成功后，Knowledge QA 与 Price Inquiry 节点主要读取 `messages[-1]` 并解析单条问题。Price Inquiry 的统一意图 Prompt 也只接收 `{question}`，没有历史上下文槽位。因此写入或恢复历史消息并不能可靠改变最终查询计划。
2. **回答结果主要由能力边界和确定性模板决定。** 当前系统是受限问答系统：MySQL 分支受 `sub_route/query_type/hard_filters` 与固定回答模板约束；RAG 必须依据公共知识库参考资料回答。用户偏好无法扩展系统能力，也无法改变数据库事实。
3. **缺少 L2 Query Plan 接入点。** 在没有受控组合查询之前，偏好最多只能注入 Prompt，不能作为已校验的默认过滤条件参与 `company_search` 等查询。这样的记忆收益不可量化，且容易污染专业答案。
4. **持久 Checkpointer 的直接收益有限。** 其价值是跨进程恢复同一 `thread_id` 的执行状态；但当前业务节点没有实现指代消解、最近实体继承、草稿槽位补全等消费逻辑。服务重启后即使能读回旧消息，也大概率无法续跑“上一家公司有没有处罚记录”这类任务。
5. **成本收益失衡。** 完整记忆工程涉及 Store 抽象、迁移、CRUD、身份认证、租户隔离、Prompt 预算、PII 风险和回归测试，而当前六类封闭能力可验证的边际收益很小。

**重启条件**

后续满足任一条件时再重新评估：

1. 系统引入 L2 受控组合查询，并有明确需要用保存条件补全空槽位的场景；
2. 产品出现真实多轮任务续跑需求，例如最近公司/项目继承；
3. 服务部署形态出现多实例会话迁移或跨进程恢复诉求；
4. 完成企微/飞书登录，并建立可信 `user_id` 与权限边界。

**替代思路**

当前优先采用显式上下文和产品化配置，而不是隐式记忆：

```json
{
  "last_company_id": "...",
  "last_project_number": "...",
  "draft_filters": {
    "province": "江苏",
    "industry": "环保设备"
  }
}
```

这些字段由前端/客户端在每次请求时传入，由 L2 Validator 校验白名单、类型和权限后，只允许补全缺失参数，不得覆盖用户本次显式输入。聊天记录如需展示和审计，使用普通业务表存储，不绑定 LangGraph checkpoint schema。

优先级调整为：

```text
1. L2 受控组合查询契约、评测集与 company_search 闭环
2. 修复结构化召回中的硬过滤/实体映射质量问题
3. 为未来多轮任务预留显式 last_entity/draft_filters API 契约
```

本文档中后续涉及阶段 4 的任务清单、安全门槛和历史章节均按此状态理解：**不再排期，不代表删除设计依据。**

**目标**：Session 记忆持久化；显式用户记忆 CRUD；Prompt 注入；候选记忆抽取 v1。

**前置**：阶段 1（异步骨架）。

**输入**：`agent/checkpointer.py`、`agent/state.py`、`agent/memory/`（新建）。

**输出**：

- `agent/checkpointer.py::AsyncCheckpointerFactory`；
- `agent/memory/{models,store,postgres_store,sqlite_store,extractor,prompt_injection,routes}.py`；
- `scripts/migrate_memory.py`。

**步骤**：

1. **Checkpointer 持久化**
   - 新增 `create_async_checkpointer(backend, connection_string)`；
   - SQLite：使用 `AsyncSqliteSaver.from_conn_string(path).setup()`；
   - Postgres：使用 `AsyncPostgresSaver.from_conn_string(dsn).setup()`；
   - 在 `AgentGraph` 启动时调用 `await checkpointer.setup()`；
   - 默认 backend 通过 `CHECKPOINTER_BACKEND` 环境变量切换。
2. **数据库 schema**

   ```sql
   CREATE TABLE IF NOT EXISTS memory_items (
     id           UUID PRIMARY KEY,
     user_id      VARCHAR(64) NOT NULL,
     category     VARCHAR(32) NOT NULL,
     source       VARCHAR(32) NOT NULL,
     content      TEXT NOT NULL,
     tags         JSONB,
     confidence   REAL NOT NULL,
     valid_from   TIMESTAMPTZ,
     valid_until  TIMESTAMPTZ,
     created_at   TIMESTAMPTZ NOT NULL,
     updated_at   TIMESTAMPTZ NOT NULL,
     version      INTEGER NOT NULL DEFAULT 1
   );
   CREATE INDEX IF NOT EXISTS idx_memory_user_cat ON memory_items(user_id, category);
   ```

   SQLite 等价使用 `TEXT` 存 JSON。
3. **接口实现**
   - `PostgresStore` 使用 `asyncpg` 连接池；
   - `SqliteStore` 使用 `aiosqlite`；
   - 实现 `upsert/list/delete/search/get`；
   - `search` 仅做关键词匹配 + JSON tag 过滤；语义检索放到第二阶段。
4. **Prompt 注入**
   - `agent/memory/prompt_injection.py` 提供 `build_memory_block(memory_items, max_tokens=400)`；
   - 节点在调用 LLM 前注入；明确标注来源与有效期。
5. **候选抽取**
   - `agent/memory/extractor.py`：
     - 用 LLM 从对话中抽取稳定事实；
     - 输出 `MemoryCandidate`，由规则/用户确认后写入；
     - 显式白名单字段（如"关注地区/行业/常用公司"），其余丢弃。
6. **CLI / API**
   - `python -m agent --memory list/delete/upsert`；
   - `scripts/migrate_memory.py` 提供 dry-run 迁移脚本。

**测试**：

- `test/test_memory_store.py`：CRUD + 并发 upsert；
- `test/test_memory_prompt.py`：注入长度截断、来源标注、过期过滤；
- `test/test_checkpointer_backends.py`：memory/sqlite/postgres 切换；
- `test/test_extractor.py`：mock LLM 输出，验证白名单过滤。

**验收**：

1. 重启进程后 session 历史可恢复；
2. 显式记忆 CRUD 不阻塞主路径；
3. 不同 `user_id` 数据强隔离；
4. Prompt 注入不会超出 max_tokens；
5. 候选抽取不会写入黑名单字段。

**回滚**：将 `CHECKPOINTER_BACKEND=memory` 即回到 MemorySaver；记忆读写开关通过 `MEMORY_ENABLED=false` 关闭。


### 阶段 5 — 流式输出（2~3 天）

**目标**：端到端 token 流；阶段事件；引用 final frame；CLI 与 FastAPI 双通道。

**前置**：阶段 1（骨架）、阶段 2/3/4（异步业务节点）。

**输入**：`agent/streaming/`、`agent/graph.py`、`agent/__main__.py`、新增 `service/api.py`（如启动 FastAPI）。

**输出**：`AgentGraph.astream()`、FastAPI `/chat/stream`、CLI `--stream`。

**步骤**：

1. **协议层**
   - `events.py` 完成 3.5；
   - `protocol.py` 提供 `format_sse(event: StreamEvent) -> bytes` 与 `parse_sse(line) -> StreamEvent`；
   - `tokens.py` 把 `BaseChatModel` 的 `astream` 产出 token 适配为 `StreamEvent(type=TOKEN, data={"delta":...})`。
2. **节点流式适配**
   - 同步节点维持向后兼容；
   - 异步节点实现 `async def astream(state) -> AsyncIterator[StreamEvent]`；
   - Router 产出 `stage` 事件（`router_done`）；
   - Knowledge QA 产出 `stage(retrieval_start)`、`retrieval(candidates)`、`token*`、`citation`、`final`；
   - Price Inquiry 产出 `stage(intent_done)`、`stage(sql_start)`、`table` 帧（partial/final）、`token*`、`final`；
   - General Chat 直接走 `astream` 输出 token；
   - Doc QA 在没有真实实现前产出 `stage` + 静态 final。
3. **Graph 流**
   - `AgentGraph.astream()` 使用 LangGraph `astream_events(version="v2")` 监听节点；
   - 自定义 adapter 把节点 emit 的事件转换为统一 envelope；
   - 当 `enable_inline_citations=true` 时，引用 frame 始终在 final 前发出。
4. **服务层**
   - `service/api.py`：

     ```python
     @app.post("/chat/stream")
     async def chat_stream(req: ChatRequest):
         agent = AgentGraph()

         async def gen():
             async for event in agent.astream(req.question, thread_id=req.thread_id, deadline_s=req.deadline_s):
                 yield format_sse(event)
                 if event.type.value == "final":
                     break

         return StreamingResponse(gen(), media_type="text/event-stream",
                                   headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})
     ```

   - 启用 SSE：`StreamingResponse(media_type="text/event-stream", headers={...})`；
   - 设置心跳：每 15s 发送 `event: ping\ndata: {}\n\n`；
   - 客户端断开时 `asyncio.CancelledError` 处理资源清理。
5. **CLI**
   - `python -m agent --question "..." --stream` 启用 `astream`；
   - 打印 `meta/stage/token/citation/final` 五类事件；
   - 进度阶段用 emoji（搜索 / 推理 / 撰写 / 完成）。
6. **取消与超时**
   - 每个请求携带 `deadline_s`；
   - `astream` 监听 `asyncio.CancelledError` 并向下传播 SQL/LLM 取消；
   - LLM 取消失败时记录 warning，引用 frame 标注 `partial: true`。

**测试**：

- `test/test_streaming_protocol.py`：序列号、heartbeat、错误事件；
- `test/test_streaming_endpoints.py`：FastAPI TestClient + SSE 解析；
- `test/test_knowledge_qa_stream.py`：知识问答流式与引用一致性；
- `test/test_price_inquiry_stream.py`：询价分帧流；
- `test/test_cli_stream.py`：stdout 解析 + 取消。

**验收**：

1. CLI 单次问答 P50 首字时间显著下降；
2. 引用校验在 final frame 命中且不与 token 内容冲突；
3. SSE 在 30s 心跳断线测试下能被客户端正常感知；
4. FastAPI 服务可水平扩展到多 worker（uvicorn + gunicorn + uvloop）；
5. 取消请求不会导致连接泄漏或 MySQL pool 占用。

**回滚**：FastAPI 路由注册开关 `STREAM_ENABLED=false`，CLI `--stream` 不传时使用 `ainvoke`。

### 阶段 6 — 观测、压测与发布门禁（1~2 天）

**目标**：补齐指标、压测脚本、发布/回滚工具。

**前置**：阶段 1~5。

**输入**：`scripts/benchmark_async.py`、`scripts/benchmark_stream.py`。

**输出**：完整指标、日志格式、灰度发布与回滚手册。

**步骤**：

1. 指标：
   - 请求计数与错误率；
   - 各阶段延迟 histogram；
   - LLM token 用量；
   - MySQL pool waiting / in-use；
   - 异步节点 active count；
   - Memory CRUD 计数；
   - SSE 心跳与断线重连次数。
2. 日志：
   - 每个请求打印一次结构化 JSON，含 request_id / thread_id / intent / sub_route / 各阶段耗时 / 引用数 / 拒答原因；
   - 引入 `request_id` 中间件（FastAPI）与 CLI UUID。
3. 压测：
   - `scripts/benchmark_async.py` 覆盖：单分支基线、混合流量、慢 SQL 注入、partial 错误注入；
   - `scripts/benchmark_stream.py` 覆盖：TTFT、token 速率、断线重连。
4. 发布：
   - `ASYNCHRONOUS_BACKEND_ENABLED=true`、`CHECKPOINTER_BACKEND=postgres`、`STREAM_ENABLED=true`；
   - 旧版本保留不低于 1 个发布周期；
   - 通过 `baseline_async_pre.json` vs `baseline_async_post.json` 比较。

**验收**：

1. 灰度流量（10% -> 50% -> 100%）期间 P95 不退化；
2. 回滚到同步路径可在 不超过 5 分钟完成；
3. 指标可视化已接入。

**回滚**：环境变量回到同步 + 内存 checkpoint + 不开流式即可立即恢复。


## 5. 测试与验收总览

### 5.1 必须通过的测试

1. 既有 `test/` 下所有 pytest 用例；
2. `scripts/run_knowledge_citation_eval.py` 引用溯源结果不退化；
3. 新增 4 各阶段的单元/集成测试；
4. 至少一次并发压测（不低于 20 并发、不低于 5 分钟）。

### 5.2 性能门槛

| 指标 | 目标 | 不达标处置 |
| --- | --- | --- |
| Knowledge QA P50 | 不高于同步基线 +10% | 优化 retrieve 串并行或缓存 |
| Knowledge QA TTFT | 不超过 1.5s | 拆分 token/检索阶段 SSE |
| Price Inquiry all P50 | 下降不低于 25% | 排查 enrichment 或 enrich SQL |
| 并发 20 错误率 | 不高于同步基线 | 排查超时/重试策略 |
| MySQL pool waiting P95 | 不超过 50ms | 调整池容量或减少热点查询 |
| LLM 429 占比 | 不超过 1% | 调整并发/重试 |

### 5.3 安全门槛

1. 记忆功能处于关闭态：`MEMORY_ENABLED=false` 且 `CHECKPOINTER_BACKEND=memory`；若未来重启记忆，必须先补齐不同 `user_id` 数据隔离自动化用例；
2. 引用 frame 与正文一致，不存在最终被替换的"幻觉引用"；
3. 取消请求 30s 内连接回收；
4. 若未来重启记忆，Memory 字段白名单必须覆盖全部写入路径；当前阶段不得存在任何未授权 Memory 写入入口。

## 6. 风险登记册

| ID | 风险 | 影响 | 缓解 | 监控信号 |
| --- | --- | --- | --- | --- |
| R-01 | 同步入口在事件循环内误调 | RuntimeError、任务失败 | `invoke()` 检测 running loop；异常信息明确 | 启动测试 |
| R-02 | SQL 超时后底层仍在执行 | 连接池耗尽 | statement timeout + 可疑连接回收 | pool waiting 趋势 |
| R-03 | 多任务共享 pymysql 连接 | cursor 状态污染 | 一任务一连接，禁止共享 | 集成测试 |
| R-04 | 异步节点内仍调同步 SDK | 事件循环阻塞 | review 检查 + lint | 静态扫描 |
| R-05 | Checkpointer 同步/异步混用 | 状态写失败 | 统一 `Async*Saver`；启动 setup | 启动日志 |
| R-06 | 未来重启长期记忆后跨用户泄露 | 合规事故 | 功能当前关闭；如重启必须 user_id 强过滤并补安全 CI | 当前无生产入口 |
| R-07 | 引用 frame 引用了未生成正文 | UI 错位 | 引用必须晚于正文生成 | 协议时序测试 |
| R-08 | SSE 代理缓冲 | 用户感知不到 token 流 | Nginx `X-Accel-Buffering: no` + 心跳 | 部署手册 |
| R-09 | 任务取消未传到底层 LLM | token 成本浪费 | astream 监听 CancelledError；token 用量监控 | 成本日报 |
| R-10 | 候选抽取污染 prompt | 错误记忆 | 白名单 + 置信度门槛 | 抽样审阅 |
| R-11 | Flow 模型热重载 checkpoint schema 变更 | 历史不可恢复 | 加 version 字段 + 迁移脚本 | 启动断言 |
| R-12 | uvloop 在 Windows 不可用 | 启动失败 | 仅 Linux/macOS 引入 uvloop | 平台 CI |
| R-13 | PostgresSaver 在 sqlite 测试下误连 | 数据错位 | 测试显式注入 backend | CI |
| R-14 | FastAPI 与 LangGraph 状态机冲突 | checkpointer 序列化失败 | 显式 `setup()`、版本锁定 | smoke test |
| R-15 | `tools` 配置变化未传播 | 节点能力不一致 | 单元测试覆盖 registry | CI |

## 7. 任务清单与里程碑

```text
Week 1
  Day 1   阶段 0 基线 + requirements + .env.example
  Day 2   阶段 1 runtime 三件套 + AgentGraph ainvoke
  Day 3-4 阶段 2 RAG async 链路（embedding/milvus/reranker）
  Day 5   阶段 3 起步：MySQL PooledDB + safe_execute

Week 2
  Day 1-2 阶段 3 收尾：询价多表并行 + 压测
  Day 3-4 ~~阶段 4 长期记忆 MVP（SqliteStore + PostgresStore）~~ → 取消；转投 L2 受控组合查询契约与评测集
  Day 5   阶段 5 起步：envelope + astream + CLI

Week 3
  Day 1-2 阶段 5 收尾：FastAPI SSE + 取消/超时
  Day 3   阶段 6 观测、压测、灰度发布
  Day 4   回归 + 文档同步
  Day 5   评审、回顾、第二阶段规划
```


## 8. 配置与依赖清单

### 8.1 requirements.txt（增量）

```text
# 异步基础设施
httpx>=0.27,<0.29
aiohttp>=3.9,<4.0
orjson>=3.10

# MySQL 连接池（与 SQLAlchemy 二选一）
DBUtils>=3.1,<4.0
# SQLAlchemy>=2.0,<2.1

# Checkpointer 后端
psycopg[binary]>=3.1,<4.0
asyncpg>=0.29
aiosqlite>=0.20

# Schema / 序列化
pydantic>=2.6,<3.0

# 重试与观测
tenacity>=8.2
prometheus-client>=0.20

# Linux/macOS 事件循环加速（可选）
uvloop>=0.19; sys_platform != "win32"
```

### 8.2 `Settings` 新增字段（`public_kb/config.py`）

```python
# 异步执行
async_backend_enabled: bool = field(default_factory=lambda: os.getenv("ASYNC_BACKEND_ENABLED", "false").lower() in {"1","true","yes"})
async_io_workers: int = int(os.getenv("ASYNC_IO_WORKERS", "16"))
async_cpu_workers: int = int(os.getenv("ASYNC_CPU_WORKERS", "4"))
llm_max_concurrency: int = int(os.getenv("LLM_MAX_CONCURRENCY", "8"))
embedding_max_concurrency: int = int(os.getenv("EMBEDDING_MAX_CONCURRENCY", "8"))
rerank_max_concurrency: int = int(os.getenv("RERANK_MAX_CONCURRENCY", "4"))
milvus_max_concurrency: int = int(os.getenv("MILVUS_MAX_CONCURRENCY", "8"))
price_recall_concurrency: int = int(os.getenv("PRICE_RECALL_CONCURRENCY", "3"))

# MySQL 池
mysql_max_pool_size: int = int(os.getenv("MYSQL_MAX_POOL_SIZE", "16"))
mysql_acquire_timeout_s: int = int(os.getenv("MYSQL_ACQUIRE_TIMEOUT", "3"))
sql_stmt_timeout_s: int = int(os.getenv("SQL_STMT_TIMEOUT_S", "8"))

# Checkpointer
checkpointer_backend: str = os.getenv("CHECKPOINTER_BACKEND", "memory")
checkpointer_sqlite_path: str = os.getenv("CHECKPOINTER_SQLITE_PATH", "checkpoints.db")
checkpointer_postgres_dsn: str = os.getenv("CHECKPOINTER_POSTGRES_DSN", "")

# 长期记忆
memory_enabled: bool = field(default_factory=lambda: os.getenv("MEMORY_ENABLED", "false").lower() in {"1","true","yes"})
memory_store_backend: str = os.getenv("MEMORY_STORE_BACKEND", "sqlite")  # sqlite | postgres
memory_pg_dsn: str = os.getenv("MEMORY_PG_DSN", "")
memory_sqlite_path: str = os.getenv("MEMORY_SQLITE_PATH", "memory.db")
memory_max_injection_tokens: int = int(os.getenv("MEMORY_MAX_INJECTION_TOKENS", "400"))
memory_min_confidence: float = float(os.getenv("MEMORY_MIN_CONFIDENCE", "0.7"))
memory_allow_extracted: bool = field(default_factory=lambda: os.getenv("MEMORY_ALLOW_EXTRACTED", "false").lower() in {"1","true","yes"})

# 流式输出
stream_enabled: bool = field(default_factory=lambda: os.getenv("STREAM_ENABLED", "false").lower() in {"1","true","yes"})
stream_heartbeat_s: int = int(os.getenv("STREAM_HEARTBEAT_S", "15"))
stream_cancel_grace_s: int = int(os.getenv("STREAM_CANCEL_GRACE_S", "5"))

# Reranker
rerank_timeout_s: int = int(os.getenv("RERANK_TIMEOUT_S", "5"))
```

### 8.3 `.env.example` 增量

```env
# ========== Async / Concurrency ==========
ASYNC_BACKEND_ENABLED=false
ASYNC_IO_WORKERS=16
ASYNC_CPU_WORKERS=4
LLM_MAX_CONCURRENCY=8
EMBEDDING_MAX_CONCURRENCY=8
RERANK_MAX_CONCURRENCY=4
MILVUS_MAX_CONCURRENCY=8
PRICE_RECALL_CONCURRENCY=3

# ========== MySQL pool ==========
MYSQL_MAX_POOL_SIZE=16
MYSQL_ACQUIRE_TIMEOUT=3
SQL_STMT_TIMEOUT_S=8

# ========== Checkpointer ==========
CHECKPOINTER_BACKEND=memory          # memory | sqlite | postgres
CHECKPOINTER_SQLITE_PATH=checkpoints.db
# CHECKPOINTER_POSTGRES_DSN=postgresql://user:pass@host:5432/langgraph

# ========== Long-term Memory ==========
MEMORY_ENABLED=false
MEMORY_STORE_BACKEND=sqlite          # sqlite | postgres
MEMORY_SQLITE_PATH=memory.db
# MEMORY_PG_DSN=postgresql://user:pass@host:5432/memory
MEMORY_MAX_INJECTION_TOKENS=400
MEMORY_MIN_CONFIDENCE=0.7
MEMORY_ALLOW_EXTRACTED=false

# ========== Streaming ==========
STREAM_ENABLED=false
STREAM_HEARTBEAT_S=15
STREAM_CANCEL_GRACE_S=5

# ========== Reranker ==========
RERANK_TIMEOUT_S=5
```


## 附录 A — 关键代码示例

> 以下片段可直接复制作为实现起点；所有错误处理必须保留。

### A.1 AgentGraph 双轨入口

```python
# agent/graph.py
class AgentGraph:
    def __init__(self, *, checkpointer_backend=None, async_enabled=None):
        from agent.runtime import init_runtime_from_settings
        from public_kb.config import Settings
        self._settings = Settings()
        init_runtime_from_settings(self._settings)

        async_enabled = (
            self._settings.async_backend_enabled
            if async_enabled is None else async_enabled
        )
        self._async_enabled = async_enabled
        self._graph = build_graph(
            async_nodes=async_enabled,
            checkpointer=create_checkpointer(self._settings.checkpointer_backend),
            acheckpointer=create_async_checkpointer(self._settings.checkpointer_backend)
            if async_enabled else None,
        )

    def invoke(self, question, thread_id="default", *, deadline_s=None):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.ainvoke(question, thread_id, deadline_s=deadline_s))
        raise RuntimeError("请在事件循环内使用 await agent.ainvoke(...)")

    async def ainvoke(self, question, thread_id="default", *, deadline_s=None):
        from langchain_core.messages import HumanMessage
        result = await self._graph.ainvoke(
            {"messages": [HumanMessage(content=question)]},
            config={"configurable": {"thread_id": thread_id}},
        )
        return self._post_process(result)

    async def astream(self, question, thread_id="default", *, deadline_s=None):
        from langchain_core.messages import HumanMessage
        async for ev in self._graph.astream_events(
            {"messages": [HumanMessage(content=question)]},
            config={"configurable": {"thread_id": thread_id}},
            version="v2",
        ):
            yield adapt_event(ev)
```

### A.2 Async Router

```python
# agent/router.py
async def _route_via_structured_output_async(llm, history_str, user_input):
    structured_llm = llm.with_structured_output(RouterDecision)
    decision = await structured_llm.ainvoke([
        SystemMessage(content=ROUTER_SYSTEM_PROMPT),
        HumanMessage(content=ROUTER_USER_TEMPLATE.format(history=history_str, user_input=user_input)),
    ])
    logger.info("路由(structured): intent=%s", decision.intent)
    return decision.intent
```

### A.3 Async Knowledge QA

```python
# agent/nodes/knowledge_qa_async.py
async def node_knowledge_qa_async(state):
    question = state["messages"][-1].content
    try:
        rag = _get_rag()
        async with acquire("llm"):
            result = await rag.aquery(question)
        return _to_business_result(result)
    except Exception as e:
        return _with_fallback_result("knowledge_qa", e)
```

### A.4 Async Reranker

```python
# public_kb/reranker.py
class AsyncSiliconFlowReranker:
    def __init__(self, settings, *, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._client = client or httpx.AsyncClient(
            base_url=settings.rerank_base_url,
            timeout=settings.rerank_timeout_s,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    async def rerank(self, query, documents, top_k=3):
        if not documents:
            return []
        async with acquire("rerank"):
            try:
                resp = await self._client.post(
                    "/rerank",
                    headers={"Authorization": f"Bearer {self._settings.rerank_api_key}"},
                    json={
                        "model": self._settings.rerank_model,
                        "query": query,
                        "documents": documents,
                        "top_n": min(top_k, len(documents)),
                    },
                )
                resp.raise_for_status()
                return resp.json()["results"]
            except (httpx.TimeoutException, httpx.HTTPError) as e:
                logger.warning("Reranker 调用失败: %s", e)
                return []

    async def aclose(self):
        await self._client.aclose()
```

### A.5 MySQL 有界池

```python
# agent/nodes/price_inquiry/db_async.py
class MySQLPool:
    def __init__(self, settings):
        from dbutils.pooled_db import PooledDB
        import pymysql
        self._pool = PooledDB(
            creator=pymysql,
            mincached=2,
            maxcached=max(2, settings.mysql_max_pool_size // 2),
            maxconnections=settings.mysql_max_pool_size,
            blocking=True,
            host=os.getenv("MYSQL_HOST", "192.168.10.120"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "iflytek"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            charset="utf8mb4",
            ping=4,
            connect_timeout=min(10, settings.sql_query_timeout),
            read_timeout=settings.sql_query_timeout,
            write_timeout=settings.sql_query_timeout,
            database=os.getenv("MYSQL_CLEAN_DB", "ztb_clean"),
        )

    def acquire(self):
        return self._pool.connection()
```

`with conn:` 上下文由 PooledDB 提供，会在退出时归还。Acquire 阶段如需 statement timeout，使用游标钩子：

```python
@asynccontextmanager
async def connection(pool: MySQLPool, *, stmt_timeout_s: int):
    conn = await run_blocking(pool.acquire)
    try:
        await run_blocking(_set_stmt_timeout, conn, stmt_timeout_s)
        yield conn
    finally:
        await run_blocking(conn.close)  # 归还池

def _set_stmt_timeout(conn, stmt_timeout_s):
    with conn.cursor() as cur:
        cur.execute("SET SESSION MAX_EXECUTION_TIME=%d" % int(stmt_timeout_s * 1000))
```

### A.6 多表并行召回

```python
# agent/nodes/price_inquiry/recall_async.py
async def query_tables_async(pool: MySQLPool, tables, intent, *, deadline: Deadline):
    async def _one(table):
        async with connection(pool, stmt_timeout_s=deadline.remaining()) as conn:
            semantic = await _semantic_recall_async(conn, intent, table)
            recall = await _run_recall_chain_async(conn, table, intent, deadline)
            merged = await run_blocking(_merge, semantic, recall)
            return await run_blocking(_enrich_full_columns, conn, table, merged)

    tasks = (run_with_deadline(_one(t), deadline, label=f"recall:{t}") for t in tables)
    results = await gather_limited(tasks, limit=intent.price_recall_concurrency)
    return await run_blocking(_finalize_results, results)
```

### A.7 长期记忆 Prompt 注入

```python
# agent/memory/prompt_injection.py
def build_memory_block(items: list[MemoryItem], *, max_tokens: int) -> str:
    if not items:
        return ""
    lines = ["[用户长期记忆]"]
    used = 0
    for it in items:
        snippet = f"- {it.content}（来源 {it.source.value}，置信度 {it.confidence:.2f}）"
        cost = len(snippet)
        if used + cost > max_tokens:
            break
        lines.append(snippet)
        used += cost
    return "\n".join(lines)
```

### A.8 SSE Adapter

```python
# agent/streaming/protocol.py
import orjson

def format_sse(event: StreamEvent) -> bytes:
    payload = orjson.dumps(event.model_dump())
    return (
        f"id: {event.request_id}\n"
        f"event: {event.type.value}\n"
        f"data: {payload.decode('utf-8')}\n\n"
    ).encode("utf-8")
```

### A.9 FastAPI SSE 端点

```python
# service/api.py
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from agent.streaming import StreamEvent, format_sse
from agent.streaming.tokens import adapt_event

app = FastAPI()

@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    agent = AgentGraph()

    async def gen():
        async for event in agent.astream(req.question, thread_id=req.thread_id, deadline_s=req.deadline_s):
            yield format_sse(event)
            if event.type.value == "final":
                break

    return StreamingResponse(gen(), media_type="text/event-stream",
                              headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})
```

### A.10 CLI 流式

```python
# agent/__main__.py
def run_stream(agent: AgentGraph, question: str):
    async def _consume():
        async for ev in agent.astream(question):
            sys.stdout.write(_render(ev))
            sys.stdout.flush()
    asyncio.run(_consume())
```

## 附录 B — 与总纲的对应关系

| 手册章节 | 总纲章节 |
| --- | --- |
| 阶段 1~3（异步） | 总纲 §3 协程并发改造 |
| 阶段 4（记忆） | 已无限期延后；历史设计对应总纲 §4，状态见总纲 §4.0 与本手册 §4.A |
| 阶段 5（流式） | 总纲 §5 流式输出改造 |
| 阶段 6（观测） | 总纲 §13 评测观测 |
| 风险登记册 | 总纲 §12.1 技术风险（落地版） |

> 实施过程中遇到非预期问题，请优先回查这三份文档。
