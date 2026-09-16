# 阶段 4 改造指南：持久会话与长期记忆 MVP

> 目标范围来自 `docs/implementation_handbook_async_memory_streaming.md` 的“阶段 4 — 长期记忆”，并结合 `docs/project_refactoring_master_plan.md` 的长期记忆分层设计收敛为 MVP。
>
> 本阶段只交付四件事：异步/持久 Checkpointer、显式长期记忆 CRUD、Prompt 注入、保守的候选记忆抽取。**不做语义向量记忆、不做跨用户画像、不引入新 State 字段。**

## 0. 当前基线与边界

- 现状：`AgentGraph.__init__()` 固定调用 `create_checkpointer(checkpointer_backend)`，同步工厂没有异步生命周期；项目尚无 `agent/memory/` 目录。
- 已有配置：`public_kb/config.py` 已包含 `MEMORY_ENABLED`、`MEMORY_STORE_BACKEND`、`MEMORY_SQLITE_PATH`、`MEMORY_PG_DSN` 等字段；`.env.example` 已有对应项。
- 已有依赖：`requirements.txt` 已声明 `langgraph-checkpoint>=2.0.0`、`asyncpg>=0.29`、`aiosqlite>=0.20`；执行前确认已安装 `langgraph-checkpoint-sqlite` 和 `langgraph-checkpoint-postgres`。
- 设计红线：
  1. `AgentState` 继续只保留 `messages / router_intent / business_result` 三个字段；
  2. `user_id` 不写入 `AgentState`，先通过 `configurable` 或请求级上下文传递；
  3. 记忆读写失败不能阻断主问答路径；
  4. 所有 Store 查询必须带 `user_id` 强过滤；
  5. 默认关闭自动抽取，`MEMORY_ALLOW_EXTRACTED=false` 时候选记忆只能入库为待确认状态或直接丢弃。

## 1. 目标产物

```text
agent/
  checkpointer.py                 # 新增 AsyncCheckpointerFactory 与 setup/close 工具
  graph.py                        # AgentGraph 支持配置化后端 + 异步初始化
  __main__.py                     # 接入 --user-id / --memory CLI
  memory/
    __init__.py
    models.py                     # MemoryItem、MemoryCategory、MemorySource、MemoryCandidate
    store.py                      # AsyncMemoryStore Protocol + 关键词搜索实现
    sqlite_store.py               # SQLite aiosqlite 实现
    postgres_store.py             # PostgreSQL asyncpg 实现
    service.py                    # 请求级编排：读取、注入、抽取、落库
    prompt_injection.py           # build_memory_block()
    extractor.py                  # LLM 候选抽取
    routes.py                     # 可选 FastAPI APIRouter，本项目暂未接入 FastAPI
scripts/
  migrate_memory.py               # 建表 / 校验 / dry-run 导入历史数据
test/
  test_memory_models.py
  test_memory_store_sqlite.py
  test_memory_store_postgres.py   # PostgreSQL 路径 mock 测试，可选 live 测试用 marker 隔离
  test_memory_prompt.py
  test_memory_extractor.py
  test_checkpointer_async.py
docs/stage4_work_report.md
```

## 2. 分步实施

### Step 0 — 先建测试基线

在改代码前先固化当前结果：

```powershell
python -m pytest test/test_bug_repairs.py test/test_p0_memory_or_related_tests.py -q
python -m pytest test/ -q --maxfail=1
```

若第二个命令当前已存在与环境相关的预存失败，把失败清单写进 `docs/stage4_work_report.md` 的“改造前基线”章节。不要在本阶段顺手修复无关测试。

推荐另建工作分支：

```powershell
git switch -c codex/stage4-memory-mvp
```

### Step 1 — 固定数据契约与模型

创建 `agent/memory/models.py`，建议使用 Pydantic v2 模型，字段和约束如下：

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `id` | `UUID` | 服务端生成，不可由外部指定 |
| `user_id` | `str` | 非空，长度 ≤64，所有查询的强制租户条件 |
| `category` | `Literal["preference", "region", "industry", "company", "project", "note"]` | 只允许白名单值 |
| `source` | `Literal["explicit", "extracted", "confirmed"]` | `extracted` 必须经过确认才能参与 Prompt 注入 |
| `content` | `str` | 非空，≤1000 字符；写入前去空白 |
| `tags` | `list[str]` | 可选，保存为 JSON 数组 |
| `confidence` | `float` | `[0,1]`；显式用户输入固定 `1.0` |
| `valid_from` / `valid_until` | aware datetime / `None` | 注入前过滤过期项 |
| `confirmed` | `bool` | 默认：`explicit=True`，`extracted=False` |
| `created_at` / `updated_at` | aware datetime | UTC 存储，序列化输出 ISO8601 |
| `version` | positive int | 冲突更新时递增 |

补充契约：

- `MemorySource.explicit` 的 `confirmed=True`；`MemorySource.extracted` 在未被确认前不得进入 Prompt；
- `category` 白名单不要开放自由字符串，防止变成无治理的用户画像表；
- 序列化和反序列化的时间必须带 UTC offset，避免 SQLite 本地时间漂移。

### Step 2 — 定义 Async Store 抽象

创建 `agent/memory/store.py`：

```python
class AsyncMemoryStore(Protocol):
    async def setup(self) -> None: ...
    async def close(self) -> None: ...
    async def upsert(self, item: MemoryItem) -> MemoryItem: ...
    async def get(self, user_id: str, memory_id: UUID) -> MemoryItem | None: ...
    async def list(
        self,
        user_id: str,
        *,
        category: str | None = None,
        include_unconfirmed: bool = False,
    ) -> list[MemoryItem]: ...
    async def search(
        self,
        user_id: str,
        query: str,
        *,
        top_k: int = 5,
    ) -> list[MemoryItem]: ...
    async def delete(self, user_id: str, memory_id: UUID) -> bool: ...
```

MVP 的 `search()` 只做关键词匹配和 tag 过滤，禁止把公共知识库 Milvus collection 复用给个人记忆：

1. 取该用户最近 N 条有效记忆（N 建议 500~1000）；
2. 对 `content` 和 tags 做大小写无关关键词匹配；
3. 按“标签命中 > 内容命中 > updated_at 倒序”排序；
4. 截断到 `top_k`；
5. 所有分支都必须保留 `WHERE user_id = ?`。

SQL 数据层注意：

- 表名定为 `memory_items_v1`，为未来 schema 演进留隔离空间；
- SQLite 版本使用 `aiosqlite.connect(...)`、`PRAGMA journal_mode=WAL`、`PRAGMA busy_timeout=3000`；
- PostgreSQL 版本使用 `asyncpg.create_pool(min_size=1, max_size=5)`；
- 每次 SQL 都必须参数化绑定，不接受拼接条件；
- SQLite 中 `tags` 存 JSON 字符串，PostgreSQL 用 `JSONB`；
- 时间列统一存 UTC ISO 字符串或 `TIMESTAMPTZ`；返回模型前统一转为 aware UTC datetime。

### Step 3 — 建 Schema 与迁移脚本

创建 `scripts/migrate_memory.py`，支持三个子命令：

```text
python scripts/migrate_memory.py init          # 创建/升级 schema
python scripts/migrate_memory.py doctor        # 检查索引、外键、时间格式、孤儿记录
python scripts/migrate_memory.py import-jsonl path [--dry-run] [--user-id ...]
```

SQLite DDL 参考：

```sql
CREATE TABLE IF NOT EXISTS memory_items_v1 (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  category TEXT NOT NULL,
  source TEXT NOT NULL,
  content TEXT NOT NULL CHECK (length(content) <= 1000),
  tags TEXT NOT NULL DEFAULT '[]',
  confidence REAL NOT NULL CHECK (confidence >= 0 AND confidence <= 1),
  valid_from TEXT,
  valid_until TEXT,
  confirmed INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1)
);

CREATE INDEX IF NOT EXISTS idx_memory_items_v1_user_cat
  ON memory_items_v1(user_id, category);

CREATE INDEX IF NOT EXISTS idx_memory_items_v1_user_updated
  ON memory_items_v1(user_id, updated_at DESC);
```

PostgreSQL DDL 参考：

```sql
CREATE TABLE IF NOT EXISTS memory_items_v1 (
  id UUID PRIMARY KEY,
  user_id VARCHAR(64) NOT NULL,
  category VARCHAR(32) NOT NULL,
  source VARCHAR(32) NOT NULL,
  content TEXT NOT NULL,
  tags JSONB NOT NULL DEFAULT '[]'::jsonb,
  confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  valid_from TIMESTAMPTZ,
  valid_until TIMESTAMPTZ,
  confirmed BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1)
);

CREATE INDEX IF NOT EXISTS idx_memory_items_v1_user_cat
  ON memory_items_v1(user_id, category);

CREATE INDEX IF NOT EXISTS idx_memory_items_v1_user_updated
  ON memory_items_v1(user_id, updated_at DESC);
```

`init` 和 `doctor` 只允许创建/检查结构，不得删除数据。导入 JSONL 时逐行校验；单条失败要统计错误并继续，最后返回退出码非零。

### Step 4 — 升级 Checkpointer

修改 `agent/checkpointer.py`，新增独立的异步工厂，不建议破坏现有 `create_checkpointer()` 同步接口：

```python
async def create_async_checkpointer(
    backend: str = "memory",
    *,
    connection_string: str | None = None,
) -> BaseCheckpointSaver:
    ...
```

实现规则：

1. `memory` 继续返回 `MemorySaver()`，无需 `setup()`；
2. `sqlite` 使用 `AsyncSqliteSaver.from_conn_string(path)`，进入上下文后执行 `.setup()`；
3. `postgres` 使用 `AsyncPostgresSaver.from_conn_string(dsn)`，进入上下文后执行 `.setup()`；
4. 返回类型可以是 saver 实例加上其自身的生命周期要求；如果当前 LangGraph 版本必须持有 context manager，则新增一个 `CheckpointerHandle`，至少暴露 `saver`、`setup()`、`close()`；
5. 初始化失败立即抛错，不允许静默降级成 `MemorySaver`；
6. 生产 `CHECKPOINTER_BACKEND=postgres` 时必须提供 DSN；本地开发可用 SQLite。

修改 `agent/graph.py`：

- `build_graph()` 保持接收现成 `checkpointer` 实例，不做数据库初始化；
- `AgentGraph.__init__()` 改为从 `settings.checkpointer_backend` 读取默认后端；
- 若初始化发生在 `asyncio.run()` 外的普通同步入口，可以先构建事件循环内完成 setup，再保留 loop/session 供后续调用复用；如果这样改动风险过大，本阶段允许暂缓 `__init__` 自动加载，改为由新的异步入口显式初始化；
- `ainvoke()`、`astream()` 前确保 checkpointer 已 setup；
- 进程退出时释放数据库型 checkpointer。

验收命令：

```powershell
CHECKPOINTER_BACKEND=memory python -m agent --question "招标方式有哪些？" --async
CHECKPOINTER_BACKEND=sqlite CHECKPOINTER_SQLITE_PATH=data/checkpoints-dev.db python -m agent --question "招标方式有哪些？"
```

再重复同一 `thread_id` 的两轮对话，重启进程后让第二轮依赖第一轮指代，检查答案是否还能还原上下文。

### Step 5 — 显式记忆 CRUD

优先做纯 CLI，因为仓库当前没有 FastAPI 应用。

新增子命令：

```powershell
python -m agent --memory list --user-id u123
python -m agent --memory upsert --user-id u123 --category preference --content "优先看江苏省的项目"
python -m agent --memory get     --user-id u123 --memory-id <uuid>
python -m agent --memory search  --user-id u123 --query 江苏
python -m agent --memory delete  --user-id u123 --memory-id <uuid>
python -m agent --memory confirm --user-id u123 --memory-id <uuid>
```

实现要求：

1. CLI 一次性操作可直接 `asyncio.run(...)`；
2. 输出必须显示 `id/category/source/confidence/confirmed/content/updated_at`；
3. `delete` 后打印被删除内容摘要，便于人工确认；
4. Postgres 和 SQLite 的 CLI 行为保持一致；
5. `MEMORY_ENABLED=false` 时普通问答不读写长期记忆，但管理类 CLI 可以显式访问；
6. 不在 CLI 里接受任意 category，仍走白名单校验。

后续若有 FastAPI 层，再补 `agent/memory/routes.py`：

```text
GET    /users/{user_id}/memories
POST   /users/{user_id}/memories
GET    /users/{user_id}/memories/{memory_id}
PATCH  /users/{user_id}/memories/{memory_id}
DELETE /users/{user_id}/memories/{memory_id}
POST   /users/{user_id}/memories/search
```

API 鉴权层负责将登录身份映射为 `user_id`，绝不能信任 query/body 中的裸 `user_id` 作为授权依据。

### Step 6 — Prompt 注入器

创建 `agent/memory/prompt_injection.py`：

```python
def build_memory_block(items: list[MemoryItem], *, max_tokens: int = 400) -> str:
    """只接受有效、已确认且未过期的记忆。"""
```

注入模板示例：

```text
[用户长期记忆]
- 关注地区：江苏省（来源：用户确认，有效期至 2027-12-31）
- 常关注行业：环保设备（来源：用户确认）
```

实现细节：

1. 模型阶段先用 DeepSeek 常规 chat context 上限粗估即可；MVP 可按中文近似公式 `token 数 ≈ ceil(len(text) * 0.75)`，并预留 `max_tokens * 0.15` 给标题和安全余量；
2. 超长时丢弃低置信度项，剩余同置信度时丢弃较旧项；
3. 不截断单条记忆中段；如果一条放不下就整条丢弃；
4. 输出必须标明来源和有效期；
5. 单元测试覆盖空列表、过期记忆、超预算截断、黑名单内容拒收、来源展示；
6. 为了稳定测试，不要在 `build_memory_block()` 内部实时读取时钟，把 `now` 作为参数传入。

### Step 7 — 节点级安全接入

不建议为了记忆新增业务节点或在图中插一个新的前置节点。最小侵入方案如下：

1. 创建 `agent/memory/service.py`；
2. `AgentGraph.ainvoke()` 解析本次调用的 `user_id`：
   - 方法签名可扩展为 `user_id: str | None = None`；
   - 缺省可用 `thread_id` 派生一个明确的 CLI 用户标识，例如 `"cli:<stable-user-id>"`；但要避免多个真实用户共享同名默认 ID；
3. `MEMORY_ENABLED=true` 时，在图调用前异步并行读取 `service.load_prompt_memories(user_id)`；
4. 将组装好的 memory block 通过运行时上下文传给允许消费记忆的节点；如果项目暂没有通用 context carrier，则先只在支持环境变量的 LLM prompt wrapper 中做局部传递，但仍然不要污染 `AgentState`；
5. 记忆读取失败时记录 warning 并继续，不让主流程降级到 fallback；
6. 响应数据可以先不额外返回 `used_memories`；如需调试，通过 verbose 日志打印命中的 memory id；
7. 询价节点现阶段主要注入偏好类信息用于自然语言追问/总结，不改 SQL 条件生成逻辑；只有 SQL builder 明确接入受控地区/行业过滤后，才允许影响检索。

这一步的目标是低风险体验增强，不是让长期记忆直接控制业务查询。任何会把记忆文本拼进 SQL WHERE 子句的行为都必须放在后续专门评审。

### Step 8 — LLM 抽取器

创建 `agent/memory/extractor.py`：

输入：最近一轮或多轮对话。

输出：

```python
class MemoryCandidate(BaseModel):
    category: MemoryCategory
    content: str
    confidence: float
    reason: str
```

LLM prompt 要求：

1. 只抽取稳定事实，不抽取临时任务：“这次帮我查山东”不是长期事实；
2. 只允许白名单 category：偏好、地区、行业、公司、项目、备注；
3. 不抽取身份证号、手机号、银行卡号、住址、密码、密钥、财务账号等敏感字段；
4. 不确定就返回空数组；
5. 相近内容合并；
6. 每条给出置信度和简短理由。

落地策略：

1. 默认 `MEMORY_ALLOW_EXTRACTED=false`：抽取器可以产出诊断结果，但不自动转正；
2. 默认开启触发词过于激进不好验证，因此首轮只在 `general_chat` 成功完成后调用一次；知识问答/询价建议第二阶段再评估召回质量后再接；
3. `confidence >= settings.memory_min_confidence` 且不含敏感模式时才生成待确认记录；
4. `confirmed=False` 的 extracted 记录不会被 `load_prompt_memories()` 读取；
5. 用户可通过 CLI `confirm` / `delete` 管理；
6. 整个 extractor 用 try/except 包裹，失败只记 warning。

简单敏感检测可以使用正则和关键词黑名单；但要清楚它只是最低门槛，后续应引入更完整的 PII 审计。

### Step 9 — 配置补齐

确认以下行为并在必要时修正现有字段：

```env
MEMORY_ENABLED=true|false
MEMORY_STORE_BACKEND=sqlite|postgres
MEMORY_SQLITE_PATH=./data/memory-dev.db
# MEMORY_PG_DSN=postgresql://user:pass@host:5432/dbname
MEMORY_MAX_INJECTION_TOKENS=400
MEMORY_MIN_CONFIDENCE=0.7
MEMORY_ALLOW_EXTRACTED=false

CHECKPOINTER_BACKEND=memory|sqlite|postgres
CHECKPOINTER_SQLITE_PATH=./data/checkpoints-dev.db
# CHECKPOINTER_POSTGRES_DSN=postgresql://user:pass@host:5432/langgraph
```

代码级要求：

1. 数据库文件默认放到 `data/` 下，并将 `data/` 加入 `.gitignore`；
2. 不要把真实 DSN 或密码写进 `.env.example`；
3. SQLite 连接数量应小于等于 2；生产推荐 Postgres；
4. Postgres pool 上限设为小值，避免长期记忆和业务池互相挤占连接；
5. 所有布尔开关必须支持 `1/true/yes`，沿用现有 Settings 解析风格。

### Step 10 — 测试计划

必测用例按模块拆开：

| 文件 | 重点 |
| --- | --- |
| `test/test_memory_models.py` | category 白名单、内容长度、confidence、UTC 时间、来源与确认态一致性 |
| `test/test_memory_store_sqlite.py` | CRUD、tag 过滤、并发 upsert、user_id 隔离、版本递增 |
| `test/test_memory_store_postgres.py` | mock asyncpg 参数、DDL 幂等、user_id 强过滤；live case 用环境变量跳过 |
| `test/test_memory_prompt.py` | 过期过滤、预算截断、来源标注、unconfirmed/extracted 不注入 |
| `test/test_memory_extractor.py` | mock LLM 输出：白名单、敏感内容拒绝、低置信度不落库、异常 fail-soft |
| `test/test_checkpointer_async.py` | memory/sqlite/postgres 工厂切换、setup 幂等、进程恢复 smoke |
| `test/test_agent_memory_integration.py` | enabled/disabled、store 抛异常不影响问答、不同 user_id 不命中对方记忆 |

禁止只有 happy path。以下回归必须有断言：

1. A 用户写入“江苏”，B 用户搜索不到；
2. `extracted + confirmed=False` 不出现在 Prompt；
3. Store raise 异常时 Agent 正常返回原答案；
4. 同一 user/memory 并发更新两次，version 从 1 递增到 2；
5. 注入块严格小于等于 `MEMORY_MAX_INJECTION_TOKENS`；
6. 删除后 get/list/search 都取不到；
7. 重启进程后同一个 thread_id 能读回 checkpoint 历史状态。

本地执行顺序：

```powershell
python -m pytest test/test_memory_models.py test/test_memory_prompt.py -q
python -m pytest test/test_memory_store_sqlite.py -q
python -m pytest test/test_memory_extractor.py test/test_agent_memory_integration.py -q
python -m pytest test/test_checkpointer_async.py -q
python -m pytest test/ -q
```

### Step 11 — 手工验收脚本

准备两个终端和两个独立用户标识。

#### A. 显式记忆

```powershell
python -m agent --memory upsert --user-id alice --category region --content "长期关注江苏省政府采购项目"
python -m agent --memory list --user-id alice
```

预期：能列出一条 `source=explicit`、`confirmed=true`、`valid_until=None` 的记忆。

#### B. 用户隔离

```powershell
python -m agent --memory search --user-id bob --query "江苏"
python -m agent --memory search --user-id alice --query "江苏"
```

预期：bob 结果为空，alice 至少命中一条。

#### C. 会话持久化

```powershell
$env:CHECKPOINTER_BACKEND="sqlite"
python -m agent --interactive
```

对话：

```text
第 1 轮：我想了解江苏省公开招标的金额门槛。
quit
python -m agent --interactive
第 2 轮：刚才说的金额门槛适用于哪种采购方式？
```

预期：第二轮能理解“刚才”指向江苏省公开招标，而不是要求重新描述全部上下文。

#### D. Prompt 注入

启动 verbose 日志后询问一句需要偏好的问题，例如“有哪些近期适合我关注的方向？”

预期日志能看到读取到的 memory id 和构建后的 memory block；回答不应把其他用户记忆当作用户偏好。

#### E. 自动抽取隔离

保持 `MEMORY_ALLOW_EXTRACTED=false`，在 general_chat 中说“请记住：我经常对比浙江和江苏环保项目的价格”。

预期：不会出现直接参与 Prompt 的 extracted 记忆；打开 extractor 日志只能看到待确认候选或安全拒绝原因。

## 3. 验收标准

满足以下条件才算阶段 4 完成：

1. `CHECKPOINTER_BACKEND=sqlite` 下，重启进程后同一 `thread_id` 的会话历史可恢复；
2. `CHECKPOINTER_BACKEND=postgres` 时 saver 初始化、schema setup、读回均有集成测试或明确标记的 live 手工验收记录；
3. 显式记忆 CRUD 全通，CLI 行为一致；
4. 不同 `user_id` 数据强隔离，交叉读写测试全部通过；
5. Prompt 注入不超过配置上限，且每条展示来源和有效期；
6. `extracted + confirmed=false` 记录绝不进入 Prompt；
7. 长期记忆服务异常不影响原有四个业务能力和 fallback；
8. 新增代码具备单元测试和核心集成测试，全量测试结果不低于改造前基线；
9. 回滚开关生效：`MEMORY_ENABLED=false` + `CHECKPOINTER_BACKEND=memory` 可完全回到阶段 3 行为；
10. 生成 `docs/stage4_work_report.md`，记录变更清单、测试证据、遗留问题和回滚步骤。

## 4. 提交切分建议

按以下粒度提交，方便审阅和回滚：

1. `feat(memory): add models and sqlite store contracts`
2. `feat(memory): add persistent migration and doctor tooling`
3. `feat(memory): add prompt injection rules`
4. `feat(agent): add async checkpointer lifecycle`
5. `feat(cli): add explicit long-term memory commands`
6. `feat(agent): wire safe memory read path`
7. `feat(memory): add conservative candidate extractor`
8. `test(memory): cover isolation, persistence and failure paths`
9. `docs: add stage 4 work report`

每个 commit 都必须能在不启用记忆的情况下跑完已有主链路。

## 5. 风险与处置

| 风险 | 影响 | 处置 |
| --- | --- | --- |
| LangGraph saver 版本的 async context manager 兼容差异 | AgentGraph 构造方式复杂化 | 先为 saver 写最小 spike 测试；必要时封装 `CheckpointerHandle` |
| 同步 CLI 反复建立事件循环/连接 | 性能浪费或 Windows 句柄问题 | CLI 单命令短连接触发，交互模式下共享 service/checkpointer handle |
| 错误记忆污染回答 | 专业问答可信度下降 | 只注入确认态、高置信度、白名单类别；提供可见可删入口 |
| 跨用户泄露 | 合规事故 | 所有 SQL 带 user_id，测试专查 A/B 隔离，API 不信任裸 user_id |
| SQLite 并发锁 | 写入偶发失败 | WAL + busy timeout + 失败重试一次；生产切 Postgres |
| Postgres 连接膨胀 | 影响业务数据库 | 独立 database/schema，pool max_size<=5 |
| 自动抽取幻觉/PII | 隐私和质量风险 | 默认关闭自动转正、白名单字段、敏感正则、待确认队列 |
| Checkpointer schema 与业务记忆耦合 | 后续迁移困难 | Checkpointer 表交给 LangGraph 管理；业务记忆单独 `memory_items_v1` |

## 6. 最终回滚方案

配置级回滚：

```env
ASYNC_BACKEND_ENABLED=false
CHECKPOINTER_BACKEND=memory
MEMORY_ENABLED=false
MEMORY_ALLOW_EXTRACTED=false
STREAM_ENABLED=false
```

如果需要进一步回到阶段 3 图形态，可直接设置：

```env
ASYNC_BACKEND_ENABLED=false
```

这会使 `AgentGraph(async_enabled=None)` 注册同步 price_inquiry/knowledge_qa 节点。长期记忆的新增文件即使保留也不应被执行。

仅回滚记忆注入而不影响持久 checkpoint 时，只改：

```env
MEMORY_ENABLED=false
```

## 7. 执行顺序速览

1. 建立 Git 分支和测试基线；
2. 写 `models.py` 和 `store.py`，先锁契约定测试；
3. 做 SQLite Store + migration/doctor；
4. 补 Prompt injection 与单元测试；
5. 做 Async Checkpointer spike，再决定 `AgentGraph` 的最优生命周期方案；
6. 加显式记忆 CLI；
7. 安全接入读取路径，跑手工验收 A-D；
8. 开发候选抽取器和待确认队列，跑手工验收 E；
9. 按需补 Postgres Store；
10. 全量回归、写工作报告、确认回滚。

> 简化执行：如果你希望先把最低价值闭环跑起来，可先完成 Step 0~7，其中 Postgres 可以留到确认 SQLite/MemorySaver 行为后再做。提取器（Step 8）是最后一个高风险增量，只有在前面全部验收后再接主链路。
