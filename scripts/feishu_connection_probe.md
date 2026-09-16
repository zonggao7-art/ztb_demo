# 飞书最小连接验证

用途：读取项目根目录 `.env`，使用飞书官方 SDK 建立长连接。
默认收到 `im.message.receive_v1` 时只输出收到事件的提示。
添加 `--reply-test` 后，对用户的单聊文字消息固定回复“已收到你的消息，连接测试成功。”。
以上两种模式均不调用 Agent。群聊、机器人发出的消息及非文字消息不会触发回复。
日志不输出凭证、完整连接 URL、用户标识或消息正文。

## 运行（项目根目录，PowerShell）

```powershell
.\.venv-react\Scripts\python.exe -m pip install lark-oapi==1.7.3
.\.venv-react\Scripts\python.exe scripts/feishu_connection_probe.py --check-config
.\.venv-react\Scripts\python.exe -u scripts/feishu_connection_probe.py
```

要测试双向收发，先停止上述进程，再运行：

```powershell
.\.venv-react\Scripts\python.exe -u scripts/feishu_connection_probe.py --reply-test
```

在飞书机器人单聊中发送“连接测试”，应收到固定回复。
需要已开通 `im:message:send_as_bot`，订阅 `im.message.receive_v1` 并发布生效。
脚本缓存最近 1000 个成功回复的消息 ID，并为同一消息设置稳定的请求 UUID。
本地缓存不会跨进程保存；平台 UUID 去重受平台有效期限制，并非永久不重复保证。
请求超时或 API 拒绝时会记录 `REPLY_FAILED`，不会谎报成功；可以再发一条新消息测试。

SDK 要求 `websockets>=11,<16`。本项目锁定 15.0.1，运行环境已通过 `pip check`。
凭证使用 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`，真实值只写入 `.env`。
`--check-config` 仅验证填写情况，不能证明凭证有效。

## 如何判断结果

### 单轮 Agent 问答模式

先确保网页后端 `http://127.0.0.1:8000` 正常运行，再停止其他连接脚本并执行：

```powershell
.\.venv-react\Scripts\python.exe -u scripts/feishu_connection_probe.py --agent-reply
```

飞书用户单聊文字 → 后台工作线程 → 网页同一个 `/chat/stream` 接口 → `final` → 飞书文字回复。
接收回调先入队返回，不等待模型。每条问题使用独立且稳定的后端 thread_id，
不携带聊天历史；追问时需写明完整对象与要求。不需要重新发布飞书配置。

- 仅采用最终 `answer`，校验其与 `business_result.answer` 相同，并保留业务状态。
- 不展示模型 token、原始工具输出或内部错误。附带最终引用中的文档名和章节索引。
- 超时、无 final 或格式不完整时，回复本轮未取得完整结果。
- 单条文本超过适配器的 16 KB 展示预算时提示缩小问题，不静默截掉保留意见或引用。
- 本地演示使用单工作线程，最多接收 8 个待处理问题；队列满时向 SDK 抛错，避免确认未接收事件。
- 进程内缓存最近 1000 个结果及发送状态；发送失败后的事件重投复用已有答案。
  进程重启后缓存消失，故不保证跨重启的模型调用去重；不是生产级持久化任务队列。
- 固定回复模式和 Agent 模式互斥。可停止 Agent 模式后以 `--reply-test` 回退。

### 日志状态

- `CONNECTING`：正在尝试，尚未连接成功。
- `CONNECTED`：SDK 已完成 WebSocket 连接，此时保持程序运行，回开放平台验证并保存长连接订阅方式。
- `EVENT_RECEIVED`：已收到一次消息事件。平台还需要添加“接收消息”事件并按发布要求使配置生效，才可验证此项。事件可能重投，此日志不代表唯一消息计数。
- `DISCONNECTED` / `RECONNECTING`：连接已断开 / SDK 正在重连。
- `REPLY_SENT`：飞书 API 已确认发送成功，实际展示还需在飞书客户端确认。
- `REPLY_FAILED`：回复请求失败，检查日志中的错误码或网络状态。
- `DUPLICATE_SKIPPED`：已成功回复过的消息再次投递，本次跳过。
- `QUESTION_QUEUED` / `AGENT_STARTED`：问题已入队 / 开始调用后端。
- `AGENT_FINAL`：拿到最终展示结果；不表示答案一定满足用户预期。
- `AGENT_FAILED`：未取得可发布的完整结果，将发送友好失败提示。
- `FAILED`：启动失败，按错误码检查应用凭证、应用能力或网络。

在终端运行时按 Ctrl+C 停止。不要同时启动多个连接验证进程，以免分流事件。
电脑关机或进程停止后，本地不再接收事件。

如由助手在后台启动，可查看 `logs/feishu_connection_probe.stdout.log`；
本次进程 PID 记录在 `logs/feishu_connection_probe.pid`。停止前用任务管理器核对进程，
不要将历史 PID 当作当前进程。后台日志和 PID 已被项目现有 `.gitignore` 忽略。

实现基于 [飞书官方 Python SDK](https://github.com/larksuite/oapi-sdk-python/tree/v1.7.3)。

## 2026-09-16 本地接入验证

- `python -m pytest test/test_feishu_connection_probe.py test/test_feishu_agent_bridge.py -q`：30 项通过；SDK 内部有两条弃用警告。
- 覆盖：固定回复回归、非单聊过滤、流分块/中文/CRLF、无 final、请求 ID 不一致、
  不泄露草稿/错误详情、业务状态、引用索引、后台入队、重复事件、发送失败后复用答案、输入限制和队列满。
- 初次真实验证时 Docker 未运行，业务工具失败，适配器正确保留 `partial` 和数据服务失败说明。
  启动现有 Docker Desktop 后 MySQL / Milvus 等容器恢复健康；未重建容器或修改数据。
- 通过同一个桥接查询函数重新请求现有本地 API：项目编号示例查询约 1.41 秒，返回最终结果；
  招标文件提供期限问题约 6.39 秒，返回最终结果和来源索引。
- 飞书固定回复双向测试此前已由客户端显示和 `REPLY_SENT` 日志共同确认。
  用户随后在飞书单聊发起项目中标详情查询，并确认收到完整答案；后台同步出现
  `QUESTION_QUEUED`、`AGENT_STARTED`、`AGENT_FINAL`、`REPLY_SENT`，标准错误日志为空。
  至此项目查询的飞书 → 本地 Agent → 飞书完整链路已实测通过。
  用户随后确认法规问答的引用展示验证合格。多轮上下文记忆未实现，本阶段按已确认的单轮范围验收。

## A6：后端停止与下一轮恢复（2026-09-16）

- 保持飞书连接进程运行，停止现有 FastAPI 后端，确认 8000 端口关闭。
- 用户在飞书发送项目中标详情问题；日志出现 `AGENT_FAILED`（`ConnectError`）及 `REPLY_SENT`，用户确认收到明确失败提示。
- 使用原虚拟环境及相同参数恢复 FastAPI，`/openapi.json` 返回 HTTP 200。
- 用户重新发送同一问题；日志新增 `AGENT_FINAL` 和 `REPLY_SENT`，用户确认收到完整业务答案。
- 结论：本次后端不可用被正确展示为请求失败；恢复后新消息可以正常问答，飞书适配器无需重启。
  这不代表 A6 全部通过，其余场景需分别核验。

## A6：网页停止生成与下一轮恢复（用户手动验收）

- 用户在“处理中停止生成，检查本轮停止状态，再发送新问题并检查是否混入上一轮内容”的验收步骤后，确认验收合格。
- 此项记录为用户手动验收通过，未将其记作助手独立复测或新增自动化测试结果。
- 其余异常场景需分别确认，不据此宣称 A6 全部通过。

## A6：网页并发会话隔离（用户手动验收）

- 用户按两个网页标签页分别新建会话、尽量同时查询项目中标详情和企业工商信息的步骤，确认验收合格。
- 记录为页面进度、答案及结果卡片未串内容的用户手动验收；本轮未独立采集请求或 thread_id 日志，不据此声称底层标识已逐条核验。
- 请求超时等剩余项目仍需分别核对；飞书重复投递和回复失败目前主要由离线测试覆盖。

## A6：网页中途断流（助手浏览器实测）

- 2026-09-16 使用临时服务在已发送模拟 token 后截断真实 HTTP chunk；页面显示“结果接收中断”，刷新后仍保留中断状态。
- 同一会话下一轮经真实后端正常返回项目中标详情，刷新后两轮状态仍正确区分。
- 本次未修改业务代码；详细方法与边界记录在 `frontend/QA.md` 的 A6 断流验收节。
