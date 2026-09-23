# -*- coding: utf-8 -*-
"""实验 02：必须缓冲【字节】，不能缓冲【字符串】

对应知识总纲：§5 第 10 步「SSE 解析器处理粘包、拆包和 UTF-8 边界」

总纲只写了"要处理 UTF-8 边界"，没说为什么。这个实验把它跑出来：
一个汉字在 UTF-8 里占 3 字节。如果 TCP 恰好从汉字中间切断，
按字符串拼接就会直接抛 UnicodeDecodeError —— 页面在中文回答上随机白屏。

浏览器端同理：TextDecoder 不加 { stream: true } 就会踩这个坑。

运行：
    python learning_lab/02_buffer_bytes_not_str.py
"""
from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from agent.streaming.events import EventType
from agent.streaming.protocol import format_sse, make_event, parse_sse_stream


async def two_pieces(part_a: bytes, part_b: bytes):
    yield part_a
    yield part_b


async def main() -> None:
    frame = format_sse(make_event(EventType.TOKEN, "req-1", {"delta": "招标方式有哪些"}))

    # 定位"招"字的第一个字节，从它中间切断
    idx = frame.find("招".encode("utf-8"))
    cut = idx + 1
    part_a, part_b = frame[:cut], frame[cut:]

    print(f'frame 共 {len(frame)} 字节，"招"字起始于第 {idx} 字节（UTF-8 占 3 字节）')
    print(f'在第 {cut} 字节处切断 —— 汉字"招"被劈成 1 + 2 两个碎片\n')
    print(f"  part_a 尾部: {part_a[-12:]!r}")
    print(f"  part_b 头部: {part_b[:12:]!r}")

    print("\n【错误做法】每个 chunk 先 decode 再拼字符串：")
    try:
        _ = part_a.decode("utf-8") + part_b.decode("utf-8")
        print("  竟然没报错（说明这次没切中汉字，重跑试试）")
    except UnicodeDecodeError as exc:
        print(f"  [炸] UnicodeDecodeError: {exc.reason} at bytes {exc.start}-{exc.end}")
        print("       -> 页面会在中文回答上随机崩溃，而且很难复现")

    print("\n【正确做法】按字节缓冲，扫到空行才 decode —— 即项目现有实现：")
    events = [ev async for ev in parse_sse_stream(two_pieces(part_a, part_b))]
    for ev in events:
        print(f"  [OK] type={ev.type.value}  payload={ev.payload}")

    print("\n【你要能回答】协议.py 为什么用 bytearray 缓冲而不是 str 缓冲？")
    print("  因为字节流可以在任意位置切开，只有完整 frame 才是合法 UTF-8。")
    print("  前端对应结论：TextDecoder 必须开 { stream: true } 增量解码。")


if __name__ == "__main__":
    asyncio.run(main())
