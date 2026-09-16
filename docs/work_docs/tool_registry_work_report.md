# 工作报告 — P1 工具化：RAG/SQL 检索 Tool 封装与工具库

> **项目**：招投标智能助手（zhaotoubiao_demo）  
> **分支**：`feat/async-memory-streaming`（改动未提交，建议新开 `feat/tool-registry` 分支收口）  
> **日期**：2026-08-31  
> **依据文档**：
> - `docs/ai_agent_architecture_upgrade_plan.md` §5 统一工具层 / §6.3 P1 工具化
> - `docs/agent_evolution_comprehensive_blueprint.md` §2.2 Tool Calling
> - 实施计划：RAG/SQL 检索工具化封装 + 工具库（Tool Registry）（已评审通过）
> **本次状态**：工具契约、注册中心、6 个检索工具、Agent 自助调用原型与离线测试全部完成；真实链路（MySQL/Milvus/LLM）冒烟待基础设施就绪后执行。

---

## 1. 改造结论

| 目标 | 结果 | 说明 |
| --- | --- | --- |
| 统一工具契约 `ToolResult` | ✅ 完成 | ok / data / error / metadata 四段式；工具永不向调用方抛异常 |
| 工具库 ToolRegistry | ✅ 完成 | 注册唯一性 / tags 发现 / 白名单过滤导出 / `to_manifest()` 平台化预留 |
| 检索能力 Tool 化（6 个） | ✅ 完成 | RAG 2 个（检索级 + 能力级）+ SQL 4 个（结构化参数，零 LLM 内嵌调用） |
| sync/async 双实现 | ✅ 完成 | `StructuredTool(func=..., coroutine=...)`；异步经 `agent.runtime.run_blocking` 复用 IO 线程池 |
| RAG 仅检索公开入口 | ✅ 完成 | `PublicKnowledgeRAG.retrieve()/retrieve_async()`（不生成答案，返回规范化证据片段） |
| Agent 自助调用原型 | ✅ 完成（默认关闭） | `langgraph.prebuilt.create_react_agent` + 工具库导出；`AGENT_TOOLS_ENABLED=true` 开启 |
| CLI 支撑 | ✅ 完成 | `--list-tools`（无需基础设施）、`--agent-mode`（交互/单次） |
| 配置收口 | ✅ 完成 | 6 个新配置项全部进入 `public_kb/config.py`；环境变量守卫测试通过 |
| 流式可观测 | ✅ 完成 | 工具入口/出口 emit `stage=tool_call` 事件；非流式上下文自动无副作用 |
| 现有节点/图零回归 | ✅ 通过 | 适配器模式，两套调用路径共用同一底层函数；全量回归 316 passed / 2 skipped |

## 2. 交付物

### 2.1 新增

| 文件 | 内容 |
| --- | --- |
| `agent/tools/base.py` | `ToolResult` 契约、错误码体系、`make_tool_result/make_error_result`、`wrap_sync_tool/wrap_async_tool` 兜底包装、`render_tool_content` 行数+字符双重截断 |
| `agent/tools/registry.py` | `ToolMeta` / `ToolRegistry` / `GLOBAL_TOOL_REGISTRY` 单例 / `get_tool_whitelist()`（收口 Settings） |
| `agent/tools/schemas.py` | 6 个工具的 pydantic v2 入参 schema（字段 description 即 LLM 参数说明）+ 表白名单常量 |
| `agent/tools/knowledge.py` | `search_public_kb`（检索级）与 `knowledge_qa`（能力级）注册 |
| `agent/tools/price_db.py` | `query_company_info` / `query_company_penalty` / `query_bid_records` / `search_business_data` 注册；SearchIntent 构造与 P0-11 校验下沉 |
| `agent/tools/__init__.py` | 导出 + `register_default_tools()` 幂等装配 + `get_enabled_tools()` 统一取用入口 |
| `agent/tools/README.md` | 工具清单、契约说明、「新增一个工具」三步指南 |
| `agent/agent_loop.py` | Agent 自助调用原型：`build_tool_agent()`、系统提示词、交互/单次 runner、工具调用轨迹渲染 |
| `test/test_tool_registry.py` | 注册中心单测（6 例）：唯一性 / get / tags 过滤 / 白名单 / to_manifest / 白名单解析 |
| `test/test_tools_contract.py` | 工具契约测试（20 例）：ToolResult 形状、异常兜底、invalid_params、SearchIntent 构造、双通道截断、async 一致性 |
| `test/test_agent_tool_loop.py` | Agent 原型测试（5 例）：Fake LLM 发起 tool_call → 工具执行 → 最终回答；开关与空库守卫；recursion_limit 语义 |

### 2.2 主要修改

| 文件 | 变更 |
| --- | --- |
| `public_kb/rag_engine.py` | 新增公开 `retrieve_async(question, top_k)`（包装 `_ensure_async_pipeline().retrieve_async`）、同步镜像 `retrieve()`（asyncio.run 桥接 + 运行中事件循环守卫，与 `AgentGraph.invoke` 同款契约）、`_normalize_chunks()`（rank/doc_name/chapter/chunk_index/chunk_uid/text/score/metadata） |
| `public_kb/config.py` | 新增 6 个配置字段（`agent_tools_enabled` / `agent_tools_whitelist` / `agent_tool_timeout_s` / `agent_tool_default_top_k` / `agent_tool_max_content_chars` / `agent_loop_max_steps`），全部 `field(default_factory=...)` 风格 |
| `.env.example` | 新增「工具化」配置块（含注释说明） |
| `agent/__main__.py` | 新增 `--list-tools` / `--agent-mode` 参数与 `run_list_tools()` / `run_agent_mode()` 入口函数 |

## 3. 关键设计

### 3.1 统一契约与错误兜底

```python
class ToolResult(TypedDict):
    ok: bool
    data: dict            # {"records": [...]} / {"chunks": [...]} / {"answer": ...}
    error: dict | None    # {"code", "message", "retryable"}
    metadata: dict        # {"tool", "elapsed_s", "source", "queried_tables", "row_count", ...}
```

包装器统一 try/except，按异常类型映射错误码：

| 错误码 | 触发 | retryable |
| --- | --- | --- |
| `invalid_params` | P0-11 校验未通过 / 白名单外表名 / 缺少查询主体 | false |
| `kb_not_initialized` | RuntimeError 含「知识库尚未初始化」 | false |
| `db_unavailable` | ConnectionError / OSError / pymysql 异常 | true |
| `timeout` | TimeoutError 系 | true |
| `internal_error` | 其余全部异常 | true |

与图级 `_with_fallback` 哲学一致：调用方 Agent 拿到结构化错误信息后可自行纠正参数重试，而非裸异常中断。

### 3.2 LLM 可见内容与结构化数据分离

工具以 `response_format="content_and_artifact"` 构建：

- **content**（进入 prompt）：精简 JSON。`records/chunks` 超过 10 行保留前 10 行并追加 `_truncated` 与提示；整体超过 `AGENT_TOOL_MAX_CONTENT_CHARS`（默认 4000）字符追加字符截断标记 —— 对应蓝图 §2.2.4「Tool 数量/返回爆炸导致 prompt 膨胀」风险项；
- **artifact**（程序侧）：完整 `ToolResult`，供流式帧、后续质量门控与审计使用。

### 3.3 工具清单与底层映射（复用不重写）

| 工具 | tags | 底层实现 | 参数要点 |
| --- | --- | --- | --- |
| `search_public_kb` | knowledge/rag/retrieval | `PublicKnowledgeRAG.retrieve_async()` | question, top_k(1~20) |
| `knowledge_qa` | knowledge/rag/qa | `PublicKnowledgeRAG.query()/aquery()` | question |
| `query_company_info` | price/sql/company | `queries._query_company_data()`，sub_route=company_query | company_name* + 行业/地区/状态/时间 |
| `query_company_penalty` | price/sql/risk | `queries._query_penalty_by_company_name()`（精确匹配） | company_name*, top_k |
| `query_bid_records` | price/sql/bidding | `queries._query_bidding_data()` | project_number 或 company_name/purchaser 至少其一 |
| `search_business_data` | price/sql/fallback | `recall._query_tables()`（Milvus 语义→FULLTEXT→LIKE→全表五级降级 + 混合重排） | keywords(1~5)*, exact_tokens, tables 白名单 |

要点：company_name 在 bid_project 表映射为 `successful_bidder` 约束（该表仅开放 purchaser/successful_bidder 两个主体字段，P0-11 语义）；查询前 try/except 包裹 `_normalize_intent_enums`（失败降级原始过滤）。

### 3.4 参数校验下沉与安全边界

- 公司全称必须过 `_is_valid_company_name`、项目编号必须过 `_looks_like_code`，未通过返回 `invalid_params` 并附格式说明（Agent 可纠正重试，而非拿到无差别空结果）；
- 只读、表白名单（company_info / company_penalty / bid_project）、只走 queries/recall 白名单查询路径，不暴露任意 SQL（蓝图 §5.4）；
- 工具绕过 node.py 的引导话术/输出模板/节点级超时，保留检索内核原语义。

### 3.5 Agent 原型（默认关闭）

- `build_tool_agent()` 基于 `langgraph.prebuilt.create_react_agent`（项目依赖仅含 langchain-core，未引入完整 langchain 包，故未迁移至 `langchain.agents.create_agent`；该 API 在 LangGraph V2 移除，届时随依赖升级一并处理，代码内已留注释）；
- 系统提示词约定工具适用场景、invalid_params 纠正重试一次、空结果如实告知、引用来源要求；
- `AGENT_LOOP_MAX_STEPS` 语义为「工具调用轮数」，换算 `recursion_limit = N*2 + 2`（每轮消耗 model+tools 两个超级步）；
- `AGENT_TOOLS_ENABLED=false` 时 `--agent-mode` 给出明确开启提示并退出；`--list-tools` 不受开关限制。

## 4. 配置项（收口 `public_kb/config.py`）

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `AGENT_TOOLS_ENABLED` | false | Agent 自助调用总开关 |
| `AGENT_TOOLS_WHITELIST` | 空 | 逗号分隔工具名；空 = 全部可用 |
| `AGENT_TOOL_TIMEOUT_S` | 20 | 单工具执行超时兜底（秒） |
| `AGENT_TOOL_DEFAULT_TOP_K` | 20 | 检索类工具默认 top_k |
| `AGENT_TOOL_MAX_CONTENT_CHARS` | 4000 | LLM 可见内容截断上限 |
| `AGENT_LOOP_MAX_STEPS` | 6 | Agent 单次任务工具调用轮数 |

## 5. 测试与验证

### 5.1 离线测试（mock，无基础设施依赖）

| 套件 | 用例 | 结果 |
| --- | --- | --- |
| `test/test_tool_registry.py` | 6 | ✅ 全部通过 |
| `test/test_tools_contract.py` | 20 | ✅ 全部通过 |
| `test/test_agent_tool_loop.py` | 5 | ✅ 全部通过 |

### 5.2 全量回归

`python -m pytest test/`（排除 legacy/archive）：**316 passed / 2 skipped**。2 个失败均与本次无关：

| 失败项 | 定性 |
| --- | --- |
| `test_config_centralization.py::test_chat_llm_construction_only_in_llm_factory` | **既有失败（HEAD 上即存在）**：守卫正则 `CHAT_LLM_RE` 把 `embedding_service.py` 的 `_SafeEmbeddings(` 误判为对话模型散落直构；本次新增代码未触碰该文件，且环境变量守卫 `test_env_reads_only_in_config_center` 通过。已建独立修复任务 |
| `test_runtime_smoke.py::test_deadline_remaining_decreases` | 负载敏感时序测试，单独重跑通过（flaky，非本次改动模块） |

### 5.3 CLI 冒烟

`python -m agent --list-tools` 输出 6 个工具（启用 6 / 过滤 0）及参数 schema，无需初始化 LLM。

### 5.4 真实链路冒烟（待执行，需 MySQL/Milvus/.env）

```bash
python -m agent --agent-mode --interactive
# 预期：「招标方式有哪些？」→ search_public_kb / knowledge_qa
#       「XX公司中标了哪些项目？」→ query_bid_records
#       「XX公司有无不良记录？」→ query_company_penalty
# 观察流式 STAGE 事件中的 tool_call 流转与回答引用来源
```

## 6. 决策记录（实施前与需求方确认）

| 决策点 | 结论 |
| --- | --- |
| RAG 工具粒度 | 两个都注册：检索级 `search_public_kb`（Agent 平台默认）+ 能力级 `knowledge_qa` |
| SQL 工具入参形态 | 结构化参数（工具内部零 LLM 调用）；现有节点继续走自己的 NL 意图解析路径 |
| 交付范围 | 工具库 + 工具 + 配置开关 + Agent 原型（默认关闭）；现有节点不改调用路径 |
| 服务化暴露 | 本次不暴露；`to_manifest()` 预留 MCP/OpenAPI 元数据接口 |

## 7. 后续工作（按蓝图排期）

1. **真实链路冒烟**：基础设施就绪后执行 §5.4，并抽样验证工具返回数据质量与引用溯源；
2. **节点接入工具层**（蓝图 P1 收尾）：`knowledge_qa` / `price_inquiry` 节点改为内部调用工具层，消除双入口（需全量回归护航）；
3. **服务化暴露**（平台化阶段）：基于 `to_manifest()` 输出 FastAPI `/tools` 端点或 MCP Server；
4. **工具调用审计落库**：当前仅 logger + metadata，后续接审计表（模板名/参数/行数/耗时，蓝图 §5.4）；
5. **修复既有守卫误报**：`CHAT_LLM_RE` 收窄或白名单补齐（独立任务，不阻塞本线）。

## 8. 文件变更清单

```text
新增:
  agent/tools/__init__.py            agent/tools/base.py
  agent/tools/registry.py            agent/tools/schemas.py
  agent/tools/knowledge.py           agent/tools/price_db.py
  agent/tools/README.md              agent/agent_loop.py
  test/test_tool_registry.py         test/test_tools_contract.py
  test/test_agent_tool_loop.py

修改:
  public_kb/config.py                # +6 配置字段
  public_kb/rag_engine.py            # +retrieve/retrieve_async/_normalize_chunks
  agent/__main__.py                  # +--list-tools / --agent-mode
  .env.example                       # +工具化配置块
```
