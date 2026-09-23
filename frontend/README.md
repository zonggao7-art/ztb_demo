# 招投标智能助手 · A3 本地真实问答

React + TypeScript + Vite，使用 CSS Modules。默认连接真实 Agent，另保留明确标识的虚构界面演示。

## 本地启动

先确保 Docker 中的 MySQL、Milvus 及其依赖运行。后端使用项目已有环境与配置；前端不读取根目录 `.env`，不持有模型密钥。

在项目根目录启动后端：

```powershell
cd D:\DEMO\zhaotoubiao_demo
.\.venv-react\Scripts\python.exe -m uvicorn service.api:app --host 127.0.0.1 --port 8000
```

另一个终端启动前端（已验证 Node.js 24.19.0 / npm 11.17.0）：

```powershell
cd D:\DEMO\zhaotoubiao_demo\frontend
npm.cmd ci
npm.cmd run dev
```

打开 [本地页面](http://127.0.0.1:5173/)。Vite 把 `/api/chat/stream` 代理到本机 8000 的 `/chat/stream`。当前仅支持后端 `unified` 模式，其他模式会显示协议未匹配提示。

```powershell
npm.cmd test
npm.cmd run build
```

构建先检查 TypeScript，再输出 `dist/`；`npm.cmd run preview` 在 `127.0.0.1:4173` 预览构建结果，只转发问答接口 `/api/chat/stream`。

## 这一步的数据流

1. 发送问题后，POST 只包含本轮问题、页面会话 UUID 和 90 秒后端预算。
2. 按 SSE 空行分帧并验证信封；网络分片不一定是完整事件，也可能截断一个汉字。
3. `meta` 绑定后端请求标识，`stage` 更新进度，`token` 逐段追加已发布正文。
4. 收到 `table`、`citations` 时暂不生成业务卡片。等待 `final`，用 `records[].step_id` 对应 `actions[].step_id`，根据工具名称分类。最终正文替换流式积累的正文，避免重复追加。
5. 工商信息呈现键值卡片，经营范围为长文本，处罚及中标结果为各自表格。缺失字段显示“未收录”；金额保留原始值，单位结合正文核对。
6. 只有明确成功且精确范围的空查询才显示“暂未收录匹配记录”；查询失败不能解释为没有记录。关联缺失或不唯一时不猜测分类，显示提示。
7. `final` 中业务状态决定“结果已返回 / 需要补充条件 / 部分完成 / 暂不支持 / 证据不足”。连接结束而没有 `final` 不能标记成功。

停止使用 AbortController 断开本次请求，并使本地迟到回调失效。已有文字和正在编辑的草稿保留。客户端等待超过 105 秒会停止等待，此提示不声称后端已证明超时原因。真实重试重新提交同一问题，保留上一轮，不保证重试成功。

## 真实问答与界面演示

- 默认真实问答：会调用已配置的模型服务及数据工具。首页直接提供法规、工商、经营范围、处罚、项目中标、企业中标历史六张卡片。每张卡片包含已核对的预设问题；点击只填入输入框，支持修改后手动发送。桌面为两行三列，手机为单列。
- 界面演示：问题不会发送到 API；场景由下拉框指定，显示的回答、企业、项目、引用均为虚构样例。错误演示的重试固定切到法规成功示例，仅用于检查交互恢复。
- 切换模式会创建新会话；历史会话保留模式标识。新建或切换会话会停止当前请求。
- 会话、完整回答、引用、分类结果、当前选中会话及草稿保存到当前浏览器的 localStorage。刷新或关闭重开可恢复；未完成的请求恢复为“结果接收中断”，不会自动重新发起。当前 unified 仍每轮独立处理，保存记录不等同于模型具有多轮记忆。

## 读代码入口

| 文件 | 职责 |
| --- | --- |
| `src/App.tsx` | 页面、消息、引用和输入交互 |
| `src/examples.ts` | 六个真实问答入口及已核对的预设问题 |
| `src/useChat.ts` | 真实请求与演示流程、取消、计时和草稿 |
| `src/api/sse.ts` | UTF-8 解码、SSE 分帧、读取释放 |
| `src/api/chat.ts` | HTTP 请求、信封验证、事件与终态分派 |
| `src/api/results.ts` | 将最终业务数据按工具契约转换为展示模型 |
| `src/BusinessResults.tsx` | 分类卡片、长文本和表格 |
| `src/state.ts` | 更新指定一轮，拒绝终态后的迟到事件 |
| `src/history.ts` | 带版本与结构校验的浏览器保存、恢复和冲突保护 |
| `src/api/chat.test.ts`、`src/state.test.ts` | 协议、分类、请求隔离和状态回归 |

可以从点击发送开始，沿 `App → useChat → requestChat → reducer → Message` 阅读。分类数据的路径是 `final → adaptFinal → BusinessResults`。

## 当前边界

- 默认启动方式是本地联调；使用下方 ngrok 脚本可临时提供公网演示。电脑关机、休眠或断网后演示不可用。
- 单独上传 `dist/` 不会自动拥有 Vite 开发代理；部署时需安排 `/api` 的服务端转发及后端基础设施。
- 未实现登录、服务端历史同步、PDF 下载、完整 Markdown 或公网访问控制。
- 回答按纯文本安全渲染，不解析模型 HTML。引用片段就地展开，保留来源标识。
- 真实服务暂不保证所有错误都能区分为超时或网络故障；页面不会编造精确错误原因。
- 浏览器断开请求不等于已确认远端模型计费立刻终止。


## 浏览器本地保存

- 使用独立键 `bidding-assistant.history.v1`。每次页面状态改变后同步写入；包含草稿与演示数据，不保存模型密钥。
- 左侧“清空记录”提供二次确认，会停止当前请求，清除本应用的全部会话和草稿，保留一个新会话。不会清理其他站点、其他应用的存储键或业务数据库。
- 浏览器拒绝读写或空间不足时显示“未保存”，不把失败写入当作成功；上次成功保存的内容保留，不会静默删掉旧会话腾空间。
- 无法识别的版本或损坏数据保留原值，不自动覆盖。用户可通过清空记录重新开始。
- 另一个标签页更新存储时，旧页面暂停保存并提示先保留未保存内容、再刷新；避免常见的旧页面覆盖新记录问题。不提供多页面实时合并编辑。
- 保存范围是同一浏览器配置及同一站点地址（协议、主机和端口）。换浏览器、从本机迁移到公网域名、清理站点数据不会自动迁移历史；无痕窗口通常不会长期保留。
- 使用此浏览器的人可查看本地历史。没有服务端用户隔离，也不是备份服务。

## 本机运行 + ngrok 公网演示

此方式用于低访问量的项目展示：浏览器 → ngrok HTTPS 链接 → 本机 `4173` 构建预览 → 本机 `8000` Agent。保留 `5173` 开发页面；公网入口只提供 `dist/` 的内容与问答代理，不转发后端文档接口。Vite preview 用于这里的演示，不作为正式生产部署服务器。

准备条件：Docker 和已有 MySQL、Milvus 服务运行，项目 `.env` 与 `.venv-react` 可正常问答，前端依赖已安装。ngrok Windows 客户端放在项目 `.cache/ngrok/ngrok.exe`，该目录已被 Git 忽略；官方下载地址为 <https://ngrok.com/download/windows>。

首次使用：

1. 在 <https://dashboard.ngrok.com/signup> 注册账号，并按页面提示完成验证。
2. 在 <https://dashboard.ngrok.com/get-started/your-authtoken> 复制 **authtoken**，不是 API key。
3. 在项目根目录运行以下命令，按提示粘贴 token。输入隐藏，不将 token 写入命令历史；ngrok 将它保存到当前 Windows 用户的配置目录，不保存到项目代码中。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\configure_ngrok.ps1
```

启动公网演示：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_public_demo.ps1
```

脚本构建前端，复用已运行的 Agent 后端或启动新后端，再启动构建预览和 ngrok。后台进程不弹窗；脚本输出的 `Public demo: https://...` 即分享链接，也保存在 `logs/public-demo/public-url.txt`。`--host-header=rewrite` 使本地 Vite 接收到正确 Host，无需将 `allowedHosts` 设为全放行；关闭 ngrok 的本地请求内容检查功能。只有成功返回链接才代表隧道已连接，还应通过手机移动网络实际打开并完成一次问答。

只启动本机演示，不连接公网：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\start_public_demo.ps1 -LocalOnly
```

停止演示：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop_public_demo.ps1
```

停止脚本只关闭它记录且启动时间仍一致的进程，避免 PID 被复用后误关其他程序。已存在的后端、开发页面和数据库不关闭。关闭启动命令所在终端不会主动执行停止脚本，需要停止分享时请显式执行停止命令。

使用边界：

- ngrok 免费账号提供账号绑定的开发域名；同一账号重新连接可继续使用该域名。首次浏览器访问会出现 ngrok 提示页，点击 `Visit` 后进入。[免费计划说明](https://ngrok.com/docs/pricing-limits/free-plan-limits/)
- 这次未增加登录和调用配额限制，持有链接的人可以调用真实模型并消耗 API 余额。演示结束可停止隧道。
- 保持电脑开机、联网且不休眠。本机服务正常不代表外部网络一定可达，实际分享前使用外部网络验证。
- 从 `127.0.0.1:5173`、`127.0.0.1:4173` 和公网域名访问属于不同站点，各自的浏览器历史互不迁移。
- 启动失败时查看 `logs/public-demo/` 中对应的 `backend`、`preview`、`tunnel` 日志。账户未绑定时先运行配置脚本，不要把 authtoken 发到聊天或提交到仓库。
