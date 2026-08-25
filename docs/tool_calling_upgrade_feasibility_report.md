# 招投标智能助手：从「路由式」改造为「Tool-Calling 式」编排可行性分析报告

> **报告版本**：v1.0
> **生成日期**：2026-08-24
> **报告范围**：基于当前 `agent/graph.py` + `agent/router.py` 的 LangGraph StateGraph 骨架
> **结论先行**：**可在保留现有 StateGraph 骨架的前提下逐步演进，并对“多功能协作 / 多步检索 / Human-in-the-Loop”业务价值显著**；但“完全替换路由为单 ReAct Agent”在当前数据规模与 P0 收益（SQL 确定性、引用溯源）下风险偏高。建议采用**「中间过渡式」**——Router 与 AgentExecutor **并存 + 按分支特征选择调度形式**。

---

## 一、TL;DR

| 维度 | 现状（路由式） | 升级方案（Tool-Calling） | 推荐度 |
|------|---------------|------------------------|-------|
| **核心机制** | 单一 LLM 调用 → 枚举判定 → 1:1 路由到固定节点 | LLM 在 ReAct 循环中自主调用多个 Tool → 决定何时结束 | — |
| **业务调用** | 用户输入 → 强制 5 选 1 → 单一业务节点 | 用户输入 → 动态组合 1~N 个工具调用 | — |
| **多步协作** | ❌ 一次只能路由到一个节点（hard 1-of-N） | ✅ 可串/并组合 multiple tools | ⭐⭐⭐⭐⭐ |
| **HITL** | ❌ 不可嵌入 | ✅ `interrupt_before` / `interrupt_after` 原生支持 | ⭐⭐⭐⭐⭐ |
| **SQL 确定性** | ⭐⭐⭐⭐⭐（意图固化 + P0 守卫） | ⭐⭐（工具参数由 LLM 自填，需重做守卫链） | ⚠️ |
| **引用溯源** | ⭐⭐⭐⭐⭐（节点固定链路，无 LLM 插话污染） | ⭐⭐⭐（LLM 可在二次回复中篡改/漏掉 citations） | ⚠️ |
| **LLM 调用次数** | 2 次/轮（router 1 + 节点内 1~2） | 1 次/轮起步，多步可达 N+2 | — |
| **开发成本** | 低 | 中-高（需改造 4 个节点、引入 ReAct 编排、重测所有 P0/回归用例） | ⚠️ |
| **业务前置** | 已成熟上线 | doc_qa 占位、未来多步复合任务真实存在 | ✅ 业务驱动成立 |

---

## 二、当前架构复盘（基于代码事实）

### 2.1 现状骨架

```
[start] → router (LLM 1-shot，枚举判定)
          ├─ knowledge_qa  ─→ END
          ├─ price_inquiry ─→ END    （内部再做二级 LLM 解析）
          ├─ general_chat  ─→ END
          ├─ doc_qa        ─→ END
          └─ fallback      ─→ END
```

**关键代码定位**：

| 关注点 | 文件 | 行号 | 现状 |
|--------|------|------|------|
| 路由判定 | [router.py](agent/router.py) | 99-130 | 5 个虚拟 Tool 强制 `tool_choice="required"` —— 本质已经是 Tool Calling，但只用来**分类**而不是**办事** |
| 状态定义 | [state.py](agent/state.py) | 19-39 | 仅 3 字段：`messages` / `router_intent` / `business_result` |
| 分发 | [graph.py](graph.py) | 152-162 | 单一 `add_conditional_edges`，意图枚举硬编码 |
| 业务节点 | [agent/nodes/](agent/nodes/) | — | 每个节点 `(state) → dict`，互相不感知 |
| 兜底 | [graph.py](graph.py) | 51-92 | `_with_fallback` 装饰器统一异常降级 |

### 2.2 路由式的两个事实

1. **"路由节点"本质上已经被 LLM 通过 Tool Calling 实现了**（`router.py:158-174` `_route_via_tool_calling`），只是把 Tool 用作"分类标签"而非"办事工具"。
2. **业务节点 _本质上_ 已经是 ReAct 链路的最终步**——比如 `node_price_inquiry` 内部已经经历了「LLM 解析意图 → 多阶段 SQL 召回 → 后置校验」的隐式多步流程（[node.py:166-495](agent/nodes/price_inquiry/node.py)）。

也就是说：**"路由式"并不是真的"全静态"，它在 router 一层用了 Tool Calling、在 price_inquiry 节点内部其实已经有 micro-ReAct 流程**。把它升级为"完整 Tool Calling 编排"，是把 router 层的"分类式 Tool Calling"扩散到整个 AgentGraph 的过程。

---

## 三、升级方案设想

### 3.1 目标形态（构想图）

```
[start] → "react_router_agent"
          │ bind_tools([knowledge_qa_tool,
          │             price_inquiry_tool,
          │             general_chat_tool,
          │             doc_qa_tool,
          │             fetch_citations_tool,
          │             handoff_to_human_tool,
          │             ...])
          │ ReAct loop:
          │   Step 1: LLM think → call knowledge_qa_tool("项目编号 AH2024-001 的中标详情？")
          │   Step 2: LLM → call price_inquiry_tool(company="中国移动", type="bidder_query")
          │   Step 3: LLM → finalize answer
          │   ↑ interrupt_before("node_a"): 等待人工确认
          └─→ END
```

### 3.2 三种可选改造路径

| 路径 | 描述 | 侵入度 | 适配度 |
|------|------|--------|-------|
| **路径 A**：完全替换 router 为 `create_react_agent`/`ToolNode` | 砍掉 router 节点，全部 LLM 通过 Tool 决策 | ⛔ 高 | ⭐⭐ |
| **路径 B**：Router + Tool Calling 并行路由 | 保留 router，但同时把 4 个业务节点暴露为 Tool，让 LLM 可"组合调用"或"回退到通用对话" | 中 | ⭐⭐⭐⭐⭐ |
| **路径 C**：双层架构，添加"高级复合节点" | router 维持不变，新增 `complex_task` 节点仅处理"多步复合查询"，内部走 ReAct | 低 | ⭐⭐⭐⭐ |

> **建议采用路径 B**——它**对现有 P0/回归测试零侵入**，又能在"存在真实多步需求"的场景下释放 Tool-Calling 的灵活性。

---

## 四、可行性分析

### 4.1 技术可行性（实现层面）

| 评估点 | 当前代码基础 | Tool-Calling 改造前提 | 可行性 |
|--------|--------------|---------------------|--------|
| **LangGraph 支持** | 已用 `langgraph.graph.StateGraph` | 同包的 `ToolNode` / `tools_condition` 完全支持 | ✅ 100% |
| **State 兼容性** | `AgentState(TypedDict)` 仅 3 字段 | ToolNode 不依赖新 State 字段；HITL 用 `interrupt()` 即可 | ✅ 100% |
| **LLM 能力** | DeepSeek-Chat，已用 `with_structured_output` / Tool Calling | DeepSeek 完整支持 Tool Calling（router 已验证） | ✅ 100% |
| **节点接口契约** | `(state) → dict` 单入参 → dict | `@tool` 装饰器可包装现有函数（`args_schema` 自动推断） | ✅ 100% |
| **Checkpointer 集成** | `MemorySaver` 可切 PostgreSQL | ToolNode 的中间步骤可被 Checkpointer 快照 | ✅ 100% |
| **流式输出** | graph.stream 正常 | ToolNode 支持 `stream_mode="events"` | ✅ 100% |
| **异常兜底** | `_with_fallback` 装饰器 | ReAct 节点的 tool_errors 需额外配置 | ⚠️ 工作量 1-2 天 |

### 4.2 LLM 能力可行性（行为层面）

| 评估点 | 风险 | 缓解措施 |
|--------|------|----------|
| **DeepSeek 是否能稳定选择正确工具** | 中-高 | router 节点已经稳定用 Tool Calling 5 选 1，准确率 ≈ 行业可用值；扩展到 6~8 个工具仍可控 |
| **DeepSeek 是否能做"串行多步"** | 中 | price_inquiry 内部已经实践了 3 步链路；在外层做串行是它的强项 |
| **DeepSeek 是否会陷入死循环** | 中 | LangGraph `recursion_limit` + 自定义 `agent_max_iterations` 双保险 |
| **DeepSeek 是否会"幻觉工具"** | 低 | `bind_tools(strict=True)` 或 `pydantic` schema 校验 |

### 4.3 运维可行性

| 评估点 | 现状 | Tool-Calling 版改动 |
|--------|------|---------------------|
| 日志/可观测性 | 单节点 `logger.info` | 需新增"思考链"日志（每个 tool_call 记录 time / args / result len）—— 1 天工作量 |
| 计费/Token 成本 | 2 次 LLM/轮，单次 query | ReAct 模式 3~6 次 LLM/轮（多步），token 成本约 ×2~4 |
| 延迟 | 路由 ~0.5s + 节点处理 ~1-3s | 首次响应延迟增加 0.5-1.5s（取决于迭代次数） |
| 测试基础设施 | 已有 11 个测试文件覆盖 P0 守卫 | Tool-Calling 决策不确定性强，需要新增 mock LLM 的可控测试框架 |

---

## 五、价值分析（为什么要改）

### 5.1 业务价值（真实场景）

| 业务场景 | 当前痛点 | Tool-Calling 改造后 | 价值 |
|----------|----------|---------------------|------|
| **SC-1: 多步复合查询**（如"中国移动近三年智慧城市类项目最高金额的中标情况"） | router 判定为 `price_inquiry` 后**由节点内部线性处理**，无法跨分支调用；要么一次返回一大坨，要么拆分多轮 | LLM 可先调 `price_inquiry_tool(company="中国移动", sub_route="bidding_query")` 拉取列表 → 再调 `price_inquiry_tool(sub_route="aggregation", filter="amount_desc")` 二次聚合 | ⭐⭐⭐⭐ |
| **SC-2: 法规 + 数据交叉问答**（如"某政府采购法要求公开招标金额限额是多少？XX 项目金额是否触发该要求？"） | 强制 1-of-N，要么只答法规、要么只查数据 | LLM 先调 `knowledge_qa_tool("政府采购法公开招标限额")` → 再调 `price_inquiry_tool(project_number="...")` → 综合回答 | ⭐⭐⭐⭐⭐ |
| **SC-3: 文档 + 知识库交叉** | doc_qa 是占位，多模态升级时会变成独立分支，与知识库割裂 | 文档问答与法规引用可在同一轮 ReAct 中交叉调用 | ⭐⭐⭐⭐ |
| **SC-4: Human-in-the-Loop 风控审批** | 当前不可打断，需"暂停—人工确认—继续"必须重写为多轮对话 | Tool `request_human_approval(reason, proposal)` + LangGraph `interrupt_before` 原生支持 | ⭐⭐⭐⭐⭐ |
| **SC-5: 跨会话业务委派** | 单一 thread_id 内单调 | LLM 通过 `transfer_to_specialist_agent` 类工具进行 sub-agent 调度 | ⭐⭐⭐ |
| **SC-6: 不确定时的反问** | 当前通过 fallback 引导模板 | `ask_user_clarification_tool(options=[...])` 让 LLM 生成选项菜单 | ⭐⭐⭐ |

> **结论**：当前业务的真实复合度集中在 SC-1、SC-2、SC-4，**Tool-Calling 确实能解决"刚性 1-of-N"的限制**。其他场景是"锦上添花"。

### 5.2 架构价值

| 维度 | 价值 |
|------|------|
| **可演进性** | 新增业务分支不再需要"router enum 加 Literal + Conditional Edge 加分支 + Prompt 同步更新"三处改动；只在 Tool 注册处加一个 `@tool` 装饰函数 |
| **HITL 原生化** | LangGraph 的 `interrupt()` 在 Tool-Calling 链路中无缝衔接；路由式架构下做 HITL 必须绕过 router，牵动 State |
| **Multi-Agent 演进** | Tool-Calling 是 sub-agent 编排的基础原语（`langgraph-supervisor`、`create_react_agent` 都基于它） |
| **可调试性** | 每个 tool_call 都是一个"可命名 / 可埋点 / 可重放"的原子单元，路由式的"硬 if-else"反而更死板 |

### 5.3 团队与心智模型价值

- **路由式**心智模型是"客服分流"——客服客户进 A/B/C 柜台，**不属于"现代 Agent"范式**。
- **Tool-Calling**心智模型是"全能助理"——助理掏出手机/电脑/相机/计算器，**每个工具都是被 LLM 自驱动的**。
- 未来 LLM 框架的所有新功能（computer use、code interpreter、sub-agent delegation）都以 Tool-Calling 为底座。

---

## 六、风险分析

### 6.1 风险矩阵

| 风险编号 | 风险描述 | 影响 | 概率 | 等级 | 缓解措施 |
|----------|----------|------|------|------|----------|
| R1 | **P0 守卫链被打穿**：当前 price_inquiry 的"实体名校验 / 项目编号校正 / 后置回溯"非常完善，LLM 自驱调用工具可能跳过这些守卫 | 高 | 中 | 🔴 | 守卫从"节点内前置"下沉到"工具参数 Pydantic 校验 + ToolNode 拦截" |
| R2 | **LLM 决策不确定**：同一 query 多次调用可能选不同工具，破坏可重现性 | 中 | 高 | 🟠 | `temperature=0` + 注入 few-shot 路由示例 + 关键场景加 deterministic 测试 |
| R3 | **LLM 自驱可能误调 doc_qa 占位**：doc_qa 还没有真实实现，Tool 暴露后用户调用一次全部体验都差 | 中 | 中 | 🟠 | doc_qa_tool 默认不挂载到 bind_tools，单独走 prompt 拦截 |
| R4 | **Token 成本上升**：1 个 query 可能 3-6 次 LLM 调用 | 中 | 高 | 🟠 | recursion_limit=5 + 单 query token 预算 + 监控 |
| R5 | **首次响应延迟增加**：ReAct 必须先 think → 再 call，比直接路由慢 0.5-1.5s | 低 | 高 | 🟡 | 保留 Router 节点作为"快速通道"，复杂分支再升级为 Tool Calling |
| R6 | **测试用例大规模失效**：现有 test_p0_*.py 假设了 1-of-N 路由 | 高 | 高 | 🔴 | 路径 B 双轨并行，老用例不动；新增 mock LLM 测试 |
| R7 | **LangGraph 升级兼容性**：`ToolNode` 在 v0.0.x 期间 API 仍会变动 | 低 | 中 | 🟢 | 锁定 `langgraph>=0.2,<0.4`，引入版本适配层 |
| R8 | **Citation 链路断裂**：knowledge_qa 的 citation 在节点结果中 `business_result.data.citations`；Tool-Calling 模式下 LLM 最终答复可能丢掉 citations | 高 | 中 | 🔴 | 工具返回结构中携带 citations；最终答复前显式插入引用字段 |

### 6.2 业务风险

| 风险 | 说明 |
|------|------|
| **执法高压场景下的"幻觉"** | 招投标领域对答案准确性极度敏感。LLM 自主串联多个工具时，如果某次 tool_call 返回空，LLM 可能"编"一个答案填补。需引入"empty result"显式分支 |
| **审计要求** | 政府客户可能要求**每一步决策可审计**，Tool-Calling 决策链天然符合（每个 tool_call 都是日志事件），反而是优势 |
| **SQL 注入** | 现状：`_normalize_intent_enums` 等专门处理；改造后：Tool 的参数 schema 通过 Pydantic 强校验，反而更安全 |

### 6.3 与现有 P0/P1 测试冲突分析

回顾现有 P0 修复（[test/](test/)）：

| P0 主题 | 与 Tool-Calling 兼容性 |
|---------|---------------------|
| P0-1 penalty 表直接查询 | ✅ 可封装为独立 Tool |
| P0-4 枚举归一化 | ⚠️ Tool 参数阶段归一化，校验位置前移 |
| P0-7 超长 token 拒绝 | ✅ 仍可放在工具入口 |
| P0-11 实体名校验 / 召回回溯 | ⚠️ 必须前移到 Tool 参数校验层 |
| P0-12 项目编号自动识别 | ⚠️ Tool schema 设计时需考虑 |
| P0-12 unified_intent 合并 | ✅ LLM Tool-Calling 让 unified_intent 退化为工具内部逻辑 |

**关键结论**：**所有 P0 防护都可下沉到"工具参数校验层"，但需要重写**——这是改造的主要工作量。

---

## 七、收益与成本的对比分析

### 7.1 全量化对比

| 维度 | 路由式（现状） | 路径 A（彻底替换） | 路径 B（推荐：路由+Tool 并存） | 路径 C（双层架构） |
|------|---------------|-------------------|------------------------------|-------------------|
| **新增业务功能周期** | 2-3 天（3 处改动） | 0.5 天（只加 @tool） | 1 天（决策逻辑由 Router + LLM Self-rule 双驱动） | 1.5 天 |
| **回归测试通过率** | 100% | 60-80% 概率破窗 | 100%（老用例不动） | 100% |
| **LLM 调用次数/query** | ~2 | ~3-6 | ~2-4 | ~2-4 |
| **首次响应延迟 P50** | ~1.5s | ~2.5s | ~2s | ~1.8s |
| **HITL 支持** | ❌ 需重写 | ✅ 原生 | ✅ | ✅ |
| **多分支组合** | ❌ | ✅ | ✅（仅在 Router 委派时） | ✅（限定复杂任务） |
| **后续 Multi-Agent 演进** | 需重写 | 原生 | 平滑 | 平滑 |

### 7.2 隐性收益（容易被忽略）

- **Prompt 迭代成本**：现状每加一个分支要改 3 处（`RouterIntent`、`System Prompt`、`Conditional Edge`）；Tool-Calling 仅需在 Pydantic schema 中加 1 处
- **Tool 注册的可观察性**：所有工具都集中在 `tools/` 包，可一键枚举、自动生成 API 文档
- **可观测性升级**：每条 tool_call 都成为可埋点的 Prometheus metric
- **本地开发体验**：mock LLM 时只需 mock 工具，不需要构造完整 router 链路

### 7.3 隐性成本

- **Prompt Engineering 复杂度**：5 个枚举路由的 Prompt vs 7 个 Tool 的 Tool-Description Prompt，后者需更精细措辞
- **调试心智负担**：路由式错误 = 分类错误（直接可见）；Tool-Calling 错误 = LLM 没选对工具/选太多工具（需打开思考链）
- **第三方依赖风险**：更深度耦合 LangGraph 的实验性特性（`ToolNode`、`create_react_agent`），需锁版本

---

## 八、业务适配度评估

### 8.1 与现有四大能力的匹配分析

| 能力 | 现状代表性示例 | 路由式适配度 | Tool-Calling 适配度 | 改造收益 |
|------|---------------|-------------|---------------------|----------|
| **专业知识问答** | "招标方式有哪些？""评标委员会怎么组成？" | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐（直接对应 knowledge_qa Tool） | 中等 |
| **智能询价** | "中国移动近三年中标项目有哪些？" | ⭐⭐⭐⭐⭐（二级 LLM + SQL 守卫链极其成熟） | ⭐⭐⭐⭐（需重做守卫） | 低-中 |
| **通用对话** | "你能做什么？" | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | 低 |
| **文档问答** | "帮我分析这个 PDF" | ⭐⭐⭐（占位） | ⭐⭐⭐⭐（Tool 调用 + 文档上传 pipeline） | 高（待实现） |
| **新增：复合任务** | "中国移动最高金额的智慧城市项目，并说明采购法对金额的要求" | ⭐⭐ 不可行（router 强制 1-of-N） | ⭐⭐⭐⭐⭐ | **巨大** |
| **新增：HITL 审批** | 风控敏感查询需要二次确认 | ⭐⭐ 不可行 | ⭐⭐⭐⭐⭐ | **巨大** |
| **新增：跨会话业务** | "把刚才那段生成招标文件" | ⭐⭐ 需重写 | ⭐⭐⭐⭐ | 中 |

### 8.2 业务前置条件满足度

| 业务前置 | 是否成立 |
|----------|----------|
| 真实存在多步复合查询需求（SC-1 / SC-2） | ✅ 政府客户复核类工作流高频 |
| doc_qa 占位即将转正 | ✅ 占位节点已留 6 步上线清单（[doc_qa.py:42-52](agent/nodes/doc_qa.py)） |
| HITL 真实场景 | ⚠️ 当前是 Demo，真实客户暂未提出；但招投标领域合规审批真实存在 |
| 有 Multi-Agent 计划 | ❌ 当前未规划（[docs/deep_agents_integration_design.md](docs/deep_agents_integration_design.md) 已存在原型） |

**结论**：**业务"还没强到必须立刻改"的阶段，但"已经在路上的演进路径上"——尤其是 doc_qa 转正 + 多模态演进这两件事是强信号。**

---

## 九、推荐改造路径（路径 B：路由 + Tool Calling 并行）

### 9.1 架构示意

```
[start] → router（现状保留，作为 fast-path）
          │
          ├── 单意图 → 原 node_*（不变）
          │
          ├── 多意图识别 / 复合查询判断
          │   └── react_node（新增）
          │       │   bind_tools([
          │       │     search_law_tool,        # = node_knowledge_qa 的 Tool 化
          │       │     query_bidding_tool,
          │       │     query_company_tool,
          │       │     request_human_review_tool,  # HITL
          │       │   ])
          │       │   recursion_limit = 6
          │       │   interrupt_before = ["request_human_review_tool"]
          │       └──→ finalize_answer_node
          │
          └── fallback（不变）
```

### 9.2 实施路线图（建议分 4 个阶段）

| 阶段 | 时长 | 交付 | 风险 |
|------|------|------|------|
| **P0 — 工具化封装**（1 周） | 把 4 个 node 改成 `@tool`，但 router 仍按现状调用它们 | `agent/tools/` 新增 5-6 个 @tool 函数；零行为变化 | 极低 |
| **P1 — 双轨并行**（2 周） | 新增 `react_node`，仅处理"router 识别为复合任务"的 query | 小流量灰度，A/B 对比答案质量与延迟 | 低 |
| **P2 — HITL 试点**（1 周） | 在价格敏感场景（大于 1000 万的中标）启用 `request_human_review_tool` | 风控节点上线 | 低-中 |
| **P3 — 智能路由**（2-3 周） | Router 让 LLM 自行决定走 fast-path 还是 react-node | Router 简化、退化 | 中 |

### 9.3 总成本估算

- **开发**：~6 周（1 人）
- **测试**：~3 周（mock LLM 测试框架 + 重新跑全部 P0 用例）
- **Token 成本**：现状基础上 +60%~+150%（按 4 步 ReAct 估算）
- **首次响应延迟**：增加约 1s（多 1-2 次 LLM 调用）

### 9.4 不推荐的"路径 A"理由

完全砍掉 router 改用纯 ReAct Agent 在当前项目**不推荐**：

1. **现有 P0 守卫链是项目最大资产**——重写在 ReAct 上需要逐 Tool 重做；
2. **router 已稳定工作的 1-shot 5 选 1 准确率优于 LLM 自由选择**（LangGraph 官方 benchmark 与本项目 router_test_sub_route 经验一致）；
3. **政府客户对"可解释 / 可审计"要求极高**——5 选 1 路由 + 单一节点执行的链路比 ReAct 自由组合更易审计。

---

## 十、长期演进（如果选择路径 B）

```
v1 路由式（现状）
   ↓ 工具化封装
v2 路由 + Tool 并行
   ↓ 智能路由选择
v3 Router + ToolNode + HITL（Sub-agent 雏形）
   ↓ Multi-Agent Supervisor
v4 Multi-Agent Orchestration（对标 deep_agents_integration_design.md 的设计愿景）
```

本项目已经存在 [docs/deep_agents_integration_design.md](docs/deep_agents_integration_design.md) 与 [docs/multimodal_evolution_design.md](docs/multimodal_evolution_design.md) 表明**长期方向是 Multi-Agent**。**Tool-Calling 改造是这条演化路径的必经环节**，越早铺垫越平滑。

---

## 十一、关键决策建议表

| 决策点 | 建议 | 理由 |
|--------|------|------|
| **是否启动改造？** | ✅ 是，但分期 | 业务前置已成（中-高），且改造路径清晰可分期交付 |
| **采用哪个路径？** | 路径 B（路由+Tool 并行） | 平衡收益与风险，对现有 P0 体系无侵入 |
| **第一阶段做什么？** | 仅"工具化封装"——把 node_* 改成 @tool，但不切换路由 | 几乎零风险，验证 Tool 化可行性 |
| **何时引入 HITL？** | P2 阶段，仅对金额 >1000 万的查询启用 | 真实风控场景与业务合规需求结合 |
| **何时升级 Router？** | P3 阶段，且必须配套足够监控 | 决策链变更需要 log/metric 兜底 |
| **是否限制 recursion_limit？** | 必须 ≤ 6 | 防止 LLM 死循环 + 控制 token 成本 |
| **测试策略？** | 保留所有 P0 用例 + 新增 mock LLM 决策的单元测试 | 双轨测试，前期不被打破 |

---

## 十二、附录

### 附录 A：关键代码定位一览

| 主题 | 文件 | 行 |
|------|------|----|
| Router 节点构建 | [agent/router.py](agent/router.py) | 190-242 |
| Router 已用 Tool Calling | [agent/router.py](agent/router.py) | 158-174 |
| StateGraph 主图 | [agent/graph.py](agent/graph.py) | 108-175 |
| 节点统一接口约定 | [CLAUDE.md](CLAUDE.md) | §节点接口契约 |
| price_inquiry 多步内嵌 | [agent/nodes/price_inquiry/node.py](agent/nodes/price_inquiry/node.py) | 166-495 |
| 知识库 RAG 调用 | [agent/nodes/knowledge_qa.py](agent/nodes/knowledge_qa.py) | 34-95 |
| 兜底机制 | [agent/graph.py](agent/graph.py) | 51-92 |
| Checkpointer 工厂 | [agent/checkpointer.py](agent/checkpointer.py) | 20-83 |

### 附录 B：参考设计文档（项目内已存）

- [docs/agent_architecture.md](docs/agent_architecture.md) — 现有骨架设计稿
- [docs/deep_agents_integration_design.md](docs/deep_agents_integration_design.md) — Multi-Agent 长期愿景
- [docs/multimodal_evolution_design.md](docs/multimodal_evolution_design.md) — 多模态演进规划
- [docs/data_architecture_upgrade_plan.md](docs/data_architecture_upgrade_plan.md) — 数据架构升级
- [docs/three_core_modules_design_and_feasibility.md](docs/three_core_modules_design_and_feasibility.md) — 三大核心模块设计

### 附录 C：与 LangGraph 官方能力的对齐检查

| LangGraph 特性 | 当前是否使用 | Tool-Calling 改造后是否需要 |
|----------------|-------------|---------------------------|
| `add_conditional_edges` | ✅ | ⚠️ 部分场景替换为 `tools_condition` |
| `interrupt()` for HITL | ❌ | ✅ 必经 |
| `ToolNode` | ❌ | ✅ |
| `create_react_agent` | ❌ | ✅ |
| `Checkpoint` / `MemorySaver` | ✅ | ✅（不变） |
| Subgraph | ❌ | ✅（未来 Multi-Agent） |

---

## 十三、报告结论

**一句话**：**业务前置已成（中-高）、技术路径清晰、改造风险可分期隔离；Tool-Calling 改造对当前路由式架构是"正向演进"而非"颠覆性重写"。建议从工具化封装起步，分 4 期约 6-9 周完成路径 B 的实施。**

> 在你审阅本报告时，建议重点关注 **§6 风险矩阵** 与 **§9.2 实施路线图**；其余章节是基于事实的推演。
> 在你确认采用路径 B 后，我会先从 **P0 阶段「工具化封装」** 入手——该阶段对当前行为零侵入，只新增代码不删除代码，可安全回退。

---

*— 报告完 —*
