# 阶段 5 改造执行指南 —— 流式输出

> 基准文档：`docs/implementation_handbook_async_memory_streaming.md` §3.3、§3.5、§4 阶段 5、附录 A。
> 适用分支：`feat/async-memory-streaming`。
> 执行原则：不改变 `AgentState` 三字段契约；同步业务语义零退化；先离线协议测试，再接图，最后接 Web。

## 0. 当前状态与差距

已完成：

- `agent/streaming/events.py` 已有 `StreamEvent(type, request_id, payload, ts)`；
- `format_sse()` / `format_jsonl()` 已能序列化；
- `PublicKnowledgeRAG.astream()` 已产出 `stage → retrieval → token → citations → final`；
- Knowledge QA 与 Price Inquiry 已有异步节点；
- `STREAM_ENABLED / STREAM_HEARTBEAT_S / STREAM_CANCEL_GRACE_S` 配置已存在。

待补齐：

1. 事件类型尚未覆盖手册要求的 `META / TABLE / PARTIAL / CANCELLED` 等帧；
2. 节点没有向 LangGraph `custom` 流写出 envelope 的统一机制；
3. `AgentGraph.astream()` 目前只转发 LangGraph 原生事件，不是稳定 envelope；
4. General Chat、Doc QA 尚无异步流式适配；
5. CLI 无 `--stream`；仓库没有 FastAPI `/chat/stream`；
6. 缺少 SSE 解析、心跳、取消、错误终态和连接清理的测试。

按顺序完成下面 6 个批次。每个批次结束都必须保持 `python -m pytest test/test_streaming_envelope.py -q` 通过。

## 1. 锁定流式契约

### 1.1 规范化事件

扩展 `agent/streaming/events.py`：

```python
class EventType(str, Enum):
    META = "meta"
    STAGE = "stage"
    TOKEN = "token"
    RETRIEVAL = "retrieval"
    CITATIONS = "citations"
    TABLE = "table"
    PARTIAL = "partial"
    FINAL = "final"
    ERROR = "error"
    CANCELLED = "cancelled"
    HEARTBEAT = "heartbeat"

    # 兼容旧客户端/旧测试的过渡别名；新代码不要继续产出
    ROUTER = "router"
    MESSAGE = "message"
```

保留 `CITATIONS` 作为规范名。手册中写的是单数 citation，但当前实现、测试和 RAG 输出都是复数 citations；本阶段不要破坏这个字段名。

保留现有 envelope 字段：

```python
class StreamEvent(BaseModel):
    type: EventType
    request_id: str
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: float = 0.0
```

除非所有消费方都同步升级，否则不要把 `payload` 改成 `data`。

### 1.2 每类事件的最低数据约定

| 事件 | 必填 payload | 说明 |
| --- | --- | --- |
| META | question_hash、intent_hint 可空、started_at | 每个请求的第一个帧 |
| STAGE | stage | 至少支持 router_done、retrieval_start、intent_done、sql_start |
| RETRIEVAL | candidates | 只放摘要与分数，不放完整大段原文 |
| TOKEN | delta；可选 synthetic | delta 是增量文本，不是累计文本 |
| TABLE | route、phase、records、display_count | phase 只能是 partial/final |
| PARTIAL | kind、text | 仅用于跨阶段中间答案或恢复摘要 |
| CITATIONS | citations | 必须在 FINAL 前 |
| FINAL | answer；可选 business_result、partial、usage | 业务状态更新以节点返回值为准 |
| ERROR | code、message、retryable | 终态 |
| CANCELLED | reason；可选 partial_answer | 终态 |
| HEARTBEAT | 可空 | 服务层定时发送 |

硬性规则：

- 一个请求必须以 `FINAL` / `ERROR` / `CANCELLED` 之一结束；
- 客户端断开时优先发本地 CANCELLED，不能保证送达也要清理资源；
- 引用帧必须在知识问答最终帧之前；
- 同一 request_id 内 token 都是增量文本，服务端拼接后必须等于 final.answer 的正文部分。

## 2. 补齐协议层

改 `agent/streaming/protocol.py`。

### 2.1 帧工厂与解析

建议新增：

```python
def make_event(
    event_type: EventType,
    request_id: str,
    payload: dict[str, Any] | None = None,
) -> StreamEvent:
    ...

def parse_sse(block: str | bytes) -> StreamEvent:
    """解析单个 SSE frame；空行分隔多行字段。"""
    ...

async def parse_sse_stream(
    chunks: AsyncIterator[bytes],
) -> AsyncIterator[StreamEvent]:
    """处理半个 frame、多个 frame 混在同一个 chunk 的边界。"""
    ...
```

实现要点：

- `parse_sse()` 校验 event/id；反序列化后校验 `request_id == id`；
- 不依赖“一个 HTTP chunk 等于一个完整 SSE 帧”；
- 新增 `format_heartbeat(request_id)`，返回规范的 HEARTBEAT envelope，而不是裸写 ping；
- 新增 `make_error_event(request_id, code, message, retryable)`，避免各处手拼错误数据。

### 2.2 本批次测试

新建 `test/test_streaming_protocol.py`，至少覆盖：

1. 所有事件都能 `format_sse() → parse_sse()` 往返；
2. 多帧数据混在一个 chunk 中能全部解析；
3. 一帧被拆成两个 chunk 后仍能还原；
4. 错误帧包含必填的 code/message/retryable；
5. 同一请求的事件 request_id 不变；
6. heartbeat 序列化后有正文字段并可解析。

此时先不要接 AgentGraph。

## 3. 建立 AgentGraph 到节点的流式通道

这是对手册 §4 阶段 5 第 3 步的落地方式修正。`astream_events(version="v2")` 能拿到底层细节，但对自定义业务帧做过滤和重包容易失控；本项目已经有统一 envelope，因此推荐改为 LangGraph custom 流：

- 节点内部通过 `get_stream_writer()` 主动发送业务进度和 token；
- 节点仍然返回原来的 AgentState 更新；
- `AgentGraph.astream()` 使用 `graph.astream(..., stream_mode="custom", version="v2")`；
- 需要节点更新结果时用 `stream_mode=["custom", "updates"]`，只把 custom 包装成对外 envelope。

Python 低于 3.11 时注意 LangGraph 对 contextvar 的版本限制；如果目标运行环境低于 3.11，先确认当前 LangGraph 文档和行为，必要时使用受控事件队列桥接。

### 3.1 抽公共 emitter

新增 `agent/streaming/context.py`：

```python
from typing import Any
from contextvars import ContextVar

from langgraph.config import get_stream_writer

from .events import EventType, StreamEvent, make_event

_REQUEST_ID: ContextVar[str] = ContextVar("stream_request_id", default="")

def bind_request_id(request_id: str) -> object:
    return _REQUEST_ID.set(request_id)

def current_request_id() -> str:
    return _REQUEST_ID.get()

def emit(event_type: EventType, payload: dict[str, Any]) -> None:
    writer = get_stream_writer()
    writer(make_event(event_type, current_request_id(), payload))
```

LangGraph 会把 custom 对象原样放入队列并透传给 astream；其 stream writer 使用线程安全投递。这个函数只能在 StateGraph 节点内调用。

### 3.2 重写 AgentGraph.astream

先把 `public_kb.rag_engine` 里的私有 event helper 抽到通用模块；RAG 内部不允许再各自生成不同形态的 request_id。

入口行为如下：

```python
async def astream(
    self,
    question: str,
    thread_id: str = "default",
    *,
    deadline_s: float | None = None,
):
    request_id = uuid4().hex
    config = {
        "configurable": {"thread_id": thread_id},
        "metadata": {"deadline_s": deadline_s, "stream_request_id": request_id},
    }

    yielded_meta = False
    try:
        async for item in self._graph.astream(
            {"messages": [HumanMessage(content=question)]},
            config=config,
            stream_mode="custom",
            version="v2",
        ):
            event = normalize_custom_event(item, request_id)
            if event.type is EventType.META:
                if yielded_meta:
                    continue
                yielded_meta = True
            yield event
    except asyncio.CancelledError:
        logger.warning("流式请求取消: request_id=%s", request_id)
        raise
    except Exception as exc:
        logger.exception("流式请求失败: request_id=%s", request_id)
        yield make_error_event(request_id, "agent_stream_failed", str(exc), True)
```

`normalize_custom_event()` 负责：

1. 已是 StreamEvent 则直接返回；
2. 否则包装为带全局 request_id 的 StreamEvent；
3. 修复空 request_id、非法时间戳和不可序列化对象。

不建议每个节点都产 META。让 Router 异步包装器产一次即可；极端路径未收到时由入口兜底。

### 3.3 Router 完成

在 Router 异步函数末尾增加：

```python
emit(EventType.STAGE, {"stage": "router_done", "intent": decision.intent})
```

注意：

- Router 失败进入 fallback 时也应发送 router_done，必要时标 degraded=true；
- 不要修改 router_intent 和 business_result 的含义；
- 不要在这里发 token。

## 4. 接入四类业务流

统一模式：异步节点正常推理/查库；过程中 emit 进度；结束后一次性提交原有 state 返回值。

### 4.1 Knowledge QA

`node_knowledge_qa_async()` 改为消费 `rag.astream(question)`：

```python
parts: list[str] = []
citations: list[dict] | None = None
final_result: dict | None = None

async for event in rag.astream(question):
    if event.type is EventType.STAGE:
        emit(EventType.STAGE, event.payload)
    elif event.type is EventType.RETRIEVAL:
        emit(EventType.RETRIEVAL, event.payload)
    elif event.type is EventType.TOKEN:
        parts.append(event.payload.get("delta", ""))
        emit(EventType.TOKEN, event.payload)
    elif event.type is EventType.CITATIONS:
        citations = event.payload.get("citations", [])
        emit(EventType.CITATIONS, event.payload)
    elif event.type is EventType.FINAL:
        final_result = event.payload.get("result") or {}
```

边界处理：

- async HTTP client 必须由 RAG pipeline/provider 内部 finally 关闭；
- final.answer 应等于拼接后的正文；不一致时以 final 结果为准重建正文，并给 final 增加 normalized=true；
- 拒答路径没有 token 时直接保留上游 FINAL；
- 把 citations 写回原来的 `business_result.data`，保证非流式测评结构不变。

测试使用 fake RAG/pipeline，不要因真实 DeepSeek 或 Milvus 导致 CI 不稳定。

### 4.2 Price Inquiry

询价的价值是结构化分帧，不是伪造 LLM token 流。

最小可验收事件序：

```text
META
STAGE(intent_done)
STAGE(sql_start)
TABLE(partial)
TABLE(partial)
TABLE(final)
CITATIONS  # 如有标准化溯源；否则省略
FINAL(answer + business_result summary)
```

改造位置：

1. `query_tables_async()` 保持当前并发模型，新增可选回调：

   ```python
   def query_tables_async(pool, tables, intent, *, deadline, progress_callback=None):
       ...
       if progress_callback:
           progress_callback(table_name, rows_for_table, phase)
   ```

2. Node 内用普通回调包一层 `emit(TABLE, ...)`；
3. 表数据量大时拆 `TABLE(part_no=..., has_more=True)`，单帧建议不超过 256 KiB 序列化字节；
4. 敏感列已在 SQL 层屏蔽的字段绝不能为了流式重新带出来；
5. 渲染后的自然语言 answer 可切成 UTF-8 安全片段发出 token，并标 synthetic=true。

兼容策略：

- 上游表帧格式不满足要求时降级为一个 `TABLE(phase="final")`；
- 询价查询失败仍走现有 fallback 语义，同时发 ERROR 与终态；
- 不为了让前端提前看到表格而绕过后置公司匹配守卫。

### 4.3 General Chat

新增 `agent/nodes/general_chat_async.py`，或把当前节点内部重构出 async 核心。

事件序：

```text
META（若还没有）
token*
FINAL
```

实现要点：

- 直接用同一 BaseChatModel 的 `astream()`；
- 只对最终自然语言回复开流，router 判定过程不得向外泄露 token；
- 最终 AIMessage.content 用全量字符串写入 state；
- 测试用 fake chat model 验证首 token、增量内容和终止帧。

### 4.4 Doc QA / Fallback

Doc QA 当前仍是占位能力。先交付稳定静态流：

```text
META（若还没有）
STAGE(stage="doc_qa_placeholder")
FINAL(answer=<现有占位回答>, partial=false)
```

Fallback 要求：

- 正常业务失败：先发 `ERROR(code=node_failed, retryable=true)`，再发 fallback 的 FINAL；
- 取消：发 CANCELLED 并执行资源回收；
- 不允许一个请求只有半截 SSE 就无声断开。

同步节点暂时保留原函数包装，不要删除同步入口。

## 5. CLI 流式接入

### 5.1 参数与路由

改 `agent/__main__.py`：

```python
parser.add_argument("--stream", action="store_true", help="启用分帧流式输出")
parser.add_argument("--timeout", type=float, default=None, help="单次问答总超时秒数")
```

分发逻辑：

```python
if args.stream:
    run_single_stream(agent, args.question, deadline_s=args.timeout)
else:
    run_single(agent, args.question)
```

交互模式可以先用 `--interactive --stream` 开启。若本期时间不足，先只交付单问流式。

### 5.2 渲染器

新增渲染函数示例：

```python
def render_stream_event(event: StreamEvent) -> str:
    if event.type is EventType.STAGE:
        return f"\n🔎 {event.payload.get('stage', '')}\n"
    if event.type is EventType.TOKEN:
        return event.payload.get("delta", "")
    return ""
```

CLI 行为：

- token 到达立刻 write + flush；
- final 后统一打印分支、引用和结构化摘要；
- Citations 复用现有 `format_citations()`，避免 CLI 出现第二套口径；
- Ctrl+C 不打印 traceback；
- 结束后检查最后一帧是否为 FINAL/ERROR/CANCELLED。

手动验证：

```powershell
python -m agent --question "招标方式有哪些？" --stream
python -m agent --question "某公司近一年中标了哪些项目？" --stream --timeout 30 --async
```

跑真实外部链路时把 ASYNC_BACKEND_ENABLED=true 放在本机 `.env`，不要改 `.env.example` 默认值。

## 6. FastAPI SSE

当前仓库还没有 FastAPI 应用；不要把示例代码散落进 CLI。

### 6.1 最小应用骨架

目录：

```text
service/
  __init__.py
  schemas.py
  api.py
```

请求模型：

```python
class ChatRequest(BaseModel):
    question: str = Field(min_length=1)
    thread_id: str = "default"
    deadline_s: float | None = Field(default=None, gt=0, le=120)
```

端点关键流程：

```python
app = FastAPI(title="Bidding Assistant Streaming API")

@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    request_id = uuid4().hex
    terminated = asyncio.Event()

    async def heartbeat():
        while not terminated.is_set():
            await asyncio.sleep(settings.stream_heartbeat_s)
            yield format_sse(make_event(EventType.HEARTBEAT, request_id))

    async def events():
        try:
            async for event in app.state.agent.astream(
                req.question,
                thread_id=req.thread_id,
                deadline_s=req.deadline_s,
            ):
                yield format_sse(normalize_request(event, request_id))
                if event.type in {
                    EventType.FINAL,
                    EventType.ERROR,
                    EventType.CANCELLED,
                }:
                    break
        except asyncio.CancelledError:
            yield format_sse(make_event(
                EventType.CANCELLED,
                request_id,
                {"reason": "client_disconnected"},
            ))
            raise
        finally:
            terminated.set()

    return StreamingResponse(
        merge_event_streams(events(), heartbeat()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

需要实现两个工具函数：

1. `merge_event_streams(primary, heartbeat)`：主事件结束后停止心跳；
2. `normalize_request(event, request_id)`：强制外层 HTTP 的 request_id，并保证 EventSource id 唯一。

AgentGraph 在应用 startup 创建一次并挂到 `app.state.agent`；不要每个 POST 都重建 Milvus/MySQL 资源。

### 6.2 取消与超时

实现顺序：

1. 客户端断开触发 Starlette 取消 response generator；
2. 在 except CancelledError 里标记请求结束；
3. 让异常向上传播给 uvicorn/Starlette，不要吞掉；
4. LangGraph task 由 astream 上下文取消，LLM astream 跟随取消；
5. MySQL bridge 归还连接必须在 finally；
6. 记录 cancelled 日志，包含 request_id/thread_id/elapsed_ms/last_event_type。

CANCELLED SSE 帧不一定能送达客户端；它的核心意义是服务端拥有确定性退出路径，日志与指标才能审计。

## 7. 测试矩阵与本地验收

### 7.1 必须新增的 pytest 文件

| 文件 | 重点 |
| --- | --- |
| `test/test_streaming_protocol.py` | SSE 往返、拆包黏包、heartbeat、错误码 |
| `test/test_graph_astream.py` | META 唯一、request_id 稳定、必须有终态、state 结构不变 |
| `test/test_knowledge_qa_stream.py` | token 拼接、引用在 final 前、拒答、fake 引擎取消 |
| `test/test_price_inquiry_stream.py` | intent/sql/table/final、分片大小、敏感字段不回流 |
| `test/test_cli_stream.py` | stdout 有增量输出、Ctrl+C 后可退出 |
| `test/test_streaming_endpoints.py` | 解析 SSE、断连不泄漏 task |

业务测试默认 mock `PublicKnowledgeRAG.astream`、fake price recall/query 和 fake chat model streaming。单元测试不得依赖真实 Milvus/DeepSeek/OpenAI。

### 7.2 回归门禁

每完成一个批次运行：

```powershell
python -m pytest test/test_streaming_envelope.py test/test_streaming_protocol.py -q
```

阶段收尾依次运行：

```powershell
python -m pytest test/ -v
python scripts/benchmark_async.py
```

如基准脚本本轮还没有流式 TTFT 统计，可延后到阶段 6；但先用日志记录：

```text
request_id, branch, first_event_at, first_token_at, final_at, total_tokens, bytes_out
```

### 7.3 手工验收清单

业务语义：

- 同一问题分别用 invoke 与 astream，final answer、intent、business_result 分支一致；
- Knowledge QA 标准引用与同步版一致；
- Price Inquiry 后置校验和引导话术不变；
- 多轮会话换 thread_id 后历史互不影响。

网络行为：

- Nginx/代理开启缓冲时 X-Accel-Buffering 生效；
- 心跳期间客户端无超时断开；
- 客户端主动断开后服务端连接尽快结束；
- 处理故意慢的 fake provider，取消后 MySQL 连接归还；
- Uvicorn 多 worker 下每个 worker 各自维护 AgentGraph，SSE 能命中任意 worker。

Windows 提醒：不要无条件导入 uvloop；仅 Linux/macOS 生产环境可选启用。

## 8. 推荐实施顺序

| Day | 内容 | DoD |
| --- | --- | --- |
| D1 上午 | 补事件枚举、帧工厂、SSE parser、heartbeat | 协议测试全绿 |
| D1 下午 | 建 streaming context；Router 发送完成事件；重写 graph astream | 空 stub 能看到稳定 envelope |
| D2 上午 | Knowledge QA/RAG stream 接入 Graph | fake 全链路事件序正确 |
| D2 下午 | CLI --stream 渲染 + 取消 | 单问首字时间明显早于同步感知 |
| D3 上午 | Price Inquiry 表格分帧；General Chat；Doc QA/Fallback | 四分支均有终态且 state 兼容 |
| D3 下午 | FastAPI SSE、心跳、取消、端点测试 | 断连与多 worker smoke 通过 |

## 9. 明确禁止项

1. 不向 AgentState 加新字段；
2. 不把数据库行原文或超大负载塞进检索事件；
3. 不因流式失败吞掉业务错误后还给用户看似成功的 final；
4. 不允许 Knowledge QA 出现 “final 在 citations 前”；
5. 不把 cancellation 当作普通 Exception 吞掉；
6. 不在同一 endpoint 内每请求创建新的 Milvus collection 或 MySQL pool；
7. 不改动真实密钥文件；不把 `.env` 内容或密钥写入测试/docs/benchmark 输出。

## 10. 完成定义

阶段 5 只有同时满足以下条件才算完成：

1. `AgentGraph.astream()` 对外只输出统一 StreamEvent；
2. Knowledge QA、Price Inquiry、General Chat、Fallback 都有稳定事件序和终态；
3. CLI `--stream` 能看到递增输出、阶段进度和完整 final 渲染；
4. `/chat/stream` 输出合规 SSE，有心跳，并处理客户端断开；
5. 全部同步接口、既有引用测评结构和 database 访问语义保持不变；
6. 第 7 节新增测试与回归门禁通过；
7. 手工取消场景证明 LLM/Milvus/MySQL 资源都能退出或归还；
8. README/启动命令标注流式参数默认关闭，生产压测留到阶段 6。
