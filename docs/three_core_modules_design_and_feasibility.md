# 三大核心功能模块精细化设计与可行性评估报告

> 前置依赖：[vague_query_improvement_feasibility_report.md](./vague_query_improvement_feasibility_report.md)（v2，产品线砍除决策）
> 评估对象：company_info（38,911 行）、company_penalty（1,805 行）、bid_project（17,742 行）
> 数据来源：`raw_tables/` CSV 字段完整性核查 + `docker/mysql/init/01-schema.sql` 索引审计
> 评估时间：2026-08-10

---

## 0. 数据基础核查（事实依据）

### 0.1 三表字段完整性

| 表 | 总行数 | 核心字段完整率 | 低质量字段 |
|----|--------|--------------|-----------|
| company_info | 38,911 | company_name/legal_person/business_status/credit_code/company_level: **100%**; registered_capital: 98.0%; establish_date: 99.5%; industry/province/city: 99.9% | **credit_rating: 仅 0.1%（20 行有效），必须从模板中移除** |
| company_penalty | 1,805 | **所有 7 列均 100% 完整** | 无 |
| bid_project | 17,742 | **所有 17 列均 100% 完整** | winning_amount 中有 341 条 = 0.0（1.9%），需在回答中区分"中标金额 0 元"与"数据缺失" |

### 0.2 关键索引现状（影响实体探测 SQL 性能）

| 表 | 需要精确/模糊匹配的列 | 是否有 B-tree 索引 | 结论 |
|----|---------------------|--------------------|------|
| company_info | company_name | ❌ **无**（仅 FULLTEXT 列 | 🔴 必须新增 `INDEX idx_company_name(company_name)` |
| company_penalty | company_name | ✅ `idx_company_name` | 可直接 LIKE 匹配 |
| bid_project | successful_bidder | ✅ `idx_successful_bidder` | 可直接 LIKE 匹配 |
| bid_project | purchaser | ✅ `idx_purchaser` | 可直接 =/LIKE 匹配 |
| bid_project | project_number | ✅ `uk_project_number` | UNIQUE 键，= 匹配 |
| bid_project | project_name | ❌ **无** | 🔴 必须新增 `INDEX idx_project_name(project_name)`，或利用 FULLTEXT（当前无 FULLTEXT 索引，需新增） |

---

## 1. 功能一：企业工商情报查询（绑定 company_info）

### 1.1 query①：查 XX 公司的工商信息

**字段映射表**

| 显示名称 | 源字段 | 空值策略 | 备注 |
|---------|--------|---------|------|
| 企业名称 | company_name | 必出，不可空 | 核心实体 |
| 统一社会信用代码 | credit_code | show_placeholder "未登记" | 仅空值占位 |
| 法定代表人 | legal_person | show_placeholder | |
| 注册资本 | registered_capital | show_placeholder | 含"万人民币"等单位 |
| 成立日期 | establish_date | show_placeholder | 日期格式 |
| 经营状态 | business_status | show_placeholder | |
| 所属行业 | industry | show_placeholder | 国民经济行业分类 |
| 企业类型 | company_type | show_placeholder | |
| 企业等级 | company_level | show_placeholder | |
| 注册地址 | address | hide | 过长时省略 |
| 经营范围 | business_scope | hide | 过长时省略（>>详细可追问） |
| ~~信用评级~~ | ~~credit_rating~~ | **已移除** | **仅 0.1% 覆盖，空洞字段** |

**标准回答模板**

```
经查询，{company_name}（统一社会信用代码：{credit_code}）是一家成立于 {establish_date} 的 {company_type}企业，法定代表人 {legal_person}，注册资本 {registered_capital}。

该公司注册地址为 {address}，所属行业为「{industry}」，企业等级为 {company_level}，目前经营状态为 {business_status}。

其经营范围为：{business_scope}。

（数据来源：ztb_clean.company_info）
```

**空行处理规则**：
- credit_code 为空时："统一社会信用代码未登记"
- business_scope 为空时：整行省略
- address 为空时：地址行省略，改为"{province}{city}"

### 1.2 query②：XX 公司是做什么行业的？/经营范围？

**字段映射表**

| 显示名称 | 源字段 | 空值策略 | 备注 |
|---------|--------|---------|------|
| 企业名称 | company_name | 必出 | |
| 所属行业 | industry | show_placeholder | |
| 企业等级 | company_level | show_placeholder | |
| 经营范围 | business_scope | show_placeholder | 核心信息，必须展示 |
| 省份/城市 | province/city | show_placeholder | 提供地域上下文 |

**标准回答模板**

```
{company_name} 所属行业为「{industry}」，企业等级为 {company_level}，注册地为 {province}{city}。

其经营范围为：{business_scope}。

（数据来源：ztb_clean.company_info）
```

### 1.3 检索策略

```
用户问题 → LLM 提取 company_name（现有 _UNIFIED_INTENT 已支持）
  │
  ├─ Step 1：实体探测
  │   SELECT company_name FROM company_info WHERE company_name LIKE '%公司名%' LIMIT 1
  │   └─ 0 行 → 回答"未收录该企业" + 三核功能引导（不进入召回链）
  │
  ├─ Step 2：全字段回表（修复 SELECT 缺列缺陷）
  │   SELECT * FROM company_info WHERE company_name LIKE '%公司名%' LIMIT 1
  │   （取输出模板声明的全部字段，非仅 semantic 分类列）
  │
  └─ Step 3：模板填充 → 回答
```

**关键风险**：company_name 目前无 B-tree 索引，`LIKE '%公司名%'` 对 38,911 行全表扫描约 2-3ms（参照日志 0.002s）可知目前是可接受的，但大库扩量后会劣化——**推荐立即新增 `INDEX idx_company_name(company_name)`，使探测降至亚毫秒级**。

---

## 2. 功能二：企业风控黑名单查询（绑定 company_penalty）

### 2.1 query：XX 公司有无不良记录？/被处罚过吗？

**字段映射表**

| 显示名称 | 源字段 | 空值策略 | 备注 |
|---------|--------|---------|------|
| 企业名称 | company_name | 必出 | |
| 统一社会信用代码 | credit_code | 必出 | penalty 表 100% 有值 |
| 处罚日期 | penalty_date | 必出 | |
| 执法单位 | law_enforcement_unit | 必出 | |
| 违法行为 | illegal_behavior | 必出，不截断 | 可能较长（"提供虚假材料谋取中标"等） |
| 处罚结果 | penalty_result | 必出，不截断 | 最长字段，含罚款金额 + 禁入期限 |

### 2.2 标准回答模板

**有处罚记录时**：

```
经查询，{company_name}（统一社会信用代码：{credit_code}）存在不良记录。

处罚日期：{penalty_date}
执法单位：{law_enforcement_unit}
违法事实：{illegal_behavior}
处罚结果：{penalty_result}

（数据来源：ztb_clean.company_penalty）
```

多条处罚记录时，按 `penalty_date DESC` 列出，每条一个独立段落。

**无处罚记录时**：

```
经查询，在系统收录的数据范围内，{company_name}（统一社会信用代码：{credit_code}）暂未发现不良记录或处罚信息。

（数据来源：ztb_clean.company_penalty，收录 {1,805} 条处罚记录）
```

**实体不存在时（company_penalty 中查无该公司）**：

```
系统中未收录"{company_name}"的不良记录信息。这可能因为：
① 该公司确无处罚记录；
② 公司名称存在差异（建议核对工商登记全称）。

如需查询该公司的工商登记信息，请提问"查{company_name}的工商信息"。
```

### 2.3 检索策略

```
用户问题 → LLM 提取 company_name + need_penalty_check=true
  │
  ├─ Step 1：直查 company_penalty（已有 _query_penalty_by_company_name 实现）
  │   SELECT * FROM company_penalty WHERE company_name LIKE '%公司名%'
  │   ORDER BY penalty_date DESC LIMIT 50
  │   └─ 命中 → Step 3 模板填充
  │   └─ 0 行 → Step 2
  │
  ├─ Step 2：company_info 降级联查（已有实现 L2195-L2256）
  │   SELECT credit_code FROM company_info WHERE company_name LIKE '%公司名%'
  │   └─ 命中 → SELECT * FROM company_penalty WHERE credit_code = ? 
  │                └─ 有记录 → 模板填充（告知"通过统一社会信用代码关联查询"）
  │                └─ 0 条 → "未收录"
  │   └─ 0 行 → "未收录" + 三核功能引导
  │
  └─ Step 3：模板填充 → 回答
```

**现有代码兼容性**：此设计直接复用 `_query_company_data()` 中 `need_penalty_check` 路径的现有逻辑（L2157-L2256），**无需新增代码**，仅需替换输出端的格式化从 `_format_records` 切换为自然语言模板。

---

## 3. 功能三：招投标中标情报查询（绑定 bid_project）

### 3.1 query①：XX 公司中标了什么项目？/中标历史？

**字段映射表（中标人视角）**

| 显示名称 | 源字段 | 空值策略 | 备注 |
|---------|--------|---------|------|
| 中标供应商 | successful_bidder | 必出 | |
| 项目名称 | project_name | 必出 | |
| 项目编号 | project_number | 必出 | |
| 采购人 | purchaser | show_placeholder | |
| 中标金额 | winning_amount | **特殊处理** | =0 时显示"金额未公开" |
| 中标日期 | winning_date | 必出 | |
| 标的物 | subject_matter | hide | 数据来源标注不规范（如空调项目标"监控设备"），准确性存疑，可选展示 |
| 项目阶段 | project_stage | hide | 默认"结果公告" |
| 来源链接 | source_url | hide | 可选，控制篇幅 |

**标准回答模板**

**单条记录**：

```
根据系统收录的招投标数据，{successful_bidder} 在 {winning_date} 中标了 {purchaser} 的「{project_name}」（项目编号：{project_number}），中标金额为 {winning_amount} 元。

（数据来源：ztb_clean.bid_project）
```

**多条记录（按 winning_date DESC，最多展示 5 条）**：

```
根据系统收录的招投标数据，{successful_bidder} 共中标 {N} 个项目，最近的中标记录如下：

① {winning_date} | {purchaser}「{project_name}」| 中标金额 {winning_amount} 元
② {winning_date} | {purchaser}「{project_name}」| 中标金额 {winning_amount} 元
…

如需查看某个项目的详细信息，请提供项目名称或编号。

（数据来源：ztb_clean.bid_project，共 {N} 条记录）
```

**无中标记录时**：

```
在系统收录的 {17,742} 条项目记录中，暂未查询到"{company_name}"的中标信息。

这可能因为：
① 该公司未在收录的区域/时段内中标；
② 公司名称写法与系统中标供应商名称不一致（建议使用工商登记全称重试）。

（数据来源：ztb_clean.bid_project）
```

### 3.2 query②：XX 项目（名称/编号）的中标情况

**字段映射表（项目视角）**

| 显示名称 | 源字段 | 空值策略 | 备注 |
|---------|--------|---------|------|
| 项目名称 | project_name | 必出 | 按名称查询时核心匹配字段 |
| 项目编号 | project_number | 必出 | 按编号查询时核心匹配字段（UNIQUE 键） |
| 采购人 | purchaser | 必出 | |
| 中标供应商 | successful_bidder | 必出 | |
| 中标金额 | winning_amount | **特殊处理** | =0 时显示"金额未公开" |
| 预算金额 | budget_amount | 必出 | |
| 中标日期 | winning_date | 必出 | |
| 代理机构 | agent | 必出 | |
| 标的物 | subject_matter | 必出 | |
| 项目类别 | project_category | hide | |
| 省份/城市 | province/city | hide | |
| 来源链接 | source_url | hide | |

**标准回答模板**

```
项目「{project_name}」（项目编号：{project_number}）由 {purchaser} 采购，于 {winning_date} 确定中标结果。

中标供应商：{successful_bidder}
中标金额：{winning_amount} 元
预算金额：{budget_amount} 元
代理机构：{agent}
标的物：{subject_matter}

（数据来源：ztb_clean.bid_project）
```

**按名称模糊匹配到多条时**：

```
根据"{project_name}"共匹配到 {N} 个相关项目，列表如下：

① [{project_number}] {project_name} | {purchaser} | 中标 {successful_bidder} | {winning_date} | 预算 {budget_amount} | {agent} | {subject_matter}
② …

如需查看某个项目的详细中标情况，请提供准确的项目编号。

（数据来源：ztb_clean.bid_project）
```

### 3.3 检索策略

**query① 投标人视角**：

```
用户问题 → LLM 提取 successful_bidder
  │
  ├─ Step 1：实体探测
  │   SELECT id FROM bid_project WHERE successful_bidder LIKE '%公司名%' LIMIT 1
  │   （idx_successful_bidder 索引命中，毫秒级）
  │   └─ 0 行 → "暂未查到" + 引导（可尝试用项目编号/采购人重新查询）
  │
  ├─ Step 2：全字段回表
  │   SELECT * FROM bid_project WHERE successful_bidder LIKE '%公司名%'
  │   ORDER BY winning_date DESC LIMIT 100
  │
  └─ Step 3：模板填充
```

**query② 项目视角**：

```
用户问题 → LLM 提取 project_name 或 project_number
  │
  ├─ 路径 A：有 project_number（精确查询）
  │   SELECT * FROM bid_project WHERE project_number = '{编号}'（UNIQUE 键命中）
  │   └─ 单条 → 直接模板填充
  │
  ├─ 路径 B：仅 project_name（模糊查询）
  │   SELECT * FROM bid_project WHERE project_name LIKE '%项目名%'
  │   LIMIT 20
  │   └─ 单条 → 直接模板填充
  │   └─ 多条 → 列表模板 + 引导用户提供编号
  │
  └─ 0 行 → "未查到"
```

**注意**：project_name 目前无索引，`LIKE '%项目名%'` 对 17,742 行全表扫描约 5ms——可接受但建议新增 `INDEX idx_project_name(project_name)`。

---

## 4. 回答模板引擎设计

### 4.1 设计原则

1. **模板驱动**：每个 query_type 绑定一个回答模板，字段映射与空值处理规则集中定义，**禁止临时拼装**；
2. **自然语言优先**：回答是连贯的叙述文本，仅当记录数≥3 时回退到编号列表；
3. **必须包含数据来源行**：每条回答末尾附 `（数据来源：ztb_clean.{table_name}）`；
4. **空结果 ≠ 沉默**：必须给"可能原因 + 下一步建议"，引导用户到其他三个核心功能。

### 4.2 与现有 OutputTemplate 框架的关系

提案不替换 `output_templates.py` 的 `OutputTemplate / FieldDescriptor` 声明式框架，而是在其之上增加一个**回答渲染层**：

```
现有框架                             新增层
┌─────────────────────────┐        ┌─────────────────────┐
│ FieldDescriptor         │───────→│ AnswerTemplate      │
│   - key / label / table │  字段   │   - query_type      │
│   - null_behavior       │  供给   │   - template_html   │
│   - max_chars           │        │   - empty_template   │
│                         │        │   - multi_template   │
│ OutputTemplate          │        │   - guidance_options │
│   - required/optional   │        │                     │
│   - display_order       │        │ _render_answer()     │
└─────────────────────────┘        │   输入：records +    │
                                   │         intent       │
                                   │   输出：自然语言文本   │
                                   └─────────────────────┘
```

渲染逻辑伪代码：

```python
def _render_answer(query_type: str, records: list[dict], intent: SearchIntent) -> str:
    tmpl = ANSWER_TEMPLATES[query_type]
    if not records:
        return tmpl["empty_template"].format(
            entity=intent.hard_filters.company_name or intent.exact_tokens[0] if intent.exact_tokens else "该企业",
            total_rows=TABLE_ROW_COUNTS[tmpl["source_table"]],
        )
    if len(records) == 1:
        return tmpl["single_template"].format(**records[0], N=1)
    else:
        items = []
        for i, rec in enumerate(records[:5]):
            items.append(tmpl["item_line"].format(index=i+1, **rec))
        return tmpl["multi_template"].format(
            entity=extracted_entity,
            N=len(records),
            items="\n".join(items),
        )
```

### 4.3 输出行为契约

| query_type | 实体存在 + 有数据 | 实体存在 + 0 条 | 实体不存在 |
|-----------|------------------|----------------|-----------|
| company_detail / company_industry | 完整自然语言段落 | 不适用（基本信息必有） | "未收录该企业" + 引导 |
| penalty_check | 处罚详情 + 来源 | "暂无不良记录" | "未收录" + 引导 |
| bidder_query | 中标列表（可扩大） | "暂未查到中标" | "未查到" + 引导 |
| project_detail | 完整项目段落 | "未查到该项目" | "未查到" + 引导 |

---

## 5. 技术可行性评估

### 5.1 与现有架构的兼容性

| 现有组件 | 复用/改动方式 | 工作量 |
|---------|-------------|--------|
| router.py（一级路由） | 保留 `route_price_inquiry` 涵盖三大核心功能的描述，按 §7 建议由能力清单渲染 | 改 3 行 Prompt |
| `_UNIFIED_INTENT_SYSTEM`（意图 Prompt） | 删除 product_query 枚举，保留 company_query/bidding_query 规则，**拆 `exact_tokens` 为 `entity_name` + `project_number` 两字段**（消除 P0-1 列映射 Bug 隐患） | 改约 30 行 |
| `_SUB_ROUTE_MAP` | 移除 product_query，保留 company_query/bidding_query | 删 4 行 |
| `output_templates.py` | 删除产品模板 + 字段，新增 §6 的 `answer_templates.py` | 删约 60 行，新增约 150 行 |
| `_query_company_data()` | **零改动**，现有 penalty_check + credit_code 联查逻辑直接复用 | 0 |
| `_query_bidding_data()` | 新增 `successful_bidder` 精确探测路径 + `project_detail` 精确查询路径（均约 30 行/路径） | 约 60 行 |
| `_HARDCODED_SCHEMA` | 移除 product_info，保留 3 表 | 删 8 行 |
| 召回链 `_build_candidate_sql` | 改为 **"主键召回 + 模板字段二次回表"**（修复 SELECT 缺列缺陷） | 重构约 40 行 |
| MySQL DDL | 新增 2 条索引（company_name / project_name） | 2 行 ALTER |
| Milvus 集合 | 重建（排除 product_info） | 后台自动 |

**总改动量估算**：约 350 行增改 + 约 130 行删除，跨 5 个文件 + 2 条 DDL。**不改数据库表结构，不新增外部依赖**。

### 5.2 实现复杂度评估

| 模块 | 复杂度 | 说明 |
|------|--------|------|
| 实体探测 + 精确匹配守卫 | 🟢 低 | 复用 penalty_check 已验证的守卫模式，只需泛化到其他 query_type |
| 回答模板引擎 | 🟢 低 | 纯 Python 字符串模板，不涉及 LLM 调用、不引入新框架 |
| SELECT 缺列修复 | 🟡 中 | 需要修改三处 SQL 构建器，改为"先召回主键、再按模板字段全量回表"，涉及与 OutputTemplate 字段清单的契约对接 |
| 回答生成层 | 🟢 低 | 在 `node_price_inquiry` 末尾替换 `_format_records → _render_answer` |
| 索引新增 | 🟢 低 | 两条 ALTER TABLE，线上执行秒级 |

### 5.3 潜在风险识别

| 风险 | 等级 | 对策 |
|------|------|------|
| company_name 无索引导致 38,911 行探测变慢（未来数据增长后） | 🟡 中 | 立即加 `idx_company_name` |
| company_info 中同一企业可能有多条记录（不同年份/来源的数据版本） | 🟡 中 | 探测 SQL 加 `ORDER BY id DESC LIMIT 1`，取最新版本；或后续建立唯一性去重策略 |
| successful_bidder 数据含噪声（如"天康生物 (中型企业)"） | 🟡 中 | `LIKE '%公司名%'` 可覆盖；长期需清洗括号后缀 |
| project_name 无索引导致 17,742 行全表扫描 | 🟢 低 | 加索引或利用 FULLTEXT（全文索引需同时新增）；当前数据量下无感知 |
| 中标金额 = 0 显示为"0 元"误导用户 | 🟢 低 | §3.1 已定义特殊处理规则 |
| 实体探测 + 模板强绑定后，LLM 无法从口语化表达提取实体名 | 🟡 中 | 现有 `_parse_unified_intent` 已验证在 DeepSeek API 下实体提取准确率可靠；注册引导类兜底机制（测不到实体就出引导而非强行检索） |
| winning_amount 0 变为"未公开"但实际数据就是 0 — 改为"零"即可 | 🟢 更低 | 对内容做区分 |

---

## 6. 具体实施路线图

### Phase A：基础设施（半天）

```
A1. 新增 MySQL 索引：
    ALTER TABLE ztb_clean.company_info ADD INDEX idx_company_name(company_name);
    ALTER TABLE ztb_clean.bid_project ADD INDEX idx_project_name(project_name);

A2. SELECT 缺列修复：
    修改 _build_candidate_sql / _build_vector_recall_sql / _build_full_scan_sql 三处，
    改为"先召回主键、再按 OutputTemplate 声明的全部字段做主键回表查询"，
    彻底消灭"未提供"问题。
```

### Phase B：代码改造（1 天）

```
B1. 新建 agent/nodes/answer_templates.py：
    - ANSWER_TEMPLATES 字典（每 query_type 一个 AnswerTemplate）
    - _render_answer() 函数
    - empty_result_guidance() 通用引导话术生成

B2. 修改 agent/nodes/price_inquiry.py：
    - _SUB_ROUTE_MAP 移除 product_query（改 4 行）
    - _HARDCODED_SCHEMA 移除 product_info（改 8 行）
    - 新增 _query_project_detail() 函数
    - 新增 _query_bidder_history() 函数（含实体探测）
    - node_price_inquiry 末尾：_format_records → _render_answer

B3. 修改 agent/nodes/output_templates.py：
    - 删除 _PRODUCT_OUTPUT_TEMPLATES 与产品字段注册

B4. 修改 agent/router.py：
    - ROUTER_SYSTEM_PROMPT 移除产品语义
    - route_price_inquiry 工具描述收敛为三核功能

B5. 修改 _UNIFIED_INTENT_SYSTEM Prompt：
    - 删除 product_query 规则
    - exact_tokens 拆为 entity_names + project_numbers
```

### Phase C：回归验证（半天）

```
C1. 核心正向测试：
    ┌────────────────────────────────┬──────────────────────┐
    │ 查询                           │ 预期行为              │
    ├────────────────────────────────┼──────────────────────┤
    │ "安徽海纳信息科技有限公司"      │ company_detail 输出    │
    │ "四川胤伟建筑工程有无不良记录"  │ penalty_check 命中     │
    │ "福建师范大学中标了哪些项目"    │ bidder_query 命中      │
    │ "[350001]FJGGZY[GK]2024013"    │ project_detail 命中    │
    │ "张三公司"（不存在的实体）      │ 未收录 + 功能引导      │
    ├────────────────────────────────┼──────────────────────┤
    │ "电剪刀供应商推荐"（产品类）    │ 能力边界引导（Product offlined）│
    └────────────────────────────────┴──────────────────────┘

C2. SELECT 修复验证：
    所有 company_detail 输出必须包含 credit_code/business_status 等真实值，
    "未提供"仅出现在源数据真空的字段（如 credit_rating）。

C3. product_query 拦截验证：
    故意问产品价格 → 不执行任何 SQL，返回能力边界说明 + 引导。
```

### Phase D：Milvus 集合重建（后台，无停机）

```
D1. 从 _HARDCODED_SCHEMA 移除 product_info
D2. 触发 mysql_price_semantic 集合重建（bootstrap 线程自动执行）
    - 仅包含 company_info / company_penalty / bid_project 三表向量
    - 重建完成前语义召回用旧集合（含产品向量但不会被 _semantic_recall_candidates 筛选命中）
```

---

## 7. 附录

### 附录 A：三表字段注册表与当前代码对照

| query_type | 输出模板需要的字段 | 当前召回 SELECT 是否覆盖 | 修复后 |
|-----------|------------------|------------------------|--------|
| company_detail | company_name, credit_code, legal_person, registered_capital, establish_date, business_status, industry, company_type, company_level, address, business_scope | ❌ 只选 company_name/business_scope/industry/address 四列 | ✅ 主键回表取全部 |
| penalty_check | company_name, credit_code, penalty_date, law_enforcement_unit, illegal_behavior, penalty_result | ✅ `SELECT *`（`_query_penalty_by_company_name`） | 不受影响 |
| bidder_query | project_name, project_number, purchaser, successful_bidder, winning_amount, winning_date | ❌ 只选 project_name/purchaser/successful_bidder/subject_matter 四列 | ✅ 主键回表取全部 |
| project_detail | project_name, project_number, purchaser, successful_bidder, winning_amount, winning_date | ❌ 同上 | ✅ 主键回表或直接按 project_number UNIQUE 键单条取全量 |

### 附录 B：索引缺失项清单

| 表 | 缺失索引 | 影响 | SQL |
|----|---------|------|-----|
| company_info | `idx_company_name` | 探测/精确匹配全表扫描 | `ALTER TABLE company_info ADD INDEX idx_company_name(company_name);` |
| bid_project | `idx_project_name` | 按项目名模糊查询全表扫描 | `ALTER TABLE bid_project ADD INDEX idx_project_name(project_name);` |
| bid_project | FULLTEXT `ft_bid_semantic` | 当前无 FULLTEXT 支持，不影响精确匹配方案；如需未来恢复语义关键词召回则需新增 | 可选，非本次必须 |

### 附录 C：本次设计方案与 v2 线砍报告的关系

本报告是在 v2 砍线报告确定"三核聚焦"方向后，对剩余功能的精细化落地设计。两份报告共同构成完整的架构改造方案：

```
v2 vague_query_improvement_feasibility_report.md（砍线决策 + 入口门禁）
    │
    ├── 确定砍除产品线、保留三核
    ├── 确定引导式处理（不含产品引导）
    ├── 发现 SELECT 缺列缺陷
    └── 确定双门禁（入口 + 出口验收）
        │
        └──→ 本报告（三核心精细化设计）
              ├── 字段映射表（回答模板的契约）
              ├── 标准回答模板（自然语言化）
              ├── 检索引擎升级（实体探测 → 主键召回 → 全字段回表）
              └── 实施路线图（Phase A→D）
```
