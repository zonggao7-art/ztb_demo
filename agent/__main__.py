"""
Agent CLI 入口。

用法：
    # 单次问答
    python -m agent --question "招标方式有哪些？"

    # 交互问答模式
    python -m agent --interactive
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

# 将项目根目录加入 path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from agent import AgentGraph
from agent.streaming import EventType, StreamEvent
from public_kb.citations import format_citations


def setup_logging(verbose: bool = False) -> None:
    """配置日志。"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="招投标智能助手 — LangGraph Agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python -m agent --question
  python -m agent --interactive
  python -m agent --question "..." --async   # 阶段 1 起可用，验证异步图
        """,
    )
    parser.add_argument(
        "--question", "-q",
        type=str,
        help="单次问答",
    )
    parser.add_argument(
        "--interactive", "-i",
        action="store_true",
        help="交互问答模式",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="显示调试日志",
    )
    parser.add_argument(
        "--async",
        dest="use_async",
        action="store_true",
        help="使用异步图（默认同步；阶段 1 调试时验证双轨入口）",
    )
    parser.add_argument(
        "--stream",
        action="store_true",
        help="启用分帧流式输出（需搭配 --async 或流式能力）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="单次问答总超时秒数",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    if not args.question and not args.interactive:
        parser.print_help()
        return

    # 初始化 Agent
    mode = "async" if args.use_async else "sync"
    print(f"正在初始化招投标智能助手 (mode={mode})...")
    try:
        agent = AgentGraph(async_enabled=args.use_async)
        print("✅ 助手就绪！\n")
    except Exception as e:
        print(f"❌ 初始化失败: {e}")
        sys.exit(1)

    if args.interactive:
        if args.stream:
            print("⚠️ 交互模式暂不启用 --stream，已回退同步模式。")
        run_interactive(agent)
    elif args.question:
        if args.stream:
            run_single_stream(agent, args.question, deadline_s=args.timeout)
        else:
            run_single(agent, args.question)


def _render_business_data(data: Any) -> None:
    """渲染 business_result.data 附带的结构化信息。

    优先输出知识问答的完整引用（chunk 文本 + 元数据，保留【来源N】标签）；
    无引用时回退到 legacy sources 计数；结构化查询输出记录数。
    """
    if not isinstance(data, dict):
        return

    citations = data.get("citations")
    if citations:
        block = format_citations(citations)
        if block:
            print(block + "\n")
        return

    if "sources" in data:
        print(f"引用来源: {len(data['sources'])} 条")
    elif "records" in data:
        print(f"查询记录: {len(data.get('records', []))} 条")


def run_single(agent: AgentGraph, question: str) -> None:
    """单次问答。"""
    print(f"🙋 问题: {question}\n")
    print("⏳ 思考中...\n")

    try:
        result = agent.invoke(question)
        print(f"🤖 回答:\n{result['answer']}\n")

        biz = result.get("business_result", {})
        branch = biz.get("branch", "unknown")
        print(f"── 分支: {branch} ──")

        _render_business_data(biz.get("data"))

    except Exception as e:
        print(f"❌ 错误: {e}")


def _render_stream_event(event: StreamEvent) -> str:
    if event.type is EventType.STAGE:
        stage = event.payload.get("stage", "")
        icons = {
            "router_done": "🧭",
            "retrieval_start": "🔍",
            "intent_done": "📋",
            "sql_start": "🗄️",
            "doc_qa_placeholder": "📄",
            "fallback": "🧯",
        }
        return f"\n{icons.get(stage, '⚙️')} {stage}\n"
    if event.type is EventType.TOKEN:
        return str(event.payload.get("delta", ""))
    if event.type is EventType.TABLE and not event.payload.get("synthetic_quiet"):
        return ""
    return ""


def run_single_stream(agent: AgentGraph, question: str, *, deadline_s: float | None = None) -> None:
    """单次分帧问答。"""
    print(f"🙋 问题: {question}\n")
    terminal_types = {EventType.FINAL, EventType.ERROR, EventType.CANCELLED}
    final_event = None

    async def consume():
        nonlocal final_event
        try:
            async for event in agent.astream(question, deadline_s=deadline_s):
                output = _render_stream_event(event)
                if output:
                    sys.stdout.write(output)
                    sys.stdout.flush()
                if event.type is EventType.CITATIONS:
                    pass
                if event.type in terminal_types:
                    final_event = event
        except KeyboardInterrupt:
            print("\n⏹️ 已取消")

    try:
        asyncio.run(consume())
    except KeyboardInterrupt:
        print("\n⏹️ 已取消")

    if not final_event or final_event.type not in terminal_types:
        print("\n❌ 流式请求未正常结束")
        return
    if final_event.type is not EventType.FINAL:
        payload = final_event.payload
        print(f"\n❌ 终态[{final_event.type.value}]: {payload.get('message', payload.get('reason', ''))}")
        return

    answer = str(final_event.payload.get("answer", ""))
    if answer:
        print(f"\n🤖 回答:\n{answer}")

    biz = (agent.get_state("default") or {}).get("business_result", {})
    branch = biz.get("branch", "unknown")
    print(f"── 分支: {branch} ──")
    _render_business_data(biz.get("data"))


def run_interactive(agent: AgentGraph) -> None:
    """交互问答模式。"""
    print("💬 交互问答模式 (输入 'quit' 或 'exit' 退出，'clear' 清空会话)")
    print("─" * 60)
    print("👋 您好！我是「招投标智能助手」，很高兴为您服务！\n")
    print("   我可以帮您处理以下事务：\n")
    print("   1️⃣  专业知识问答")
    print("       招投标法律法规、招标方式、评标规则、采购流程等专业问题")
    print("   2️⃣  中标情报获取")
    print("       查询历史中标项目、产品中标价格、中标公司等市场情报")
    print("   3️⃣  企业工商信息查询")
    print("       查询公司基本信息、经营范围、注册资本等工商数据")
    print("   4️⃣  企业风险排查")
    print("       查询公司不良记录、行政处罚、经营异常等风险信息\n")
    print("   直接输入您的问题，我会尽力为您解答！\n")
    print("─" * 60 + "\n")

    thread_id = "interactive-session"
    turn = 0

    while True:
        try:
            question = input(f"[{turn}] 🙋 您: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n👋 再见！")
            break

        if not question:
            continue

        if question.lower() in ("quit", "exit"):
            print("👋 再见！")
            break

        if question.lower() == "clear":
            thread_id = f"interactive-session-{turn}"
            print("🔄 已清空对话历史\n")
            continue

        print("⏳ ...")

        try:
            result = agent.invoke(question, thread_id=thread_id)
            print(f"🤖 助手: {result['answer']}\n")
            _render_business_data(
                (result.get("business_result") or {}).get("data")
            )
            turn += 1
        except Exception as e:
            print(f"❌ 错误: {e}\n")


if __name__ == "__main__":
    main()
