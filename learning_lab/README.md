# learning_lab —— 前端知识的可运行实验

配套文档：[`docs/design_docs/知识落地学习路径_在项目中学_20260911.md`](../docs/design_docs/知识落地学习路径_在项目中学_20260911.md)

这里的脚本**不连后端、不连数据库、不调模型**，只用项目自己的 `agent/streaming/protocol.py`
就能把几个最容易背错的概念跑出来。目的是：先看见现象，再回头看文档。

## 运行

不需要装任何新依赖，用项目后端已有的环境即可：

```bash
cd D:/DEMO/zhaotoubiao_demo
./.venv-react/Scripts/python.exe learning_lab/01_chunk_not_frame.py
./.venv-react/Scripts/python.exe learning_lab/02_buffer_bytes_not_str.py
```

实验 03 需要一份真实抓包，先抓再用（注意 `-s` 和 `-o`，详见下节）：

```bash
./.venv-react/Scripts/python.exe learning_lab/03_read_sse_timeline.py raw_sse.txt
```

## 覆盖的知识点

| 脚本 | 对应总纲位置 | 要记住的结论 |
| --- | --- | --- |
| `01_chunk_not_frame.py` | §5 第 10 步、§16.1 | chunk 是传输分块，不是业务消息边界 |
| `02_buffer_bytes_not_str.py` | §5 第 10 步「UTF-8 边界」 | 必须缓冲字节；前端 `TextDecoder` 要开 `{ stream: true }` |
| `03_read_sse_timeline.py` | §5、§6.4、§13.1 | 真实事件顺序、静默间隙、token 的真实来源 |

## 抓包命令（Windows / PowerShell 专用坑）

PowerShell 有两个默认行为会把抓包结果毁掉，务必用下面的写法：

```powershell
# 1) 请求体写进文件，避免 PowerShell 拆引号
'{"question":"招标方式有哪些？","thread_id":"demo-001","deadline_s":60}' | Out-File -Encoding utf8 request.json

# 2) -s 抑制进度条；-o 直接写文件，不要用 Tee-Object
curl.exe -s -N -X POST http://127.0.0.1:8000/chat/stream `
  -H "Content-Type: application/json" `
  --data-binary "@request.json" -o raw_sse.txt
```

| 坑 | 后果 | 正确做法 |
| --- | --- | --- |
| PowerShell 用单引号传 JSON | 双引号被吃掉，后端报 `json_invalid` | 写进 `.json` 文件用 `--data-binary @file`，或转义 `\"` |
| 不加 `-s` | 进度条和正文交错显示，屏幕一片乱 | 加 `-s` |
| 用 `Tee-Object -FilePath` | **默认写成 UTF-16LE**，字节流里没有 `\n\n`，任何 SSE 解析器都返回 0 帧 | 用 `curl -o` 或 `Out-File -Encoding utf8` |

> 最后一条最容易误判：抓包工具编码错了，看起来却像解析器有 bug。
> **分不清"数据错"和"代码错"，是排障时最贵的一种错。**

## 建议用法

**先预测，再运行。** 跑之前先写下你认为会输出什么，然后对比。
预测错的地方才是你要花时间的地方。
