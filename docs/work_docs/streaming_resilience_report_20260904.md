# 流式输出故障注入与修复报告

日期：2026-09-04。范围：CLI `--interactive --stream` / `--question --stream`、`AgentGraph.astream`、知识库流式节点、FastAPI SSE。

## 结论

用户日志不是一次正常的“超纲拒答”。请求已路由至 `knowledge_qa`，在 Embedding 阶段发生 `httpcore.PoolTimeout → httpx.PoolTimeout → openai.APITimeoutError`，尚未完成知识库检索，不能据此认定知识库没有答案。

已在本机使用真实 HTTP/1.1 keep-alive 服务、实际 `_SafeEmbeddings → OpenAI SDK → HTTPX/httpcore` 调用链复现跨轮连接失败。原 CLI 每轮 `asyncio.run()` 关闭事件循环，但 Router/RAG/Embedding/Reranker 实例及连接池继续复用，异步连接所依赖的旧循环已关闭。本机基线表现为首轮成功、后续 `APIConnectionError`；它证明了跨循环复用问题，但不声称逐字重现了原始线上堆栈的每一层。

修复后：交互式流式会话只使用一个 `asyncio.Runner`。另外用同一连接池实际制造 `PoolTimeout`、`ReadTimeout` 和取消，各场景随后连续 20 次 Embedding 请求成功，没有把一次失败变成永久故障。

## 发现及修复

| 问题 | 修改 | 验证 |
| --- | --- | --- |
| 每轮销毁循环，但复用异步连接池 | `agent/__main__.py`：会话级 Runner，每轮向同一个循环提交消费任务 | 本地真实 Embedding 接口四轮：成功、HTTP 503、成功、成功；循环唯一 |
| 上轮错误残留在 `business_result` | `agent/graph.py`：新请求显式清空业务结果，消息历史保留 | 故障后再走意图 fallback，不再复读上轮错误；后续 12 轮正常 |
| RAG 初始化失败后仍缓存半初始化对象 | `agent/nodes/knowledge_qa.py`：锁内初始化成功才发布单例 | 首次初始化失败可重试；16 线程、64 次获取，只初始化一次且不暴露半成品 |
| 任意 `RuntimeError` 被误报成“未初始化” | 新增 `KnowledgeBaseNotReadyError`，只处理明确的未就绪状态 | 循环关闭、流中断等运行时错误走错误终态，不再伪装成未入库 |
| 拒答/fallback 只有 final 没有 token，CLI 只显示分支 | 终态正文补显，已输出的正文不重复打印 | 正常答案与拒答均显示一次 |
| 节点与 RAG 重复发 `retrieval_start` | 由 RAG 流提供一次检索阶段事件 | 每次知识库问题只显示一次检索开始 |
| 断流但没有 final，被当作完整成功答案 | 知识库节点要求完整终态；中断走 fallback | 半途读失败、无 final 流都不会保存为成功回答 |
| 同一请求先 error 又 final；meta 出现在最后 | 统一为首帧 meta、唯一末帧终态；终态等图执行/检查点完成后再交付 | CLI/SSE 使用相同终态规则，拿到 final 后可读取本轮状态 |
| `deadline_s` 只在 metadata 中传递，没有总超时 | 图执行使用 `asyncio.timeout`，CLI 交互模式透传 `--timeout` | 50ms 超时取消挂起任务，下一轮成功 |
| 提前结束消费时生成器未显式关闭 | RAG、LLM 流、节点和 SSE 使用 `aclosing` | SSE 断开后生成器关闭，无额外遗留 asyncio 任务，下一请求正常 |
| SSE 心跳未发送，旧辅助协程反而抛错 | 有界队列合并正文和心跳；结束时取消并回收生产任务 | 首次正文前可见心跳；断开可清理；请求 ID 一致 |

异常降级统一以 `error` 作为唯一终态，携带友好 `message`、`retryable` 和 fallback 分支；正常业务拒答仍是 `final`。已发出的不完整 token 不被包装成成功答案。

## 测试规模与结果

新增 `test/test_streaming_resilience.py`，覆盖本地 TCP 接口、真实 LangGraph、可控 RAG 故障以及 SSE 消费。

- **并发故障注入**：200 请求、16 并发；40 次有意注入 Embedding 超时，160 次正常请求；会话状态与 request_id 隔离。
- **持续恢复**：超时、循环错误、半途断流、无 final 四类故障，各验证后续 12 轮正常回答。
- **真实连接池恢复**：PoolTimeout、ReadTimeout、取消三类场景，每类随后连续 20 次成功。
- **资源和交互**：拒答显示、正文不重复、检索阶段不重复、总超时、外部取消、内部取消、SSE 心跳与断开、初始化并发。
- **全量回归**：`390 passed, 1 skipped, 2 warnings`，75.36 秒。跳过的是既有的可选 live RAG 用例；另行执行了下述真实接口冒烟。
- 两条既有警告分别是 `create_react_agent` 弃用提示及 `test_runtime_smoke.py` 的未 await 协程警告，本次未修改这些无关代码。

首次基线日志中的失败包含当时未安装 FastAPI 导致的 SSE 导入失败，以及后来拆分为内/外部取消的初始断言，不能把失败条数直接等同于独立产品缺陷数。

## 真实接口冒烟

同一个真实 `AgentGraph`、同一会话、同一个事件循环，依次请求；每轮总超时上限 35 秒。未 Mock Router、Embedding、Milvus、Reranker 或 LLM。

| 顺序 | 问题 | 分支 | 耗时 | token 事件数 | 结果 |
| --- | --- | --- | --- | --- | --- |
| 1 | 今天火星天气怎么样？ | general_chat | 3.36s | 13 | final，终态唯一 |
| 2 | 投标需要交保证金吗 | knowledge_qa | 25.66s | 177 | final，终态唯一 |
| 3 | 招标方式有哪些？ | knowledge_qa | 15.41s | 603 | final，终态唯一 |

这三轮证明超出领域的问题之后，两个领域问题都能正常完成真实流式调用。它不是对真实 API 注入故障后的线上恢复压测，也不是答案法律正确性/引用准确率评测；故障注入与恢复验证在可控本地链路完成。没有向付费接口发送高并发流量。

## 环境与复现

本次使用系统 Python 3.12.7，HTTPX 0.27.0、httpcore 1.0.2、OpenAI SDK 2.53.0、langchain-openai 1.4.1、LangGraph 1.1.10。现有环境与 `requirements.txt` 的部分版本约束有差异，本次未升级全局依赖。

系统环境原本缺少 FastAPI。仅为 SSE 测试将 FastAPI 0.115.0、Starlette 0.38.6 安装至 `test_report/.sse-test-deps`，通过当前命令的 `PYTHONPATH` 使用，不改变全局 Python 环境。

PowerShell 复现完整回归：

```powershell
$env:PYTHONPATH = "$PWD\test_report\.sse-test-deps;$PWD"
python -m pytest test/test_streaming_resilience.py -v
python -m pytest test/ -q
```

如果环境已安装项目所需 FastAPI，可以直接执行 pytest；缺少 FastAPI 时新增的两项 SSE 测试会明确跳过，而不是伪造通过。

重启现有 CLI 进程后复测：

```powershell
python -m agent --interactive --stream --timeout 60
```

不能让已经被旧代码关闭的事件循环原地恢复，因此已有异常进程需要重启一次。`clear` 清的是会话记忆，并不负责修复 HTTP 客户端。

测试记录：

- `test_report/streaming_resilience_before.txt`：修改前基线。
- `test_report/streaming_resilience_after.txt`：定向回归。
- `test_report/streaming_full_regression.txt`：完整 pytest 回归。
- `test_report/streaming_live_smoke.jsonl`：三轮真实调用的结构化统计，不存储密钥或完整生成内容。

## 边界

- 当前修复覆盖上述 CLI 流式入口、`astream` 和 SSE。没有改造遗留的同步 `AgentGraph.stream()` 缓冲接口，也没有把共享异步客户端改为可在任意事件循环之间迁移。外部异步调用方仍应在同一个长生命周期循环中复用 Agent/RAG，而不是反复 `asyncio.run(agent.astream/ainvoke...)`。
- `deadline_s` 可以取消可取消的异步任务，但已在线程池中执行的同步 Milvus/SQL 调用仍需由其底层超时结束，不能保证强制停止阻塞线程。
- 服务供应商持续宕机、限流或网络中断仍会正常返回错误；本次不通过增加无限重试、扩大连接池或伪造知识库答案掩盖故障。
- 未改动 `.env`，未重建/清空知识库或数据库，未提交 Git；保留工作区原有未提交修改。
