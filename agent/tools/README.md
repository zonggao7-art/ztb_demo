# agent/tools — 统一工具层（P1 工具化）

把 RAG 检索与 SQL 检索封装为标准 LangChain Tool，由 `ToolRegistry` 统一维护，
为 Agent 平台化（LLM 自助调用工具）提供基础。设计对齐
`docs/ai_agent_architecture_upgrade_plan.md` §5/§6.3 与
`docs/agent_evolution_comprehensive_blueprint.md` §2.2。

## 工具清单（第一批）

| 工具 | 类型 | 底层实现 | 说明 |
|---|---|---|---|
| `search_public_kb` | RAG 检索级 | `PublicKnowledgeRAG.retrieve_async()` | 返回 top-K 法规证据片段（含 chunk_uid/章节/分数），调用方自行组织答案 |
| `knowledge_qa` | RAG 能力级 | `PublicKnowledgeRAG.query()/aquery()` | 完整问答（含拒答判断与【来源N】标准化引用） |
| `query_company_info` | SQL | `queries._query_company_data()` | 企业工商情报（company_info） |
| `query_company_penalty` | SQL | `queries._query_penalty_by_company_name()` | 企业行政处罚（精确匹配，最安全） |
| `query_bid_records` | SQL | `queries._query_bidding_data()` | 中标记录（project_number / company_name / purchaser 三种主体） |
| `search_business_data` | SQL | `recall._query_tables()` | 关键词兜底检索三张核心表（多级降级召回链） |

## 统一契约

所有工具返回 `ToolResult`（`base.py`）：

```python
{"ok": bool, "data": {...}, "error": None | {"code", "message", "retryable"}, "metadata": {...}}
```

- **永不抛异常**：`wrap_sync_tool/wrap_async_tool` 统一兜底，错误码
  `invalid_params / kb_not_initialized / db_unavailable / timeout / internal_error`
- **双通道**：`response_format="content_and_artifact"` —
  content 为 LLM 可见精简 JSON（行数 + 字符双重截断防 prompt 膨胀），
  artifact 为完整 ToolResult
- **sync/async 双实现**：`StructuredTool(func=..., coroutine=...)`，
  异步路径经 `agent.runtime.run_blocking` 复用 IO 线程池
- **流式可观测**：工具入口/出口 emit `stage=tool_call` 事件（非流式上下文自动无副作用）
- **安全边界**：只读、表白名单（三张核心表）、只走 queries/recall 白名单查询路径，
  不暴露任意 SQL（蓝图 §5.4）

## 如何使用

```python
from agent.tools import get_enabled_tools

tools = get_enabled_tools()              # 注册 + 白名单过滤
llm_with_tools = llm.bind_tools(tools)   # 交给任意 tool-calling Agent
```

CLI 验证：

```bash
python -m agent --list-tools             # 查看工具库清单（无需 LLM/基础设施）
python -m agent --agent-mode --interactive   # Agent 自助调用模式（需 AGENT_TOOLS_ENABLED=true）
```

## 如何新增一个工具（3 步）

1. **定义入参 schema**：在 `schemas.py` 添加 pydantic v2 模型
   （字段 description 会直接成为 LLM 的参数说明，以「让 Agent 一次填对」为标准）；
2. **实现 func + coroutine**：在对应模块写两个同名函数，签名与 schema 字段一致，
   返回 `ToolResult`（用 `make_tool_result` / `make_error_result`）；
3. **注册**：在 `register_xxx_tools()` 中用
   `StructuredTool.from_function(func=wrap_sync_tool(name, fn), coroutine=wrap_async_tool(name, afn), args_schema=..., name=..., description=..., response_format="content_and_artifact")`
   构建并 `registry.register(tool, ToolMeta(name, description, tags=frozenset({...})))`。

约束：入参必须结构化（工具内部零 LLM 调用）；底层必须复用既有查询路径，不在工具层重写业务逻辑；
公司名/项目编号等实体入参必须过 `_is_valid_company_name` / `_looks_like_code` 校验（P0-11 下沉）。

## 配置（收口 `public_kb/config.py`，.env 驱动）

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `AGENT_TOOLS_ENABLED` | false | Agent 自助调用总开关（`--agent-mode` 需要 true） |
| `AGENT_TOOLS_WHITELIST` | 空 | 逗号分隔工具名；空 = 全部可用 |
| `AGENT_TOOL_TIMEOUT_S` | 20 | 单工具执行超时兜底（秒） |
| `AGENT_TOOL_DEFAULT_TOP_K` | 20 | 检索类工具默认 top_k |
| `AGENT_TOOL_MAX_CONTENT_CHARS` | 4000 | LLM 可见内容截断上限 |
| `AGENT_LOOP_MAX_STEPS` | 6 | Agent 原型单次任务工具调用轮数 |

## 测试

```bash
python -m pytest test/test_tool_registry.py test/test_tools_contract.py test/test_agent_tool_loop.py -v
```

均离线运行（mock 底层查询与 RAG 引擎），无 MySQL/Milvus/LLM 依赖。

## 后续平台化预留

- `ToolRegistry.to_manifest()` 已输出 name/description/tags/parameters 元数据，
  可直接映射 MCP tool manifest 或 OpenAPI operation
- 现有节点接入工具层、FastAPI `/tools` 端点、MCP Server 见主计划的「明确不做」清单
