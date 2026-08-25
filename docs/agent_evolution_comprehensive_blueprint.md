# 招投标智能助手：从「LLM 路由系统」升级为「真正 Agent 系统」全栈演进蓝图

> **报告版本**：v1.0
> **生成日期**：2026-08-24
> **评估范围**：基于 [agent/graph.py](agent/graph.py) + [agent/router.py](agent/router.py) + [public_kb/rag_engine.py](public_kb/rag_engine.py) 的现有骨架
> **前置依赖**：本文不重复以下两份文档的结论，仅在其基础之上扩展——请先阅读：
> 1. [docs/agent_architecture.md](docs/agent_architecture.md)（现行骨架）
> 2. [docs/deep_agents_integration_design.md](docs/deep_agents_integration_design.md)（Quality Guard / Critic 设计）
> 3. [docs/multimodal_evolution_design.md](docs/multimodal_evolution_design.md)（多模态演进）
>
> **核心命题**：当前系统严格意义上**还不是一个真正的 Agent 系统**——它是"披着 LangGraph 外衣的 LLM 路由 + 业务节点"骨架。本报告系统评估如何升级到 ReAct / Tool Calling / DeepAgent / Multi-Agent / MCP / SKII / 多模态（识图）的真正 Agent 形态。

---

## 0. TL;DR

| 维度 | 现状 | 真正 Agent 系统 |
|------|------|----------------|
| **驱动模式** | 静态路由（router 1-shot 决策 → 1 个 node） | 动态决策（LLM 在 ReAct 循环中自主选/组/串工具） |
| **工具抽象** | 0 个 Tool（业务节点不暴露给 LLM） | N 个 Tool（含数据库/知识库/HITL/子 Agent） |
| **自我反思** | 无（quality_guard 仅作后置闸口） | 内置自我反思/规划/子任务委派 |
| **多步协作** | 不可强制 1-of-N | 可串/并/嵌套调用 |
| **HITL** | 不原生支持 | 原生 `interrupt()` |
| **子 Agent** | 单一 Agent | Supervisor / Worker / Critic 等角色分工 |
| **跨服务** | 进程内 | MCP 跨进程/跨机器 |
| **多模态** | 纯文本 | 文本 + 图像 + PDF 等 |

**核心结论**：

1. **技术栈 7 项可分为三档**：
   - **必须引入**（DeepAgent、ReAct、Tool Calling）— 是"Agent 系统"的最小定义
   - **强烈建议引入**（Multi-Agent、MCP）— 是规模化与生态扩展的必经之路
   - **按需引入**（SKII、多模态识图）— 业务驱动型，引入需先确认 ROI

2. **推荐实施节奏**：5 阶段、约 16 周，**建议分两个大版本（v2.0 / v3.0）发布**

3. **关键约束**：现有 P0 守卫链与 citation 体系是项目最大资产，**任何改造必须可旁路、可灰度、可回滚**

---

## 一、现状与"真正 Agent 系统"的差距分析

### 1.1 现有系统的"非 Agent 性"清单

| 维度 | Agent 系统应具备 | 当前实现 | 差距 |
|------|---------------|---------|------|
| **自主规划** | LLM 在循环中规划步骤 | router 一次性判定后硬约束 | 🔴 高 |
| **工具调用** | bind_tools 后 LLM 自驱调用 | 0 个 Tool 暴露给 LLM（router 的 Tool 仅用于分类） | 🔴 高 |
| **自我反思** | LLM 评估自身输出 | 仅有 plan 的 quality_guard（节点级） | 🟠 中-高 |
| **子任务委派** | Agent 可调 sub-agent | 单层 StateGraph | 🔴 高 |
| **HITL** | `interrupt()` 原生 | 不支持（需通过 Chat 多轮实现） | 🔴 高 |
| **持久记忆** | Checkpointer 支持任意中间步骤回放 | 仅整轮结束后 checkpoint | 🟡 中 |
| **多模态** | image/audio/pdf 全模态 | 纯文本 | 🔴 高（如业务需要） |
| **跨进程/服务** | MCP 等开放协议 | 进程内调用 | 🟠 中 |
| **规划可解释** | 每个 thinking step 可追溯 | router 单一判定无法追溯 | 🟠 中 |
| **错误恢复** | 优雅重试/降级/切换工具 | `_with_fallback` 装饰器兜底 | 🟢 低 |

### 1.2"伪 Agent"现象的具体表现

| 现象 | 在本项目中的体现 | 业务影响 |
|------|------------------|---------|
| **强制 1-of-N 路由** | [graph.py:152-162](agent/graph.py#L152) `add_conditional_edges` 5 选 1 | 复杂查询必须拆分多轮 |
| **Tool Calling 仅作分类** | [router.py:48-89](agent/router.py#L48) 5 个 `@tool` 仅返回固定字符串 | LLM 没有"办事"工具 |
| **quality_guard 缺失自我反思** | plan 中已存在但未实施 | LLM 幻觉可能漏过 |
| **业务节点之间不通信** | `node_price_inquiry` 不调用 `node_knowledge_qa` | 法规+数据交叉问答不可行 |
| **多模态全无** | doc_qa 是占位 | 用户上传图片无法处理 |
| **无 HITL 机制** | 任何节点都不能中断等待人工 | 风控审批需重写 |
| **记忆仅整轮快照** | Checkpointer 默认结束态 | 中间步骤不可回放 |

---

## 二、7 项技术分别评估

### 2.1 ReAct（Reason + Act）

#### 2.1.1 概念
ReAct = "让 LLM 在 `Thought → Action → Observation` 循环中自主推理与行动"，是 Agent 系统的最核心范式。

#### 2.1.2 与本项目契合度

| 适配点 | 现状基础 | 改造 |
|--------|---------|------|
| Router 已具备 1-shot 决策 | [router.py:99-130](agent/router.py) | 增加 ReAct 循环能力 |
| price_inquiry 内部已经是"伪 ReAct" | [price_inquiry/node.py:166-495](agent/nodes/price_inquiry/node.py) | 把内部链路显式化为 ReAct |
| LangGraph 支持 `ToolNode` + `tools_condition` | 已用 LangGraph | 引入即可 |

#### 2.1.3 落地路径

```python
# 推荐：保留现有 router，新增一个"高级 ReAct"模式
from langgraph.prebuilt import ToolNode, tools_condition

# 现状 router（fast-path）不变
# 新增 react_router_node（slow-path，多步复合）
react_node = StateGraph(AgentState) \
    .add_node("agent", call_model_with_tools) \
    .add_node("tools", ToolNode(all_tools)) \
    .add_conditional_edges("agent", tools_condition) \
    .add_edge("tools", "agent")  # 循环
```

#### 2.1.4 收益与风险
- **收益**：解决"复合查询无法编排"的根本问题（SC-1、SC-2 业务场景）
- **风险**：recursion 失控 → 必须设 `recursion_limit`
- **token 成本**：约 +60%~+150%（基于 3-5 步 ReAct）

**适配度评分**：⭐⭐⭐⭐⭐ **必须引入**

---

### 2.2 Tool Calling

#### 2.2.1 概念
LLM 通过 schema 描述的工具主动调用真实函数，是 Agent 系统的事实标准。

#### 2.2.2 与本项目契合度

| 评估点 | 详情 |
|--------|------|
| **现有 Tool** | 仅 [router.py:48-89](agent/router.py) 5 个虚拟 Tool |
| **业务节点 Tool 化** | 需把 node_knowledge_qa / node_price_inquiry / node_general_chat / node_doc_qa 封装为 `@tool` |
| **状态隔离** | Tool 的返回 schema 直接成为 LLM 下一轮 prompt 的一部分，需谨慎设计避免 prompt 膨胀 |

#### 2.2.3 工具清单设计（待实施时建议）

```python
# agent/tools/__init__.py
TOOL_REGISTRY = {
    # 业务能力类（核心）
    "query_bidding":     {"impl": node_price_inquiry, "schema": SearchIntent},
    "query_company":     {"impl": node_price_inquiry, "schema": SearchIntent},
    "search_law":        {"impl": node_knowledge_qa, "schema": Question},
    "chat_general":      {"impl": node_general_chat, "schema": Question},
    
    # 复合能力类
    "extract_project_number": {"impl": _extract_project_number, "schema": Text},
    "normalize_company_name": {"impl": _normalize_company_name, "schema": Text},
    "compute_aggregation":    {"impl": _compute_agg, "schema": AggregationParams},
    
    # 协作类
    "request_human_review":   {"impl": _human_review, "schema": ReviewRequest},  # HITL
    "transfer_to_specialist": {"impl": _transfer, "schema": SpecialistType},     # Multi-Agent
    "ask_user_clarification": {"impl": _ask_clarify, "schema": Options},        # 反问
    
    # 多模态类
    "analyze_image":   {"impl": _analyze_img, "schema": {image_url: str, question: str}},
    "parse_pdf":       {"impl": _parse_pdf, "schema": {file_ref: str}},
    
    # 工具类
    "execute_sql":          {"impl": _run_sql, "schema": SQLQuery},
    "milvus_search":        {"impl": _milvus, "schema": {collection: str, query: str, top_k: int}},
    "calculate":            {"impl": _calc, "schema": {expr: str}},
    "current_date":         {"impl": lambda: datetime.now()},  # 内置工具
}
```

#### 2.2.4 风险与设计要点

| 风险 | 设计应对 |
|------|---------|
| **Tool 数量爆炸导致 prompt 膨胀** | 工具分组 + context-only 按需 bind（如 "basic" / "advanced" / "multimodal" 三个 set） |
| **LLM 误调工具** | Pydantic 严格 schema + 业务前置校验下沉到工具参数 |
| **Token 消耗** | 单次 ReAct ≤ 6 步，`recursion_limit=6` |
| **Citation 链路断裂** | Tool 返回结构显式携带 `citations`、`sources` 字段 |

**适配度评分**：⭐⭐⭐⭐⭐ **必须引入**

---

### 2.3 DeepAgent（深度自治 Agent）

#### 2.3.1 概念
"DeepAgent" 不是某个具体框架，而是 **Anthropic 在 2024-Q4 提出的 Agent 设计哲学**——主张 Agent 应具备：
1. **规划能力**（Planning）：把复杂任务拆解为子任务
2. **反思能力**（Reflection）：评估自身输出
3. **工具使用**（Tool Use）：调用工具
4. **持久记忆**（Long-term Memory）
5. **子任务委派**（Sub-task Delegation）

它与 ReAct 是"超集与子集"关系：ReAct 是 DeepAgent 的运行时核心，DeepAgent 在外层补齐规划、记忆、反思。

#### 2.3.2 与本项目契合度

- 项目已存在 [deep_agents_integration_design.md](docs/deep_agents_integration_design.md)（v2026-08-24 撰）
- 该文档聚焦"Quality Guard（Critic 节点）"——是 DeepAgent "反思能力"的一个具体落地
- **DeepAgent 的其他三大能力（规划/记忆/委派）在该文档中未涵盖**，这是本次评估的关键补全点

#### 2.3.3 项目内的 DeepAgent 能力映射

| DeepAgent 能力 | 现状 | 改造 |
|---------------|------|------|
| **规划（Planning）** | 缺失（router 1-shot） | 引入 Planner：把 query 拆为子步骤，每步选 Tool |
| **反思（Reflection）** | plan 中 | 实施 QualityGuard + ReAct 内置自我反思 |
| **工具使用（Tool Use）** | 缺失（Tool 仅用于分类） | 见 §2.2 |
| **持久记忆（Long-term Memory）** | 仅 Checkpointer 整轮快照 | 引入"摘要压缩器"定期 checkpoint + 全局知识图谱 |
| **子任务委派（Sub-task Delegation）** | 不存在 | 见 §2.4 Multi-Agent |

#### 2.3.4 落地路径

```python
# DeepAgent 全功能 manifest（项目内 v3.0 目标）
class DeepAgentV3:
    state: AgentState  # 单一共享 State（不破现有原则）
    
    def plan(self, query: str) -> List[SubTask]:
        """任务规划：query → 可执行的子任务列表"""
        ...
    
    def reflect(self, output: Any, ground_truth: Any = None) -> ReflectionVerdict:
        """自我反思：评估输出、识别缺陷、决定是否重做"""
        ...
    
    def use_tools(self, task: SubTask) -> ToolResult:
        """工具调用"""
        ...
    
    def compress_memory(self) -> None:
        """长期记忆压缩"""
        ...
    
    def delegate(self, task: SubTask, agent_name: str) -> Any:
        """子任务委派到 sub-agent"""
        ...
```

#### 2.3.5 收益与风险

| 收益 | 风险 |
|------|------|
| 彻底解决"复合任务" | 调试复杂度指数上升 |
| 提供 planning 可解释性 | Token/延迟成本显著增加 |
| 与学术/工业界最新 Agent 范式对齐 | 对小团队认知负担重 |

**适配度评分**：⭐⭐⭐⭐⭐ **必须引入**（分阶段，先从 quality_guard 开始）

---

### 2.4 Multi-Agent 协同

#### 2.4.1 概念
将一个复杂任务拆给多个专职 Agent 协作（Supervisor / Worker / Critic 等角色分工）。

#### 2.4.2 业内主流编排模式

| 模式 | 描述 | 适用场景 |
|------|------|---------|
| **Hierarchical（树形）** | Supervisor 分发 → Worker 执行 → Aggregator 汇总 | 任务可清晰拆解 |
| **Collaborative（网状）** | Agent 互相通信，自由协作 | 复杂谈判、辩论 |
| **Pipeline（流水线）** | Agent A → Agent B → ... | 流程化任务 |
| **Debate（辩论）** | 多个 Agent 产生答案 → Critic 评判 | 极高准确性需求 |
| **Supervisor-Tool** | Supervisor 是 LLM，把 Worker 当 Tool 调用 | **当前阶段最推荐** |

#### 2.4.3 本项目内的 Multi-Agent 角色设计

| 角色 | 职责 | 实现位置 |
|------|------|---------|
| **MainAgent（Supervisor）** | 接收 query、规划、路由、整合 | [graph.py](agent/graph.py) 顶层（升级后） |
| **KnowledgeExpert（Worker）** | 仅处理知识库问答 | `node_knowledge_qa` 升级版 |
| **DataAnalyst（Worker）** | 仅处理结构化数据查询 | `node_price_inquiry` 升级版 |
| **DocumentAnalyst（Worker）** | 仅处理文档问答 | `node_doc_qa` 升级版 |
| **CriticAgent** | 质量把关、Reflexion | `quality_guard`（plan 中） |
| **HITLAgent** | 人工介入（特殊场景） | `interrupt()` 调用 |
| **GeneralChatAgent** | 闲聊 fallback | `node_general_chat` 升级版 |

#### 2.4.4 Multi-Agent 与"工具化封装"的关系

**核心洞察**：Multi-Agent 与 ReAct + Tool Calling 本质上是**同一件事的不同抽象层级**——
- "把 Worker Agent 暴露为 Supervisor 的 Tool" = Multi-Agent 的一种实现方式
- LangGraph 1.x 后强烈推荐 `Supervisor → handoff(tool)` 范式

#### 2.4.5 收益与风险

| 收益 | 风险 |
|------|------|
| 每个 Worker 可独立演进 | 跨 Agent 状态共享复杂 |
| 与 LangGraph 1.x 范式一致 | 调试需要 trace 多 Agent 协作链 |
| 为未来客户化（不同客户不同 sub-agent）奠基 | 早期成本投入大 |

**适配度评分**：⭐⭐⭐⭐ **强烈建议引入（v3.0 中后期）**

---

### 2.5 MCP（Model Context Protocol）

#### 2.5.1 概念
MCP（Anthropic 于 2024-Q4 提出的开放协议）让 Agent 通过统一协议调用外部工具和数据源，类似于"Agent 时代的 USB 接口"。

#### 2.5.2 与本项目契合度

| 契合点 | 价值 |
|--------|------|
| **将 Milvus/MySQL 暴露为 MCP Server** | 未来其他 Agent 可调用本项目的数据 |
| **调用外部 MCP Server** | 例如：政府公告 MCP、政策法规 MCP、信用中国 MCP |
| **企业内部工具集成** | 例如：OA/CRM/工单系统 MCP |
| **多 Agent 跨服务** | 与 Multi-Agent 互补 |

#### 2.5.3 落地路径（建议第二阶段）

```python
# mcp_servers/__init__.py
async def serve():
    """本项目作为 MCP Server，对外提供："""
    server = MCPServer([
        Tool(name="search_law_kb", description="..."),
        Tool(name="query_bidding_db", description="..."),
        Tool(name="analyze_contract_pdf", description="..."),
    ])
    await server.serve_stdio()  # 或 serve_http(port=8080)
```

```python
# 客户端调用示例
client = MCPClient("http://other-agent:8080")
result = await client.call_tool("check_blacklist", {"company": "XX"})
```

#### 2.5.4 收益与风险

| 收益 | 风险 |
|------|------|
| 标准化互操作 | 生态尚未完全成熟（2024-Q4 才发布） |
| 让本项目成为"Agent 基础设施" | 需锁定 langchain-mcp-adapters 版本 |
| 与未来 Agent 市场对接 | 自定义工具与 MCP 协议映射的工作量 |

**适配度评分**：⭐⭐⭐⭐ **强烈建议引入（v3.0 早期）**

---

### 2.6 SKII — 解读与评估

> ⚠️ **歧义声明**："SKII" 在 Agent 生态中至少存在 3 种可能含义。本节按最可能的顺序分别评估，由你在审批时明确选定。

#### 选项 A：Microsoft Semantic Kernel（AI 编排框架）
- **介绍**：微软推出的 LLM 编排框架，定位与 LangChain/LangGraph 并列
- **优势**：与 .NET/C# 生态无缝集成；内置规划器与记忆
- **与本项目契合度**：低——本项目已用 LangChain/LangGraph 体系，**引入 Semantic Kernel 等于技术栈切换**，不适合作为后续演进

#### 选项 B：SKII = "Skills"（技能系统）
- **介绍**：Anthropic、LangChain 等框架内置的"Skill"机制——把高频/复杂操作封装为可复用、可组合、可热更新的"技能包"
- **示例**：`bid_aggregation_skill`、`law_citation_skill`、`contract_review_skill`
- **与本项目契合度**：⭐⭐⭐⭐ **高**——本质上是"业务节点模板化 + 热插拔"
- **落地建议**：把现有 `node_*.py` 视为"内置技能"，新增 `agent/skills/` 目录承载"可插拔技能"

#### 选项 C：第三方商业 SKII 平台
- 不在评估范围内（缺乏公开材料）

#### 我的强烈建议
**采用选项 B（Skills 技能系统）解读**，原因是：
1. 与 LangGraph/Anthropic Agent 生态对齐
2. 与现有 `node_*.py` 改造为 `@tool` 的思路一致
3. 是 Agent 系统规模化（多客户、多行业）的天然抽象

#### Skills 系统设计

```python
# agent/skills/__init__.py — 注册中心
SKILL_REGISTRY = {
    "bid_aggregator": {
        "version": "1.0.0",
        "author": "core-team",
        "tools": ["compute_max_winning_amount", "rank_by_amount"],
        "prompts": "skills/bid_aggregator/prompts/*.md",
        "config_schema": SkillConfigSchema,
    },
    "law_citation": {
        "version": "2.1.0",
        "tools": ["search_law", "extract_citations", "validate_citation"],
        "prompts": "...",
    },
}
```

#### 收益与风险

| 收益 | 风险 |
|------|------|
| 多客户/多场景共享技能库 | 技能版本管理复杂 |
| 业务节点热更新 | 技能间冲突/循环依赖需管理 |
| 与 Anthropic/Claude Skills 对齐 | 缺少成熟的开源 reference 实现 |

**适配度评分**：⭐⭐⭐⭐（按选项 B 解读）/**⭐⭐**（按选项 A 解读）

> 🙏 **请在审批本报告时明确"SKII"的具体含义**——选项 B 是我的强烈推荐。

---

### 2.7 多模态（识图功能）

#### 2.7.1 概念
让 Agent 接收并理解图像（OCR / 图像描述 / 视觉问答）。

#### 2.7.2 与本项目契合度

| 业务场景 | 现状 | 改造 |
|---------|------|------|
| 用户上传扫描件（PDF/图片）并提问 | doc_qa 占位 | doc_qa + 多模态解析 |
| 用户上传资质证书图片并询问合规性 | 不支持 | 新增 |
| 用户上传招标文件截图并询问条款 | 不支持 | 新增 |
| OCR 营业执照识别并自动入库 | 不支持 | 新增 |

#### 2.7.3 技术选型

| 方案 | 优势 | 劣势 | 选型 |
|------|------|------|------|
| **Claude/GPT-4V 直接识图** | 一行代码调用，效果最好 | 闭源、按 token 计费贵 | ⭐⭐⭐⭐⭐ 主选 |
| **PaddleOCR / Tesseract** | 开源、本地化、私有化 | 仅 OCR 不做语义理解 | ⭐⭐⭐ 备选 |
| **Qwen-VL / GLM-4V** | 开源、可私有化、效果好 | 需 GPU 部署 | ⭐⭐⭐⭐ 备选 |
| **EasyOCR + LLM** | 本地 OCR + LLM 语义 | 两阶段链路复杂 | ⭐⭐⭐ |

**强烈推荐**：项目内 minigpt 或 claude-3-5-sonnet 接 vision 接口，避免重复造轮子

#### 2.7.4 多模态在 StateGraph 中的接入

```python
# agent/nodes/multimodal_router.py
def node_image_understanding(state: AgentState) -> dict:
    """识图节点：处理用户上传图片的请求"""
    image_url = state.get("image_ref")
    question = state.get("last_user_text")
    
    # 用 Vision LLM 理解图片
    result = vision_llm.invoke([
        {"type": "text", "text": question or "请描述这张图片"},
        {"type": "image_url", "image_url": {"url": image_url}},
    ])
    
    return {"business_result": {"branch": "image_qa", "answer": result}}
```

#### 2.7.5 收益与风险

| 收益 | 风险 |
|------|------|
| 极大扩展业务边界（OCR 资质、识图招标书） | Vision API 调用成本高 |
| 与现有 doc_qa 自然融合 | 需要存储/带宽支持图片上传 |
| 客户感知价值大 | 涉及合规（个人证件信息） |

**适配度评分**：⭐⭐⭐⭐ **强烈建议引入（v3.0 早期）**

---

## 三、整体技术栈演进图

```
当前 (v1)
  ├── LangGraph StateGraph（路由 + 5 业务节点）
  ├── DeepSeek LLM
  ├── MySQL + Milvus
  └── 0 Tool

v2.0 (建议 6-8 周)
  ├── + ReAct 节点（处理复杂复合查询）
  ├── + Tool Calling 框架（业务节点全部 @tool 化）
  ├── + Quality Guard（plan 中已存在，待实施）
  ├── + HITL（小额风控场景）
  └── + Skills 注册中心（agent/skills/）

v3.0 (建议 8-10 周)
  ├── + Multi-Agent Supervisor
  ├── + MCP Server/Client
  ├── + 多模态（识图、PDF）
  ├── + 长期记忆压缩器
  └── + 业务可热加载的 Skill 库

v4.0 (远期)
  ├── + Multi-Agent Debate（极高准确度场景）
  ├── + Agent Marketplace（MCP 注册中心）
  └── + 跨企业数据交换协议
```

---

## 四、与业务适配度评估

### 4.1 七项技术与业务的匹配分析

| 技术 | 业务驱动 | 实施门槛 | 适配度 | 推荐时机 |
|------|---------|---------|--------|---------|
| **ReAct** | 复合查询（SC-1、SC-2） | 中 | ⭐⭐⭐⭐⭐ | v2.0 |
| **Tool Calling** | 所有业务节点的接口标准化 | 低-中 | ⭐⭐⭐⭐⭐ | v2.0 |
| **DeepAgent** | 反思能力（quality_guard）+ 规划 | 中 | ⭐⭐⭐⭐⭐ | v2.0 中后期 |
| **Multi-Agent** | 角色化业务（专家轮值） | 高 | ⭐⭐⭐⭐ | v3.0 |
| **MCP** | 跨系统集成、客户化 | 中 | ⭐⭐⭐⭐ | v3.0 |
| **SKII（Skills）** | 业务节点模板化/客户化 | 低-中 | ⭐⭐⭐⭐ | v2.0 后期 |
| **多模态（识图）** | 资质证书、招标书截图 | 中 | ⭐⭐⭐⭐ | v3.0 早期 |

### 4.2 业务真实场景清单

| 场景 ID | 场景描述 | 启用技术 |
|---------|---------|---------|
| SC-1 | "中国移动近三年智慧城市类项目最高金额" | ReAct + Tool Calling |
| SC-2 | "政府采购法限额 + 某项目是否触发" | ReAct + Multi-knowledge |
| SC-3 | "上传资质证书图片，询问合规风险" | 多模态 + Skills |
| SC-4 | 风控敏感查询（>1000 万）的二次确认 | HITL（Tool Calling + interrupt） |
| SC-5 | 多客户共享数据源 | MCP Server |
| SC-6 | 与客户现有 OA/CRM 集成 | MCP Client |
| SC-7 | 招标文件分析（PDF + 法规交叉引用） | Multi-Agent + 多模态 + Skills |

### 4.3 与现有项目的兼容性

| 既有资产 | 兼容性 | 风险 |
|---------|--------|------|
| 11 个 test_p0_*.py | 100% 兼容（v2.0 工具化阶段不变更行为） | 🟢 |
| answer_templates.py | 100% 兼容（与 Skills 注册兼容） | 🟢 |
| quality_guard plan | 100% 兼容（v2.0 与 plan 对齐） | 🟢 |
| citation 链路 | 兼容（Tool 返回结构携带 citations） | 🟡 |
| MySQL/Milvus 连接池 | 兼容（MCP 化或保留） | 🟢 |

---

## 五、风险综合分析

### 5.1 风险矩阵

| 风险 | 影响 | 概率 | 等级 | 缓解措施 |
|------|------|------|------|---------|
| **R1：P0 守卫链被打穿**（LLM 自驱调工具绕过守卫） | 🔴 | 中 | 🔴 | 工具参数 Pydantic schema + 工具入口前置校验 |
| **R2：Token 成本失控**（ReAct 循环无限制） | 🟠 | 高 | 🔴 | `recursion_limit=6` + 单 query token 预算 |
| **R3：调试复杂度上升**（多步决策链难以追溯） | 🟠 | 高 | 🔴 | 强制 decision log（每个 tool_call 写日志） + LangSmith 接入 |
| **R4：Multi-Agent 状态共享失败** | 🟠 | 中 | 🟠 | 单一 State 原则 + 显式 State schema 演进 |
| **R5：MCP 生态不成熟** | 🟡 | 中 | 🟠 | 锁定版本，等待生态成熟，先做内部 MCP 试验 |
| **R6：Skills 滥注册**（业务节点越来越多） | 🟡 | 中 | 🟠 | 技能注册审批流程 + Registry 自动校验 |
| **R7：多模态合规风险**（个人证件信息） | 🟠 | 中 | 🟠 | 上传前脱敏、Vision 阶段不打日志、客户数据隔离 |
| **R8：现有测试大规模失效** | 🔴 | 高 | 🔴 | v2.0 阶段所有现有测试不变更行为；新增测试覆盖新功能 |
| **R9：团队学习曲线** | 🟡 | 高 | 🟠 | 引入内部"Agent 训练营"、分阶段培训 |

### 5.2 隐性风险

| 风险 | 说明 |
|------|------|
| **认知负担过载** | 7 项技术不是 7 个独立工作，而是耦合演进；建议选定 1-2 个先打通 |
| **供应商锁定** | LangChain/LangGraph/Anthropic/MCP 都是相对新的标准，避免过早绑定 |
| **测试基础设施不足** | 多步决策链需要 mock LLM 的 deterministic 测试；目前项目无此框架 |

---

## 六、实施路线图

### 6.1 五大阶段总览

```
2026 Q3 — v2.0（基础 Agent 化，6-8 周）
  ├── Phase 1（2 周）: Tool Calling 基础设施
  ├── Phase 2（2 周）: ReAct 节点 + 双轨并行
  ├── Phase 3（2 周）: Quality Guard + HITL 试点
  └── Phase 4（2 周）: Skills 注册中心

2026 Q4 — v3.0（Multi-Agent + 生态扩展，8-10 周）
  ├── Phase 5（3 周）: Multi-Agent Supervisor
  ├── Phase 6（3 周）: MCP Server/Client
  └── Phase 7（4 周）: 多模态 + Skills 市场
```

### 6.2 详细阶段说明

#### Phase 1：Tool Calling 基础设施（**2 周**）
- 把 4 个业务节点封装为 `@tool`
- 保留 router（fast-path），不切换行为
- 新增 `agent/tools/__init__.py` 注册中心
- 不引入 LangGraph `ToolNode`（避免一次性大改造）
- **退出标准**：现有全部 P0 测试 100% 通过；CLI 调用方式不变

#### Phase 2：ReAct 节点 + 双轨并行（**2 周**）
- router 增加"复合查询检测"分支
- 新增 `react_node`，内部跑 ReAct（recursion_limit=6）
- 灰度开启 5% 流量
- **退出标准**：复杂查询（如 SC-1）响应质量可量测提升

#### Phase 3：Quality Guard + HITL 试点（**2 周**）
- 按 [deep_agents_integration_design.md](docs/deep_agents_integration_design.md) Phase A-D 实施 quality_guard
- 增加 `request_human_review_tool` + LangGraph `interrupt()`
- 仅在"金额 >1000 万"的风控场景启用 HITL
- **退出标准**：quality_guard 灰度开启 50%，LLM-as-Judge 采样率 5%

#### Phase 4：Skills 注册中心（**2 周**）
- 落地理念：把节点 → Tool → Skill 三层抽象
- 新增 `agent/skills/__init__.py`，注册 bid_aggregator / law_citation / contract_review 等技能
- Skill 可独立版本化、可热加载（先在内存实现）
- **退出标准**：至少 2 个业务功能可"以 Skill 形式加载"

#### Phase 5：Multi-Agent Supervisor（**3 周**）
- LangGraph 1.x 的 `handoff` 范式落地
- MainAgent（Supervisor）通过 Tool 调用各 WorkerAgent
- WorkerAgent = 现业务节点的"Agent 化升级版"
- **退出标准**：MainAgent 可独立处理"先查询 → 再校验 → 再修订"3 步任务

#### Phase 6：MCP Server/Client（**3 周**）
- 把本项目核心能力（search_law、query_bidding）暴露为 MCP Server
- MCP Client 调用外部数据源（如政策公示 MCP）
- **退出标准**：本项目可被任意其他 MCP Client 调用；可调用至少 1 个外部 MCP Server

#### Phase 7：多模态 + Skills 市场（**4 周**）
- 多模态：Claude-3.5-sonnet 接 vision 接口 / 备选 Qwen-VL 私有化
- Skills 市场：技能可视化注册 / 版本管理 / 灰度发布
- **退出标准**：识图功能上线，至少 3 个 Skills 可被热加载

### 6.3 关键里程碑

| 里程碑 | 时间 | 业务验收 |
|--------|------|---------|
| **M1** | Phase 1 完成 | Tool Calling 基础设施就绪；现有功能 0 退化 |
| **M2** | Phase 2 完成 | 复杂查询响应质量 P50 提升 30%（主观评估） |
| **M3** | Phase 4 完成 | Skills 框架可承载 2 个新业务功能 |
| **M4** | Phase 5 完成 | Multi-Agent 决策链可追溯；HITL 在风控场景应用 |
| **M5** | Phase 7 完成 | 识图功能上线；MVP 级别 MCP 互通 |

### 6.4 与 [docs/deep_agents_integration_design.md](docs/deep_agents_integration_design.md) 的衔接

本报告**未重复** deep_agents_integration_design.md 中 quality_guard 的细节，但已确认：
- quality_guard = DeepAgent 反思能力的具体落地
- 在 Phase 3 实施（与本蓝图保持一致）
- **本报告补充**：除反思外，DeepAgent 还需规划/记忆/委派能力，分别在 Phase 2/4/5 实施

---

## 七、关键决策建议

### 7.1 决策清单（请逐项 ✅）

| 决策项 | 选项 | 推荐 |
|--------|------|------|
| 1. 是否启动 v2.0 改造？ | 是 / 否 / 待评估 | ✅ 是 |
| 2. 7 项技术是否全部引入？ | 全部 / 按推荐档位 | ✅ 按推荐档位（必须/强烈建议/按需） |
| 3. SKII 的具体含义？ | A. Semantic Kernel / B. Skills 技能系统 / C. 其他 | ✅ **B（强烈推荐）** |
| 4. 第一阶段从哪开始？ | Tool Calling / ReAct / Quality Guard | ✅ **Tool Calling（Phase 1）** |
| 5. 灰度策略？ | 全量切换 / 5% 灰度 / 双轨并行 | ✅ **双轨并行（零侵入）** |
| 6. Token 预算上限？ | 不限 / 2x / 4x | ✅ **≤ 3x** |
| 7. 多模态模型选型？ | Claude-3.5-V / Qwen-VL / PaddleOCR + LLM | ✅ **Claude-3.5-V（主选） + Qwen-VL（备选）** |
| 8. 现有 P0 测试是否 100% 保留？ | 是 / 否 | ✅ **是** |
| 9. 是否采用 LangGraph 1.x 的 handoff 范式？ | 是 / 否 | ✅ **是** |
| 10. MCP 阶段是否可独立延期？ | 不可延 / 可延 | ✅ **可延**（v3.0 中期，根据业务反馈决定） |

### 7.2 不要做的事情（反面清单）

| ❌ 决策 | 原因 |
|--------|------|
| 不要在 Phase 1-2 全量替换 router | 当前 router 稳定，价值已证明 |
| 不要在 Phase 1-2 引入 Multi-Agent | 会导致复杂度爆炸 |
| 不要过早引入 MCP | 生态未成熟，且本项目尚未有跨服务需求 |
| 不要同时启用 quality_guard 全 3 档决策 | 先 PASS-only，再 REPAIR，再 REJECT |
| 不要让 DeepAgent 的"规划"功能在 v2.0 投入使用 | 规划是 v3.0 任务 |
| 不要把现有 answer_templates.py 重写 | 它是已被多场景验证的资产 |

---

## 八、长期愿景（v4.0+ 展望）

### 8.1 Agent 平台化
- 把本项目升级为"招投标 Agent 平台"
- 通过 MCP 接入更多业务系统
- 通过 Skills 市场支持多客户定制

### 8.2 多 Agent 市场
- 不同客户可订阅不同 Skills
- Skills 可由第三方开发者贡献
- 形成"招投标 Agent 生态"

### 8.3 关键能力地图

| 能力 | v1 现状 | v2 目标 | v3 目标 | v4 愿景 |
|------|--------|---------|---------|---------|
| 路由 | 1-shot | 双轨 | Supervisor | Multi-Agent 市场 |
| 工具 | 0 | N+Core | N+Core+MCP | N+MCP+Community |
| 反思 | 0 | QualityGuard | Multi-Judge | Debate |
| 记忆 | 整轮 | 检查点+摘要 | 长期知识图谱 | 跨 Agent 共享 |
| 多模态 | 0 | 图片 | 图片+PDF+音频 | 全模态 |
| HITL | 0 | 通用 interrupt | 智能路由 interrupt | 自适应 interrupt |
| 可扩展 | 改代码 | 业务节点 | 注册 Skill | 注册 MCP Server |

---

## 九、报告结论

### 一句话总结

**业务前置已成（中-高）、技术路径清晰、改造风险可分期隔离；建议立即启动 v2.0 改造，按 5 阶段（约 16 周）分两个大版本（v2.0/v3.0）逐步上线 — 把现有"LLM 路由系统"改造为"真正 Agent 系统"。**

### 三个最关键的决策点

1. **🔴 关键 1**：SKII 的具体含义——请明确告知（建议选 B：Skills 技能系统）
2. **🔴 关键 2**：是否启动 v2.0——若启动，建议从 Phase 1（Tool Calling）开始，**零侵入**
3. **🟠 关键 3**：多模态模型选型——Claude-3.5-V 主选 + Qwen-VL 私有化备选

### 三个让你放心推进的事实

- ✅ 现有 11 个 P0 测试在 v2.0 阶段全部保留（行为零变更）
- ✅ 改造分阶段实施，任一阶段可独立旁路回滚
- ✅ 项目内已存在 [deep_agents_integration_design.md](docs/deep_agents_integration_design.md) 与 [multimodal_evolution_design.md](docs/multimodal_evolution_design.md) 两份规划，本文是它们的"全栈蓝图"扩展

---

## 附录

### 附录 A：关键代码定位一览

| 关注点 | 文件 | 行号 | 现状 |
|--------|------|------|------|
| Router 节点 | [agent/router.py](agent/router.py) | 99-242 | 5 个虚拟 Tool 已用 Tool Calling |
| StateGraph 主图 | [agent/graph.py](agent/graph.py) | 108-175 | 5 选 1 条件边 |
| State 字段 | [agent/state.py](agent/state.py) | 19-39 | 仅 3 字段 |
| 业务节点契约 | [CLAUDE.md](CLAUDE.md) | §节点接口契约 | `(state) → dict` |
| 多步复用点 | [agent/nodes/price_inquiry/node.py](agent/nodes/price_inquiry/node.py) | 166-495 | 已经有 micro-ReAct 流程 |
| Citation 体系 | [test/test_citation_tracing.py](test/test_citation_tracing.py) | 全文件 | R1-R7 校验规则 |
| Quality Guard plan | [docs/deep_agents_integration_design.md](docs/deep_agents_integration_design.md) | 全文件 | 嵌入式 Critic 设计 |
| 多模态 plan | [docs/multimodal_evolution_design.md](docs/multimodal_evolution_design.md) | 全文件 | 多模态演进蓝图 |

### 附录 B：现有 reference 文档优先级阅读清单

如需深入实施细节，请按以下顺序阅读：

1. [docs/agent_architecture.md](docs/agent_architecture.md) — 现行骨架（必读）
2. [docs/deep_agents_integration_design.md](docs/deep_agents_integration_design.md) — Quality Guard 详细设计（Phase 3 必读）
3. [docs/three_core_modules_design_and_feasibility.md](docs/three_core_modules_design_and_feasibility.md) — 业务契约（任何阶段必读）
4. [docs/multimodal_evolution_design.md](docs/multimodal_evolution_design.md) — 多模态演进（Phase 7 必读）
5. [docs/data_architecture_upgrade_plan.md](docs/data_architecture_upgrade_plan.md) — 数据架构升级（任何阶段参考）
6. [docs/project_overview.md](docs/project_overview.md) — 总体参考（详尽但篇幅长）

### 附录 C：术语对照表

| 缩写 | 全称 | 说明 |
|------|------|------|
| **ReAct** | Reason + Act | LLM 在循环中推理与行动 |
| **Tool Calling** | 工具调用 | LLM 通过 schema 描述调用真实函数 |
| **DeepAgent** | 深度自治 Agent | Anthropic 提出的 Agent 设计哲学，包含规划/反思/工具/记忆/委派 |
| **Multi-Agent** | 多智能体协作 | 多个专职 Agent 角色分工 |
| **MCP** | Model Context Protocol | Anthropic 提出的 Agent 互操作协议（"Agent USB 接口"） |
| **SKII** | ⚠️ 待确认 | 倾向解读为 Skills 技能系统；如指 Semantic Kernel 请明确 |
| **HITL** | Human-in-the-Loop | 人在回路（LangGraph `interrupt()` 支撑） |
| **Supervisor** | 监管者（Agent） | Multi-Agent 中的角色 |
| **Worker** | 工作者（Agent） | Multi-Agent 中的角色 |
| **Critic** | 评审者（Agent） | 反思型 Agent |

### 附录 D：报告间的引用关系

```
                    ┌─ agent_architecture.md       (现行骨架)
                    │
本报告 ─────────────┼─ deep_agents_integration_design.md  (Quality Guard)
                    │
                    ├─ multimodal_evolution_design.md     (多模态演进)
                    │
                    └─ project_overview.md       (总览，本蓝图的总参考)
```

---

*— 报告完 —*

**下一步建议**：审阅本报告，特别确认 §7.1 的 10 个决策项（特别是 SKII 的具体含义）；确认后我会从 **Phase 1：Tool Calling 基础设施** 入手——该阶段对当前行为零侵入，新增代码不动现有行为，可随时回滚。
