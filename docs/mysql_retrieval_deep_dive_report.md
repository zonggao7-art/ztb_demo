# MySQL 数据库检索策略全面排查分析报告

> **生成日期**：2026-08-08  
> **分析范围**：`agent/nodes/price_inquiry.py` 完整检索链路 + `test/` 诊断工具链  
> **数据基础**：MySQL `ztb_clean` 数据库（4 张表，company_info / company_penalty / product_info / bid_project）  
> **分析目标**：排查非结构化自然语言查询失效的根因，输出可落地优化方案

---

## 目录

1. [现有检索策略完整逻辑拆解](#1-现有检索策略完整逻辑拆解)
2. [全链路流程图](#2-全链路流程图)
3. [当前检索能力的边界测试记录](#3-当前检索能力的边界测试记录)
4. [非结构化自然语言查询失效根因分析](#4-非结构化自然语言查询失效根因分析)
5. [可落地优化方向建议](#5-可落地优化方向建议)
6. [附录：关键代码索引](#6-附录关键代码索引)

---

## 1. 现有检索策略完整逻辑拆解

### 1.1 系统架构总览

当前系统存在两条完全独立的检索路径，由 LangGraph Agent 骨架中的 Router（LLM 意图分类）进行分发：

| 路由意图 | 目标节点 | 检索引擎 | 数据源 | 核心技术 |
|----------|---------|----------|--------|----------|
| `knowledge_qa` | `node_knowledge_qa` | Milvus 向量检索 (RAG) | PDF 法规文档 (3本) | BGE-M3 Embedding + COSINE 相似度 + Reranker |
| `price_inquiry` | `node_price_inquiry` | MySQL FULLTEXT 全文检索 | ztb_clean 数据库 (4张表) | ngram 分词 + Boolean Mode + 混合重排序 |
| `general_chat` | `node_general_chat` | 无检索 | — | 纯 LLM 对话 |
| `doc_qa` | `node_doc_qa` | 占位 | — | Demo 阶段未实现 |

**本次分析聚焦于 `price_inquiry` → MySQL 检索路径**，这是唯一涉及结构化数据库查询的分支，也是用户反馈的非结构化查询失效问题的核心发生区域。

### 1.2 MySQL 检索全链路详解（五阶段架构）

#### 阶段 0：入口 — 从用户输入到结构化意图

```
用户输入（自然语言）
    │
    ▼
┌─────────────────────────────────┐
│  Router (agent/router.py)       │
│  LLM 意图分类 (deepseek-chat)    │
│  → router_intent = "price_inquiry" │
└──────────────┬──────────────────┘
               ▼
┌─────────────────────────────────┐
│  node_price_inquiry(state)      │
│  提取 messages[-1].content      │
│  → question (原始用户输入)       │
└──────────────┬──────────────────┘
               ▼
         【进入阶段 1】
```

#### 阶段 1：统一意图解析（LLM → SearchIntent）

**文件**：[agent/nodes/price_inquiry.py](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py) L298-L337  
**核心函数**：`_parse_unified_intent(question, llm)`

**处理流程**：

```
用户问题 (question)
    │
    ▼
┌─────────────────────────────────────────────────┐
│  _UNIFIED_INTENT_PROMPT (ChatPromptTemplate)     │
│  System: 招投标领域智能查询意图解析专家            │
│  → 一次性完成两项任务：                           │
│     ① 判断二级路由 (sub_route)                    │
│     ② 提取结构化过滤条件 (hard_filters)           │
└────────────────────┬────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────┐
│  LLM (deepseek-chat, temperature=0.0)            │
│  → 输出 JSON (RouterDecision schema)             │
└────────────────────┬────────────────────────────┘
                     ▼
┌─────────────────────────────────────────────────┐
│  _extract_json() → 解析 JSON                      │
│  SearchIntent.from_dict() → 构造 SearchIntent    │
└────────────────────┬────────────────────────────┘
                     ▼
               SearchIntent 对象
```

**SearchIntent 数据结构**（L97-L113）：

| 字段 | 类型 | 用途 | 示例值 |
|------|------|------|--------|
| `sub_route` | `Literal["company_query" \| "product_query" \| "bidding_query" \| "all"]` | 二级路由目标 | `"bidding_query"` |
| `query_type` | `str` | 查询子类型 | `"purchaser_query"` / `"price_inquiry"` |
| `hard_filters` | `HardFilters` | 确定性过滤条件 | `{province: "福建", time_range: {start: "2024-01-01"}}` |
| `semantic_keywords` | `list[str]` | 语义关键词（→ FULLTEXT 检索词） | `["保温材料", "福建"]` |
| `exact_tokens` | `list[str]` | 精确匹配 token（→ `=` 条件） | `["ZB2024-001"]` |
| `sort_by` | `str \| None` | 排序方式 | `"amount_desc"` |
| `aggregation` | `str \| None` | 聚合类型 | `"max_amount"` |
| `top_n` | `int \| None` | 返回 Top-N | `5` |
| `need_penalty_check` | `bool` | 是否需要联查处罚表 | `false` |
| `need_contact` | `bool` | 是否需要联系方式 | `false` |

**容错机制**：
- LLM 调用失败或 JSON 解析失败 → 回退到 `_extract_keywords()` 兜底关键词提取（L359-L372）
- `_safe_parse_intent()` 对缺失字段回填默认值（L340-L353）

#### 阶段 2：二级路由分发

**文件**：[agent/nodes/price_inquiry.py](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py) L993-L1010

**路由映射表** (`_SUB_ROUTE_MAP`)：

| sub_route | 目标表 | 查询函数 |
|-----------|--------|---------|
| `company_query` | `company_info`, `company_penalty` | `_query_company_data()` |
| `product_query` | `product_info` | `_query_product_data()` |
| `bidding_query` | `bid_project` | `_query_bidding_data()` |
| `all` | 全部 4 张表 | `_query_all_tables()` |

#### 阶段 3：确定性 SQL 构建（核心检索层）

**文件**：[agent/nodes/price_inquiry.py](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py) L606-L649  
**核心函数**：`_build_candidate_sql(table, classification, intent)`

##### 3.1 硬编码 Schema（列分类）

每张表的列被预分类为 6 种角色（`_HARDCODED_SCHEMA` L391-L429）：

| 角色 | 含义 | bid_project 示例列 | SQL 用途 |
|------|------|-------------------|----------|
| `semantic` | FULLTEXT 索引覆盖列 | `project_name`, `purchaser`, `successful_bidder`, `subject_matter` | MATCH...AGAINST 目标 |
| `time` | 时间列 | `winning_date`, `publish_date` | 时间范围硬过滤 |
| `budget` | 金额列 | `winning_amount`, `budget_amount` | 金额范围硬过滤 |
| `region` | 地区列 | `province`, `city`, `district` | 地区精确匹配 |
| `exact` | 精确标识列 | `project_number` | 精确 token 匹配 |
| `id` | 主键 | `id` | 唯一标识 |

> **关键设计约束**：只有 `semantic` 角色列参与 FULLTEXT 索引，`region`/`time`/`budget` 列通过独立的 `=` / `>=` / `<=` 条件过滤，不参与全文检索。

##### 3.2 FULLTEXT 检索词构造

**核心函数**：`_build_search_term(intent)` (L445-L454)

```python
def _build_search_term(intent: SearchIntent) -> str:
    parts: list[str] = []
    for kw in intent.semantic_keywords:
        if kw:
            parts.append(f"+{kw}")       # ← 每个关键词前加 +
    for token in intent.exact_tokens:
        if token:
            parts.append(f'+"{token}"')  # ← 精确短语用双引号
    return " ".join(parts)
```

**构造规则**：
- `+keyword`：该词 **必须** 出现在 FULLTEXT 索引列中（MySQL Boolean Mode AND 语义）
- `+"exact phrase"`：精确短语匹配

**典型输出示例**：

| 用户输入 | LLM 提取的 keywords | 生成的 search_term |
|----------|-------------------|-------------------|
| "福建师范大学" | `["福建师范大学"]` | `+福建师范大学` |
| "电剪刀" | `["电剪刀"]` | `+电剪刀` |
| "保温材料" | `["保温材料"]` | `+保温材料` |
| "我想找福建那边做保温材料的供应商" | `["保温材料", "供应商", "福建"]` | `+保温材料 +供应商 +福建` |

##### 3.3 硬过滤条件构造

**核心函数**：`_build_hard_conditions_extended()` (L517-L584)

条件生成方式（以 bidding_query 为例）：

| 过滤维度 | SQL 条件 | 匹配方式 |
|----------|---------|----------|
| 省份 | `` `province` = %s `` | **精确等值**（需完全一致） |
| 城市 | `` `city` = %s `` | **精确等值** |
| 时间范围 | `` `winning_date` >= %s AND `winning_date` <= %s `` | 范围匹配 |
| 中标金额 | `` `winning_amount` >= %s AND `winning_amount` <= %s `` | 范围匹配 |
| 采购人 | `` `purchaser` = %s `` | **精确等值** |
| 中标供应商 | `` `successful_bidder` = %s `` | **精确等值** |
| 项目编号 | `` `project_number` = %s `` | **精确等值** |
| 项目阶段 | `` `project_stage` = %s `` | **精确等值** |

##### 3.4 最终 SQL 模板

```sql
SELECT `id` AS `_id_`,
       LEFT(`project_name`, 800) AS `project_name`,
       LEFT(`purchaser`, 800) AS `purchaser`,
       LEFT(`successful_bidder`, 800) AS `successful_bidder`,
       LEFT(`subject_matter`, 800) AS `subject_matter`,
       MATCH(`project_name`, `purchaser`, `successful_bidder`, `subject_matter`)
           AGAINST (%s IN BOOLEAN MODE) AS `_score_`
FROM `bid_project`
WHERE MATCH(`project_name`, `purchaser`, `successful_bidder`, `subject_matter`)
          AGAINST (%s IN BOOLEAN MODE)
  AND `province` = %s                           -- 硬过滤
  AND `winning_date` >= %s                      -- 硬过滤
  AND `winning_date` <= %s                      -- 硬过滤
ORDER BY `_score_` DESC
LIMIT 200
```

**关键约束**：
- 如果 `search_term` 为空 **且** 无硬过滤条件 → 该表被跳过，不执行任何查询（L638-L640）
- 必须有 FULLTEXT MATCH 条件或硬过滤条件中的至少一个
- LIMIT 200 硬截断
- `LEFT(col, 800)` 对长文本列截断以控制网络 I/O

#### 阶段 4：SQL 执行与结果收集

**核心函数**：`_query_tables(tables, intent)` (L712-L788)

```
for table in tables:
    ├── _get_classification(table)  → 获取表的列分类
    ├── _build_candidate_sql()      → 构建 SQL
    ├── cur.execute(sql, params)    → 执行（含耗时记录 _profile_execute）
    ├── 异常处理：
    │   ├── FULLTEXT 缺失 → 记录 WARNING，跳过该表
    │   └── 其他异常 → 记录 DEBUG，跳过该表
    └── fetchall() → 收集结果行（标记 _source_db, _source_table）
```

**关键容错**（L742-L747）：
```python
except Exception as e:
    if "fulltext" in str(e).lower():
        logger.warning("[FULLTEXT_MISSING] ...")
    else:
        logger.debug("查询 ... 失败: ...")
    continue  # ← 静默跳过，不降级到 LIKE
```

#### 阶段 5：混合重排序

**核心函数**：`_rank_records(records, intent, top_k=20)` (L685-L706)

```
for each record:
    mysql_score = FLOAT(record["_score_"])         # MySQL FULLTEXT 相关性得分
    text = 拼接所有非元数据字段
    semantic_score = _hybrid_score(intent, text)    # Python 关键词命中得分
    record["_hybrid_score_"] = mysql_score + semantic_score

排序 → 截断 Top 20
```

**Python 关键词语义得分** (`_hybrid_score()` L672-L682)：

```python
def _hybrid_score(intent, text):
    score = 0.0
    for kw in intent.semantic_keywords:
        if kw in text_lower:
            score += 1.0 * text_lower.count(kw.lower())  # 每命中一次 +1
    for token in intent.exact_tokens:
        if token in text:
            score += 10.0                                 # 精确匹配 +10
    return score
```

### 1.3 MySQL 索引配置

#### FULLTEXT 索引（ztb_clean 数据库）

| 表 | 索引名 | 覆盖列 | 解析器 |
|-----|--------|--------|--------|
| `company_info` | `ft_company_info` | `company_name`, `business_scope`, `industry`, `address` | ngram |
| `company_penalty` | `ft_penalty` | `company_name`, `illegal_behavior`, `penalty_result` | ngram |
| `product_info` | `ft_product` | `product_name`, `supplier_name`, `product_parameters`, `category` | ngram |
| `bid_project` | `ft_bid_project` | `project_name`, `purchaser`, `successful_bidder`, `subject_matter` | ngram |

#### ngram 分词器配置

```ini
[mysqld]
ngram_token_size=2    # 中文按每 2 个字符切分
ft_min_word_len=1     # 最小索引词长度
```

**分词示例**（以 `ngram_token_size=2` 切分 "福建师范大学"）：

| 原文 | ngram 切分结果 |
|------|---------------|
| 福建师范大学 | `福建` `建师` `师范` `范大` `大学` |

> 可以看到，"建师"、"范大" 是无意义的噪声 token，但 "福建" 和 "大学" 会被保留为有效索引词。

#### BTREE 索引（精确匹配用）

| 表 | 索引列 | 用途 |
|----|--------|------|
| `company_info` | `province`, `city`, `industry`, `credit_code` (UNIQUE) | 精确过滤 |
| `product_info` | `product_name`, `category`, `price`, `province` | 精确过滤 |
| `bid_project` | `purchaser`, `successful_bidder`, `winning_date`, `winning_amount`, `province`, `project_stage`, `project_number` (UNIQUE) | 精确过滤 |

---

## 2. 全链路流程图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          用户自然语言输入                                 │
│         例: "我想找福建那边做保温材料的供应商，价格要便宜一点的"              │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  [阶段 0] Agent Router — LLM 意图分类                                    │
│  ─────────────────────────────────────────                              │
│  模型: deepseek-chat (temperature=0)                                     │
│  输出: router_intent = "price_inquiry"                                   │
│  路由: START → router → price_inquiry                                    │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  [阶段 1] 统一意图解析 — LLM → SearchIntent                              │
│  ─────────────────────────────────────────                              │
│  Prompt: _UNIFIED_INTENT_SYSTEM (L175-L246)                              │
│  ┌─────────────────────────────────────────────────────┐                │
│  │ 输入: "我想找福建那边做保温材料的供应商，价格要便宜一点的"  │                │
│  │ 输出 JSON:                                            │                │
│  │ {                                                     │                │
│  │   "sub_route": "product_query",                       │                │
│  │   "query_type": "supplier_search",                    │                │
│  │   "hard_filters": {                                   │                │
│  │     "province": "福建",                                │                │
│  │     "price_range": {"min": null, "max": null}          │                │
│  │   },                                                  │                │
│  │   "semantic_keywords": ["保温材料", "供应商"],          │                │
│  │   "exact_tokens": [],                                 │                │
│  │   "sort_by": "price_asc"                              │                │
│  │ }                                                     │                │
│  └─────────────────────────────────────────────────────┘                │
│                                                                         │
│  容错: LLM失败/JSON解析失败 → _extract_keywords() 兜底                   │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  [阶段 2] 二级路由分发                                                    │
│  ─────────────────────                                                  │
│  sub_route = "product_query" → tables = ["product_info"]                │
│  query_fn = _query_product_data()                                        │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  [阶段 3] 确定性 SQL 构建                                                 │
│  ───────────────────────                                                │
│                                                                         │
│  3a. 获取 Schema: _HARDCODED_SCHEMA["product_info"]                     │
│      semantic: [product_name, supplier_name, product_parameters,        │
│                  category]                                              │
│      budget:   [price]                                                  │
│      region:   [province, city]                                         │
│                                                                         │
│  3b. 构造 FULLTEXT 检索词: _build_search_term(intent)                    │
│      semantic_keywords = ["保温材料", "供应商"]                           │
│      → search_term = "+保温材料 +供应商"                                  │
│      ⚠ 使用 + 前缀 → MySQL Boolean Mode AND 语义                        │
│                                                                         │
│  3c. 构造硬过滤条件: _build_hard_conditions_extended()                    │
│      province = "福建" → `province` = '福建'                              │
│      ⚠ 精确等值匹配，需完全一致                                           │
│                                                                         │
│  3d. 生成最终 SQL:                                                       │
│  ┌──────────────────────────────────────────────────────────┐           │
│  │ SELECT `id` AS `_id_`,                                    │           │
│  │   LEFT(`product_name`, 800) AS `product_name`,           │           │
│  │   LEFT(`supplier_name`, 800) AS `supplier_name`,          │           │
│  │   LEFT(`product_parameters`, 800) AS `product_parameters`,│           │
│  │   LEFT(`category`, 800) AS `category`,                    │           │
│  │   MATCH(`product_name`, `supplier_name`,                  │           │
│  │     `product_parameters`, `category`)                     │           │
│  │     AGAINST ('+保温材料 +供应商' IN BOOLEAN MODE)          │           │
│  │     AS `_score_`                                          │           │
│  │ FROM `product_info`                                       │           │
│  │ WHERE MATCH(`product_name`, `supplier_name`,              │           │
│  │       `product_parameters`, `category`)                   │           │
│  │       AGAINST ('+保温材料 +供应商' IN BOOLEAN MODE)        │           │
│  │   AND `province` = '福建'                                  │           │
│  │ ORDER BY `price` ASC                                      │           │
│  │ LIMIT 200                                                 │           │
│  └──────────────────────────────────────────────────────────┘           │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  [阶段 4] SQL 执行与结果收集                                              │
│  ─────────────────────────                                              │
│  cur.execute(sql, params) → 记录耗时 [SQL_PROFILE]                       │
│  rows = cur.fetchall()                                                   │
│  异常: FULLTEXT缺失 → 跳过, 其他异常 → 跳过                               │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  [阶段 5] 混合重排序                                                      │
│  ─────────────────                                                      │
│  综合得分 = MySQL _score_ (FULLTEXT相关性)                                │
│           + Python _hybrid_score_ (关键词命中计数)                        │
│  排序 → 截断 Top 20 → 输出字段筛选 → 返回用户                              │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 当前检索能力的边界测试记录

### 3.1 测试方法

基于 `test/test_sub_route.py` 中的测试用例和代码逻辑分析，将用户查询分为以下类别进行边界测试：

### 3.2 测试结果矩阵

#### 类别 A：独立关键词查询（✅ 预期正常）

| 查询输入 | LLM 提取的 keywords | 期望召回 | 实际结果 | 原因分析 |
|----------|-------------------|----------|----------|----------|
| "福建师范大学" | `["福建师范大学"]` | ✅ | ✅ 正常 | 单个关键词，ngram 可匹配 |
| "电剪刀" | `["电剪刀"]` | ✅ | ✅ 正常 | 3字词 → ngram 切分为 `电剪`+`剪刀`，可匹配 |
| "保温材料" | `["保温材料"]` | ✅ | ✅ 正常 | 4字词 → ngram 可匹配 |
| "防水涂料" | `["防水涂料"]` | ✅ | ✅ 正常 | 4字词 → ngram 可匹配 |

#### 类别 B：口语化自然语言查询（❌ 预期失效）

| 查询输入 | LLM 提取的 keywords | 生成的 search_term | 期望 | 实际 | 失效原因 |
|----------|-------------------|--------------------|------|------|----------|
| "我想找福建那边做保温材料的供应商，价格要便宜一点的" | `["保温材料", "供应商"]` + hard_filter: `province="福建"` | `+保温材料 +供应商` AND `province='福建'` | 应有结果 | **大概率空** | 见根因 4.1~4.5 |
| "有没有哪个公司在福建中标过保温材料相关项目" | `["保温材料", "福建"]` | `+保温材料 +福建` | 应有结果 | **大概率空** | 见根因 4.1~4.3 |
| "查一下最近一年福州那边的采购项目都有哪些" | `["福州", "采购项目"]` + hard_filter: `time_range` | `+福州 +采购项目` + 时间过滤 | 应有结果 | **可能空** | 见根因 4.2 |
| "帮我看看有没有做防水材料的靠谱厂家" | `["防水材料", "厂家"]` | `+防水材料 +厂家` | 应有结果 | **可能空** | 见根因 4.1 |

#### 类别 C：边界条件测试

| 测试场景 | 输入 | 预期行为 | 实际行为 | 分析 |
|----------|------|----------|----------|------|
| 单字关键词 | "床" | 无结果 | ❌ 空 | ngram_token_size=2，单字无法被索引 |
| 超长关键词 | 一段 30 字的描述性文字 | 回退到关键词提取 | 取决于 LLM | LLM 需能正确提取核心实体 |
| 混合中英文 | "采购ERP系统" | 部分匹配 | ⚠️ 不稳定 | ngram 对英文不友好 |
| 同义词查询 | "询价" vs 数据中的"询价采购" | 部分匹配 | ⚠️ 可能空 | ngram 切分 `询价` 可匹配，但 `询价采购`→`询价`+`价采`+`采购` |
| FULLTEXT 索引未建 | 任意查询 | 跳过该表 | ⚠️ 静默跳过 | 有 WARNING 日志但无用户提示 |
| 全部 keywords 为空 | "你好" | 全表无搜索条件 | ⚠️ 跳过 | `search_term` 为空且无 hard_filters → 不执行查询 |

---

## 4. 非结构化自然语言查询失效根因分析

### 4.1 根因一：MySQL Boolean Mode 的 AND 语义过严（🔴 核心根因）

**位置**：[price_inquiry.py L445-L454](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py#L445-L454)，`_build_search_term()`

**问题机制**：

```python
for kw in intent.semantic_keywords:
    parts.append(f"+{kw}")   # ← 每个关键词前强制加 + (AND 语义)
```

MySQL Boolean Full-Text Search 中，`+` 前缀表示该词 **必须出现在匹配行中**。当 LLM 从自然语言查询中提取出多个关键词时，生成的检索词串变为：

```
+keyword1 +keyword2 +keyword3
```

这意味着 **所有 N 个关键词必须同时出现在 FULLTEXT 索引覆盖的那几列中**。

**以 product_info 表为例**：

- FULLTEXT 索引列：`product_name`, `supplier_name`, `product_parameters`, `category`
- 查询 `+保温材料 +供应商` 要求这四个列的组合文本中 **同时包含 "保温材料" 和 "供应商"**

**失效场景分析**：

| 场景 | 数据示例 | 是否匹配 | 原因 |
|------|---------|----------|------|
| product_name="保温材料", supplier_name="XX建材公司" | "保温材料" 存在，"供应商" 不存在 | ❌ | "供应商" 未出现在任何索引列中 |
| product_name="保温材料供应商名录" | 两词都存在 | ✅ | 恰好都在同一列中 |
| category="保温隔热材料", supplier_name="福建A供应商" | 两词分别在不同列 | ✅ | 跨列匹配，MySQL FULLTEXT 支持 |

**核心问题**：自然语言查询的 LLM 提取结果往往包含 **多个语义角色不同的关键词**（如产品名 + 地区 + 动作意图词），但 FULLTEXT 索引只覆盖了有限列，且很多过滤维度（如地区）存储在独立的列中（不在 FULLTEXT 索引覆盖范围内）。当 LLM 将一个地区词放入 `semantic_keywords` 时，这个地区词需要在索引列中找到——但索引列（如 `product_name`、`category`）中通常不包含地区信息。

### 4.2 根因二：硬过滤条件的精确等值匹配与数据不一致（🔴 核心根因）

**位置**：[price_inquiry.py L577-L582](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py#L577-L582)

```python
if hf.province:
    conditions.append("`province` = %s")   # ← 精确等值
    params.append(hf.province)
```

**问题**：LLM 提取的硬过滤条件值与数据库实际存储值存在以下不一致：

| LLM 提取值 | 数据库可能值 | 是否匹配 | 问题 |
|-----------|-------------|----------|------|
| `"福建"` | `"福建省"` | ❌ | 简称 vs 全称 |
| `"福州"` | `"福州市"` | ❌ | 简称 vs 全称 |
| `"内蒙古"` | `"内蒙古自治区"` | ❌ | 简称 vs 全称 |
| `"北京"` | `"北京市"` | ❌ | 简称 vs 全称 |
| `"新疆"` | `"新疆维吾尔自治区"` | ❌ | 简称 vs 全称 |

**此外**：当 LLM 同时将地区信息放入 `semantic_keywords` 和 `hard_filters` 时，会造成双重过滤压力——FULLTEXT 要在索引列中找到地区词（大概率找不到），hard_filter 又要精确匹配（大概率对不上）。

### 4.3 根因三：ngram_token_size=2 的粒度局限（🟡 中等根因）

**问题**：中文 ngram 双字分词对所有文本一视同仁地按 2 字滑动窗口切分，不区分词边界。

**具体影响**：

| 用户查询中的词 | ngram 切分 | 数据库中的词 | ngram 切分 | 是否匹配 |
|--------------|-----------|-------------|-----------|----------|
| "电剪刀" | `电剪` `剪刀` | "电剪刀" | `电剪` `剪刀` | ✅ |
| "剪刀" | `剪刀` | "电剪刀" | `电剪` `剪刀` | ✅ |
| "电剪" | `电剪` | "电剪刀" | `电剪` `剪刀` | ✅ |
| "剪" (单字) | — (无法索引) | "电剪刀" | `电剪` `剪刀` | ❌ |
| "师范大学" | `师范` `范大` `大学` | "福建师范大学" | `福建` `建师` `师范` `范大` `大学` | ⚠️ 部分 (噪声 token 干扰得分) |

> ngram 的 "无意义 token"（如 `建师`、`范大`）不仅浪费索引空间，还可能产生虚假匹配，降低相关性得分精度。

### 4.4 根因四：无降级/回退检索机制（🟡 中等根因）

**位置**：[price_inquiry.py L742-L747](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py#L742-L747)，[price_inquiry.py L638-L640](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py#L638-L640)

当 MySQL 查询返回 0 行时，系统行为：

```
FULLTEXT返回0行 → 该表被跳过 → 无任何结果 → 提示"未找到相关记录"
```

**缺失的降级路径**：

1. **无 LIKE 回退**：FULLTEXT 查不到时，不会自动降级为 `LIKE '%keyword%'` 模糊匹配
2. **无分词回退**：不会尝试对关键词进行单字拆分后重新检索
3. **无跨表回退**：当前表无结果时，不会扩大检索范围到其他表
4. **无去 AND 化回退**：不会尝试将 `+A +B` 改为 `A B`（OR 语义）或逐个关键词重试

### 4.5 根因五：LLM 意图解析提取粒度不稳定（🟡 中等根因）

**位置**：[price_inquiry.py L175-L246](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py#L175-L246)

LLM 对同一类查询可能输出 **不同的关键词粒度和数量**：

| 查询变体 | LLM 可能的 semantic_keywords | 影响 |
|----------|---------------------------|------|
| "保温材料供应商" | `["保温材料", "供应商"]` | 2 个词，AND 压力中等 |
| "有没有做保温材料相关的供应商推荐" | `["保温材料", "供应商", "推荐"]` | 3 个词，其中"推荐"是噪音词 |
| "我想了解一下保温材料行业的供应商情况" | `["保温材料", "供应商", "行业", "情况"]` | 4 个词，AND 压力极大 |

LLM 提取的关键词越多，MySQL Boolean AND 匹配失败的概率越大——因为多出一个噪音关键词就可能导致整条查询返回 0 结果。

**兜底机制** `_extract_keywords()` 也无法解决此问题，因为它只是做了简单的停用词过滤和分词，同样会提取出多个关键词。

### 4.6 根因六：FULLTEXT 索引列范围与查询意图不匹配（🟢 次要根因）

以 `product_info` 表为例：

- FULLTEXT 索引列：`product_name`, `supplier_name`, `product_parameters`, `category`
- 当用户查询 "福建的保温材料" 时，LLM 提取：`keywords=["保温材料", "福建"]`
- "保温材料" 在 `product_name`/`category` 中可能匹配 ✅
- "福建" 在 `province` 列中可能匹配，但 **`province` 不在 FULLTEXT 索引列中** ❌

结果：`+保温材料 +福建` → 0 结果（因为 "福建" 不在 FULLTEXT 索引覆盖的任何列中）。

### 4.7 根因总结表

| 序号 | 根因 | 严重级别 | 影响面 | 核心矛盾 |
|------|------|----------|--------|----------|
| 4.1 | Boolean Mode AND 语义过严 | 🔴 核心 | 所有多关键词查询 | FULLTEXT `+A +B` 要求所有词必须共存于索引列 |
| 4.2 | 硬过滤精确等值不兼容简称 | 🔴 核心 | 所有含地区/状态过滤的查询 | `province='福建'` vs 数据中 `'福建省'` |
| 4.3 | ngram 双字分词粒度局限 | 🟡 中等 | 短词、英文混合词 | 单字无法索引，产生噪声 token |
| 4.4 | 无降级回退检索 | 🟡 中等 | FULLTEXT 返回 0 行时 | 无 LIKE 回退、无 OR 语义回退 |
| 4.5 | LLM 提取粒度不稳定 | 🟡 中等 | 口语化长查询 | 关键词越多，AND 失败概率越大 |
| 4.6 | 索引列与查询意图维度不匹配 | 🟢 次要 | 跨维度查询 | 地区词在 province 列但不在索引列中 |

---

## 5. 可落地优化方向建议

### 5.1 优先级矩阵

```
                    高收益
                      │
         P0: 改AND为OR  │  P1: 多级降级检索
         P0: LIKE 软匹配 │  P1: 向量语义检索
                      │
    ──────────────────┼──────────────────
        低复杂度      │      高复杂度
                      │
         P2: 关键词LLM│  P3: Text-to-SQL
           去噪+改写  │  P3: 全量Milvus向量化
                      │
                    低收益
```

### 5.2 P0 - 紧急修复（1-3 天，高收益低复杂度）

#### 5.2.1 将 FULLTEXT Boolean Mode 从 AND 改为 OR 语义

**问题**：当前 `+keyword1 +keyword2` → 所有词必须共存

**方案**：去掉 `+` 前缀，改用自然语言模式或混合策略

```python
# 当前（AND 模式）
def _build_search_term(intent):
    parts = [f"+{kw}" for kw in intent.semantic_keywords]
    return " ".join(parts)
# → "+保温材料 +供应商 +福建"  （全部必须匹配）

# 优化后（OR + 权重混合模式）
def _build_search_term(intent):
    if len(intent.semantic_keywords) == 1:
        return f"+{intent.semantic_keywords[0]}"  # 单关键词仍用 +
    else:
        # 多关键词：不强制AND，让MySQL自然打分
        return " ".join(intent.semantic_keywords)
# → "保温材料 供应商 福建"  （任意匹配即可，相关性自动排序）
```

**预期效果**：召回率大幅提升，MySQL FULLTEXT 会按 TF-IDF 自然排序，匹配更多关键词的文档得分更高。

#### 5.2.2 增加 LIKE 降级回退

**问题**：FULLTEXT 返回 0 行时直接放弃

**方案**：在 `_query_tables()` 中增加降级逻辑

```python
# 伪代码：在 _query_tables() 中增加降级
if not rows and search_term:
    # 降级 1：尝试 LIKE 模式（使用第一个关键词）
    like_term = f"%{intent.semantic_keywords[0]}%"
    like_sql = original_sql.replace(
        "MATCH(...) AGAINST (... IN BOOLEAN MODE)",
        f"`{semantic_cols[0]}` LIKE %s"
    )
    # 执行 LIKE 查询
```

#### 5.2.3 硬过滤条件改为 LIKE 模糊匹配（地区/状态类）

**问题**：`province = '福建'` 无法匹配 `'福建省'`

**方案**：对地区、状态等枚举类条件改用 LIKE 前缀匹配

```python
# 当前
conditions.append("`province` = %s")
params.append("福建")

# 改为
conditions.append("`province` LIKE %s")
params.append("福建%")   # 匹配 "福建"、"福建省"
```

### 5.3 P1 - 短期优化（1-2 周，高收益中等复杂度）

#### 5.3.1 多级降级检索链

实现完整的检索降级链，一级失败自动进入下一级：

```
Level 1: FULLTEXT Boolean Mode (OR 语义，快速)
    ↓ 无结果
Level 2: FULLTEXT Boolean Mode (AND 语义，精确)
    ↓ 无结果
Level 3: LIKE 通配符匹配 (第一个关键词)
    ↓ 无结果
Level 4: 逐关键词拆分 → 单关键词 FULLTEXT → 合并去重
    ↓ 无结果
Level 5: 全表扫描 + Python 字符串包含匹配 (最后兜底，需加 LIMIT)
```

#### 5.3.2 LLM 关键词改写与去噪

**问题**：LLM 从口语中提取出噪音词（如"推荐"、"查询"、"情况"）

**方案**：在 Prompt 中增加关键词质量控制指令，或增加后处理去噪

```python
# 在 _UNIFIED_INTENT_SYSTEM 中增加
# "semantic_keywords 仅保留业务实体名词（产品、地区、公司名），
#  去除动词（推荐/查询/帮/找）和抽象名词（情况/信息/数据）"
```

#### 5.3.3 引入向量语义检索作为 MySQL 的语义层

**方案**：为 MySQL 表的每条记录生成文本拼接摘要 → Embedding 向量化 → 存入 Milvus → 检索时先向量召回再回表取完整数据

```
用户查询
  │
  ├─→ Milvus 向量检索 (语义相似度) → 获取 MySQL 主键 ID 列表
  │
  └─→ MySQL: SELECT * FROM product_info WHERE id IN (...)
```

> 项目已具备完整的 Milvus + Embedding 基础设施（`public_kb/` 模块），扩展成本较低。

### 5.4 P2 - 中期改进（2-4 周，中等收益低复杂度）

#### 5.4.1 ngram_token_size 评估与调整

**方案**：评估将 `ngram_token_size` 从 2 改为 1 的影响

| 维度 | token_size=2 (当前) | token_size=1 (候选) |
|------|---------------------|---------------------|
| 单字词支持 | ❌ | ✅ |
| 索引大小 | 小 | 大（约 2-3x） |
| 噪声 token | 少 | 多 |
| 召回率 | 低（丢单字词） | 高 |
| 精确率 | 高 | 中等（噪声增加） |

建议：在测试环境对比验证后决定。

#### 5.4.2 同义词/变体词典

**方案**：建立招投标领域同义词映射表，对查询关键词和 LLM 输出进行扩展

```python
SYNONYM_MAP = {
    "福建": ["福建省"],
    "北京": ["北京市"],
    "中标": ["成交", "中标结果"],
    "招标": ["采购", "招标公告"],
    "询价": ["询价采购", "询价比选"],
}
```

### 5.5 P3 - 长期规划（1-3 月，最高收益高复杂度）

#### 5.5.1 Text-to-SQL 完整方案

利用已有的 LLM 能力和 Schema 缓存，将当前 "LLM→JSON→模板SQL" 模式升级为 "LLM→直接SQL"：

```
当前:  LLM → SearchIntent(JSON) → _build_candidate_sql() → SQL
升级:  LLM → 直接生成 SQL（携带 Schema 上下文）
```

优势：可实现跨表 JOIN、复杂聚合、子查询等当前模板无法覆盖的场景。

#### 5.5.2 MySQL 全量数据向量化

将 ztb_clean 的 4 张表全量拼接为语义文本 → Embedding 向量化 → 存入专用 Milvus 集合，实现与 `public_kb` 同架构的语义检索。

---

## 6. 附录：关键代码索引

| 组件 | 文件 | 行号 | 说明 |
|------|------|------|------|
| SearchIntent 数据模型 | [price_inquiry.py](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py) | L97-L154 | 结构化查询意图 |
| 统一意图解析 Prompt | [price_inquiry.py](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py) | L175-L246 | LLM 意图提取的系统指令 |
| 意图解析入口 | [price_inquiry.py](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py) | L298-L337 | `_parse_unified_intent()` |
| 关键词兜底提取 | [price_inquiry.py](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py) | L359-L372 | `_extract_keywords()` |
| 硬编码 Schema | [price_inquiry.py](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py) | L391-L429 | `_HARDCODED_SCHEMA` |
| **FULLTEXT 检索词构造** | [price_inquiry.py](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py) | L445-L454 | ⚠️ **根因所在**：`+keyword` AND 语义 |
| 硬过滤条件构造 | [price_inquiry.py](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py) | L517-L584 | ⚠️ **根因所在**：精确等值匹配 |
| 候选 SQL 构建 | [price_inquiry.py](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py) | L606-L649 | `_build_candidate_sql()` |
| SQL 执行与容错 | [price_inquiry.py](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py) | L712-L788 | FULLTEXT 缺失静默跳过 |
| 混合重排序 | [price_inquiry.py](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py) | L672-L706 | `_hybrid_score()` + `_rank_records()` |
| 二级路由表 | [price_inquiry.py](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py) | L993-L1010 | `_SUB_ROUTE_MAP` |
| 节点入口 | [price_inquiry.py](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py) | L1022-L1141 | `node_price_inquiry()` |
| DDL Schema | [schema.sql](file://d:\DEMO\zhaotoubiao_demo\scripts\schema.sql) | 1-147 | 4 张表的完整 DDL + FULLTEXT 索引定义 |
| FULLTEXT 索引创建 | [_step5_fulltext.py](file://d:\DEMO\zhaotoubiao_demo\test\_step5_fulltext.py) | 1-45 | ztb_clean 库的 FULLTEXT 索引创建脚本 |
| 输出模板 | [output_templates.py](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\output_templates.py) | 1-469 | 字段筛选与格式化 |
| Router 意图分类 | [router.py](file://d:\DEMO\zhaotoubiao_demo\agent\router.py) | L162-L215 | LLM 意图路由节点 |
| 全局配置 | [config.py](file://d:\DEMO\zhaotoubiao_demo\public_kb\config.py) | L22-L128 | Milvus/Embedding/LLM 配置参数 |

---

> **报告结论**：当前 MySQL 检索系统在 **独立关键词查询** 场景下表现良好，但在 **口语化非结构化自然语言查询** 场景下存在系统性失效，根因集中在：(1) MySQL Boolean Mode `+` AND 语义过严导致多关键词全部匹配要求极难满足；(2) 硬过滤条件使用精确等值匹配无法兼容地域名简称/全称差异；(3) 缺少 FULLTEXT→LIKE→逐词重试的多级降级机制；(4) ngram 双字分词对短词和混合文本支持不足。建议优先实施 P0 级修复（AND→OR + LIKE 回退 + LIKE 模糊过滤），可在 1-3 天内显著提升召回率。
