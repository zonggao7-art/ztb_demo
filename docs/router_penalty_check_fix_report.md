# 路由器企业处罚查询修复评估报告

> **文档定位**：方案 A 已实施修复后的补充评估，重点对方案 B（关键词预路由）和方案 C（意图重命名重构）进行深度技术评估，并为项目长期优化提供方向指引。
>
> **关联文件**：`agent/router.py`、`agent/nodes/price_inquiry.py`、`agent/nodes/output_templates.py`
>
> **日期**：2026-08-09

---

## 目录

1. [问题回顾与方案 A 实施总结](#1-问题回顾与方案-a-实施总结)
2. [方案 B 评估：关键词预路由增强](#2-方案-b-评估关键词预路由增强)
3. [方案 C 评估：意图重命名架构重构](#3-方案-c-评估意图重命名架构重构)
4. [方案 B vs 方案 C 对比矩阵](#4-方案-b-vs-方案-c-对比矩阵)
5. [推荐的最佳实践方案](#5-推荐的最佳实践方案)
6. [长期优化建议](#6-长期优化建议)

---

## 1. 问题回顾与方案 A 实施总结

### 1.1 问题根因

用户查询 `项城市魅翔商贸有限公司有没有不良记录？` 被一级路由器误判为 `fallback`，导致企业处罚查询功能（`penalty_check`）完全不可用。根因是 **路由器 Tool 描述与节点实际能力的语义脱节**：

- `route_price_inquiry` 的 docstring 仅覆盖"中标价格/报价"语义，未覆盖"不良记录/处罚"语义
- `price_inquiry` 节点已演进为通用结构化数据查询引擎（3 个 sub_route、10+ query_type），但路由器描述未同步更新

### 1.2 方案 A 实施内容

对 `agent/router.py` 进行了 5 处修改：

| 修改点 | 位置 | 内容 |
|--------|------|------|
| 导入 `SystemMessage` | L17 | 为系统提示词提供消息类型支持 |
| 扩充 `route_price_inquiry` docstring | L57-67 | 覆盖企业处罚/价格/招投标/供应商/企业详情 5 类触发场景 |
| 新增 `ROUTER_SYSTEM_PROMPT` | L113-133 | 包含完整意图分类说明 + 7 条 few-shot 路由示例 |
| Tool Calling 路径注入系统提示词 | L166-169 | `[SystemMessage, HumanMessage]` 双消息结构 |
| Structured Output 路径注入系统提示词 | L185-188 | 同上，确保两条路径语义一致 |

### 1.3 方案 A 验证要点

- **Tool Calling 路径**（当前活跃路径，日志显示 `路由(tool)`）：`route_price_inquiry` docstring 扩充后，LLM 可直接匹配"不良记录/处罚"语义
- **Structured Output 路径**：新增 `ROUTER_SYSTEM_PROMPT` 补充了 `price_inquiry` 的完整场景描述和 few-shot 示例，消除了此前该路径仅有 Literal 枚举值、无意图说明的盲区
- **向后兼容**：未修改 `RouterIntent` 枚举、`_TOOL_INTENT_MAP`、`graph.py` 条件边映射，不引入破坏性变更

---

## 2. 方案 B 评估：关键词预路由增强

### 2.1 方案概述

在 `router_node` 函数中增加一层轻量级关键词预检逻辑：对用户输入进行正则匹配，若命中"不良记录/处罚/违法/黑名单/信用"等明确特征词，则短路判定为 `price_inquiry`，跳过 LLM 调用。

### 2.2 技术可行性分析

#### 实现路径

```python
# router.py 新增关键词预检
_PENALTY_KEYWORDS = re.compile(r"不良记录|处罚|违法|黑名单|信用记录|违规|行政处罚")

def _keyword_pre_route(user_input: str) -> Optional[str]:
    """关键词预路由：命中明确特征词时短路返回意图。"""
    if _PENALTY_KEYWORDS.search(user_input):
        return "price_inquiry"
    return None

# 在 router_node 中调用
def router_node(agent_state: AgentState) -> dict:
    ...
    user_input = str(messages[-1].content)
    # 关键词预检短路
    pre_routed = _keyword_pre_route(user_input)
    if pre_routed:
        logger.info("路由(keyword): intent=%s", pre_routed)
        return {"router_intent": pre_routed}
    # 正常 LLM 路由
    ...
```

#### 可行性结论

- **技术可行性：高**。仅需在 `router_node` 入口处增加 ~15 行代码，不涉及图结构、状态定义或节点接口变更
- **与方案 A 的兼容性：完全兼容**。关键词预检作为 LLM 路由的前置过滤层，未命中时自然降级到 LLM 路由
- **风险点**：关键词列表需要持续维护，且存在误命中风险（如"招标方式有没有违法的？"实际是知识问答）

### 2.3 优势与劣势

| 维度 | 优势 | 劣势 |
|------|------|------|
| **性能** | 命中时跳过 LLM 调用，路由延迟从 ~2s 降至 <1ms | 未命中时仍需完整 LLM 调用，无性能提升 |
| **准确率** | 对明确特征词（"不良记录""处罚"）命中率 100% | 对模糊表述（"这家公司靠谱吗""有没有问题"）无法覆盖 |
| **维护性** | 关键词列表直观易懂，新增场景只需加正则 | 关键词列表膨胀后易产生冲突，需定期清理 |
| **扩展性** | 可按意图扩展多组关键词预检规则 | 每新增意图都需维护对应关键词表，线性增长维护成本 |
| **可控性** | 规则透明、可审计、可单测 | 规则刚性，无法处理语义近似但措辞不同的查询 |

### 2.4 实施难度评估

| 评估项 | 评分（1-5） | 说明 |
|--------|------------|------|
| 代码改动量 | ★☆☆☆☆ | ~15 行，单文件修改 |
| 测试复杂度 | ★★☆☆☆ | 需构造正反例验证关键词命中与误命中 |
| 回归风险 | ★★☆☆☆ | 预检误命中会将知识问答误路由到数据查询 |
| 维护负担 | ★★★☆☆ | 关键词列表需随业务演进而持续维护 |

**综合难度：低**。适合作为方案 A 的补充增强，但不适合作为独立方案。

---

## 3. 方案 C 评估：意图重命名架构重构

### 3.1 方案概述

将一级路由意图 `price_inquiry` 重命名为 `data_query`（或 `structured_query`），消除"价格"语义偏见，使意图名称与节点实际能力（企业查询/产品查询/招投标查询/处罚查询）对齐。涉及 `RouterIntent` 枚举、`RouterDecision`、Tool 名称、`_TOOL_INTENT_MAP`、`graph.py` 条件边映射、节点注册名、日志等多处同步修改。

### 3.2 技术可行性分析

#### 影响范围分析

| 影响层 | 涉及文件 | 修改内容 |
|--------|---------|---------|
| 路由枚举 | `router.py` L29-35 | `RouterIntent` Literal 值 `price_inquiry` → `data_query` |
| 路由决策模型 | `router.py` L38-42 | `RouterDecision.intent` 类型自动跟随 |
| Tool 定义 | `router.py` L57-67 | `route_price_inquiry` → `route_data_query`，函数名+返回值 |
| Tool 映射 | `router.py` L100-106 | `_TOOL_INTENT_MAP` key/value 同步更新 |
| 系统提示词 | `router.py` L113-133 | `ROUTER_SYSTEM_PROMPT` 中意图名称同步 |
| 图节点注册 | `graph.py` L150 | `graph.add_node("price_inquiry", ...)` → `graph.add_node("data_query", ...)` |
| 条件边映射 | `graph.py` L161-171 | `add_conditional_edges` 字典 key 同步 |
| 边连接 | `graph.py` L175 | `graph.add_edge("price_inquiry", END)` → `graph.add_edge("data_query", END)` |
| 分支标签 | `graph.py` L43-48 | `_BRANCH_LABELS` key 同步 |
| 节点导入 | `graph.py` L29-35 | `node_price_inquiry` 导入名可保留（函数名不变） |
| 节点内部 | `price_inquiry.py` | `business_result.branch` 值需同步更新 |
| 输出模板 | `output_templates.py` | 无需修改（使用 sub_route，不依赖一级意图名） |
| 前端/CLI | `__main__.py` | 若有意图名展示逻辑需同步 |
| 测试用例 | `test/` | 所有引用 `price_inquiry` 字符串的断言需更新 |

#### 可行性结论

- **技术可行性：中**。改动逻辑简单（全局替换），但影响面广，需保证全链路同步
- **破坏性风险：中高**。任何遗漏的引用点都会导致运行时 KeyError 或路由断链
- **回滚成本：高**。涉及多文件联动修改，回滚需整体还原

### 3.3 优势与劣势

| 维度 | 优势 | 劣势 |
|------|------|------|
| **语义准确性** | 意图名称 `data_query` 准确反映节点能力，消除"价格"偏见 | LLM 对 `data_query` 的语义理解可能不如 `price_inquiry` 直观 |
| **维护性** | 意图名与功能对齐，降低新开发者的认知成本 | 一次性改动成本高，需全链路同步验证 |
| **扩展性** | 为未来新增独立意图（如 `company_query` 独立化）预留命名空间 | 重命名本身不增加新能力，仅为命名优化 |
| **兼容性** | 无技术债，命名清爽 | 破坏性变更，所有下游引用需同步更新 |
| **可测试性** | 重命名后可重新编写更精准的测试用例 | 回归测试范围大，需覆盖全部路由分支 |

### 3.4 实施难度评估

| 评估项 | 评分（1-5） | 说明 |
|--------|------------|------|
| 代码改动量 | ★★★☆☆ | 涉及 5+ 文件，~30 处引用点 |
| 测试复杂度 | ★★★★☆ | 需全量回归测试所有路由分支和节点 |
| 回归风险 | ★★★★☆ | 任何遗漏引用导致运行时错误 |
| 维护负担 | ★☆☆☆☆ | 一次性改动，完成后无持续维护成本 |

**综合难度：中高**。适合在版本大更新或架构重构窗口期实施，不宜在功能迭代中混合进行。

---

## 4. 方案 B vs 方案 C 对比矩阵

| 评估维度 | 方案 B（关键词预路由） | 方案 C（意图重命名重构） |
|---------|----------------------|------------------------|
| **路由准确率** | ★★★★☆（明确特征词 100%，模糊表述依赖 LLM） | ★★★☆☆（依赖 LLM 理解新意图名，需重新调优 prompt） |
| **路由性能** | ★★★★★（命中时 <1ms，跳过 LLM） | ★★☆☆☆（仍需完整 LLM 调用，无性能提升） |
| **代码改动量** | ★★☆☆☆（~15 行，单文件） | ★★★★☆（~30 处，多文件联动） |
| **维护成本** | ★★★☆☆（关键词列表需持续维护） | ★☆☆☆☆（一次性改动，无持续成本） |
| **扩展性** | ★★☆☆☆（每新增意图需加关键词表） | ★★★★☆（命名清晰，为未来拆分预留空间） |
| **回归风险** | ★★☆☆☆（预检误命中风险可控） | ★★★★☆（多文件联动，遗漏风险高） |
| **语义准确性** | ★★☆☆☆（基于表面词汇，非深层语义） | ★★★★★（意图名与功能对齐） |
| **与方案 A 协同** | ★★★★★（作为 A 的前置增强层） | ★★★☆☆（替代 A 的 prompt 优化，但可共存） |
| **实施时机** | 随时可行，增量交付 | 建议在架构重构窗口期集中实施 |

---

## 5. 推荐的最佳实践方案

### 5.1 短期推荐：方案 A（已实施）+ 方案 B（选择性增强）

**理由**：

1. **方案 A 已解决核心问题**：扩充 Tool 描述 + 系统提示词 + few-shot 示例，使 LLM 能正确识别"不良记录/处罚"类查询并路由到 `price_inquiry` 节点
2. **方案 B 作为可选增强**：对高频明确查询（"XX公司有没有不良记录"）提供 <1ms 短路路由，降低 LLM 调用成本。但需控制关键词列表规模，避免过度工程化
3. **避免短期引入方案 C**：重命名涉及多文件联动，在未充分回归测试的情况下引入破坏性变更风险过高

### 5.2 中期推荐：方案 C（在下一版本重构窗口实施）

**理由**：

1. **消除技术债**：`price_inquiry` 这个名称已严重偏离节点实际能力，长期来看会造成持续的认知摩擦
2. **为意图拆分铺路**：未来若将 `company_query` 从 `price_inquiry` 中独立为一级意图，清晰的命名是前提
3. **建议实施策略**：在下一个大版本中，结合意图拆分一并重构，避免多次破坏性变更

### 5.3 分阶段实施路线图

```
阶段 1（已完成）：方案 A — Prompt 语义覆盖修复
  └─ 扩充 Tool 描述 + 系统提示词 + few-shot 示例

阶段 2（建议 1-2 周内）：方案 B — 高频关键词预路由
  └─ 对 "不良记录/处罚/违法" 等高频明确查询增加短路路由
  └─ 控制关键词列表在 10-15 个以内，定期审查

阶段 3（建议下个大版本）：方案 C — 意图重命名 + 架构优化
  └─ price_inquiry → data_query 重命名
  └─ 评估是否将 company_query 独立为一级意图
  └─ 全量回归测试
```

---

## 6. 长期优化建议

### 6.1 路由器意图识别的扩展策略

#### 6.1.1 建立意图注册表机制

当前意图定义分散在 `RouterIntent` Literal、`ROUTER_TOOLS`、`_TOOL_INTENT_MAP`、`graph.py` 条件边映射等多处，新增意图需同步修改 4+ 个位置。建议建立统一的意图注册表：

```python
# router.py — 意图注册表（集中管理）
@dataclass
class IntentDef:
    name: str           # 意图枚举值
    tool_name: str      # Tool Calling 函数名
    description: str    # 语义描述（用于 Tool docstring + System Prompt）
    examples: list[str] # few-shot 示例

INTENT_REGISTRY: list[IntentDef] = [
    IntentDef("knowledge_qa", "route_knowledge_qa", "...", [...]),
    IntentDef("price_inquiry", "route_price_inquiry", "...", [...]),
    ...
]
```

**收益**：新增意图只需在注册表中添加一条记录，`RouterIntent`、`ROUTER_TOOLS`、`_TOOL_INTENT_MAP`、System Prompt 均自动生成，消除多处同步修改的遗漏风险。

#### 6.1.2 引入意图置信度评分

当前路由为硬分类（单选），LLM 必须从 5 个意图中选择一个。对于边界模糊的查询（如"招标方式有哪些不合规的？"既涉及知识问答又涉及合规查询），建议引入置信度评分机制：

- LLM 输出 top-2 意图及置信度
- 当 top-1 置信度 < 0.7 时，执行 top-2 意图对应的节点并合并结果
- 或引导用户澄清意图

#### 6.1.3 路由效果可观测性

建议增加路由准确率监控：

- 在 `router_node` 中记录每次路由的 `user_input`、`intent`、`reason`
- 定期采样人工审核路由准确率
- 对误路由案例进行归因分析，反哺 Prompt 优化

### 6.2 企业处罚查询功能的优化方向

#### 6.2.1 当前功能局限分析

当前 `_query_company_data` 的处罚联查逻辑存在以下局限：

1. **依赖 credit_code 精确匹配**：若 `company_info` 表中 `credit_code` 为空或与 `company_penalty` 表不一致，联查失败
2. **仅合并第一条处罚记录**：`penalties[0]` 只取最新一条，无法展示完整处罚历史
3. **无独立 penalty_check 查询路径**：处罚查询必须先查 `company_info` 再联查 `company_penalty`，当用户只知道公司名但 `company_info` 表中无记录时，无法直接查 `company_penalty`

#### 6.2.2 优化建议

| 优化项 | 优先级 | 描述 |
|--------|--------|------|
| 支持公司名直接查 penalty 表 | P0 | 当 `need_penalty_check=true` 且 `company_info` 无结果时，直接用 `company_name` 模糊查 `company_penalty` 表 |
| 展示完整处罚历史 | P1 | 将 `penalties` 列表完整合并到记录中，而非仅取第一条 |
| 处罚统计聚合 | P2 | 支持"XX公司被处罚了几次"等聚合查询，返回处罚次数、最近处罚时间等摘要信息 |
| 处罚严重度分级 | P3 | 根据 `penalty_result` 字段内容进行严重度分级（警告/罚款/吊销执照等） |

#### 6.2.3 查询路径优化示意

```
当前路径：
  company_info (by company_name) → credit_code → company_penalty (by credit_code)

优化后路径：
  ┌─ company_info (by company_name) → credit_code → company_penalty (by credit_code)
  │
  └─ company_penalty (by company_name LIKE)  ← 新增直接查询路径
```

### 6.3 整体架构的可维护性改进点

#### 6.3.1 节点命名与职责对齐

`price_inquiry` 节点已承担 4 类结构化数据查询（企业/产品/招投标/处罚），建议中期重命名为 `data_query` 或 `structured_query`，使节点名与实际职责对齐。这与方案 C 一致，应在架构重构窗口期一并完成。

#### 6.3.2 二级路由配置外置化

当前 `_SUB_ROUTE_MAP` 硬编码在 `price_inquiry.py` 中（L2302-2319），与查询函数强耦合。建议将其外置为配置文件（YAML/JSON），使新增 sub_route 无需修改代码：

```yaml
# config/sub_routes.yaml
company_query:
  tables: [company_info, company_penalty]
  query_fn: _query_company_data
  query_types:
    - penalty_check
    - supplier_recommend
    - company_detail
    - mixed
product_query:
  tables: [product_info]
  query_fn: _query_product_data
  ...
```

#### 6.3.3 意图解析 Prompt 模板化管理

当前 `_UNIFIED_INTENT_SYSTEM` 是一个 200+ 行的硬编码字符串（`price_inquiry.py` L245-317），维护困难。建议：

- 将 Prompt 模板拆分为独立的 `.txt` 或 `.j2` 文件
- 按sub_route 分段管理（company_query 段、product_query 段、bidding_query 段）
- 支持版本化管理和 A/B 测试

#### 6.3.4 统一错误处理与降级策略

当前 `_with_fallback` 装饰器（`graph.py` L51-92）对所有业务节点统一降级为 fallback 提示。建议细化降级策略：

- **可重试错误**（DB 超时、LLM 超时）：自动重试 1 次后再降级
- **数据不存在**：返回"未找到记录"而非"功能不可用"
- **参数错误**：引导用户修正查询条件

### 6.4 未来功能扩展规划

#### 6.4.1 短期扩展（1-3 个月）

| 功能 | 描述 | 涉及模块 |
|------|------|---------|
| 企业关联图谱 | 查询企业的股东、分支机构、关联企业 | 新增 `company_relation` 表 + graph 查询 |
| 处罚趋势分析 | 按时间/地区/行业统计处罚分布 | `company_penalty` 聚合查询 |
| 多企业对比 | 同时查询多个企业的处罚/资质对比 | `price_inquiry` 节点扩展 |

#### 6.4.2 中期扩展（3-6 个月）

| 功能 | 描述 | 涉及模块 |
|------|------|---------|
| 智能推荐引擎 | 基于采购需求自动推荐合格供应商 | 新增 `supplier_recommend` 节点 |
| 价格趋势预测 | 基于历史中标数据预测产品价格走势 | `product_info` + 时序分析 |
| 招投标风险预警 | 自动识别异常中标 pattern（围标/串标） | `bid_project` + 规则引擎 |

#### 6.4.3 长期扩展（6-12 个月）

| 功能 | 描述 | 涉及模块 |
|------|------|---------|
| 多模态查询 | 支持上传图片/表格进行查询 | `doc_qa` 节点扩展 |
| 知识图谱融合 | 将结构化数据与非结构化法规知识图谱融合 | RAG 引擎 + Graph DB |
| 主动式智能助手 | 基于用户画像主动推送相关招标/处罚信息 | Agent + 用户画像系统 |

---

## 附录：方案 A 修改详情

### 修改文件

- `agent/router.py`（5 处修改，+40 行 / -10 行）

### 修改清单

1. **L17**：导入 `SystemMessage`
2. **L57-67**：扩充 `route_price_inquiry` docstring，覆盖 5 类触发场景
3. **L113-133**：新增 `ROUTER_SYSTEM_PROMPT`，包含完整意图说明 + 7 条 few-shot 示例
4. **L166-169**：Tool Calling 路径注入 `[SystemMessage, HumanMessage]` 双消息
5. **L185-188**：Structured Output 路径注入 `[SystemMessage, HumanMessage]` 双消息

### 验证方法

```bash
# 启动交互模式测试
python -m agent --interactive

# 测试用例
> 项城市魅翔商贸有限公司有没有不良记录？
# 预期：路由(tool): intent=price_inquiry → 执行 penalty_check 查询

> 这家公司被处罚过吗？
# 预期：路由(tool): intent=price_inquiry

> 查一下电剪刀的历史中标价格
# 预期：路由(tool): intent=price_inquiry（回归验证，原功能不受影响）

> 评标委员会怎么组成？
# 预期：路由(tool): intent=knowledge_qa（回归验证）

> 你能做什么？
# 预期：路由(tool): intent=general_chat（回归验证）
```
