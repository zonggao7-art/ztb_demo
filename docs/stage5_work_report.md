# 工作报告 — 阶段5：流式输出改造

> **项目**：招投标智能助手（zhaotoubiao_demo）  
> **分支**：`feat/async-memory-streaming`  
> **日期**：2026-08-27  
> **依据文档**：
> - `docs/stage5_refactor_guide.md`
> - `docs/implementation_handbook_async_memory_streaming.md` §阶段5
> **本次状态**：协议层、Graph custom 流、四类业务帧、CLI、FastAPI SSE 与离线测试已完成。

---

## 1. 改造结论

| 目标 | 结果 | 说明 |
| --- | --- | --- |
| 统一 `StreamEvent` 契约 | ✅ 完成 | 新增规范事件并保留旧事件兼容；不改变现有 envelope 字段 |
| SSE 协议闭环 | ✅ 完成 | 支持 format / parse / 黏包拆包 / heartbeat / 错误帧 |
| LangGraph 节点流式通道 | ✅ 完成 | 使用 `stream_mode="custom"` 替代不稳定的外层事件重包 |
| Graph 输出稳定 envelope | ✅ 完成 | `AgentGraph.astream()` 只输出统一 StreamEvent |
| 业务节点流式适配 | ✅ 最小可用闭环完成 | Knowledge QA、Price Inquiry、General Chat、Doc QA、Fallback 均有终态 |
| CLI 流式 | ✅ 完成 | 新增 `--stream` / `--timeout` 和分帧渲染 |
| FastAPI SSE | ✅ 骨架完成 | `/chat/stream` + heartbeat + 客户端断开日志；共享 AgentGraph |
| 离线回归 | ✅ 通过 | 新增流式测试 21 通过；全量回归 280 passed / 2 skipped，4 个既有 402 环境失败 |

## 2. 交付物

### 2.1 新增

| 文件 | 内容 |
| --- | --- |
| `agent/streaming/context.py` | 绑定 request_id；基于 LangGraph stream writer 的节点级 emit 通道 |
| `service/__init__.py` / `service/schemas.py` / `service/api.py` | FastAPI 应用、请求契约与 `/chat/stream` SSE endpoint |
| `test/test_streaming_protocol.py` | 全事件往返、多帧 chunk、半个 frame 拆包、id 校验、heartbeat |
| `test/test_graph_astream.py` | astream 的 request_id、META 唯一、引用先于 FINAL、终态约束 |
| `test/test_knowledge_qa_stream.py` | RAG event relay 和 business_result 兼容结构 |
| `test/test_price_inquiry_stream.py` | 表召回进度回调的 partial/final 行为 |
| `test/test_cli_stream.py` | stage/token 渲染行为 |

### 2.2 主要修改

| 文件 | 变更 |
| --- | --- |
| `agent/streaming/events.py` | 扩展 `META/TABLE/PARTIAL/CANCELLED` 等规范事件；保留 `ROUTER/MESSAGE` 兼容；迁移到 Pydantic v2 config |
| `agent/streaming/protocol.py` | 新增 `make_event()`、错误/心跳工厂、SSE 解析器、异步流解析器和 custom event normalizer |
| `agent/streaming/__init__.py` | 导出阶段5公共 API |
| `agent/router.py` | 异步路由发送 `router_done`，降级时带 `degraded=true` |
| `agent/graph.py` | 重写 `astream()` 使用 `stream_mode="custom"`；统一 request_id、兜底 META、ERROR 终态和 context 清理；异步注册 General Chat / Doc QA / Fallback |
| `agent/nodes/knowledge_qa_async.py` | 消费 `PublicKnowledgeRAG.astream()`；RAG 无 astream 时回退 `aquery()`；citations/sources 结构不变 |
| `agent/nodes/price_inquiry/recall_async.py` | `query_tables_async()` 增加 progress callback，产出表级 partial/final 数据 |
| `agent/nodes/price_inquiry/node_async.py` | 发送 intent/sql 进度、TABLE 帧、合成 token 和唯一 FINAL；保留原守卫语义 |
| `agent/nodes/general_chat.py` / `doc_qa.py` / `fallback.py` | 提供异步流式适配与终态帧 |
| `agent/__main__.py` | 新增 CLI streaming 渲染、取消处理、终态校验 |
| `requirements.txt` | 补充 `fastapi`、`uvicorn` |

## 3. 关键设计

### 3.1 custom 流

手册原文建议监听 `astream_events(v2)`；实际实现改用 LangGraph `stream_mode="custom"`。原因：

- 项目已有业务级 `StreamEvent`；
- 内部事件回调会暴露 router/工具层细节，外层过滤和重包容易造成脆断言；
- LangGraph custom writer 会把对象透传给 `astream()` 且线程安全；
- 节点仍正常返回原 state 更新，业务结果零变化。

入口对外只 yield `StreamEvent`；不一致 request_id 由 normalizer 强制校正。

### 3.2 事件合同

新代码只使用：`META / STAGE / TOKEN / RETRIEVAL / CITATIONS / TABLE / PARTIAL / FINAL / ERROR / CANCELLED / HEARTBEAT`。

过渡兼容保留：`ROUTER / MESSAGE`，但禁止新路径继续产生。

Knowledge QA 的关键顺序固定为：

```text
STAGE(retrieval_start) → RETRIEVAL → TOKEN* → CITATIONS → FINAL
```

Price Inquiry 的最小闭环为：

```text
META → STAGE(intent_done) → STAGE(sql_start) → TABLE* → TOKEN(synthetic)* → FINAL
```

所有普通请求必须以 `FINAL`、Graph 异常以 `ERROR` 结束；客户端取消由上游 asyncio cancellation 传播并由服务层记录。

### 3.3 API 与资源生命周期

FastAPI lifespan 只创建一个 `AgentGraph(async_enabled=True)` 并放在 `app.state.agent`，避免每个 POST 重复初始化 Milvus / MySQL。SSE 心跳在主事件结束后停止；客户端断开记录 `request_id/thread_id/elapsed_ms/last_event_type` 后向上传播取消。

## 4. 启动与验证命令

```powershell
# 单元与离线集成
python -m pytest test/test_streaming_envelope.py test/test_streaming_protocol.py `
  test/test_graph_astream.py test/test_knowledge_qa_stream.py `
  test/test_price_inquiry_stream.py test/test_cli_stream.py -q

# CLI 单次流式（推荐打开异步总开关后验证真实链路）
python -m agent --question "招标方式有哪些？" --stream --async

# FastAPI 服务
uvicorn service.api:app --host 0.0.0.0 --port 8000
```

SSE 冒烟示例：

```powershell
$body = @'
{"question":"招标方式有哪些？","thread_id":"demo","deadline_s":60}
'@
Invoke-WebRequest -Uri "http://127.0.0.1:8000/chat/stream" -Method Post `
  -ContentType "application/json" -Body $body
```

生产部署提示：

```bash
gunicorn service.api:app \
  -k uvicorn.workers.UvicornWorker \
  --workers 2 --bind 0.0.0.0:8000
```

Nginx 必须关闭响应缓冲；Windows 不启用 `uvloop`。

## 5. 测试与回归

### 5.1 阶段5定向测试

```text
21 passed
```

覆盖：

- 全部 EventType SSE roundtrip；
- 多帧合并、半个 frame 拆包、id/type/request_id 校验；
- heartbeat 与标准错误帧；
- astream 的 META 唯一和 citations→final 顺序；
- Knowledge QA fake RAG 全链路及 state 兼容；
- Price Inquiry progress callback partial/final；
- CLI stage/token 渲染；
- 现有询价异步测试保持通过。

### 5.2 全量回归

```text
280 passed, 2 skipped, 4 failed — 约 39s
```

对比阶段3报告中的已知失败集完全一致：`test/test_sub_route.py::TestSubRouteClassification` 4 个用例依赖真实 DeepSeek LLM，当前密钥返回：

```json
{"error":{"message":"Insufficient Balance","code":"invalid_request_error"}}
```

失败发生于 `_parse_unified_intent()` 的外部 LLM 调用，随后关键词兜底导致期望分类不匹配；与流式协议、Graph、SQL、CLI 或 API 改动无关。

## 6. 已知限制

1. General Chat 当前是静态引导文案，因此采用 UTF-8 安全分段 synthetic token；未来替换为 LLM 回复时可自然获得原生 token 流。
2. Price Inquiry token 来自渲染后的最终 answer，属于低风险展示型伪流；表格与守卫数据优先于视觉流畅性。
3. Doc QA 仍为官方占位能力，只输出 placeholder stage 和 final。
4. 尚未接入 TTFT 基准指标与真实浏览器联调；此项按计划留到阶段6观测压测。
5. SSE 断线重连的 Last-Event-ID 续传未实现；本阶段要求是可靠终止、心跳和客户端感知。

## 7. 验收对照

| 手册/guide 验收项 | 状态 | 备注 |
| --- | --- | --- |
| CLI P50 first token 显著下降 | ⏸ 待真实环境 | 同步静态分支无网络等待；LLM/Milvus场景需要 DeepSeek 可用后测 TTFT |
| 引用在 final 前且不冲突 | ✅ 通过 | 定向单测覆盖 |
| 30s 心跳断线感知 | ✅ 实现完成 | 服务端默认15s可配置；真实代理断连需部署环境复验 |
| Uvicorn 多 worker 水平扩展 | ✅ 架构支持 | 每个进程独立构建 AgentGraph；给出 gunicorn 命令 |
| 取消不泄漏连接/pool | ✅ 主路径实现 | astream 向上取消；DB bridge已有 finally 归还；完整压测留待阶段6 |
| 同步语义零退化 | ✅ 回归通过 | 除既有402，其余全部通过 |

## 8. 回滚方案

HTTP 层开关尚未新增独立路由开关时，可通过不改前端调用回到同步链路：

```powershell
python -m agent --question "..." # 不加 --stream
ASYNC_BACKEND_ENABLED=false
```

Web 服务则停止 `service.api` 或将 Nginx upstream 切回旧入口；协议代码独立存在，不影响同步 `invoke()`、`query()` 和检查点逻辑。

## 9. 下一步建议

1. 为 DeepSeek 账户充值后重跑全量测试，确认4个402用例恢复；
2. 在 Linux/Nginx 环境执行 SSE 心跳、断开、慢 LLM 取消和多 worker smoke；
3. 将阶段6观测字段入库：request_id、branch、first_event_at、first_token_at、final_at、bytes_out；
4. 根据前端体验决定 TABLE 帧是否渲染紧凑预览；
5. 如业务后续引入 Postgres/Redis Checkpointer，复测 thread state 和流式 final 的一致性。

---

> 报告人：Codex（OpenAI 编码代理）  
> 审核人：待定
