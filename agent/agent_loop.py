"""agent_loop — Agent 自助调用原型（tool-calling 循环）。

P1 工具化的端到端验证交付物：让 LLM 通过 bind_tools 自主选择并调用
工具库（agent.tools）中的检索工具，验证工具 schema/description 可被
模型正确理解与使用。默认关闭（.env: AGENT_TOOLS_ENABLED=true 开启）。

复用蓝图既定路线（agent_evolution_comprehensive_blueprint.md §2.2）：
LangGraph prebuilt create_react_agent + 工具库导出，不自造循环。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool

from public_kb.config import Settings
from public_kb.llm_factory import create_llm

from .tools import get_enabled_tools

logger = logging.getLogger(__name__)

AGENT_SYSTEM_PROMPT = """你是招投标智能助手的自主调用 Agent，通过调用工具完成用户任务。

可用工具分两类：
1. 法规知识类（RAG）：
   - search_public_kb：检索法规证据片段（推荐；返回原文片段，由你综合作答并注明 doc/chapter 来源）
   - knowledge_qa：一步到位生成法规问答（内含拒答判断与标准化引用）
2. 结构化数据类（SQL，入参必须是完整规范的公司全称 / 项目编号）：
   - query_company_info：企业工商情报查询
   - query_company_penalty：企业行政处罚/不良记录查询（精确匹配）
   - query_bid_records：招投标中标记录查询（project_number 或 company_name/purchaser 至少其一）
   - search_business_data：关键词兜底检索三张核心业务表

使用准则：
- 涉及法规依据的问题优先 search_public_kb，回答需引用片段的文档/章节来源
- 涉及企业/项目数据的问题使用对应 SQL 工具，公司名必须使用用户提供的完整全称，不得自行缩写
- 工具返回 ok=false 且 code=invalid_params 时，按 error.message 纠正参数后重试一次
- 工具返回空结果时如实告知用户未命中，严禁编造数据
- 信息足够后立即给出最终回答；单次任务工具调用不超过 {max_steps} 次
"""


def build_tool_agent(
    *,
    llm: Optional[BaseChatModel] = None,
    checkpointer: Any = None,
    tools: Optional[list[BaseTool]] = None,
    settings: Optional[Settings] = None,
):
    """构建 tool-calling Agent 原型。

    Args:
        llm: 对话模型；None 则按 Settings 自动创建。
        checkpointer: 会话记忆后端；None 则无持久化。
        tools: 显式工具列表（测试注入用）；None 则从工具库按白名单取用。
        settings: 配置；None 则从 .env 加载。

    Raises:
        RuntimeError: 总开关未开启 / 工具库为空。
    """
    settings = settings or Settings()
    if not settings.agent_tools_enabled:
        raise RuntimeError(
            "Agent 自助调用未启用：请在 .env 设置 AGENT_TOOLS_ENABLED=true 后重试"
        )
    if tools is None:
        tools = get_enabled_tools()
    if not tools:
        raise RuntimeError(
            "工具库为空：请检查 AGENT_TOOLS_WHITELIST 是否过滤掉了全部工具"
        )

    from langgraph.prebuilt import create_react_agent

    # 注：LangGraph V1 起该 API 标记迁移至 langchain.agents.create_agent（V2 移除）；
    # 项目依赖仅含 langchain-core，未安装完整 langchain 包，故继续使用 langgraph.prebuilt。
    model = llm or create_llm(settings)
    return create_react_agent(
        model=model,
        tools=tools,
        prompt=AGENT_SYSTEM_PROMPT.format(max_steps=settings.agent_loop_max_steps),
        checkpointer=checkpointer,
        name="ztb_tool_agent",
    )


def _agent_invoke_config(settings: Settings, thread_id: str) -> dict:
    """Agent 调用 config。

    recursion_limit 按 graph 超级步计（每轮工具调用消耗 model+tools 两步），
    AGENT_LOOP_MAX_STEPS 语义为「工具调用轮数」，故 ×2 后留 2 步余量给首尾。
    """
    return {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": settings.agent_loop_max_steps * 2 + 2,
    }


def _render_tool_trace(new_messages: list) -> list[str]:
    """从新增消息中提取工具调用轨迹（供 CLI 展示）。"""
    lines: list[str] = []
    for msg in new_messages:
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            for call in msg.tool_calls:
                args_preview = str(call.get("args", {}))[:120]
                lines.append(f"🔧 调用工具 {call.get('name')}({args_preview})")
        elif isinstance(msg, ToolMessage):
            content = str(msg.content)
            status = "✅" if getattr(msg, "status", "success") == "success" else "⚠️"
            lines.append(f"   {status} {content[:160]}")
    return lines


def run_interactive_agent(compiled: Any, settings: Settings) -> None:
    """Agent 自助调用交互会话（--agent-mode 入口）。"""
    print("🤖 Agent 自助调用模式（工具库驱动，quit/exit 退出，clear 清空会话）")
    print("─" * 60)
    tools_desc = ", ".join(t.name for t in get_enabled_tools())
    print(f"可用工具: {tools_desc}\n")

    thread_id = "agent-session"
    seen = 0
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
            thread_id = f"agent-session-{turn}"
            seen = 0
            print("🔄 已清空会话\n")
            continue

        print("⏳ Agent 思考与调用工具中...")
        try:
            result = compiled.invoke(
                {"messages": [HumanMessage(content=question)]},
                config=_agent_invoke_config(settings, thread_id),
            )
            messages = result.get("messages", [])
            for line in _render_tool_trace(messages[seen:]):
                print(line)
            seen = len(messages)

            final = next(
                (m.content for m in reversed(messages) if isinstance(m, AIMessage) and m.content),
                "",
            )
            print(f"\n🤖 助手: {final or '（未产出回答）'}\n")
            turn += 1
        except KeyboardInterrupt:
            print("\n⏹️ 已取消\n")
        except Exception as e:
            print(f"❌ 错误: {e}\n")


def run_single_agent(compiled: Any, settings: Settings, question: str) -> None:
    """Agent 自助调用单次问答（--agent-mode --question 入口）。"""
    print(f"🙋 问题: {question}\n⏳ Agent 思考与调用工具中...\n")
    try:
        result = compiled.invoke(
            {"messages": [HumanMessage(content=question)]},
            config=_agent_invoke_config(settings, "single-agent"),
        )
        messages = result.get("messages", [])
        for line in _render_tool_trace(messages):
            print(line)
        final = next(
            (m.content for m in reversed(messages) if isinstance(m, AIMessage) and m.content),
            "",
        )
        print(f"\n🤖 回答:\n{final or '（未产出回答）'}")
    except Exception as e:
        print(f"❌ 错误: {e}")
