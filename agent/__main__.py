"""
Agent CLI 入口。

用法：
    # 单次问答
    python -m agent --question "招标方式有哪些？"

    # 交互问答模式
    python -m agent --interactive

    # 工具库清单（无需 LLM/基础设施）
    python -m agent --list-tools

    # Agent 自助调用模式（需 .env: AGENT_TOOLS_ENABLED=true）
    python -m agent --agent-mode --question "XX公司有无不良记录？"
    python -m agent --agent-mode --interactive
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from contextlib import aclosing
from pathlib import Path
from typing import Any
from dataclasses import replace
from uuid import uuid4

# 将项目根目录加入 path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from agent import AgentGraph
from agent.nodes.general_chat import GENERAL_GUIDANCE
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
  python -m agent --list-tools               # 查看工具库清单
  python -m agent --agent-mode --interactive # Agent 自助调用模式（需 AGENT_TOOLS_ENABLED=true）
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
        help="token 级流式输出（自动启用异步图；可与 --question 或 --interactive 搭配）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="单次问答总超时秒数",
    )
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="列出工具库清单（工具名/标签/参数 schema），无需初始化 LLM",
    )
    parser.add_argument(
        "--agent-mode",
        dest="agent_mode",
        action="store_true",
        help="Agent 自助调用模式：LLM 通过 tool-calling 自主调用工具库（需 AGENT_TOOLS_ENABLED=true）",
    )

    parser.add_argument("--execution-mode", choices=["legacy", "hybrid", "unified"],
                        help="仅覆盖本次 CLI 进程；hybrid 仅放行本会话，不修改 .env")
    parser.add_argument("--thread-id", help="指定测试会话 ID；clear 后生成新 ID")
    args = parser.parse_args()
    if args.thread_id is not None and (not args.thread_id.strip() or "," in args.thread_id):
        parser.error("--thread-id 不能为空或包含逗号")
    if args.agent_mode and (args.execution_mode or args.thread_id or args.stream or args.timeout):
        parser.error("这些选项请使用 --execution-mode unified 入口，不要同时使用 --agent-mode")
    setup_logging(args.verbose)

    if args.list_tools:
        run_list_tools()
        return

    if args.agent_mode:
        run_agent_mode(args)
        return

    if not args.question and not args.interactive:
        parser.print_help()
        return

    # token 级流式事件只在异步节点链路（rag.astream → knowledge_qa_async）产出，
    # 同步节点的 rag.query() 是阻塞调用，不带 --async 时 --stream 只会一次性出全文。
    if args.stream and not args.use_async:
        args.use_async = True
        print("💡 --stream 已自动启用异步图（token 级流式仅在异步节点链路可用）")

    # 初始化 Agent
    mode = "async" if args.use_async else "sync"
    print(f"正在初始化招投标智能助手 (mode={mode})...")
    try:
        from public_kb.config import Settings
        thread_id = args.thread_id or (uuid4().hex if args.interactive else "default")
        settings = Settings()
        if args.execution_mode:
            settings = replace(settings, agent_execution_mode=args.execution_mode,
                               agent_react_rollout_percent=0,
                               agent_react_thread_allowlist=thread_id if args.execution_mode == "hybrid" else "")
        agent = AgentGraph(async_enabled=args.use_async, settings=settings)
        agent._cli_execution_mode = args.execution_mode
        _describe_session(agent, thread_id)
        print("✅ 助手就绪！\n")
    except Exception as e:
        print(f"❌ 初始化失败: {e}")
        sys.exit(1)

    try:
        if args.interactive:
            if args.stream:
                run_interactive_stream(agent, deadline_s=args.timeout, thread_id=thread_id)
            else:
                run_interactive(agent, thread_id=thread_id, deadline_s=args.timeout)
        elif args.question:
            if args.stream:
                run_single_stream(agent, args.question, deadline_s=args.timeout, thread_id=thread_id)
            else:
                run_single(agent, args.question, thread_id=thread_id, deadline_s=args.timeout)
    finally:
        agent.close()


def _describe_session(agent, thread_id):
    from agent.execution.service import execution_path
    settings = getattr(agent, "_settings", None)
    if settings is None:
        return
    if getattr(agent, "_cli_execution_mode", None) == "hybrid":
        # clear creates a fresh history while preserving the explicit CLI choice.
        agent._settings = replace(settings, agent_react_thread_allowlist=thread_id)
        settings = agent._settings
    path = execution_path(settings, thread_id)
    reason = ("本次 CLI 显式选择" if getattr(agent, "_cli_execution_mode", None) else
              "默认统一主 Agent" if path == "unified" else
              "命中白名单/灰度" if path == "hybrid" else
              "配置为 legacy" if settings.agent_execution_mode == "legacy" else "未命中白名单/灰度")
    labels = {"unified": "unified（统一主 Agent）", "hybrid": "hybrid（受控分流）", "legacy": "legacy（旧流程）"}
    print(f"执行入口：{labels[path]}；{reason}；会话：{thread_id}")


def run_list_tools() -> None:
    """打印工具库清单（注册 + 白名单过滤视角），无需初始化 LLM/基础设施。"""
    from agent.tools import GLOBAL_TOOL_REGISTRY, get_enabled_tools, register_default_tools

    register_default_tools()
    enabled = {t.name for t in get_enabled_tools()}
    manifest = GLOBAL_TOOL_REGISTRY.to_manifest()

    print(f"工具库清单（共 {len(manifest)} 个，启用 {len(enabled)} 个）\n")
    for item in manifest:
        mark = "✅" if item["name"] in enabled else "⛔"
        tags = "/".join(item["tags"])
        readonly = "只读" if item["readonly"] else "读写"
        print(f"{mark} {item['name']}  [{tags}] {readonly} v{item['version']}")
        print(f"   {item['description']}")
        params = item.get("parameters", {}).get("properties", {})
        if params:
            required = set(item.get("parameters", {}).get("required", []))
            print(f"   参数: {', '.join(f'{k}{"*" if k in required else ""}' for k in params)}")
        if not enabled:
            break_note = "（全部被 AGENT_TOOLS_WHITELIST 过滤）" if item["name"] not in enabled else ""
            if break_note:
                print(f"   {break_note}")
        print()


def run_agent_mode(args: argparse.Namespace) -> None:
    """Agent 自助调用模式入口（tool-calling 循环）。"""
    from agent.agent_loop import (
        build_tool_agent,
        run_interactive_agent,
        run_single_agent,
    )
    from public_kb.config import Settings

    settings = Settings()
    if not args.question and not args.interactive:
        print("⚠️  --agent-mode 需要搭配 --question 或 --interactive 使用。")
        return

    print("正在初始化 Agent 自助调用模式...")
    try:
        compiled = build_tool_agent(settings=settings)
    except RuntimeError as e:
        print(f"❌ {e}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ 初始化失败: {e}")
        sys.exit(1)

    print("✅ Agent 就绪！\n")
    if args.interactive:
        run_interactive_agent(compiled, settings)
    else:
        run_single_agent(compiled, settings, args.question)


def _render_business_data(data: Any) -> None:
    """渲染 business_result.data 附带的结构化信息。

    优先输出知识问答的完整引用（chunk 文本 + 元数据，保留【来源N】标签）；
    无引用时回退到 legacy sources 计数；结构化查询输出记录数。
    """
    if not isinstance(data, dict):
        return
    execution = data.get("execution")
    if execution:
        print(f"执行结果：{execution.get('actual_branch')}；状态：{data.get('status')}；"
              f"工具：{', '.join(execution.get('tools', [])) or '无'}")

    citations = data.get("citations")
    if citations:
        block = format_citations(citations, include_text=data.get("citation_display") != "compact")
        if block:
            print(block + "\n")
        return

    if "sources" in data:
        print(f"引用来源: {len(data['sources'])} 条")
    elif "records" in data:
        print(f"查询记录: {len(data.get('records', []))} 条")


def run_single(agent: AgentGraph, question: str, *, thread_id="default", deadline_s=None) -> None:
    """单次问答。"""
    print(f"🙋 问题: {question}\n")
    print("⏳ 思考中...\n")

    try:
        result = agent.invoke(question, thread_id=thread_id, deadline_s=deadline_s)
        print(f"🤖 回答:\n{result['answer']}\n")

        biz = result.get("business_result", {})
        branch = biz.get("branch", "unknown")
        print(f"── 分支: {branch} ──")

        _render_business_data(biz.get("data"))

    except Exception as e:
        print(f"❌ 错误: {e}")


def _render_stream_event(event: StreamEvent) -> str:
    if event.type is EventType.META:
        return f"\n执行链路：{event.payload.get('mode', 'legacy')}\n"
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
        labels = {"router_start": "正在理解任务", "router_done": "任务分流完成",
                  "execution_start": "开始执行", "validation_start": "正在核验证据",
                  "validation_done": "证据核验完成", "tool_call": "工具执行",
                  "execution_degraded": "执行未完整完成", "router_rejected": "分流方案未通过校验",
                  "model_repair": "申请不符合规则，正在纠正一次",
                  "request_failed": "请求未通过核验", "input_limit": "输入容量达到上限"}
        details = []
        for key in ("mode", "reason", "task_id", "tool", "status", "ok", "code", "attempt", "bytes", "limit"):
            if key in event.payload:
                details.append(f"{key}={event.payload[key]}")
        if event.payload.get("issues"):
            details.append("错误位置=" + ", ".join(
                f"{issue['path']} ({issue['type']})" for issue in event.payload["issues"]))
        tasks = event.payload.get("tasks", [])
        if tasks:
            details.append("任务=" + ", ".join(f"{t['task_id']}:{t['capability']}" for t in tasks))
        return f"\n{icons.get(stage, '⚙️')} {labels.get(stage, stage)} {'；'.join(details)}\n"
    if event.type is EventType.TOKEN:
        return str(event.payload.get("delta", ""))
    if event.type is EventType.TABLE and not event.payload.get("synthetic_quiet"):
        return ""
    return ""


def _consume_astream_turn(
    agent: AgentGraph,
    question: str,
    thread_id: str = "default",
    *,
    deadline_s: float | None = None,
    runner: asyncio.Runner | None = None,
) -> StreamEvent | None:
    """消费一次流式问答：token/stage 实时打印，返回终态事件（中断返回 None）。"""
    terminal_types = {EventType.FINAL, EventType.ERROR, EventType.CANCELLED}
    final_event = None
    rendered_parts: list[str] = []

    async def consume():
        nonlocal final_event
        async with aclosing(agent.astream(
            question, thread_id=thread_id, deadline_s=deadline_s,
        )) as events:
            async for event in events:
                output = _render_stream_event(event)
                if output:
                    sys.stdout.write(output)
                    sys.stdout.flush()
                if event.type is EventType.TOKEN:
                    rendered_parts.append(str(event.payload.get("delta", "")))
                if event.type in terminal_types:
                    final_event = event
                if event.type is EventType.FINAL:
                    answer = str(event.payload.get("answer", ""))
                    rendered = "".join(rendered_parts)
                    if answer and answer != rendered.strip():
                        if answer.startswith(rendered):
                            sys.stdout.write(answer[len(rendered):])
                        else:
                            sys.stdout.write(f"\n{answer}")
                        sys.stdout.flush()

    try:
        if runner is None:
            with asyncio.Runner() as single_runner:
                try:
                    single_runner.run(consume())
                finally:
                    if hasattr(agent, "_exit_stack"):
                        single_runner.run(agent.aclose())
        else:
            runner.run(consume())
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n⏹️ 已取消")
        return None

    if final_event and final_event.type not in terminal_types:
        return None
    return final_event


def run_single_stream(agent: AgentGraph, question: str, *, deadline_s: float | None = None, thread_id="default") -> None:
    """单次分帧问答。"""
    print(f"🙋 问题: {question}\n")
    final_event = _consume_astream_turn(agent, question, thread_id, deadline_s=deadline_s)

    if not final_event or final_event.type is not EventType.FINAL:
        payload = final_event.payload if final_event else {}
        print(f"\n❌ 终态[{getattr(final_event, 'type', 'none')}]: "
              f"{payload.get('message', payload.get('reason', '流式请求未正常结束'))}")
        return

    biz = final_event.payload.get("business_result") or {}
    if "data" not in biz:
        biz = (agent.get_state(thread_id) or {}).get("business_result", {})
    branch = biz.get("branch", "unknown")
    print(f"\n── 分支: {branch} ──")
    _render_business_data(biz.get("data"))


def run_interactive_stream(agent: AgentGraph, *, deadline_s: float | None = None, thread_id=None) -> None:
    """交互问答模式（流式：token 实时上屏）。"""
    with asyncio.Runner() as runner:
        try:
            _run_interactive_stream(agent, runner, deadline_s=deadline_s, thread_id=thread_id)
        finally:
            if hasattr(agent, "_exit_stack"):
                runner.run(agent.aclose())


def _run_interactive_stream(
    agent: AgentGraph, runner: asyncio.Runner, *, deadline_s: float | None = None, thread_id=None,
) -> None:
    """整段交互会话复用同一事件循环，避免 HTTP 连接池跨循环复用。"""
    print("💬 交互问答模式 — 流式输出 (输入 'quit' 或 'exit' 退出，'clear' 清空会话)")
    print("─" * 60 + "\n")

    from uuid import uuid4
    thread_id = thread_id or uuid4().hex
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
            thread_id = uuid4().hex
            _describe_session(agent, thread_id)
            print("🔄 已清空对话历史\n")
            continue

        final_event = _consume_astream_turn(
            agent, question, thread_id=thread_id, deadline_s=deadline_s, runner=runner,
        )
        turn += 1
        if final_event is None:
            continue
        if final_event.type is not EventType.FINAL:
            payload = final_event.payload
            print(f"\n❌ 终态[{final_event.type.value}]: "
                  f"{payload.get('message', payload.get('reason', ''))}")
            continue

        # token 已实时上屏，这里只补分支与引用信息
        print()
        biz = final_event.payload.get("business_result") or {}
        if "data" not in biz:
            biz = (agent.get_state(thread_id) or {}).get("business_result", {})
        print(f"── 分支: {biz.get('branch', 'unknown')} ──")
        _render_business_data(biz.get("data"))
        print()


def run_interactive(agent: AgentGraph, *, thread_id=None, deadline_s=None) -> None:
    """交互问答模式。"""
    print("💬 交互问答模式 (输入 'quit' 或 'exit' 退出，'clear' 清空会话)")
    print("─" * 60)
    print(GENERAL_GUIDANCE + "\n")
    print("─" * 60 + "\n")

    thread_id = thread_id or uuid4().hex
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
            thread_id = uuid4().hex
            _describe_session(agent, thread_id)
            print("🔄 已清空对话历史\n")
            continue

        print("⏳ ...")

        try:
            result = agent.invoke(question, thread_id=thread_id, deadline_s=deadline_s)
            print(f"🤖 助手: {result['answer']}\n")
            _render_business_data(
                (result.get("business_result") or {}).get("data")
            )
            turn += 1
        except Exception as e:
            print(f"❌ 错误: {e}\n")


if __name__ == "__main__":
    main()
