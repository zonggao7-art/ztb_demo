# 企业处罚查询召回失效根因深度分析报告

> **文档定位**：针对 `项城市魅翔商贸有限公司有没有不良记录？` 查询返回无关企业（company_info 记录）而非处罚记录（company_penalty 记录）的全面排查
>
> **关联文件**：`agent/nodes/price_inquiry.py`、`docker/mysql/init/01-schema.sql`、`agent/nodes/output_templates.py`
>
> **证据来源**：终端 Terminal 1-415 日志 + 代码静态分析
>
> **日期**：2026-08-09

---

## 目录

1. [问题现象与日志证据](#1-问题现象与日志证据)
2. [路由机制验证（一级 + 二级）](#2-路由机制验证一级--二级)
3. [意图解析验证（SearchIntent / need_penalty_check / query_type）](#3-意图解析验证)
4. [数据查询逻辑深度剖析（核心缺陷所在）](#4-数据查询逻辑深度剖析核心缺陷所在)
5. [SQL 构建分析](#5-sql-构建分析)
6. [数据完整性与表关联验证](#6-数据完整性与表关联验证)
7. [附加问题发现](#7-附加问题发现)
8. [完整根因链总结](#8-完整根因链总结)
9. [解决方案与代码改进建议](#9-解决方案与代码改进建议)

---

## 1. 问题现象与日志证据

### 1.1 现象描述

用户查询 `项城市魅翔商贸有限公司有没有不良记录？` 返回了 20 条**完全无关的企业记录**，所有处罚字段均显示"未提供"：

```
[1] 企业名称: 上海梦姿洁服装有限公司项城分公司 | 处罚日期: 未提供 | 违法行为: 未提供 | 处罚结果: 未提供
[2] 企业名称: 马鞍山市霄翔商贸有限公司 | 处罚日期: 未提供 | ...
...
数据来源：ztb_clean.company_info   ← 注意：只有 company_info，没有 company_penalty
```

### 1.2 关键日志证据链

| 日志行 | 内容 | 诊断意义 |
|--------|------|---------|
| `路由(tool): intent=price_inquiry` | 一级路由正确 | 路由器已修复 ✓ |
| `[UNIFIED_INTENT] sub_route=company_query query_type=penalty_check` | 二级路由正确 | 意图解析正确 ✓ |
| `[SEMANTIC_RECALL] tables=['company_info'] 命中={'company_info': 24}` | **仅查 company_info** | 🔴 未查 company_penalty |
| `[RECALL_FUNNEL] table=company_info total_rows=38911 constraint_only_rows=38911` | 约束性条件未过滤任何行 | 说明无时间/数值等约束条件 |
| `[RECALL_RELAX] table=company_info 全硬过滤链零行，放宽偏好性过滤重试` | **company_name 精确匹配零行** | 🔴 目标企业不在 company_info 表中 |
| `数据来源：ztb_clean.company_info` | 最终结果仅含 company_info | 🔴 处罚联查未执行或执行无效 |

---

## 2. 路由机制验证（一级 + 二级）

### 2.1 一级路由验证（router.py）

**结论：一级路由正确，已通过方案 A 修复。**

```
路由(tool): intent=price_inquiry  ✓
```

`route_price_inquiry` 的 docstring 已扩充为覆盖"企业不良记录/处罚/违法/黑名单/信用"等语义，`ROUTER_SYSTEM_PROMPT` 中的 few-shot 示例也包含"XX公司有没有不良记录？→ price_inquiry"。路由器不再将此类查询误判为 fallback。

### 2.2 二级路由验证（price_inquiry.py 意图解析）

**结论：二级路由正确，`sub_route=company_query`、`query_type=penalty_check` 均正确识别。**

```
[UNIFIED_INTENT] sub_route=company_query query_type=penalty_check
keywords=['项城市魅翔商贸有限公司不良记录']
```

`_UNIFIED_INTENT_SYSTEM` Prompt 中的规则 `是否有不良记录/处罚/违法 → "penalty_check"，同时 need_penalty_check=true` 正确触发。

---

## 3. 意图解析验证（SearchIntent / need_penalty_check / query_type）

### 3.1 SearchIntent 字段解析结果

从日志中的 `[UNIFIED_INTENT] raw_output` 可确认：

| 字段 | 解析值 | 是否正确 |
|------|--------|---------|
| `sub_route` | `"company_query"` | ✅ 正确 |
| `query_type` | `"penalty_check"` | ✅ 正确 |
| `hard_filters.company_name` | `"项城市魅翔商贸有限公司"` | ✅ 正确提取 |
| `need_penalty_check` | `true`（推断，因为 query_type=penalty_check） | ✅ 正确 |
| `semantic_keywords` | `['项城市魅翔商贸有限公司不良记录']` | ⚠️ 关键词质量差（见 7.2） |
| `exact_tokens` | 未显示，可能包含公司名 | 需确认 |

### 3.2 意图解析层无缺陷

LLM 意图解析完全正确，`need_penalty_check=true` 被正确设置，`query_type=penalty_check` 被正确识别。**问题不在意图解析层，而在数据查询层。**

---

## 4. 数据查询逻辑深度剖析（核心缺陷所在）

### 4.1 `_query_company_data` 的执行流程（问题根源）

```python
# price_inquiry.py L2103-2170
def _query_company_data(intent: SearchIntent) -> dict[str, Any]:
    """公司信息查询：company_info + 条件联查 company_penalty。"""
    tables = ["company_info"]          # ← 🔴 主查询仅查 company_info
    result = _query_tables(tables, intent)

    # 条件联查 company_penalty
    if intent.need_penalty_check and result["records"]:   # ← 依赖 company_info 有结果
        credit_codes = set()
        for rec in result["records"]:
            cc = rec.get("credit_code")                   # ← 依赖 credit_code 存在
            if cc and cc not in ("", "None", "未提供"):
                credit_codes.add(cc)

        if credit_codes:                                  # ← 依赖 credit_codes 非空
            # 按 credit_code 查 company_penalty
            ...
```

**这是一个"串联依赖"架构，存在 4 个串联断点：**

```
用户查询
  → [断点1] company_info 中是否存在该企业？（不存在 → 返回无关企业）
  → [断点2] 返回的记录是否有有效 credit_code？（无关企业的 credit_code 无意义）
  → [断点3] company_penalty 中是否有该 credit_code 的记录？（无关联）
  → [断点4] 处罚记录是否被正确合并到结果？
```

### 4.2 断点 1：目标企业不在 company_info 表中

日志证据：
```
[RECALL_RELAX] table=company_info 全硬过滤链零行，放宽偏好性过滤重试
```

`company_name = '项城市魅翔商贸有限公司'` 的精确匹配在 `company_info` 表中返回 **0 行**。这说明该企业：
- 不存在于 `company_info` 表（数据缺失），或
- 存在但名称有细微差异（如空格、括号等）

**这是第一个串联断点：主查询找不到目标企业。**

### 4.3 断点 1 的连锁反应：LIKE 放宽返回无关企业

由于 `company_name` 被分类为"偏好性过滤"（`_PREFERENCE_FILTER_FIELDS`），当精确匹配零行时，`_strip_preference_filters` 会**剥离 company_name 条件**，然后 LIKE 匹配返回包含相似字符的无关企业：

```python
# price_inquiry.py L1420-1425
_PREFERENCE_FILTER_FIELDS = (
    "purchaser", "region", "province", "city", "status",
    "company_name",   # ← 🔴 company_name 是偏好性过滤！
    "industry", "company_level", ...
)
```

剥离后的 LIKE 查询：
```sql
WHERE (`company_name` LIKE '%项城市魅翔商贸有限公司%' OR ...)
```

这返回了包含"翔"、"项城"等字符的无关企业（如"马鞍山市霄翔商贸有限公司"），共 20 条。

### 4.4 断点 2：无关企业的 credit_code 无意义

处罚联查从这 20 条无关企业中提取 credit_code，然后去 `company_penalty` 表查询。这些 credit_code 对应的是无关企业，而非用户查询的"项城市魅翔商贸有限公司"。

### 4.5 断点 3：company_penalty 中无对应记录

即使这些无关企业的 credit_code 有效，`company_penalty` 表中也大概率没有它们的处罚记录，导致联查结果为空。

### 4.6 最终结果：返回无关企业信息，处罚字段全部"未提供"

由于处罚联查未返回任何数据，`penalty_date`、`illegal_behavior`、`penalty_result` 等字段均未被填充，最终输出显示"未提供"。

---

## 5. SQL 构建分析

### 5.1 实际执行的 SQL（从日志提取）

| 阶段 | SQL 关键条件 | 结果 |
|------|-------------|------|
| Milvus 回表 | `WHERE id IN (2206, 178, 1942, ...)` | 24 条（无关企业） |
| Level-1 FULLTEXT_OR | `WHERE company_name = '项城市魅翔商贸有限公司' AND MATCH(...)` | 0 行 |
| Level-3 LIKE | `WHERE company_name = '项城市魅翔商贸有限公司' AND (... LIKE ...)` | 0 行 |
| 放宽后 FULLTEXT | `WHERE MATCH(... AGAINST '项城市魅翔商贸有限公司...')` | 返回含相似字符的无关企业 |
| 放宽后 LIKE | `WHERE (... LIKE '%项城市魅翔商贸有限公司%' OR ...)` | 同上 |

**关键问题**：所有 SQL 均只查询 `company_info` 表，**没有任何 SQL 直接查询 `company_penalty` 表**。

### 5.2 company_penalty 表的索引缺陷

从 `01-schema.sql` 确认：

```sql
CREATE TABLE IF NOT EXISTS `company_penalty` (
    ...
    INDEX `idx_company_name` (`company_name`),   -- 普通 B-tree 索引
    INDEX `idx_credit_code` (`credit_code`),
    INDEX `idx_penalty_date` (`penalty_date`)
)
```

**company_penalty 表没有 FULLTEXT 索引**，只有普通 B-tree 索引。这意味着：
- 即使将 `company_penalty` 加入召回链，FULLTEXT 查询也会报错
- `_HARDCODED_SCHEMA` 中 `company_penalty` 的 `semantic` 列（`company_name`, `illegal_behavior`, `penalty_result`）无法被 FULLTEXT 检索

### 5.3 `_build_candidate_sql` 对 company_penalty 的兼容性

`_HARDCODED_SCHEMA["company_penalty"]["semantic"]` 定义为：
```python
"semantic": ["company_name", "illegal_behavior", "penalty_result"]
```

但 `_build_candidate_sql` 会生成：
```sql
MATCH(`company_name`, `illegal_behavior`, `penalty_result`) AGAINST (...)
```

由于 company_penalty 表没有 FULLTEXT 索引，这条 SQL 会抛出 `fulltext` 错误，被 `_execute_recall_chain_core` 中的异常处理捕获并记录：
```python
except Exception as e:
    if "fulltext" in str(e).lower():
        logger.warning("[FULLTEXT_MISSING] db=%s table=%s: %s", ...)
```

---

## 6. 数据完整性与表关联验证

### 6.1 表关联设计

```
company_info.credit_code  ←→  company_penalty.credit_code
```

两表通过 `credit_code`（统一社会信用代码）关联。但存在以下风险：

| 风险 | 描述 |
|------|------|
| credit_code 缺失 | 若 company_info 中某企业的 credit_code 为空，则无法关联 penalty |
| credit_code 不一致 | 两表中同一企业的 credit_code 可能存在格式差异 |
| company_penalty 独立存在 | 处罚记录可能独立于 company_info 存在（只有处罚数据，无企业基础信息） |

### 6.2 数据缺失验证

当前日志无法直接验证 `company_penalty` 表中是否存在"项城市魅翔商贸有限公司"的记录，但从代码逻辑推断：
- 若该企业不在 `company_info` 表中，则其 credit_code 无法被获取
- 即使 `company_penalty` 表中有该企业的处罚记录（通过 company_name 关联），也无法被查询到

---

## 7. 附加问题发现

### 7.1 `company_name` 被错误分类为"偏好性过滤"

**严重度：P0**

`company_name` 在 `_PREFERENCE_FILTER_FIELDS` 中被列为偏好性过滤字段。这意味着当精确匹配零行时，company_name 条件会被**完全剥离**，导致 LIKE 匹配返回无关企业。

**对于 penalty_check 查询，company_name 应该是"约束性过滤"**，因为用户明确指定了要查询哪家企业。

### 7.2 语义关键词质量差

```
keywords=['项城市魅翔商贸有限公司不良记录']
```

LLM 将整个"公司名+不良记录"合并为一个关键词，而非拆分为 `["项城市魅翔商贸有限公司", "不良记录"]`。这导致 FULLTEXT 检索时使用了超长的复合词，命中率极低。

### 7.3 `_SUB_ROUTE_MAP` 中 company_penalty 被声明但未被使用

```python
_SUB_ROUTE_MAP = {
    "company_query": {
        "tables": ["company_info", "company_penalty"],  # ← 声明了 company_penalty
        "query_fn": "_query_company_data",
    },
    ...
}
```

`tables` 列表中包含了 `company_penalty`，但 `_query_company_data` 函数内部只将 `["company_info"]` 传给 `_query_tables`，company_penalty 从未被主查询引擎使用。

### 7.4 处罚联查只合并第一条处罚记录

```python
# L2159-2164
if penalties:
    p = penalties[0]   # ← 只取第一条
    rec["penalty_date"] = p.get("penalty_date", "")
    rec["illegal_behavior"] = p.get("illegal_behavior", "")
    ...
```

若某企业有多条处罚记录，只会显示最新的一条，历史记录丢失。

---

## 8. 完整根因链总结

```
用户查询: "项城市魅翔商贸有限公司有没有不良记录？"
  │
  ├─ [一级路由] intent=price_inquiry ✓（已修复）
  │
  ├─ [二级路由] sub_route=company_query, query_type=penalty_check ✓
  │
  ├─ [_query_company_data] 主查询仅查 company_info
  │     │
  │     ├─ [精确匹配] company_name = '项城市魅翔商贸有限公司' → 0 行
  │     │     （企业不在 company_info 表中，或名称有细微差异）
  │     │
  │     ├─ [偏好性过滤放宽] company_name 被剥离
  │     │
  │     └─ [LIKE 匹配] 返回 20 条含"翔/项城"字符的无关企业
  │
  ├─ [处罚联查] 从无关企业提取 credit_code → 查 company_penalty → 无相关记录
  │
  └─ [最终输出] 20 条无关企业，处罚字段全部"未提供"
```

**根本原因**：`_query_company_data` 的架构设计存在"串联依赖"缺陷——必须先找到 company_info 记录，才能通过 credit_code 联查 company_penalty。当目标企业不在 company_info 表中时，处罚记录完全不可达。

---

## 9. 解决方案与代码改进建议

### 9.1 P0 修复：为 penalty_check 增加直接查询 company_penalty 的路径

**核心思路**：当 `query_type=penalty_check` 且 `need_penalty_check=true` 时，直接用 `company_name` 查询 `company_penalty` 表，不依赖 company_info 的 credit_code。

```python
def _query_company_data(intent: SearchIntent) -> dict[str, Any]:
    """公司信息查询：company_info + company_penalty 双路查询。"""
    tables = ["company_info"]
    result = _query_tables(tables, intent)

    if intent.need_penalty_check:
        # ── 路径 A：原有 credit_code 联查（保留）──
        if result["records"]:
            credit_codes = {rec.get("credit_code") for rec in result["records"]
                           if rec.get("credit_code") not in (None, "", "None", "未提供")}
            penalty_results = _query_penalty_by_credit_codes(credit_codes) if credit_codes else []
        else:
            penalty_results = []

        # ── 路径 B（新增）：company_name 直接查 company_penalty ──
        hf = intent.hard_filters
        target_company = hf.company_name or (intent.exact_tokens[0] if intent.exact_tokens else None)
        if target_company and not penalty_results:
            direct_penalty = _query_penalty_by_company_name(target_company)
            if direct_penalty:
                # 直接返回处罚记录，不依赖 company_info
                result["records"] = direct_penalty
                result["queried_tables"] = [f"{_CLEAN_DB}.company_penalty"]

        # 合并逻辑...
    return result


def _query_penalty_by_company_name(company_name: str) -> list[dict]:
    """直接用公司名查询 company_penalty 表（LIKE 模糊匹配）。"""
    conn = _get_connection(_CLEAN_DB)
    if not conn:
        return []
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                """SELECT * FROM `company_penalty`
                   WHERE `company_name` LIKE %s
                   ORDER BY `penalty_date` DESC
                   LIMIT 50""",
                (f"%{company_name}%",)
            )
            return [_clean_row(row) for row in cur.fetchall()]
    except Exception as e:
        logger.debug("直接查询 company_penalty 失败: %s", e)
        return []
    finally:
        _release_connection(conn)
```

### 9.2 P0 修复：penalty_check 查询时 company_name 升级为约束性过滤

```python
# 在 _strip_preference_filters 中，当 query_type=penalty_check 时保留 company_name
def _strip_preference_filters(hf: HardFilters, query_type: str = "mixed") -> HardFilters:
    relaxed = replace(hf)
    skip_fields = {"company_name"} if query_type == "penalty_check" else set()
    for name in _PREFERENCE_FILTER_FIELDS:
        if name not in skip_fields:
            setattr(relaxed, name, None)
    return relaxed
```

### 9.3 P1 修复：为 company_penalty 添加 FULLTEXT 索引

```sql
-- 在 01-schema.sql 中为 company_penalty 添加 FULLTEXT 索引
ALTER TABLE `company_penalty`
  ADD FULLTEXT INDEX `ft_penalty_semantic` (`company_name`, `illegal_behavior`, `penalty_result`);
```

### 9.4 P1 修复：改进语义关键词提取

在 `_UNIFIED_INTENT_SYSTEM` Prompt 中增加规则：
```
- 企业名称必须单独作为一个 semantic_keyword，不要与查询意图词合并
- 示例："XX公司有没有不良记录" → semantic_keywords: ["XX公司", "不良记录"]
```

### 9.5 P2 修复：处罚记录完整展示

```python
# 合并所有处罚记录，而非仅取第一条
if penalties:
    rec["penalty_records"] = penalties  # 完整列表
    # 最新一条作为摘要
    p = penalties[0]
    rec["penalty_date"] = p.get("penalty_date", "")
    rec["illegal_behavior"] = p.get("illegal_behavior", "")
    rec["penalty_result"] = p.get("penalty_result", "")
    rec["total_penalties"] = len(penalties)
```

### 9.6 修复优先级总结

| 优先级 | 问题 | 修复方案 | 改动量 |
|--------|------|---------|--------|
| **P0-1** | 无直接 company_penalty 查询路径 | 增加 `_query_penalty_by_company_name` 路径 | ~30 行 |
| **P0-2** | company_name 被错误剥离 | penalty_check 时保留 company_name | ~10 行 |
| **P1-1** | company_penalty 无 FULLTEXT 索引 | 添加 FULLTEXT 索引 | 1 行 DDL |
| **P1-2** | 语义关键词质量差 | Prompt 规则 + `_post_process_intent` 切分 | ~20 行 |
| **P2-1** | 只展示第一条处罚 | 完整处罚历史展示 | ~15 行 |
