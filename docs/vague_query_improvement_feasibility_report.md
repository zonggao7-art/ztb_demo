# 产品查询功能线砍除与系统能力收敛可行性评估报告（v2）

> 评估对象：`agent/nodes/price_inquiry.py`、`agent/nodes/output_templates.py`、`agent/router.py`、Milvus 集合 `mysql_price_semantic`
> 证据来源：代码静态分析 + "电剪刀供应商"实地测试日志 + `raw_tables/product_info.csv` 数据核查
> 版本说明：本报告替代 v1《含糊查询改进方案可行性评估》。v1 的四项措施判定调整如下——
> - **措施一（表-问题类型绑定）**：维持现状，暂不调整；
> - **措施二（预设输出结构）**：升级为**整条产品查询功能线砍除**；
> - **措施三（模糊查询引导）**：由直接拒绝改为**引导式处理**，且引导选项中**不包含产品价格查询**。
>
> 评估时间：2026-08-10

---

## 0. 结论速览（TL;DR）

| 维度 | 结论 |
|------|------|
| 砍线技术可行性 | ✅ **高**。影响面集中于 4 个文件 + 1 个 Milvus 集合，无跨模块硬依赖，全程可逆 |
| 预期收益 | 意图输出空间缩减约 1/3，失败模式面显著收窄，与"小而精"三核心能力战略完全对齐 |
| 核心风险 | ① Prompt 清理不彻底导致残留路由（用确定性拦截兜底化解）；② **SELECT 缺列缺陷是全系统性的，砍线后剩余三线仍会大面积"未提供"，必须同步修复** |
| 实施周期 | 三阶段：代码下线（半天）→ 向量库重建（后台自动）→ 回归验证（半天） |
| 架构影响 | 二级路由从 3+all 收敛为 2+兜底，能力集固化为三大核心功能，为能力契约制铺路 |

**两项必须随报告强调的事实核查结论**：

1. **"报价未提供"的真正根因是 SELECT 缺列 Bug，不是数据缺失**：`product_info.csv` 的 price 列 **100% 有值（19,139/19,139）**，但三个召回 SQL 构建器（`_build_candidate_sql` L1551、`_build_vector_recall_sql` L1643、`_build_full_scan_sql` L1598）只 SELECT `semantic` 分类的 4 列，price 属 `budget` 分类，**从未被取出**。砍线决策因此应被定义为**业务价值与战略收敛判断**，而非"数据无药可救"——报告的措辞与对外解释需按此口径。
2. **该缺陷是全系统性的**：company_info 召回 SQL 只取 `company_name/business_scope/industry/address` 四列，而 company_detail 输出模板要求的 `credit_code、business_status、legal_person、registered_capital` 等字段同样从未入库——这正是来凤县案例输出中"统一社会信用代码: 未提供 | 经营状态: 未提供"的根因。**若不修复此项，砍掉产品线后剩余三大功能线的输出仍会大面积"未提供"，能力收敛的价值将大打折扣。**

---

## 1. 决策背景与证据链

### 1.1 实地测试案例复盘（"电剪刀供应商"查询）

用户输入："给我推荐一些电剪刀的供应商，要价格便宜的"。

| 步骤 | 日志证据 | 判定 |
|------|---------|------|
| 意图解析 | `sub_route=product_query query_type=supplier_search product_name="电剪刀"`，耗时 1.810s | ✅ 解析正确 |
| 语义召回 | `[SEMANTIC_RECALL] {'product_info': 24}` | ✅ 正常 |
| FULLTEXT 召回 | `stage=FULLTEXT_OR rows=2` | 🔴 **3.8 万级产品库中"电剪刀"全文检索仅命中 2 行** |
| 排序 | SQL 含 `ORDER BY price ASC`，但 **SELECT 列表无 price 列** | 🔴 排序列与展示列脱节 |
| 最终输出 | 20 条记录，其中约 17 条为"深圳市艾威博尔"的各类无关五金工具（钢锯架/美工刀片/PVC 割刀），且**全部"报价: 未提供"** | 🔴 精度与字段完整度双重失效 |

### 1.2 证据修正：根因是取数 Bug，数据本身完好

对 `raw_tables/product_info.csv` 的核查结果：

```
总行数：19,139
price 列非空：19,139（100.0%）
样例：('PE材质预埋套筒…', '0.7', '个')、('钢筋套筒…', '1.7', '个')
```

price 与 price_unit 数据完整可用。技术层面的准确表述是：**召回链路的 SELECT 字段集只覆盖 `semantic` 分类列，`budget`（price）、`time`、`region` 等分类列一律未取，导致输出模板拿到的记录天然缺字段**。此缺陷在 v1 报告中未被识别，本次测试日志首次暴露。

### 1.3 为什么砍线决策依然成立

即使价格字段 Bug 可修复（在 SELECT 中补列即可），产品线的独立短板仍然存在：

1. **召回精度结构性偏弱**：产品名是短文本且高度同质化（"电剪刀"FULLTEXT 仅 2 命中），语义召回 24 条候选中约 7 成为同供应商的无关品类，ngram 全文索引对短商品名区分度不足——修复价格显示后，"推荐电剪刀供应商却返回美工刀片"的精度问题依旧；
2. **业务必要性存疑**：招投标核心场景的决策依据是企业资信（工商情报）、风险（黑名单）与业绩（中标情报），产品报价数据来自供应商自报，时效性与权威性均弱于前三者；
3. **战略对齐**：砍线是"小而精、能力边界明确"路线的直接落地——与其维持一条"能召回、答不准"的功能线，不如把工程资源集中到三条可验收（实体精确命中/查无即答）的核心线。

**决策定性：这是基于业务价值与质量确定性的产品决策，技术证据（价格 Bug）是可修复项，不构成砍线的必要性，但强化了"该功能线质量债务最重"的判断。**

---

## 2. 调整后的方案总览

| 措施 | v1 判定 | v2 调整 | 说明 |
|------|---------|---------|------|
| 一：表-问题类型绑定 | 修正后采纳 | **维持现状，暂不调整** | 砍线后现有 `_SUB_ROUTE_MAP` 天然收敛为 company_query(company_info+penalty) 与 bidding_query(bid_project)，绑定关系更清晰，无需额外改造 |
| 二：预设输出结构 | 补齐采纳 | **砍除产品查询整条功能线** | 删除 product_query 路由、4 套产品输出模板、12 个产品字段注册；剩余两线的输出模板保留 |
| 三：模糊查询引导 | P0 采纳 | **改为引导式处理，引导项不含产品价格** | 产品类问题命中时给出"能力边界说明 + 三大核心功能引导"，不硬拒答 |

砍线后的系统能力集：

| 核心功能 | 绑定表 | 标准问法 |
|---------|--------|---------|
| 企业工商情报查询 | company_info | "XX公司详情/工商信息" |
| 企业风控黑名单查询 | company_penalty | "XX公司有无不良记录/处罚" |
| 招投标中标情报查询 | bid_project | "XX公司中标了什么项目/XX项目中标金额" |

---

## 3. 技术可行性分析

### 3.1 影响面清单（代码级盘点结果）

| 文件 | 涉产品功能位置 | 处理动作 |
|------|---------------|---------|
| `agent/nodes/price_inquiry.py`（52 处命中） | `_UNIFIED_INTENT_SYSTEM` 的 product_query 分支与 6 个产品字段（L250/L262-266/L287/L297-300）、`HardFilters` 产品字段（L151-154）、`_HARDCODED_SCHEMA["product_info"]`（L632）、`_SUB_ROUTE_MAP["product_query"]`、`_query_product_data()`（L2260）、price_range 条件构建（L1313-1319） | 分"删除"与"拦截保留"两类处理，见 §6 |
| `agent/nodes/output_templates.py`（51 处命中） | `_PRODUCT_OUTPUT_TEMPLATES` 4 套模板（L194-251）、12 个产品字段注册（L88-106）、`_ROUTE_TEMPLATES["product_query"]` | 整块删除 |
| `agent/router.py` | `route_price_inquiry` 工具描述"产品中标价/报价/行情/多少钱"（L61）、`ROUTER_SYSTEM_PROMPT` 产品条目 | 删除产品语义条目 |
| Milvus `mysql_price_semantic` | 集合中含 product_info 向量（pk 前缀 `product_info:`） | 重建集合；一致性校验 `_get_expected_semantic_row_count()` 基于 `_HARDCODED_SCHEMA` 统计，删除 schema 条目后自动对齐 |
| MySQL `ztb_clean.product_info` | 数据表本体 | **保留不删**（回滚资产） |
| `test/` 检索测试脚本 | 含产品类查询用例 | 用例改写为"应返回引导话术" |

### 3.2 关键设计：确定性拦截兜底，不依赖 Prompt 清理彻底性

LLM 意图解析无法保证 100% 不再输出 `product_query`（Prompt 清理总有遗漏，且用户可能用极隐晦的方式问价）。因此砍线**必须以节点层确定性拦截为安全网**：

```python
# node_price_inquiry 入口，意图解析完成后：
if intent.sub_route == "product_query":
    return _build_capability_boundary_answer(question)   # 能力边界说明 + 三核心功能引导，不执行任何 SQL
```

这样即使一级路由或意图 Prompt 残留产品语义，最坏结果也只是"提前进入引导分支"，绝无可能再触发 product_info 检索。**先立拦截、再清 Prompt，顺序不可颠倒。**

### 3.3 Milvus 语义集合的处理

- `_semantic_recall_candidates(intent, tables)` 只检索调用方传入的表，砍线后传入表集合不再包含 product_info，残留的 product 向量不会被命中——**即使不立即重建集合，系统行为也已正确**；
- 但 `_get_expected_semantic_row_count()` 按 `_HARDCODED_SCHEMA` 全表统计期望行数。删除 product_info schema 条目后，期望行数下降，现有集合行数 ≥ 期望值，`_is_mysql_semantic_collection_ready()` 仍判定就绪，不会误触发重建——**一致性检查天然兼容**；
- 建议在 Phase 2 择机重建集合（清除死数据、缩减检索噪声），后台 bootstrap 机制已支持，无停机风险。

### 3.4 可行性结论

**高**。无数据库结构变更、无外部接口变更、无前端强依赖（引导话术是纯文本），全部改动集中在应用层，git revert 即可完整回滚。

---

## 4. 预期收益评估

| 收益维度 | 量化/具体化 |
|---------|------------|
| 意图空间收敛 | LLM 意图输出空间：sub_route 从 4 值减为 3 值；query_type 枚举减少 4 个（price_inquiry/supplier_search/product_detail/mixed）；hard_filters 输出字段从 22 个减为 16 个——**输出空间缩减约 1/3，意图误判与字段错填概率同比例下降** |
| 失败模式消除 | 一次性消灭：短文本产品名召回精度差、价格排序与展示脱节、need_contact 联系方式输出合规风险、supplier_search 与 supplier_recommend 的归类混淆 |
| 语义召回质量 | 集合重建后候选全部来自企业/中标数据，实体查询的向量召回不再被产品行稀释（当前 TOP_K=64 中产品行可占 24 席） |
| 维护面收缩 | 删除约 180 行模板/注册代码 + 意图 Prompt 中约 15 行规则，文档与测试用例同步减负 |
| 产品叙事清晰 | 对外能力边界 = 三大核心功能，能力说明、快捷提问、引导话术全部可静态枚举，为能力契约制（v1 报告 §7 方向）奠定清单基础 |

---

## 5. 潜在风险分析

### 5.1 🔴 最高优先级：SELECT 缺列缺陷是全系统性的（砍线的必要伴随修复）

如 §0 所述，召回 SQL 只取 semantic 列的缺陷同样作用于 company_info 与 bid_project：

- company_detail 输出模板要求 `credit_code/business_status/legal_person/registered_capital/establish_date` 等——均不在召回 SELECT 中，来凤县案例输出的"未提供"即由此产生；
- bid_project 的 `winning_amount/winning_date/project_number` 属 budget/time/exact 分类，同样未取。

**风险表述：若砍线后不修复此项，用户查询"XX公司详情"仍会看到大面积"未提供"，砍线的质量收益无法兑现，且舆论上会形成"砍了产品线也没变好"的负面认知。**

**必选伴随修复（约 20 行）**：召回阶段只取主键，命中后按主键二次回表取输出模板声明的全部字段（`SELECT * FROM table WHERE id IN (...)`），字段契约以 OutputTemplate 为唯一事实源。此修复使"报价未提供"类问题在剩余三线一次性绝迹。

### 5.2 残留路由风险（低，已有对策）

一级路由可能继续把"XX多少钱"判入 price_inquiry（路由工具描述清理滞后时）。§3.2 的确定性拦截保证最坏情况落入引导分支，无数据风险。

### 5.3 用户体验风险（中）

存量用户若习惯问产品价格，将遇到功能下线说明。对策：话术明确告知"本系统专注于企业工商情报、风控黑名单与中标情报查询"，并给出三个可直接点击/复述的示例问法——**把每次撞墙转化为一次能力教育**。

### 5.4 数据资产与可逆性（低）

product_info 表、CSV 源数据、Milvus 集合备份均保留；代码改动可 git revert。**砍线是"功能下线"而非"数据销毁"，业务价值重估时可低成本恢复。**

### 5.5 决策口径风险（中，管理层面）

因价格 Bug 实为可修复缺陷，砍线决策对外解释时必须按 §1.3 口径（业务价值 + 战略收敛），避免"修不好所以砍掉"的错误叙事损害团队技术信誉。

---

## 6. 具体执行思路与实施步骤

### Phase 1：代码下线（半天，顺序敏感）

1. **立拦截**：`node_price_inquiry` 入口添加 `product_query → 能力边界引导` 确定性分支（先于一切清理动作）；
2. **清路由**：`router.py` 删除 `route_price_inquiry` 描述与 `ROUTER_SYSTEM_PROMPT` 中的产品语义条目；
3. **收意图**：`_UNIFIED_INTENT_SYSTEM` 删除 product_query 分支规则与产品字段；`_safe_parse_intent` 的 valid_routes 移除 product_query（配合步骤 1 的拦截，双保险）；
4. **删执行**：移除 `_SUB_ROUTE_MAP["product_query"]`、`_query_product_data()`、`_HARDCODED_SCHEMA["product_info"]`；`HardFilters` 产品字段与 price_range 条件构建代码保留一个版本周期后再清（降低回滚成本）；
5. **删模板**：`output_templates.py` 删除 `_PRODUCT_OUTPUT_TEMPLATES`、`_ROUTE_TEMPLATES["product_query"]` 与 12 个产品字段注册；
6. **写话术**：能力边界引导话术定稿（含三大核心功能示例问法），引导选项中明确不含产品价格项。

### Phase 2：向量库与数据（后台，无停机）

7. 触发 `mysql_price_semantic` 集合重建（排除 product_info），复用现有后台 bootstrap 线程机制；
8. MySQL `product_info` 表保留，仅添加表注释标记 deprecated。

### Phase 3：验证与回归（半天）

9. **砍线回归**："给我推荐电剪刀的供应商" / "防水涂料多少钱" → 期望：能力边界引导话术，全程 0 条 product_info SQL；
10. **核心线无损回归**：企业详情 / 不良记录 / 中标历史三类标准查询行为不回退；
11. **SELECT 修复验证**（若 §5.1 伴随修复已实施）：company_detail 输出中 credit_code/business_status 等字段真实呈现，"未提供"仅出现在数据真空的字段；
12. 更新 `test/` 用例与 `docs/` 能力文档。

---

## 7. 对整体系统架构的影响评估

### 7.1 路由结构收敛

```
砍线前：                              砍线后：
router(5 意图)                        router(5 意图，产品语义清除)
  └─ price_inquiry                      └─ price_inquiry
       ├─ company_query → 2 表               ├─ company_query → company_info + penalty
       ├─ product_query → product_info ✂     ├─ bidding_query → bid_project
       ├─ bidding_query → bid_project        └─ product_query → 能力边界引导（确定性拦截）
       └─ all → 4 表遍历
```

措施一"维持现状"与砍线天然兼容：表绑定关系无需重构，只是集合变小、边界变硬。

### 7.2 与"能力边界明确"战略的衔接

砍线是能力契约制的第一次实战收缩：能力清单从 10 项收敛为 6 项（penalty_check / company_detail / bidder_query / purchaser_query / project_detail / bid_aggregation）。后续引导机制（措施三）的选项集、前端能力展示、路由 Prompt 均可直接由这份收敛后的清单派生。

### 7.3 必须同步清偿的架构欠账

本次测试暴露的 SELECT 缺列缺陷（§5.1）说明：**召回链"取数最小化 + 模板最大化"之间存在契约断裂**。砍线减少了一条暴露面，但断裂本身仍在。建议将"召回命中 → 按 OutputTemplate 声明字段全量回表"确立为检索链的固定二级结构，作为砍线后的第一项架构改进。

### 7.4 总体判定

砍除产品查询功能线在技术上**低风险、可逆、收益明确**，在战略上是"小而精"路线的必要收敛。其成败不取决于砍线动作本身，而取决于两件事是否同步做到：**确定性拦截先于 Prompt 清理**（防残留路由），**SELECT 缺列缺陷同步修复**（防"砍了也没变好"）。两项做到，则本次调整成为系统从"大而全的必答型架构"转向"边界明确的专业化架构"的第一个完整闭环。

---

## 附录：关键证据索引

| 证据 | 位置 |
|------|------|
| SELECT 只取 semantic 列（三处构建器） | `price_inquiry.py` `_build_candidate_sql` L1551-1553 / `_build_vector_recall_sql` L1643-1646 / `_build_full_scan_sql` L1598-1600 |
| price 在 budget 分类、不在 semantic 列 | `price_inquiry.py` `_HARDCODED_SCHEMA["product_info"]` L632-639 |
| price 数据 100% 完整 | `raw_tables/product_info.csv` 核查：19,139/19,139 行非空 |
| "电剪刀" FULLTEXT 仅 2 命中 | 测试日志 `[RECALL_CHAIN] table=product_info stage=FULLTEXT_OR rows=2` |
| 排序列未取出 | 测试日志 SQL `ORDER BY price ASC` 但 SELECT 无 price |
| company 线同样缺列的证据 | 来凤县案例输出"统一社会信用代码: 未提供 | 经营状态: 未提供"（credit_code/business_status 不在召回 SELECT） |
| 产品功能代码分布面 | price_inquiry.py 52 处 / output_templates.py 51 处 / router.py 工具描述 |
| Milvus 一致性检查天然兼容 | `_get_expected_semantic_row_count()` 基于 `_HARDCODED_SCHEMA` 动态统计 |
