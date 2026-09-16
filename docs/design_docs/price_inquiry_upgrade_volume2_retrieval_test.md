# 下册：检索逻辑改造与测试上线导航报告

> **所属方案**：[price_inquiry_sub_route_upgrade_plan.md](./price_inquiry_sub_route_upgrade_plan.md)  
> **阶段编号**：阶段二（共两阶段）  
> **依赖关系**：**强制依赖上册全部产出物**——`ztb_clean` 数据库及其 4 张已填充索引的业务表必须已就绪且通过验证。若上册未完成，本阶段所有检索代码均无法运行（数据库连接失败 / 表不存在 / 索引不生效）。  
> **衔接点**：本阶段接收上册的数据库连接配置（`.env` 中的 `MYSQL_CLEAN_DB=ztb_clean`）和 4 张表的字段结构作为输入，在其上构建二级路由检索逻辑。本阶段完成后，整个升级改造交付上线。

---

## 1. 总体设计思想与关键决策

### 1.1 设计思想

本阶段在 `price_inquiry` 节点内部实现三个二级意图路由（`company_query` / `product_query` / `bidding_query`），使系统能够根据用户问题的业务类型，精准路由到对应的数据表并执行差异化检索。

**核心原则**：
- **最小侵入**：不修改 `RouterIntent` 枚举、`AgentState` 定义、Graph 条件边结构——所有改动内聚在 `price_inquiry.py` 和新增的 `output_templates.py` 中。
- **LLM 调用合并**：将原方案中的"二级路由判断 + 结构化意图抽取"两次串行 LLM 调用合并为一次统一 Prompt 调用（`_UNIFIED_INTENT_PROMPT`），节省约 25% 的 LLM 延迟。
- **配置驱动输出**：通过 `FieldDescriptor` + `OutputTemplate` 声明式配置模型统一管理三个路由的输出字段筛选、空值处理、截断规则，避免散落各处的硬编码字段逻辑。
- **兜底保底**：`all` 模式遍历全部 4 张新表，确保意图分类不确定时仍能覆盖所有数据。

### 1.2 关键决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| 二级路由嵌入方式 | 内聚在 `price_inquiry` 节点内部（不新增一级 RouterIntent） | 一级路由保持 5 分类不变，避免降低路由准确率；三个二级路由检索逻辑高度同构，代码复用最大化 |
| 意图解析策略 | 统一 Prompt 单次调用（合并 sub_route 判断 + hard_filters 抽取） | 从 4 次 LLM 调用降至 3 次，节省 ~1.5s 延迟；边界歧义场景下统一上下文判断更准 |
| 查询函数签名 | `query_fn(intent: SearchIntent)` — 接收预解析的 SearchIntent，不内部调 LLM | 解耦意图解析与 SQL 执行；查询函数变为纯数据检索函数，可独立测试 |
| 输出字段管理 | `output_templates.py` 配置模型 + `_apply_output_template()` 运行时引擎 | 空值处理、文本截断、字段上限裁剪集中管控；新增 query_type 仅需声明配置 |
| 旧代码清理 | 完全移除 `_PRICE_DBS` 及 `_query_price_data()` 逻辑 | 旧数据库全扫描方案彻底退役，避免新旧代码共存引发维护混乱 |
| 数据源 | 仅使用 `ztb_clean` 的 4 张新表 | 不依赖任何旧数据库，数据源纯净可追溯 |

---

## 2. 改造流程与步骤清单

按执行顺序，每步完成后进行验证再进入下一步。

### 步骤 1：创建输出字段配置框架

**目标**：新建 `agent/nodes/output_templates.py`，定义 `FieldDescriptor`、`OutputTemplate` 配置模型和 `_apply_output_template()` 运行时引擎。

**内容**：
- `FieldDescriptor` dataclass：定义单个字段的机器名、中文标签、来源表/列、默认优先级、空值行为、截断阈值
- `OutputTemplate` dataclass：定义某 `query_type` 的 required / conditional / optional 字段集和 display_order
- `_FIELD_REGISTRY`：全局字段注册表，调用 `_register()` 逐字段注册
- `_apply_output_template()`：根据模板和 `SearchIntent` 筛选 + 格式化输出字段
- `_eval_condition()`：安全的条件表达式求值（支持 `intent.xxx` 布尔字段）
- `_merge_templates()`：合并多个模板的 required 字段并集（用于 `mixed` 模式）

**实现参考**：[price_inquiry_sub_route_upgrade_plan.md §7.4.2~§7.4.3](./price_inquiry_sub_route_upgrade_plan.md#742-配置模型定义)

**验证**：Python 导入无报错；`_FIELD_REGISTRY` 包含三个路由的全部字段。

### 步骤 2：扩展数据模型 `SearchIntent` 和 `HardFilters`

**目标**：在 `agent/nodes/price_inquiry.py` 中扩展 `HardFilters` 和 `SearchIntent` dataclass。

**改动要点**：
- `HardFilters` 新增字段：`province`、`city`、`company_name`、`credit_code`、`industry`、`company_level`、`company_type`、`business_status`、`product_name`、`category`、`supplier_name`、`price_range`、`successful_bidder`、`agent`、`project_number`、`project_category`、`project_stage`、`winning_amount_range`
- `SearchIntent` 新增字段：`sub_route`、`query_type`、`sort_by`、`aggregation`、`top_n`、`need_penalty_check`、`need_contact`

**实现参考**：[price_inquiry_sub_route_upgrade_plan.md §8.1](./price_inquiry_sub_route_upgrade_plan.md#81-统一数据模型扩展)

**验证**：`SearchIntent()` 无参构造成功，所有新字段有默认值。

### 步骤 3：实现统一意图解析

**目标**：实现 `_UNIFIED_INTENT_PROMPT` 和 `_parse_unified_intent()` 函数，替代旧方案中的 `_classify_sub_intent()`。

**核心逻辑**：
1. `_UNIFIED_INTENT_PROMPT`：单次 LLM 调用同时输出 `sub_route`、`query_type`、`hard_filters`、`semantic_keywords` 等全部字段
2. `_parse_unified_intent(question, llm) → SearchIntent`：链式调用 `_UNIFIED_INTENT_PROMPT | llm.with_structured_output(SearchIntent)`
3. `_safe_parse_intent()`：容错回填——`sub_route` 缺失 → `"all"`；`hard_filters` 为 None → 空 `HardFilters()` 等

**实现参考**：[price_inquiry_sub_route_upgrade_plan.md §8.5.2~§8.5.5](./price_inquiry_sub_route_upgrade_plan.md#852-统一意图解析-prompt)

**验证**：输入典型用户问题（如"找几个防水涂料的供应商"），确认返回的 `SearchIntent.sub_route == "product_query"` 且 `hard_filters` 含正确过滤条件。

### 步骤 4：扩展列分类与硬编码 Schema

**目标**：新增 `_HARDCODED_SCHEMA` 字典，为 4 张新表提供硬编码列分类（跳过 `information_schema` 查询）；扩展 `_build_hard_conditions_extended()`。

**改动要点**：
- `_HARDCODED_SCHEMA`：为每张表预定义 `semantic`、`time`、`budget`、`region`、`exact`、`text` 等列分类
- `_build_hard_conditions_extended()`：在复用原有条件构造逻辑的基础上，追加三类路由的专用过滤条件和 `province`/`city` 地区过滤

**实现参考**：[price_inquiry_sub_route_upgrade_plan.md §8.2~§8.3](./price_inquiry_sub_route_upgrade_plan.md#82-列分类规则扩展)

**验证**：单元测试——传入含 `industry='软件信息'` 的 `SearchIntent`，确认生成的 WHERE 子句含 `` `industry` = %s ``。

### 步骤 5：实现三个专用查询函数

**目标**：实现 `_query_company_data(intent)`、`_query_product_data(intent)`、`_query_bidding_data(intent)`，每个函数接收预解析的 `SearchIntent`，执行对应表的 SQL 检索并返回格式化结果。

**各函数要点**：

| 函数 | 查询表 | 特殊逻辑 |
|------|--------|---------|
| `_query_company_data` | `company_info` + 条件联查 `company_penalty` | 当 `need_penalty_check=True` 时，先查 `company_info` 获取 `credit_code`，再联查 `company_penalty` |
| `_query_product_data` | `product_info` | 当 `sort_by` 为 `price_asc`/`price_desc` 时，SQL 层 `ORDER BY price` |
| `_query_bidding_data` | `bid_project` | 当 `aggregation` 不为 null 时，跳过 FULLTEXT，走聚合 SQL（`ORDER BY winning_amount DESC LIMIT top_n`） |

**实现参考**：[price_inquiry_sub_route_upgrade_plan.md §7.1.3~§7.3.3](./price_inquiry_sub_route_upgrade_plan.md#713-检索逻辑)

**验证**：使用真实的 `ztb_clean` 数据测试——各查询函数输入典型的 `SearchIntent`，确认返回非空结果集。

### 步骤 6：实现排序逻辑适配

**目标**：实现 `_build_order_clause(intent)` 函数，根据 `SearchIntent.sort_by` 返回对应 ORDER BY 子句。

**排序映射**：
- `price_asc` → `ORDER BY price ASC`
- `price_desc` → `ORDER BY price DESC`
- `amount_desc` → `ORDER BY winning_amount DESC`
- `amount_asc` → `ORDER BY winning_amount ASC`
- `date_desc` → `ORDER BY winning_date DESC`
- `date_asc` → `ORDER BY winning_date ASC`
- 其他 / `relevance` → `ORDER BY _score_ DESC`（FULLTEXT 默认）

**混合排序调整**：当 `sort_by` 为金额/时间排序时，Python 层 `_hybrid_score()` 的关键词得分权重降至 0.3，确保业务排序优先。

**实现参考**：[price_inquiry_sub_route_upgrade_plan.md §8.4](./price_inquiry_sub_route_upgrade_plan.md#84-排序逻辑适配)

### 步骤 7：改造 `node_price_inquiry()` 入口

**目标**：重构 `node_price_inquiry()` 函数，实现"统一意图解析 → 二级路由分发 → 结果格式化"的新流程。

**新流程**：
```
① question = messages[-1].content
② intent = _parse_unified_intent(question, llm)      # 一次 LLM 调用
③ intent = _safe_parse_intent(intent)                 # 容错回填
④ route_config = _SUB_ROUTE_MAP[intent.sub_route]
⑤ query_fn = getattr(module, route_config["query_fn"])
⑥ raw_records = query_fn(intent)                      # 不传 llm
⑦ formatted_records = _apply_output_template(raw_records, intent, template)
⑧ business_result = { ... }  # 包含 sub_route、query_type、formatted records
```

**同时移除**：
- `_PRICE_DBS` 配置及 `_query_price_data()` 全部旧逻辑
- `_classify_sub_intent()` 函数（已被 `_parse_unified_intent` 替代）
- `_SUB_ROUTE_MAP` 中的 `intent_prompt` 字段

**实现参考**：[price_inquiry_sub_route_upgrade_plan.md §8.5.3](./price_inquiry_sub_route_upgrade_plan.md#853-二级路由分发入口合并后)

**验证**：启动 Agent，输入典型问题，确认 `business_result` 含正确的 `sub_route` 和检索结果。

### 步骤 8：（可选）扩展 `RouterDecision`

**目标**：在 `agent/router.py` 的 `RouterDecision` 中新增 `sub_intent: Optional[str] = None` 字段，为未来一级路由的精细化决策预留扩展点。

**注意**：此步骤为**可选**——不修改 `RouterDecision` 也不影响核心功能，二级路由信息完全在 `price_inquiry` 内部流转。若选择不改，则 `router.py` 零改动。

### 步骤 9：编写测试用例

**目标**：新建 `test/test_sub_route.py`，覆盖二级路由分类准确率和端到端检索。

**测试分层**：

| 层级 | 用例数 | 内容 | 覆盖 |
|------|--------|------|------|
| 单元测试：意图分类 | ≥30 条（3 路由 × 10 条） | 给定用户问题，断言 `SearchIntent.sub_route` 和 `query_type` 正确 | company_query / product_query / bidding_query 各 ≥10 条 |
| 单元测试：输出字段筛选 | ≥9 条（3 路由 × 3 query_type） | 给定模拟 `SearchIntent` + 模拟数据行，断言 `_apply_output_template()` 输出字段正确 | 各路由的主要 query_type |
| 集成测试：端到端 | ≥15 条（3 路由 × 5 条） | 真实用户问题 → `node_price_inquiry()` → 验证 `business_result` 含有效数据 | 三类路由真实问题各 5 条 |
| 性能基准测试 | 4 条 | 测量 4 张表各执行一条 FULLTEXT 查询的耗时 | `company_info` / `company_penalty` / `product_info` / `bid_project` |

**验证**：所有测试用例通过。

### 步骤 10：灰度上线与日志监控

**目标**：将改造后的代码部署上线，通过日志观察二级路由分类准确率和检索效果。

**操作**：
1. 在 `node_price_inquiry()` 中添加性能日志：记录 `sub_route`、`query_type`、命中表、耗时
2. 先使用 `all` 兜底模式运行半天，确认无报错
3. 切换到完整二级路由模式，持续观察 1 天
4. 监控指标：分类准确率（人工抽查）、平均响应时间、FULLTEXT 索引命中率

---

## 3. 参与改造的代码文件及改动要点

| 文件 | 性质 | 改动量 | 改动要点 |
|------|------|--------|---------|
| `agent/nodes/price_inquiry.py` | **核心改造** | +250 / -80 行 | ① 扩展 `HardFilters` + `SearchIntent` 数据模型；② 新增 `_UNIFIED_INTENT_PROMPT` 统一意图解析 Prompt；③ 新增 `_parse_unified_intent()` + `_safe_parse_intent()`；④ 新增 `_HARDCODED_SCHEMA` 硬编码列分类；⑤ 新增 `_build_hard_conditions_extended()` + `_build_order_clause()`；⑥ 新增 `_query_company_data()` / `_query_product_data()` / `_query_bidding_data()` / `_query_all_tables()`；⑦ 新增 `_SUB_ROUTE_MAP` 路由配置表；⑧ 重构 `node_price_inquiry()` 入口；⑨ **移除** `_PRICE_DBS`、`_query_price_data()`、`_classify_sub_intent()` |
| `agent/nodes/output_templates.py` | **新建** | +200 行 | ① `FieldDescriptor` + `OutputTemplate` 配置模型；② 三路由完整字段注册表 `_FIELD_REGISTRY`；③ 运行时筛选引擎 `_apply_output_template()` + `_eval_condition()` |
| `agent/router.py` | **可选修改** | +5 行 | `RouterDecision` 新增 `sub_intent: Optional[str] = None`（不修改也可正常运行） |
| `test/test_sub_route.py` | **新建** | +150 行 | 意图分类单元测试 + 输出筛选单元测试 + 端到端集成测试 |

**零改动文件**（确认无需触碰）：
- `agent/state.py` — `AgentState` 不变，`business_result` 仍是泛型 `dict`
- `agent/graph.py` — Graph 节点和条件边完全不变
- `agent/nodes/__init__.py` — 不新增节点
- `agent/__main__.py` — CLI 入口不变
- `agent/checkpointer.py` — 无关
- `public_kb/` 全部 — Milvus RAG 独立运行
- `.env` — 上册已完成

---

## 4. 关键命令与脚本说明

### 4.1 单元测试

```bash
cd d:\DEMO\zhaotoubiao_demo
python -m pytest test/test_sub_route.py -v
```

或单独运行意图分类测试：
```bash
python -m pytest test/test_sub_route.py::TestSubRouteClassification -v
```

### 4.2 端到端集成测试（手动交互）

```bash
cd d:\DEMO\zhaotoubiao_demo
python -m agent
```

进入对话后，依次输入三类典型问题：
```
# company_query
"安徽软件信息行业中型及以上企业有哪些？"
"河源市赞爷餐饮管理服务有限公司是否有不良记录？"

# product_query
"电剪刀的市场行情价怎么样？"
"找几个防水涂料的供应商，要价格便宜的"

# bidding_query
"福建师范大学招标过什么项目？"
"福州怡富电梯有限公司2024年中标金额最大的项目是哪个？"
```

### 4.3 性能基准测试

```bash
cd d:\DEMO\zhaotoubiao_demo
python -m pytest test/test_sub_route.py::TestPerformance -v --benchmark-only
```

### 4.4 日志监控查询（上线后）

```sql
-- 查看二级路由调用分布
SELECT sub_route, COUNT(*) AS cnt
FROM agent_query_log  -- 假设已配置日志入库
WHERE created_at >= NOW() - INTERVAL 1 DAY
GROUP BY sub_route;
```

---

## 5. 前置条件、输入物与产出物

### 前置条件

| 条件 | 说明 | 来源 |
|------|------|------|
| `ztb_clean` 数据库就绪 | 4 张表存在、数据已导入、FULLTEXT + BTREE 索引已验证 | **上册产出** |
| `.env` 含 `MYSQL_CLEAN_DB` | `MYSQL_CLEAN_DB=ztb_clean` | **上册产出** |
| Python 环境可用 | Anaconda Python 3.12，含 `pymysql`、`langchain`、`langgraph`、`pydantic` | 项目已配置 |
| LLM API 可用 | DeepSeek `deepseek-chat` 模型可正常调用 | 项目已配置 |
| 现有 Agent 可运行 | `python -m agent` 启动正常，一级路由 `price_inquiry` 可被触发 | 改造前基准 |

### 输入物

| 输入 | 路径 | 说明 |
|------|------|------|
| `ztb_clean` 数据库 | MySQL `192.168.10.120:3306` | 上册产出：4 张表含完整数据和索引 |
| `.env` | 项目根目录 | 上册产出：含 `MYSQL_CLEAN_DB=ztb_clean` |
| 升级方案文档 | `docs/price_inquiry_sub_route_upgrade_plan.md` | §7 三个二级路由方案 + §8 检索逻辑 SQL 实现 |
| 现有 `price_inquiry.py` | `agent/nodes/price_inquiry.py` | 改造基准文件 |

### 产出物

| 产出 | 说明 | 验收标准 |
|------|------|---------|
| 改造后的 `price_inquiry.py` | 含完整二级路由逻辑，旧代码已清理 | `node_price_inquiry()` 可正确分发到三个子路由 |
| `output_templates.py` | 统一输出字段配置框架 | `_FIELD_REGISTRY` 含全部字段；`_apply_output_template()` 正确筛选 |
| `test_sub_route.py` | 测试用例集 | 全部用例通过 |
| （可选）改造后的 `router.py` | RouterDecision 增加 `sub_intent` | 向后兼容 |

### 回退方案

若二级路由上线后出现严重准确率问题：
1. 将 `_SUB_ROUTE_MAP` 中默认值强制改为 `"all"`（一行修改）
2. `all` 模式遍历全部 4 张新表，功能等价于升级前的遍历行为（但数据源已切换为新库）
3. 收集错误 case 优化 Prompt 后重新上线

---

> **前置依赖**：本阶段开始前，必须确认 **[上册：数据准备与基础设施搭建导航报告](./price_inquiry_upgrade_volume1_data_infra.md)** 的所有产出物已就绪并通过验证。
