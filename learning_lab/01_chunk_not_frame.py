# -*- coding: utf-8 -*-
"""实验 01：网络 chunk 边界 != SSE frame 边界

对应知识总纲：§5（一次请求的完整数据流）、§16.1「网络 chunk 与 SSE frame 的区别」

这个实验不连后端、不连数据库、不花钱调模型，只用项目自己的
agent/streaming/protocol.py 就能说明问题。

运行：
    python learning_lab/01_chunk_not_frame.py

观察什么：
    1. 后端明明只产出了 3 条业务事件；
    2. 但浏览器侧按 TCP 收到的"块"远多于 3 个，且切割位置完全随机；
    3. 有一条 chunk 停在了 JSON 中间（"typ），另一条跨过了 frame 边界；
    4. 解析器仍然还原出恰好 3 条业务事件。

结论：frame 边界只能靠扫描空行得到，不能靠"一次 read 就是一条消息"。
"""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

# 让脚本能直接 import 项目包（不依赖工作目录）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from agent.streaming.events import EventType
from agent.streaming.protocol import format_sse, make_event, parse_sse_stream


def build_backend_bytes() -> bytes:
    """模拟后端：连续产出 2 条 token + 1 条 final，拼成完整字节流。"""
    return b"".join(
        [
            format_sse(make_event(EventType.TOKEN, "req-1", {"delta": "招标方式"})),
            format_sse(make_event(EventType.TOKEN, "req-1", {"delta": "有公开招标"})),
            format_sse(make_event(EventType.FINAL, "req-1", {"answer": "ok"})),
        ]
    )


async def fake_browser_chunks(payload: bytes, cuts: list[int]):
    """模拟浏览器：把字节流按任意位置切成 chunk 后逐块喂进来。"""
    for i in range(len(cuts) - 1):
        yield payload[cuts[i] : cuts[i + 1]]


async def main() -> None:
    payload = build_backend_bytes()
    print(f"后端一共产出 {len(payload)} 字节 = 3 条 SSE frame\n")

    # 故意在 JSON 中间、frame 边界中间随便切
    cuts = [0, 40, 41, 42, 100, 137, len(payload)]
    chunks = [payload[cuts[i] : cuts[i + 1]] for i in range(len(cuts) - 1)]

    print(f"浏览器实际收到 {len(chunks)} 个 chunk（切割位置是随机的）:")
    for i, chunk in enumerate(chunks, 1):
        tail = chunk[:34]
        ellipsis = "..." if len(chunk) > 34 else ""
        print(f"  chunk{i}: {len(chunk):4d} bytes  {tail!r}{ellipsis}")

    print("\n注意：chunk1 停在 JSON 中间，chunk5 跨过了一个 frame 边界\n")

    events = [ev async for ev in parse_sse_stream(fake_browser_chunks(payload, cuts))]
    print(f"解析器还原出的业务事件：恰好 {len(events)} 条")
    for ev in events:
        print(f"  type={ev.type.value:6s} request_id={ev.request_id}  payload={ev.payload}")

    print("\n【你要能回答】为什么不能每收到一个 chunk 就 json.loads()？")
    print("  因为 chunk 是传输层分块，不是业务消息边界。")


if __name__ == "__main__":
    asyncio.run(main())
