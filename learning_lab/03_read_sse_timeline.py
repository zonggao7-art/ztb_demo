# -*- coding: utf-8 -*-
"""实验 03：把原始 SSE 抓包变成一张能看懂的时间线

对应知识总纲：§5 数据流、§6.4 统一事件模型、§13.1 前端单元测试

用法：
    # 1) 先抓一份干净的真实输出（注意 -s，别让进度条混进来）
    curl.exe -s -N -X POST http://127.0.0.1:8000/chat/stream ^
      -H "Content-Type: application/json" ^
      --data-binary "@request.json" -o raw_sse.txt

    # 2) 读它
    python learning_lab/03_read_sse_timeline.py raw_sse.txt

它会告诉你每一帧是什么、间隔多久、载荷多大，并标出三件容易看漏的事：
  - 静默间隙（心跳就是为它存在的）
  - 时间戳相同的帧（说明这些事件是同一瞬间批量产生的）
  - token 帧的时间分布（决定你的"打字机"动画是真实还是表演）
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from agent.streaming.protocol import parse_sse_stream  # noqa: E402

CHUNK_SIZE = 512  # 故意小块喂入，证明网络分块与 frame 边界无关
SEP_LF = b"\n\n"
SEP_CRLF = b"\r\n\r\n"


async def feed(data: bytes, size: int):
    """模拟浏览器：字节流按任意大小分块到达。"""
    for i in range(0, len(data), size):
        yield data[i:i + size]


def load_sse_bytes(path: Path) -> tuple[bytes, str]:
    """读文件并统一成 UTF-8 字节。

    坑：PowerShell 5.1 的 Tee-Object -FilePath 默认写 UTF-16LE，
    于是每个字符后面多一个 \\x00，字节流里再也找不到 \\n\\n，
    再正确的 SSE 解析器也会返回 0 帧。抓包工具本身出问题，
    不代表解析器有 bug —— 这个区别在排障时非常关键。
    """
    raw = path.read_bytes()
    if raw[:2] == b"\xff\xfe":
        note = "UTF-16LE（含 BOM）—— PowerShell Tee-Object 的默认编码，不是解析器的问题"
        return raw[2:].decode("utf-16-le").encode("utf-8"), note
    if raw[:2] == b"\xfe\xff":
        note = "UTF-16BE（含 BOM）"
        return raw[2:].decode("utf-16-be").encode("utf-8"), note
    if raw[:3] == b"\xef\xbb\xbf":
        note = "UTF-8 with BOM（BOM 会污染第一帧的 id 字段）"
        return raw[3:], note
    return raw, "UTF-8 / ASCII，干净"


def summarize(ev) -> str:
    p = ev.payload or {}
    t = ev.type.value
    if t == "meta":
        return f"mode={p.get('mode')}"
    if t == "stage":
        bits = [str(p.get("stage", "?"))]
        if p.get("tool"):
            bits.append(f"tool={p['tool']}")
        if p.get("status"):
            bits.append(f"status={p['status']}")
        if p.get("ok") is not None:
            bits.append(f"ok={p['ok']}")
        if p.get("step_id"):
            bits.append(f"step={p['step_id']}")
        return " ".join(bits)
    if t == "token":
        d = p.get("delta", "")
        return f"{len(d):>4d} 字符  {d[:34]!r}{'...' if len(d) > 34 else ''}"
    if t == "citations":
        cits = p.get("citations", [])
        docs = {c.get("doc_name", "?") for c in cits}
        pages = sum(len(str(c.get("text", ""))) for c in cits)
        return f"{len(cits)} 条引用 / {len(docs)} 个文档 / 原文共 {pages} 字符"
    if t == "table":
        return f"{len(p.get('records', []))} 行"
    if t == "retrieval":
        return json.dumps(p, ensure_ascii=False)[:60]
    if t == "final":
        ans = str(p.get("answer", ""))
        br = p.get("business_result", {})
        return f"answer {len(ans)} 字符, business_result keys={list(br)[:4]}"
    if t in ("error", "cancelled"):
        return f"code={p.get('code')} retryable={p.get('retryable')}"
    return json.dumps(p, ensure_ascii=False)[:60]


async def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"找不到文件：{path}")
        return

    raw, note = load_sse_bytes(path)
    frames = [ev async for ev in parse_sse_stream(feed(raw, CHUNK_SIZE))]

    print(f"文件      : {path}")
    print(f"编码      : {note}")
    print(f"原始字节  : {len(raw):,} bytes（已转为 UTF-8）")
    print(f"分帧符统计: CRLF-CRLF x{raw.count(SEP_CRLF)}  |  LF-LF x{raw.count(SEP_LF)}")
    print(f"按 {CHUNK_SIZE} 字节切块: 约 {-(-len(raw) // CHUNK_SIZE)} 个网络 chunk")
    print(f"还原业务帧: {len(frames)} 条   <- chunk 数 != frame 数，这就是全部要点\n")

    if not frames:
        print("没解析出任何帧。先确认抓包文件本身是不是有效 SSE 字节流。")
        return

    t0 = frames[0].ts
    print(f"{'#':>3} {'事件':<10} {'距上条':>8} {'距开始':>8} {'字节':>7}  载荷摘要")
    print("-" * 108)
    prev = t0
    silent = []
    for i, ev in enumerate(frames, 1):
        dt = ev.ts - prev
        el = ev.ts - t0
        size = len(json.dumps(ev.model_dump(), ensure_ascii=False).encode("utf-8"))
        flag = ""
        if dt > 1.0 and i > 1:
            flag = "  <== 静默"
            silent.append((i, ev.type.value, dt))
        print(f"{i:>3} {ev.type.value:<10} {dt:>7.3f}s {el:>7.3f}s {size:>7,}  {summarize(ev)}{flag}")
        prev = ev.ts

    print("-" * 108)
    total = frames[-1].ts - t0
    print(f"首帧到终态: {total:.3f}s")

    sizes = [len(json.dumps(e.model_dump(), ensure_ascii=False).encode("utf-8")) for e in frames]
    print(f"载荷总量  : {sum(sizes):,} bytes（其中最大一帧占 {max(sizes) / sum(sizes):.0%}）")

    print("\n=== 三个容易看漏的点 ===")

    if silent:
        print(f"1) 最长静默 {max(s[2] for s in silent):.1f}s（第 {[s[0] for s in silent]} 帧前）")
        print("   静默期=用户盯着屏幕什么都没发生。心跳就是为它存在的，")
        print("   而心跳间隔默认 15s：静默不超过 15s 时，你一个 heartbeat 都看不到。")
    else:
        print("1) 没有超过 1s 的静默间隙。")

    tok = [e for e in frames if e.type.value == "token"]
    if tok:
        uniq = {e.ts for e in tok}
        print(f"2) {len(tok)} 条 token 帧，但只有 {len(uniq)} 个不同时间戳。")
        if len(uniq) == 1:
            print("   -> 整段答案是在同一瞬间切块推出来的，不是模型逐字生成。")
            print("      前端做'逐字打字机'动画是在表演，不是在复刻真实 token 流。")
        else:
            span = max(uniq) - min(uniq)
            print(f"   -> token 真实时间跨度 {span:.3f}s，平均每字 {span / max(len(tok), 1) * 1000:.0f}ms。")

    fin = [e for e in frames if e.type.value == "final"]
    cit = [e for e in frames if e.type.value == "citations"]
    if fin and tok:
        tok_text = "".join(e.payload.get("delta", "") for e in tok)
        fin_ans = str(fin[-1].payload.get("answer", ""))
        if fin_ans and tok_text.startswith(fin_ans[:20]):
            print("3) final 里的 answer 与前面 token 拼接的内容重复。")
            print(f"   最终帧 {len(json.dumps(fin[-1].model_dump(), ensure_ascii=False)):,} 字符，"
                  "等于把答案和引用又传了一遍。")
    if cit:
        print(f"   引用帧携带原文全文（{len(json.dumps(cit[0].payload, ensure_ascii=False)):,} 字符），"
              "前端不该原样渲染。")


if __name__ == "__main__":
    asyncio.run(main())
