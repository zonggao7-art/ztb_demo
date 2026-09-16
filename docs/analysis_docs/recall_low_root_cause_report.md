# 召回率低下根因深度调研报告（第三轮）

> 调研对象：`agent/nodes/price_inquiry.py`（结构化检索链路）、`public_kb/qa_chain.py` + `rag_engine.py`（RAG 知识库链路）
> 证据来源：代码静态分析 + 交互模式运行日志（终端 Terminal 1-704）
> 调研时间：2026-08-09

---

## 0. 结论速览（TL;DR）

| # | 问题 | 结论 | 严重度 |
|---|------|------|--------|
| 1 | SQL 硬性条件过滤 | **确认成立，且比前两轮报告预估更严重**：硬过滤条件被无差别地附加到**全部 6 个召回阶段**（含 Milvus 向量回表和全表扫描兜底），语义召回的结果在 SQL 层被 100% 过滤。另发现一个**此前未被识别的致命 bug**：`exact_tokens`（公司名/单位名）被错误映射为 `project_number = '公司名'` 这类不可能成立的条件 | 🔴 P0 |
| 2 | 向量召回数量不足 | **部分成立**：MySQL 结构化链路语义召回 `TOP_K=24 / 每表上限 8`，样本池过小；RAG 链路 `dense=10 / sparse=10 / fusion=10 / top_k=3` 偏保守，降级路径仍保留 0.65 硬阈值 | 🟠 P1 |
| 3 | FULLTEXT "+" 操作符 | **部分成立**：P0 优化仅作用于 Level-1 阶段的语义关键词，`+` 操作符在 Level-2 AND 阶段、Level-4 单关键词重试、以及**所有阶段的 exact_tokens 强制短语**（`+"token"`）中仍然存在 | 🟠 P1 |

三条日志证据链共同指向同一个失败模式：

```
Milvus 语义召回命中 8 条候选
  → SQL 回表附加硬过滤（含错误的 project_number=公司名 / 不存在的枚举值）
  → 0 行
  → FULLTEXT OR/AND/LIKE/拆分/全表扫描 每一级都带同样的硬过滤
  → 每一级 0 行
  → candidate_rows=0 → 拒答
```

---

## 1. 当前检索流程技术架构分析

### 1.1 结构化数据链路（price_inquiry，本次故障主链路）

```
用户问题
  │
  ├─ [阶段1] _parse_unified_intent()          LLM 一次性输出 JSON：
  │          (price_inquiry.py L422)           sub_route + hard_filters + semantic_keywords + exact_tokens
  │
  ├─ [阶段2] _semantic_recall_candidates()     Milvus 集合 mysql_price_semantic
  │          (L994)                            TOP_K=24, 阈值0.35, 每表上限8 → 召回主键 ID
  │
  ├─ [阶段3] _query_semantic_rows()            对召回 ID 回表 MySQL
  │          (L1694) → _build_vector_recall_sql ⚠️ 附加全部硬过滤
  │
  ├─ [阶段4] _execute_recall_chain_for_table() 五级降级链（每级均附加全部硬过滤）：
  │          (L1584)
  │            Level-1  FULLTEXT_OR        mode="or"（关键词无+号）但 exact_tokens 仍 +"token"
  │            Level-2  FULLTEXT_AND       所有关键词 +kw 强制
  │            Level-3  LIKE_FALLBACK      首关键词 LIKE '%kw%'
  │            Level-4  SPLIT_KEYWORD      逐关键词 +kw FULLTEXT / LIKE 重试
  │            Level-5  FULL_SCAN          无关键词全表扫描（仍带硬过滤, LIMIT 100）
  │
  └─ [阶段5] _rank_records()                   混合重排：FULLTEXT分 + 向量分×2 + 关键词命中，Top 20
```

关键配置（`price_inquiry.py` L74-L87，均可被环境变量覆盖，但 `.env` 中均未设置）：

| 参数 | 默认值 | 含义 |
|------|--------|------|
| `MYSQL_SEMANTIC_TOP_K` | 24 | Milvus 单次召回总候选数 |
| `MYSQL_SEMANTIC_PER_TABLE_LIMIT` | 8 | 每表最多保留候选数 |
| `MYSQL_SEMANTIC_THRESHOLD` | 0.35 | 向量相似度截断阈值 |
| `MYSQL_SEMANTIC_TEXT_TRUNCATE` | 120 | 入库文本截断长度 |

### 1.2 RAG 知识库链路（public_kb，本轮日志未触发但参数同属排查范围）

```
question
  ├─ 稠密检索 COSINE (limit=10, nprobe=32)  ┐
  ├─ 稀疏检索 BM25   (limit=10)             ├─ RRF(k=60) 融合取 10
  │                                          ┘
  ├─ Reranker 精排 bge-reranker-v2-m3 (top_k=retrieval_top_k=3)
  ├─ 动态阈值过滤：top1≥0.75→0.40 / top1≥0.50→0.45 / 否则 0.50
  └─ 降级路径（collection 不可用/稀疏字段缺失/异常）：
       纯稠密检索 limit=10 → 硬阈值 similarity_threshold=0.65 → top_k=3
```

配置见 `public_kb/config.py` L109-L117：`retrieval_top_k=3`、`similarity_threshold=0.65`、`hybrid_dense_limit=10`、`hybrid_sparse_limit=10`、`hybrid_fusion_limit=10`。

---

## 2. 问题一：SQL 硬性条件过滤 —— 确认成立（根因之首）

### 2.1 发现 A（致命 bug）：exact_tokens 被错误映射到错误列

`_build_hard_conditions()`（L1152-L1157）：

```python
# 精确 token
if intent.exact_tokens and classification.get("exact"):
    exact_col = classification["exact"][0]
    for token in intent.exact_tokens:
        conditions.append(f"`{exact_col}` = %s")
        params.append(token)
```

`exact_tokens` 的语义是"精确的公司名、项目编号等"（Prompt L274），但代码**不区分 token 类型**，一律写入该表 `exact` 角色的第一列：

| 表 | exact 列 | LLM 实际抽取的 token | 生成的条件 | 结果 |
|----|----------|---------------------|-----------|------|
| `bid_project` | `project_number` | "福建师范大学" | `project_number = '福建师范大学'` | 恒假 |
| `bid_project` | `project_number` | "武汉江腾铁路工程有限责任公司" | `project_number = '武汉江腾铁路工程有限责任公司'` | 恒假 |
| `company_penalty` | `credit_code` | 公司名 | `credit_code = '公司名'` | 恒假 |

**日志铁证**（查询 [4]"福建师范大学招标过什么项目？"）：

```
[SQL_PROFILE] ... WHERE `purchaser` = %s AND `project_number` = %s AND `project_stage` LIKE %s ...
params=('福建师范大学', '福建师范大学', '结果公告%', ...)
```

`purchaser = '福建师范大学'` 本可命中，但 `AND project_number = '福建师范大学'` 使任何行都不可能满足。查询 [5] 同理：`project_number = '武汉江腾铁路工程有限责任公司' AND successful_bidder = '武汉江腾铁路工程有限责任公司'`。

**该条件贯穿全部召回阶段**（`_build_candidate_sql`、`_build_like_fallback_sql`、`_build_full_scan_sql`、`_build_vector_recall_sql` 四处均调用 `_build_hard_conditions_extended`），因此无论降级到哪一级，结果恒为 0。这是当前"查具体单位/公司必挂"的第一根因。

### 2.2 发现 B：LLM 生成的枚举值与数据实际值不对齐

硬过滤大量使用 `=` 精确匹配（`industry = %s`、`company_level = %s`、`purchaser = %s`、`successful_bidder = %s`），但没有枚举值校验/归一化层。

**日志铁证**（查询 [3]"合肥市做批发行业的中大型供应商"）：

```
params=('批发', '中大型企业', '安徽%', '合肥%', ...)
```

- `industry = '批发'`：数据库实际采用国民经济行业分类（从查询 [1] 返回结果可见 `所属行业: 零售业`），真实值应为 **"批发业"** 或 "批发和零售业"，`=` 精确匹配必然落空；
- `company_level = '中大型企业'`：这是 LLM 对用户口语"中大型"的**原样拼接**，数据字典中大概率只有"大型企业/中型企业/小型企业"等离散值，"中大型企业"这个值根本不存在。

即使最后一级全表扫描兜底（无任何关键词条件）也带着这两个条件：

```
... WHERE `industry` = %s AND `company_level` = %s AND `province` LIKE %s AND `city` LIKE %s
ORDER BY `id` DESC LIMIT 100  params=('批发', '中大型企业', '安徽%', '合肥%')
[SQL_PROFILE] summary: candidate_rows=0 returned_rows=0
```

**兜底阶段本应是"放弃相关性、保住召回"的最后防线，当前实现却让它继承了全部硬过滤，兜底名存实亡。**

### 2.3 发现 C：向量召回结果在 SQL 回表时被硬过滤全歼

`_build_vector_recall_sql()`（L1386-L1416）对 Milvus 召回的主键回表时，同样叠加全部硬过滤：

```python
hard_conds, hard_params = _build_hard_conditions_extended(table, classification, intent)
hard_conds.append(f"`{id_col}` IN ({placeholders})")
```

三条日志中语义召回均命中 8 条候选，回表后全部为 0：

| 查询 | 语义召回 | 回表 SQL 关键条件 | 回表结果 |
|------|---------|------------------|---------|
| [3] 合肥批发供应商 | `{'company_info': 8}` (ids: 2839,271,336,…) | `industry='批发' AND company_level='中大型企业' AND id IN (...)` | 0 |
| [4] 福建师范大学 | `{'bid_project': 8}` (ids: 365,464,503,…) | `purchaser='福建师范大学' AND project_number='福建师范大学' AND id IN (...)` | 0 |
| [5] 武汉江腾铁路 | `{'bid_project': 8}` (ids: 1703,1582,1620,…) | `project_number='武汉江腾...' AND successful_bidder='武汉江腾...' AND id IN (...)` | 0 |

**这直接回答了用户的核心疑问：混合检索中语义召回确实工作了，但其"海选"价值被 SQL 层的 AND 硬过滤完全抵消——向量召回出的行恰恰是"文本相近但字段值不完全相等"的行，而硬过滤要求的正是字段值完全相等，两者逻辑上互斥。**

### 2.4 次要发现：默认 project_stage 过滤

Prompt 规定"项目阶段默认'结果公告'"（L304），所有 bidding_query 都附加 `project_stage LIKE '结果公告%'`。若数据源实际用值为"中标公告/成交公告/结果公告(含其他后缀)"之外的写法，该默认条件将进一步压缩召回。目前无法从日志证伪，但属于同一类"隐式硬过滤"风险。

---

## 3. 问题二：向量召回数量与阈值 —— 部分成立

### 3.1 结构化链路：候选池过小（k=24，每表仅 8）

- `MYSQL_SEMANTIC_TOP_K=24`、`PER_TABLE_LIMIT=8`：在 4 张表、数千至上万行的库中，每表只允许 8 条进入回表，一旦其中任何一条被硬过滤淘汰，无补充候选；
- 阈值 0.35 本身不算严苛（bge-m3 COSINE 下合理），**当前瓶颈不在阈值而在数量与回表过滤**；
- 查询向量构造（`_build_semantic_query_text` L985）将 `原问题 | 关键词 | exact_tokens` 三段拼接后整体 embedding，长尾噪声（如"中标过什么项目"）会稀释核心实体语义，建议改为**仅用核心实体（公司名/产品名/地区）构造查询向量**。

### 3.2 RAG 链路：k 值保守 + 降级路径保留 0.65 硬阈值

| 环节 | 当前值 | 评估 |
|------|--------|------|
| 稠密候选 `hybrid_dense_limit` | 10 | 偏小，法律问答常需跨条款取证，建议 30~50 |
| 稀疏候选 `hybrid_sparse_limit` | 10 | 同上 |
| RRF 融合 `hybrid_fusion_limit` | 10 | Reranker 只能在这 10 条里精排，漏斗过窄 |
| 精排输出 `retrieval_top_k` | 3 | 前两轮报告已指出偏保守 |
| 主路径阈值 | 自适应 0.40/0.45/0.50 | 已较 0.65 改善 |
| **降级路径阈值** | **`similarity_threshold=0.65`（`_dense_only_retrieve` qa_chain.py L420/L451）** | **用户所指的 0.65 仍存在——只要稀疏字段缺失、collection 不可用或混合检索抛异常，系统就静默退回 0.65 硬阈值 + top_k=3 的旧行为** |

结论：主路径阈值已改善，但 0.65 作为降级兜底阈值仍是潜在的召回黑洞；且 `describe_collection` 每次查询都调用一次，属可忽略但可缓存的开销。

---

## 4. 问题三：FULLTEXT "+" 操作符 —— 部分回归

### 4.1 P0 优化只覆盖了 Level-1 的语义关键词

`_build_search_term()`（L1065-L1099）与 `_execute_recall_chain_for_table()`（L1584）组合后的真实行为：

| 阶段 | 语义关键词 | exact_tokens | 实际 AGAINST 示例（取自日志） |
|------|-----------|-------------|------------------------------|
| Level-1 FULLTEXT_OR | 无 `+`（OR 语义，符合 P0） | **`+"token"` 强制** | `'福建师范 大学 +"福建师范大学"'` |
| Level-2 FULLTEXT_AND | **`+kw` 全强制** | `+"token"` 强制 | `'+合肥 +批发'` |
| Level-4 SPLIT_KEYWORD | **`+kw`（mode="single"）** | 不含 | `'+合肥'` / `'+批发'` |
| auto 模式单关键词 | **`+kw`** | `+"token"` | — |

日志中可直接抓到 `+` 操作符的实锤：

```
AGAINST (%s IN BOOLEAN MODE) ... params=('+合肥 +批发', '+合肥 +批发')   ← Level-2
AGAINST (%s IN BOOLEAN MODE) ... params=('+合肥', '+合肥')             ← Level-4
AGAINST (%s IN BOOLEAN MODE) ... params=('+批发', '+批发')             ← Level-4
```

### 4.2 为什么 Level-1 的 OR 也没救回召回

Level-1 虽然关键词走 OR，但 `include_exact_tokens=True` 默认开启，强制短语 `+"福建师范大学"` 使整条 AGAINST 退化为"必须包含该短语"。再叠加 2.1 的恒假硬过滤，Level-1 同样恒空。

此外查询 [5] 暴露了**关键词提取质量问题**：LLM 把整句"武汉江腾铁路工程有限责任公司中标过什么项目"当作一个 semantic_keyword 输出，导致 AGAINST 中出现超长无分词串，ngram 布尔检索下几乎无法命中。

---

## 5. 性能数据对比（基于终端日志实测）

| 查询 | 意图解析 | 语义召回 | SQL 条数 | SQL 总耗时 | 候选行 | 返回行 | 端到端 |
|------|---------|---------|---------|-----------|--------|--------|--------|
| [3] 合肥批发供应商 | 2.057s | 8 条(id) | 9 | 0.051s | **0** | 0 | 2.309s |
| [4] 福建师范大学 | 1.878s | 8 条(id) | 4 | 0.012s | **0** | 0 | 2.071s |
| [5] 武汉江腾铁路 | 1.724s | 8 条(id) | 4 | 0.054s | **0** | 0 | 1.952s |

关键观察：

1. **SQL 执行本身极快（≤54ms），性能不是瓶颈，召回失败 100% 由条件构造错误导致**；
2. 每条查询执行 4~9 条 SQL 全部空转——降级链在恒假条件下逐级重试，纯属浪费（也提示：**硬过滤命中 0 时应有条件回退机制，而不是原样换检索方式**）；
3. 语义召回阶段（embedding API + Milvus）耗时约 0.2~0.3s，占总耗时 10%~15%，扩 k 的成本可接受。

---

## 6. 解决方案与实施建议

### P0-1：修复 exact_tokens 列映射 bug（预计收益最大，改动最小）

`exact_tokens` 不应无条件写入 `exact` 列。改为按 token 语义分发：

```python
# 伪代码：_build_hard_conditions 中
for token in intent.exact_tokens:
    if _looks_like_project_number(token):        # 含数字/编号特征
        conditions.append(f"`{exact_col}` = %s")
    elif classification.get("purchaser"):        # 否则视为实体名，走 LIKE 匹配语义列
        conditions.append(f"`{classification['purchaser'][0]}` LIKE %s")  # 或移入 FULLTEXT 强制短语
    # 无法归类时：不加硬条件，仅作为检索词
```

配套：在意图 Prompt 中将 `exact_tokens` 拆为 `project_numbers` 与 `entity_names` 两个字段，从源头消除歧义。

### P0-2：硬过滤分级——区分"约束性过滤"与"偏好性过滤"

将硬过滤分为两类并在 SQL 层差异化处理：

| 类别 | 字段 | 处理方式 |
|------|------|---------|
| 约束性（保留） | credit_code、project_number（真编号）、time_range、金额范围 | 所有阶段保留 |
| 偏好性（降级为软条件） | industry、company_level、province/city、project_stage、purchaser/successful_bidder 名称 | 仅用于重排序加权；或作为第一遍过滤，**0 行时自动放宽重查** |

最小改动实现：`_execute_recall_chain_for_table` 中，若"全硬过滤"链跑完为 0 行，自动用**剔除偏好性条件**的 intent 重跑一遍降级链（`dataclasses.replace` 已在使用，成本极低）。

### P0-3：兜底阶段（Level-5）必须无关键词、无偏好性硬过滤

`_build_full_scan_sql` 当前保留全部硬过滤，应改为仅保留约束性条件；同时在日志中输出被过滤淘汰的行数（`SELECT COUNT(*)` 对比），让后续排查可量化。

### P0-4：枚举值归一化层

- 启动时从 `ztb_clean` 拉取 `industry`、`company_level`、`project_stage`、`province` 的 `DISTINCT` 值缓存；
- 意图解析后对 LLM 输出做最近邻匹配（"批发"→"批发业"，"中大型"→`["中型企业","大型企业"]` 展开为 `IN (...)`）；
- 无法对齐时放弃该硬过滤（转软条件），绝不原样下推。

### P1-1：扩大向量召回池 + 纯净化查询向量

```
MYSQL_SEMANTIC_TOP_K          24  → 64
MYSQL_SEMANTIC_PER_TABLE_LIMIT 8  → 24
MYSQL_SEMANTIC_THRESHOLD      0.35 → 0.30（扩池后靠重排收敛精度）
```

`_build_semantic_query_text` 改为优先使用 `exact_tokens + semantic_keywords`（实体优先），原问题仅作补充，避免疑问句式稀释向量。

### P1-2：清除残余 "+" 操作符

- Level-2 AND 阶段改为"加权而非强制"：`AGAINST` 保持 OR，用 `_hybrid_score` 对全命中记录加权；
- Level-4 `mode="single"` 去掉 `+`（单关键词有无 `+` 在布尔模式下等价于是否允许 0 分排除，实际影响小，但应统一）；
- exact_tokens 强制短语仅在 P0-1 确认为真实体后保留，且改为可选加权项。

### P1-3：RAG 链路参数调整

```
hybrid_dense_limit  10 → 30     hybrid_sparse_limit 10 → 30
hybrid_fusion_limit 10 → 30     retrieval_top_k      3 → 5
similarity_threshold 0.65 → 0.45（降级路径同步放宽，主路径已有自适应阈值）
```

### P2：结构性优化

- 关键词质量：在 `_post_process_intent` 中对超长关键词（>12 字）做二次切分，杜绝"整句当关键词"；
- `project_stage` 默认值从 Prompt 中移除，改为"未明确时不加该条件"；
- 为降级链增加"条件放宽计数器"日志（每级记录 过滤前行数/过滤后行数），形成召回漏斗可观测性；
- 长期：将"硬过滤+全文"替换为"先召回后过滤"架构——即所有候选（向量+FULLTEXT）先入候选池，过滤条件仅在排序阶段作为惩罚项。

---

## 7. 预期改善效果评估

| 场景（日志复现） | 现状 | P0 修复后 | 依据 |
|------------------|------|-----------|------|
| "福建师范大学招标过什么项目" | 0 行 | ≥8 行（语义召回 8 条全部可回表，purchaser LIKE 精确命中更多） | 恒假条件移除 + purchaser 改 LIKE |
| "武汉江腾铁路…中标过什么项目" | 0 行 | ≥8 行 | 同上 |
| "合肥市批发行业中大型供应商" | 0 行 | 预计 10+ 行 | industry 归一化为"批发业"、company_level 展开为 IN('中型企业','大型企业') 或转软条件 |
| RAG 降级路径长尾问题 | 0.65 拒答 | 0.45 阈值 + 30 候选池，漏召显著下降 | 与主路径自适应阈值对齐 |

量化估计：当前三条典型实体查询召回率为 **0%**；完成 P0-1~P0-4 后，实体型查询（purchaser/bidder/company 类）召回率预计恢复至 **80%+**（剩余缺口来自数据源本身无该记录的情况）；完成 P1 后，模糊型查询（"推荐供应商"类）召回率预计从当前接近 0 提升至 **60%+**。

风险与代价：偏好性条件转软过滤后，Top 结果中可能混入地区/行业不完全匹配的记录，需依靠 `_rank_records` 加权把严格匹配项排前——现有打分框架（`_RECALL_STAGE_WEIGHTS` + `_hybrid_score`）已具备该能力，只需为硬过滤命中项增加加分项。

---

## 8. 实施优先级排序

| 优先级 | 事项 | 改动量 | 预期收益 | 依赖 |
|--------|------|--------|---------|------|
| **P0-1** | 修复 exact_tokens → project_number 错误映射 | 小（~30 行） | 实体型查询从 0 到有 | 无 |
| **P0-2** | 硬过滤分级 + 0 行自动放宽重查 | 中（~80 行） | 消除"语义召回被全歼"的根本结构缺陷 | 无 |
| **P0-3** | Level-5 兜底去偏好性条件 + 漏斗日志 | 小（~20 行） | 保证最终防线有效且可观测 | P0-2 |
| **P0-4** | 枚举值归一化（industry/company_level/stage） | 中（~100 行 + 一次 DISTINCT 扫描） | 口语化查询可用 | 无 |
| **P1-1** | 向量召回扩池（24→64，8→24）+ 查询向量纯净化 | 小（改环境变量 + ~10 行） | 候选多样性提升 3 倍 | P0-2（否则扩池仍被过滤） |
| **P1-2** | 清除 Level-2/4 及 exact_tokens 残余 "+" | 小（~20 行） | FULLTEXT 召回面扩大 | 无 |
| **P1-3** | RAG 参数放宽（10→30，top_k 3→5，0.65→0.45） | 极小（config.py 改 4 个数字） | 知识库长尾问答漏召下降 | 无 |
| **P2** | 超长关键词切分 / 移除默认 project_stage / "先召回后过滤"架构 | 中~大 | 长期稳定性 | P0/P1 完成 |

**建议执行顺序**：P0-1 → P0-2 → P0-3 → P0-4 → 用日志中三条失败查询做回归验证 → P1-1/P1-2/P1-3 并行 → P2 视效果决定。

---

## 附录：关键证据索引

| 证据 | 位置 |
|------|------|
| 恒假条件生成代码 | `agent/nodes/price_inquiry.py` `_build_hard_conditions()` L1152-L1157 |
| 向量回表附加硬过滤 | `_build_vector_recall_sql()` L1406 |
| 五级降级链全部带硬过滤 | `_execute_recall_chain_for_table()` L1584-L1691（各 builder 均调用 `_build_hard_conditions_extended`） |
| `+` 操作符残留 | `_build_search_term()` L1082-L1098（and/single 模式与 exact_tokens 分支） |
| 语义召回参数 | L74-L87 |
| RAG 0.65 降级阈值 | `public_kb/qa_chain.py` `_dense_only_retrieve()` L420/L451 + `public_kb/config.py` L110 |
| 日志：语义召回 8 条 → 回表 0 行 | 终端日志查询 [3][4][5] 的 `[SEMANTIC_RECALL]` 与首条 `[SQL_PROFILE]` |
| 日志：`+合肥 +批发` 实锤 | 终端日志查询 [3] 的 Level-2/Level-4 `[SQL_PROFILE]` |
