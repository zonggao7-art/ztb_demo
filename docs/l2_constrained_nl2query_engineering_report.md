# L2 参数化组合查询工程方案

> 目标：在不引入自由 text2sql、不做模型微调的前提下，把现有六个固定问答能力升级为“可组合、可校验、可解释”的结构化查询平台。
>
> 本报告对应仓库当前实现：一级路由在 `agent/router.py`；询价意图解析在 `agent/nodes/price_inquiry/intent.py`；SQL 构建在 `agent/nodes/price_inquiry/sql_builders.py`；输出字段控制在 `agent/nodes/output_templates.py`；自然语言渲染在 `agent/nodes/answer_templates.py`。

## 1. 结论速览

L2 不等于让大模型自由生成 SQL。它应定义为：

```text
受控 NL2Query = LLM 理解 + 能力注册中心 + 白名单 Schema Linking + 确定性 SQL Builder + 输出模板治理
```

系统边界如下：

| 层 | 职责 | 禁止事项 |
| --- | --- | --- |
| LLM | 抽取能力名、过滤条件、实体、排序、聚合、追问参数 | 生成 SQL、选择任意表和字段 |
| Capability Registry | 声明业务能力和可参数范围 | 出现未注册能力 |
| Schema Registry | 维护字段映射、枚举值、同义词、必填条件 | 拼接未知列名 |
| Validator | 校验类型、枚举、权限、组合合法性 | 放行越权或恒假过滤 |
| SQL Builder | 根据查询计划生成索引友好的参数化 SQL | 直接执行 LLM SQL |
| Renderer | 按 query plan 元数据选择结构化模板或半开放模板 | 用模型覆写数据库事实 |

这比自由 text2sql 更适合项目现阶段的五个理由：

1. 保留现有路由、召回链和固定模板资产；
2. 数据库安全边界由代码控制，不依赖模型不幻觉；
3. 可以逐个能力灰度，失败时可回退旧链路；
4. 可用同一套评测集量化改进；
5. 不需要训练语料、GPU 和长期微调周期。

## 2. 从现状到 L2 的定位

当前系统已有三级能力形态：

- **L0/L1 固定问答**：主路由识别 `knowledge_qa / price_inquiry / general_chat / doc_qa / fallback`；MySQL 链路再抽取 `sub_route/query_type/hard_filters`。
- **准 L2 雏形**：`HardFilters` 已支持企业行业、经营状态、等级、采购人、中标供应商、代理机构、项目编号等字段；`SearchIntent` 也存在排序和聚合占位。
- **主要缺口**：缺少统一 Query Plan 对象、能力注册表、白名单校验器、组合语义、计划回放元数据和多计划输出协议。

因此 L2 的改造对象不是从零写 text2sql，而是把已有的 `SearchIntent` 收敛为更强约束的 `QueryPlan`，再让每个注册能力声明自己允许的过滤、排序、聚合和展示规则。

## 3. 核心抽象：QueryPlan 与 Capability Contract

### 3.1 QueryPlan 结构

建议新建 `agent/nodes/structured_query/models.py`：

```python
class QueryFilter(TypedDict, total=False):
    op: Literal["eq", "in", "range", "like_fulltext", "keyword"]
    field: str
    value: str | int | float | list[str] | dict[str, Any]

class QueryOrder(TypedDict):
    field: str
    direction: Literal["asc", "desc"]

class QueryAggregation(TypedDict, total=False):
    kind: Literal["count", "sum", "max_amount", "min_amount"]
    field: str | None
    group_by: list[str]

class QueryPlan:
    plan_id: UUID
    capability: str
    filters: list[QueryFilter]
    keywords: list[str]
    exact_entities: list[str]
    project_numbers: list[str]
    order: list[QueryOrder]
    aggregation: QueryAggregation | None
    limit: int
    rationale: str
    missing_required: list[str]
    ambiguities: list[str]
    follows_up: bool
```

约束：

- `capability` 必须精确匹配注册表 key；
- `filters[].field` 必须命中该能力的字段白名单；
- `limit` 默认 20，最大 100；
- `exact_entities` 与 `project_numbers` 分开，禁止继续共用 `exact_tokens`；
- `rationale` 只用于调试和展示解释，不参与 SQL 拼接；
- 计划必须可 JSON 序列化并写入响应 debug 元数据，便于评测回放。

### 3.2 Capability Contract 示例

```python
CapabilityContract(
    name="company_search",
    title="企业检索",
    base_table="company_info",
    allowed_filters={
        "industry": FieldSpec(kind="enum_like"),
        "business_status": FieldSpec(kind="enum", enum_values=["存续", "在业", "注销"]),
        "company_level": FieldSpec(kind="enum_like", multi_value=True),
        "province": FieldSpec(kind="enum_like"),
        "city": FieldSpec(kind="enum_like"),
        "registered_capital": FieldSpec(
            kind="numeric_range",
            min=0,
            max=10_000_000_000,
        ),
        "establish_date": FieldSpec(kind="date_range"),
    },
    required_entity={
        any_of=["keywords", "industry", "region"],
    },
    allowed_order=[
        "registered_capital_desc",
        "establish_date_desc",
        "relevance",
    ],
    default_limit=20,
    max_limit=100,
)
```

实现上可以先用 dataclass 或 Pydantic v2 定义 `FieldSpec`、`RequiredEntityRule`、`OutputView`。关键是**合同即文档**：后端不再散落判断某个字段能不能查，而是统一由 registry 回答。

## 4. 受控 DSL 设计

LLM 的唯一结构化输出应是 QueryPlan JSON。示例输入：

```text
找江苏地区存续状态、注册资本5000万以上的环保设备公司，
按注册资本降序，最多返回10家。
```

期望输出：

```json
{
  "capability": "company_search",
  "filters": [
    {"op": "eq", "field": "province", "value": "江苏"},
    {"op": "eq", "field": "business_status", "value": "存续"},
    {"op": "range", "field": "registered_capital", "value": {"min": 50000000}},
    {"op": "eq", "field": "industry", "value": "环保设备"}
  ],
  "keywords": ["环保设备"],
  "order": [{"field": "registered_capital", "direction": "desc"}],
  "limit": 10,
  "rationale": "用户要求按地区、状态、注册资本和行业筛选公司，并按注册资本降序"
}
```

Validator 处理流程：

```text
1. JSON 解析失败 → 触发 clarify
2. capability 未注册 → 尝试 alias 映射或 ask_user
3. field 不在白名单 → 丢弃并记录 ambiguity
4. 枚举不在 Schema Registry → 归一化；失败则标为 candidate_filter，不得进硬过滤
5. range 类型非法 → 触发 clarify
6. required entity 缺失 → 进入补参流程
7. 全部通过 → 绑定执行器
8. 执行后统计 applied/rejected_filters
```

关键原则：**不能识别的偏好只能降级成软权重或追问项，不能直接变成 WHERE 条件。**

## 5. SQL Builder 与执行策略

保留现有 MySQL/Milvus/FULLTEXT 混合召回思路，但由 Plan Builder 统一组装：

```text
QueryPlan
  → Handler 选择：exact_project / exact_company / company_search / bidding_search
  → Constraint Partition：hard constraints / soft preferences / keywords
  → Candidate Generation：Milvus semantic ids 或 FULLTEXT OR
  → Deterministic Fetch SQL
  → Scoring & Rerank
  → Output View
```

硬约束与软偏好的初始划分：

| 类别 | 字段示例 | 处理策略 |
| --- | --- | --- |
| Hard | `credit_code`、真实项目编号、明确金额区间、用户显式选择的状态 | 所有阶段保留 |
| Soft | 行业、地区、城市、企业等级、模糊采购人、中标商别名 | 首轮尝试过滤；零结果时降级为加分项重查 |
| Keyword | 企业简称、主营业务词、项目主题词 | Milvus + FULLTEXT OR + LIKE 兜底 |
| Exact | 统一社会信用代码、项目编号 | 走专用 handler，优先级最高 |

SQL 必须满足：

1. 列名只来自注册表，使用反引号包裹；
2. 值一律参数化绑定；
3. 排序列必须在白名单内；
4. `LIMIT` 有默认值和上限；
5. 单次查询设置 statement timeout；
6. `EXPLAIN` 校验没有无界全表扫描；
7. 输出响应里带 `plan_id/applied_filters/rejected_filters/served_view/elapsed_ms/result_source`。

零结果不是最终失败，应有四级退化：

```text
第一轮：hard + soft + keyword
第二轮：剔除 soft constraints，仅 hard + keyword
第三轮：把 rejected enum-like 条件转成 rerank 权重
第四轮：返回最接近结果并提示放宽了哪些条件
```

每轮都要记录降级原因，防止“看似有答案但用户不知道口径变了”。

## 6. 输出与回答策略

L2 不是彻底抛弃固定模板，而是三层渲染：

| 层级 | 适用情况 | 说明 |
| --- | --- | --- |
| 固定模板 | entity lookup、penalty check、project detail | 保持现在的高可信格式 |
| 结构化列表模板 | company/bidding search 多行结果 | 表格、卡片、分页和字段裁剪 |
| 半开放摘要 | 用户明确要求总结、比较、推荐理由 | LLM 只能基于 result payload 总结，不得补充库外事实 |

半开放摘要 Prompt 要求：

```text
你只能基于下方结构化结果回答。
不得新增数据库名称、公司、项目、日期或金额。
若结果不足，请说明缺失字段或建议修改筛选条件。
引用信息时必须直接来自 JSON rows。
```

响应核心结构：

```json
{
  "answer": "...",
  "result_type": "list",
  "rows": [],
  "display_view": "company_card",
  "pagination": {"page": 1, "has_more": true},
  "query_meta": {
    "plan_id": "...",
    "applied_filters": [],
    "rejected_filters": [],
    "relaxed_conditions": [],
    "result_source": "mysql_fulltext_or_milvus_rerank"
  }
}
```

前端可根据 `display_view/pagination/query_meta` 渲染筛选器、表格、分页和“为什么少了某些条件”的解释条。

## 7. 六类问题的迁移设计

| 现有能力 | L2 Capability | 升级内容 |
| --- | --- | --- |
| 企业工商信息 | `company_profile.get` | `credit_code/company_name` 精确入口不变；支持返回指定 view |
| 企业经营范围 | `company_business_scope.get` | 保持详情模板；增加经营范围片段高亮与截断配置 |
| 企业处罚信息 | `company_penalty.search` | 支持处罚时间范围、执法单位关键词、是否合并企业主体变体 |
| 项目中标情况 | `bid_project.detail` | 项目编号唯一入口保持不变；补充项目编号候选消歧 |
| 公司中标历史 | `bid_project.by_bidder` | 支持时间、行业、采购人、代理机构、金额区间组合；订单可配置 |
| 供应商推荐 | `company_search` | 升级为真正的多条件组合能力，是 L2 第一试点 |
| 法律法规 RAG | 暂不纳入 L2 | 不把个人偏好混入公共法规结论 |
| 通用聊天 | 不纳入 L2 | 继续静态引导 |
| 文档问答 | 占位，暂不纳入 | 等上传与解析链路落地后再评估 |

首期不要试图合并全部六类。建议先做 `company_search` 和 `bid_project.by_bidder`，因为它们天然具备组合筛选需求。

## 8. 业务场景举例

### 场景 A：环保设备供应商筛选

用户：

```text
帮我找江苏做污水处理设备的存续公司，注册资本5000万以上，
最近三年有中标记录的优先，前10家。
```

QueryPlan：

```json
{
  "capability": "company_search",
  "filters": [
    {"op": "eq", "field": "province", "value": "江苏"},
    {"op": "eq", "field": "business_status", "value": "存续"},
    {"op": "range", "field": "registered_capital", "value": {"min": 50000000}},
    {"op": "like_fulltext", "field": "business_scope", "value": "污水处理设备"}
  ],
  "keywords": ["污水处理设备"],
  "soft_preferences": [
    {"type": "has_recent_win", "window": "P3Y"}
    ],
  "order": [{"field": "relevance", "direction": "desc"}],
  "limit": 10
}
```

处理逻辑：

1. `has_recent_win` 是跨表增强条件，首轮不作为 `company_info` 的裸 JOIN 过滤；
2. 先查公司候选，再批量到 `bid_project.successful_bidder` 匹配近三年中标；
3. 有中标的公司加 `recent_win_score`；
4. 无近期中标但基础条件命中的公司仍可显示，标注“暂无近三年中标记录”；
5. 输出企业卡片和标签：“注册资本达标 / 近三年有中标”。

收益：把原来只能分次问“工商信息”“是否有处罚”“中标历史”的过程压缩成一次探索式检索。

### 场景 B：某公司中标历史多维复盘

用户：

```text
看下XX公司在江苏2024年到2025年的公开招标项目，
先按中标金额降序，超过1000万的单独汇总。
```

QueryPlan：

```json
{
  "capability": "bid_project.by_bidder",
  "exact_entities": ["XX公司"],
  "filters": [
    {"op": "eq", "field": "province", "value": "江苏"},
    {"op": "range", "field": "winning_date", "value": {
      "start": "2024-01-01", "end": "2025-12-31"
    }},
    {"op": "range", "field": "winning_amount", "value": {"min": 10000000}}
  ],
  "order": [{"field": "winning_amount", "direction": "desc"}],
  "aggregation": {
    "kind": "sum",
    "field": "winning_amount",
    "group_by": []
  },
  "limit": 50
}
```

注意执行语义：

1. 如果用户说的金额阈值是“重点查看1000万以上”，则明细只查 ≥1000 万；
2. 如果用户说的是“整个历史，再把超过1000万单独汇总”，则需要两个查询计划：
   - 明细计划：不加金额下限，按金额降序；
   - 汇总计划：加 `min_winning_amount=1000万` 并计算 sum/count；
3. Planner 应通过 clarify question 或规则区分这两种说法；
4. 返回页面同时给明细表和汇总卡。

收益：让既有中标历史查询从单实体输出变成时间、地区、金额、行业和聚合复盘工具。

### 场景 C：风险筛查前置

用户：

```text
这几个入围供应商有没有处罚记录？重点看2024年以后环保或安全生产相关处罚。
```

多个精确实体可拆成一个批量 Plan：

```json
{
  "capability": "company_penalty.batch_search",
  "exact_entities": ["A供应商", "B供应商", "C供应商"],
  "filters": [
    {"op": "range", "field": "penalty_date", "value": {"start": "2024-01-01"}},
    {"op": "in", "field": "risk_topic", "value": ["环保", "安全生产"]}
  ],
  "group_by_company": true,
  "limit": 100
}
```

实现方式：

1. `company_name IN (...)` 参数化批量初筛；
2. `illegal_behavior/penalty_result` 使用 FULLTEXT OR 召回；
3. 环保、安全生产属于主题分类词，先用同义词扩展；
4. 每家供应商输出三态：有相关处罚 / 近期无相关处罚 / 工商名可能不一致需确认；
5. 不确定匹配的企业不得自动归入“无处罚”。

收益：投标资格复核场景比单企业查询更贴近实际业务。

### 场景 D：采购人画像查询

用户：

```text
XX单位2024年以来委托过哪些项目？
每类项目做了多少个，总金额是多少？
先看金额最大的三类。
```

QueryPlan 组：

```json
[
  {
    "capability": "bid_project.by_purchaser",
    "exact_entities": ["XX单位"],
    "filters": [
      {"op": "range", "field": "publish_date", "value": {"start": "2024-01-01"}}
    ],
    "aggregation": {
      "kind": "count_sum",
      "field": "winning_amount",
      "group_by": ["project_category"]
    },
    "order": [{"field": "sum_amount", "direction": "desc"}],
    "limit": 3
  },
  {
    "capability": "bid_project.by_purchaser",
    "exact_entities": ["XX单位"],
    "filters": [
      {"op": "range", "field": "publish_date", "value": {"start": "2024-01-01"}}
    ],
    "order": [{"field": "publish_date", "direction": "desc"}],
    "limit": 20
  ]
]
```

实现要点：

1. 现有 `purchaser_query` 虽然存在于意图枚举中，但要验证底层 SQL 是否真正稳定支持；
2. `project_category` 若枚举脏，需要先做类别归一化；
3. 聚合 SQL 由 handler 写死允许的 group-by 字段；
4. 结果用“Top 3 类别 + 明细表”呈现。

收益：采购人不再是简单历史列表，而能获得支出结构和项目组合视角。

### 场景 E： clarify 补参闭环

用户：

```text
帮我找几家合适的供应商。
```

Planner 应返回：

```json
{
  "status": "need_input",
  "missing_required": ["industry_or_keywords", "region"],
  "questions": [
    "请提供主营产品或行业关键词，例如环保设备、医疗器械。",
    "请说明目标省份或城市，例如江苏省、合肥市。"
  ]
}
```

不允许因为缺条件就全表扫描，也不允许拿未确认的全局默认偏好当硬条件。

收益：降低无效召回，同时把“开放式问法”引导到系统能力边界内。

## 9. Planner Prompt 框架

System Prompt 应包含四段：

1. **能力目录**：只注入当前激活能力名称、描述、必填项、代表性例子；
2. **Schema 卡片**：每个能力只列白名字段、类型、示例枚举；
3. **判定规则**：精确编号、实体消歧、软硬条件划分、多义时 need_input；
4. **JSON only**：禁止 SQL、注释、多余文本。

输出模型示例：

```python
class PlannerResponse(BaseModel):
    plans: list[QueryPlan] = []
    status: Literal["ok", "need_input", "unsupported"] = "ok"
    questions: list[str] = []
    unsupported_reason: str | None = None
```

重要限制：

- 不要一次性注入全库 DDL；
- 每个 Planner 请求最多暴露 8~12 个能力；
- 枚举字典过大时只给 high-frequency values 和“其他”，运行时再做归一化；
- temperature 设为 0；
- 若当前 DeepSeek structured output 不稳定，改用 Tool Calling + Pydantic schema 描述，再二次解析。

## 10. 实施路线

### Milestone 1：契约与评测基线（2~3 天）

交付：

1. `QueryPlan / FieldSpec / CapabilityContract` 模型；
2. `company_search` 初版合同；
3. 60~100 条结构化问句评测集；
4. 当前链路 baseline 报告。

指标：

- 能力命中率；
- filter F1；
- end-to-end recall@20；
- answer precision；
- invalid plan rate；
- P50/P95 latency。

### Milestone 2：单一能力闭环（3~4 天）

交付：

1. Planner node；
2. JSON 解析与白名单 Validator；
3. `company_search` deterministic SQL handler;
4. zero-result relaxation chain；
5. 企业卡片输出视图。

验收：

```text
1. 至少30条组合条件问句全链路跑通；
2. 非法字段不会进入 SQL；
3. soft 条件零结果会降级；
4. 同一问题保存并回放相同 plan_id + normalized plan；
5. EXPLAIN 显示走 FULLTEXT/主键/普通索引，不由 LLM 造成无界扫描。
```

### Milestone 3：中标历史扩展（2~3 天）

交付：

1. `bid_project.by_bidder` Plan contract；
2. 时间区间、金额区间、代理机构、采购人等可选条件；
3. 明细 + 小结双视图；
4. 老能力 alias 到新 Plan。

验收：

1. 原“某公司中标历史”句式全部兼容；
2. 新组合句式相比老链路有更高 recall@20；
3. 计划被降级时前端能解释原因。

### Milestone 4：治理与回归（2~3 天）

交付：

1. 能力注册中心单元测试；
2. Validator 安全测试；
3. SQL EXPLAIN 回归；
4. schema drift 检测脚本；
5. 静态回答模板与 L2 视图切换开关。

上线开关：

```env
STRUCTURED_QUERY_L2_ENABLED=false
CAPABILITY_COMPANY_SEARCH_ENABLED=true
CAPABILITY_BID_BY_BIDDER_ENABLED=true
PLANNER_PROVIDER=deepseek
PLANNER_TIMEOUT_S=8
QUERY_MAX_ROWS=100
```

关闭后节点立即回到当前 SearchIntent 链路。

## 11. 目录建议

```text
agent/nodes/structured_query/
  __init__.py
  models.py              # QueryPlan / FilterSpec / PlannerResponse
  planner.py             # LLM query planner node
  validator.py           # capability-aware validation
  registry.py            # capability contracts
  normalization.py       # 枚举、省份、城市、行业、金额归一化
  executors/
    __init__.py
    company_executor.py
    bid_executor.py
  sql_builders.py
  renderer.py            # rows / summary / citation / query meta
  node.py                # node_structured_query_l2
scripts/
  eval_structured_planner.py
test/
  test_query_plan_models.py
  test_capability_registry.py
  test_plan_validator.py
  test_company_sql_builder.py
  test_bid_sql_builder.py
  test_zero_result_relaxation.py
```

不建议立刻替换 `agent/nodes/price_inquiry/`。L2 先以并行节点接入，通过特性开关灰度。

## 12. 测试矩阵

| 分类 | 必测点 |
| --- | --- |
| Planner | 合法 JSON、复杂组合、缺参追问、不支持能力 |
| Validator | 未注册字段、越权排序、非法 limit、负数金额、日期倒置 |
| Normalization | 省/市别名、行业同义词、“千万”换算、企业状态别名 |
| Executor | exact token 分离、zero-result relaxation、超时、空集 |
| SQL Safety | 参数化、列白名单、EXPLAIN、max rows、timeout |
| Renderer | 固定模板兼容、多行列表、半开放摘要不编造 |
| Regression | 关闭 L2 后原六类能力不受影响 |

典型评测样本：

```text
1. 找江苏环保设备存续公司
2. 注册资本5000万以上的合肥软件企业
3. XX公司2024年江苏1000万以上中标项目
4. 这些供应商2024年后有没有环保处罚
5. XX单位今年发了多少个项目
6. 注册资本高且近期中标多的环保公司
7. 我要找几家合适供应商（期望补参）
8. 帮我写一个完整投标书（期望 unsupported）
9. 北京有没有注册资本一亿元以上的医院（期望澄清是找医院客户还是医药企业）
10. XX公司去年 did something unsupported in English（期望按领域边界拒答或澄清）
```

## 13. 风险与控制

| 风险 | 后果 | 控制 |
| --- | --- | --- |
| LLM 输出不稳定 | 下游无法执行 | JSON schema + retry once + Tool Calling fallback |
| 枚举值脏 | 恒假条件、低召回 | synonym dictionary + soft preference + candidate display |
| 组合条件过严 | 零结果体验差 | hard/soft 分层 + 自动放宽 + explainability |
| 用户误认为覆盖全市场 | 决策偏差 | 每次返回数据截止时间和来源表 |
| 实体歧义 | 错绑公司/项目 | 候选实体卡 + user confirm |
| 性能下降 | 接口变慢 | candidate top-k、SQL timeout、并发预算、缓存 plan/schema |
| Prompt 泄露 | 安全问题 | Prompt 只含白名单 schema，不含凭据和任意 DDL |
| 开放感不足 | 产品价值弱 | 用搜索结果卡、条件 chips、追问补参形成探索界面 |
| 工期膨胀 | 拖慢主线 | 首期只做两个 capability，其余开关关闭 |

## 14. 成功标准

L2 一期成功的定义不是“看起来开放”，而是：

1. 原六类固定问法兼容率 100%；
2. 至少两个能力支持多条件组合；
3. 组合问句 baseline→M2 的 end-to-end recall@20 提升 ≥15 个百分点；
4. invalid plan rate ≤5%；
5. 零结果场景中有解释或放宽建议的比例 ≥80%;
6. P95 不高于现有 price_inquiry 主链路的 120%；
7. 全部 SQL 通过参数化与 EXPLAIN 回归；
8. 关闭开关后现有行为完全恢复。

## 15. 给导师的一句话论证

> 我们不做高风险自由 text2sql，也不做昂贵的预训练微调，而是把现有 LLM 意图抽取升级为 schema-constrained NL2Query：模型负责理解自然语言并产出可校验查询计划，系统负责白名单校验、确定性 SQL、混合召回和可解释输出。这样可以在可控成本下，把六类机械问答升级为有限但真实的开放式业务检索体验。
