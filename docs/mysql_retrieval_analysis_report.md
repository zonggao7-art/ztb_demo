# MySQL 检索系统技术分析报告

> 生成日期：2026-08-05  
> 分析范围：`agent/nodes/price_inquiry.py` 三阶段检索架构、性能诊断工具链、升级路径  
> 数据基础：服务器 `192.168.10.120:3306`，47 个用户数据库，累计约 1100 万行数据

---

## 目录

1. [检索逻辑拆解](#1-检索逻辑拆解)
2. [检索速度评估](#2-检索速度评估)
3. [检索准确性评估](#3-检索准确性评估)
4. [升级潜力分析](#4-升级潜力分析)
5. [自建分类数据库与路由方案可行性论证](#5-自建分类数据库与路由方案可行性论证)

---

## 1. 检索逻辑拆解

### 1.1 总体架构

当前检索系统实现于 [agent/nodes/price_inquiry.py](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py)，核心入口为 `_query_price_data()` 函数（L589），采用 **"意图结构化抽取 → 硬过滤 → 全文检索与混合排序"** 三阶段架构。该节点在 LangGraph 骨架中被注册为 `price_inquiry` 分支，由 [router.py](file://d:\DEMO\zhaotoubiao_demo\agent\router.py) 中的 LLM 意图路由分发到该节点。

```
用户查询
  │
  ├─ [阶段1] _parse_intent()         ← LLM 意图结构化抽取
  │     └─ 输出: SearchIntent { hard_filters, semantic_keywords, exact_tokens }
  │
  ├─ [阶段2] _build_candidate_sql()   ← 确定性 SQL 生成器
  │     ├─ _classify_columns()        ← 列名模式匹配归类
  │     ├─ _build_hard_conditions()   ← 硬过滤 WHERE 子句
  │     ├─ _build_fulltext_expression() ← FULLTEXT MATCH...AGAINST
  │     └─ LIMIT 200                  ← 候选集截断
  │
  └─ [阶段3] _rank_records()          ← Python 侧重排序
        ├─ MySQL _score_ (FULLTEXT 得分)
        ├─ _hybrid_score() (关键词命中计数)
        └─ 综合排序 → Top 20
```

### 1.2 阶段一：意图结构化抽取

**入口**：`_parse_intent()` (L176)

通过 LLM（DeepSeek-Chat，temperature=0）将用户自然语言查询解析为结构化 JSON：

```python
# agent/nodes/price_inquiry.py L107-L127
_INTENT_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是招投标/采购领域的查询意图解析专家。
请把用户的自然语言查询解析成结构化 JSON...
{{
  "hard_filters": {{
    "time_range": {{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}} 或 null,
    "budget_range": {{"min": number, "max": number}} 或 null,
    "purchaser": "采购人名称或 null",
    "region": "省/市/地区或 null",
    "status": "招标/中标/招标公告/中标公告等状态或 null"
  }},
  "semantic_keywords": ["用于业务内容匹配的关键词"],
  "exact_tokens": ["项目编号、特定资质、公司名等精确 token"]
}}"""),
    ("user", "用户查询：{question}\n请仅输出 JSON。"),
])
```

**意图数据结构**（L59-L73）：

| 字段 | 类型 | 用途 |
|------|------|------|
| `hard_filters.time_range` | `{start, end}` | 时间范围硬过滤 |
| `hard_filters.budget_range` | `{min, max}` | 预算金额范围过滤 |
| `hard_filters.purchaser` | `str` | 采购人精确匹配 |
| `hard_filters.region` | `str` | 地区精确匹配 |
| `hard_filters.status` | `str` | 招标/中标状态过滤 |
| `semantic_keywords` | `list[str]` | 语义关键词，用于 FULLTEXT 检索 |
| `exact_tokens` | `list[str]` | 精确 token（项目编号、资质等） |

**容错机制**：
- 内置 `_INTENT_CACHE` 字典级缓存 (L130)，相同问题不重复调用 LLM
- LLM 输出解析失败 → 回退到 `_extract_keywords()` 关键词提取 (L221)
- LLM 调用异常 → 同样回退到关键词提取

### 1.3 阶段二：确定性 SQL 生成器

**列分类规则** (`_classify_columns()` L347) 是整个检索系统的核心元数据层。通过对每个表的列名进行模式匹配，将列归入 9 个检索角色：

| 角色 | 匹配模式示例 | 用途 |
|------|-------------|------|
| `semantic` | `title`, `project_name`, `content`, `标的物`, `采购内容` | FULLTEXT 检索目标列 |
| `time` | `publish_date`, `bid_date`, `发布时间`, `created_at` | 时间范围硬过滤 |
| `budget` | `bid_amount`, `price`, `预算金额`, `中标金额` | 预算范围硬过滤 |
| `purchaser` | `purchaser`, `purchasing_unit`, `采购人`, `招标人` | 采购人精确匹配 |
| `region` | `province`, `city`, `region`, `省份`, `地区` | 地区精确匹配 |
| `status` | `stage`, `status`, `project_phase`, `项目阶段` | 状态过滤 |
| `exact` | `project_id`, `project_no`, `项目编号`, `招标编号` | 精确 token 匹配 |
| `id` | 主键列 (`COLUMN_KEY == "PRI"`) | 唯一标识 |
| `text` | 所有 `varchar`/`text`/`longtext` 列 | 兜底候选 |

**SQL 构建** (`_build_candidate_sql()` L481)：

```sql
-- 典型生成的 SQL 示例（以查询"皮艺沙发中标记录"为例）
SELECT `_id_` AS `_id_`,
       LEFT(`tender_title`, 800) AS `tender_title`,
       LEFT(`content`, 800) AS `content`,
       MATCH(`tender_title`, `content`) AGAINST ('+皮艺沙发' IN BOOLEAN MODE) AS `_score_`
FROM `tender`
WHERE MATCH(`tender_title`, `content`) AGAINST ('+皮艺沙发' IN BOOLEAN MODE)
ORDER BY `_score_` DESC
LIMIT 200
```

关键设计决策：
- **`LEFT(col, 800)` 截断**：长文本列只取前 800 字符，避免网络 I/O 爆炸
- **`MATCH...AGAINST` 布尔模式**：使用 `+keyword` 语法要求关键词必须出现
- **`LIMIT 200`**：每表最多返回 200 条候选，控制候选集规模
- **无硬过滤条件时跳过**：若既无 FULLTEXT 检索词也无硬过滤条件，该表被跳过（避免全表扫描）

### 1.4 阶段三：混合排序

**FULLTEXT 得分**：MySQL 内置的相关性评分，基于 ngram 词频和逆文档频率。

**Python 侧重排序** (`_hybrid_score()` L551)：

```python
def _hybrid_score(intent: SearchIntent, text: str) -> float:
    score = 0.0
    text_lower = text.lower()
    for kw in intent.semantic_keywords:
        if kw in text_lower:
            score += 1.0 * text_lower.count(kw.lower())   # 每命中一次 +1
    for token in intent.exact_tokens:
        if token in text:
            score += 10.0                                  # 精确匹配 +10
    return score
```

**综合得分**：`_hybrid_score_` = MySQL `_score_` + Python `_hybrid_score()`

排序后截取 Top 20 返回。

### 1.5 数据源与索引依赖

**当前覆盖的数据库**（`_PRICE_DBS` L47-L53）：

| 数据库 | 表数 | 估算行数 | 数据量 |
|--------|------|----------|--------|
| `xunfei_202605_01` | 5 | 214,359 | 1,842 MB |
| `bidding_information_dai` | 5 | 619,811 | 536 MB |
| `xunfei5` | 20 | 348,167 | 2,009 MB |
| `xunfei_06` | 6 | 56,956 | 56 MB |
| `tm` | 6 | 19,211 | 40 MB |

**FULLTEXT 索引概览**（基于 [recommended_indexes.sql](file://d:\DEMO\zhaotoubiao_demo\test\recommended_indexes.sql)）：

共推荐 22 个 FULLTEXT 索引，覆盖 5 个数据库的关键语义列：

```sql
-- 示例：bidding_information_dai 库的索引
ALTER TABLE `bidding_information_dai`.`notifications`
    ADD FULLTEXT INDEX `ft_notifications_project_name_title_content`
    (`project_name`, `title`, `content`) WITH PARSER ngram;
```

**索引创建工具**：[create_fulltext_indexes.py](file://d:\DEMO\zhaotoubiao_demo\test\create_fulltext_indexes.py) 可自动扫描语义列并生成 DDL。

**ngram 配置要求**：
```
[mysqld]
ngram_token_size=2       # 中文双字分词粒度
ft_min_word_len=1        # 最小词长为 1
```

### 1.6 未被覆盖的数据孤岛

基于 [db_explore_output/quick_overview.json](file://d:\DEMO\zhaotoubiao_demo\db_explore_output\quick_overview.json) 的数据探查结果，服务器上共有 47 个用户数据库，当前检索仅覆盖其中 5 个（约 10.6%）。以下高价值数据库未纳入检索范围：

| 数据库 | 表数 | 估算行数 | 数据量 | 潜在价值 |
|--------|------|----------|--------|---------|
| `lin_gang_6_ju_tou_1` | 12 | 3,611,590 | 3,605 MB | 临港六局招标数据 |
| `ifyltek4_2` | 6 | 3,328,667 | 2,390 MB | 讯飞四期二组数据 |
| `xunfei4` | 9 | 192,440 | 1,843 MB | 讯飞四期数据 |
| `xunfei07_rag_db` | 3 | 219,996 | 1,779 MB | RAG 知识库数据 |
| `xunfei` | 9 | 253,182 | 1,894 MB | 讯飞主库 |
| `relissc_rag` | 23 | 238,464 | 137 MB | RAG 结构化数据 |

---

## 2. 检索速度评估

### 2.1 性能剖析工具链

项目中已建立完整的性能诊断工具：

| 工具 | 文件 | 功能 |
|------|------|------|
| EXPLAIN 分析器 | [test/explain_sql.py](file://d:\DEMO\zhaotoubiao_demo\test\explain_sql.py) | 执行 `EXPLAIN ANALYZE`，自动检测全表扫描/Filesort/Temporary |
| 实时 Profiler | [test/profile_current_price.py](file://d:\DEMO\zhaotoubiao_demo\test\profile_current_price.py) | 运行 price_inquiry 并打印每条 SQL 耗时 |
| 性能日志 | `_profile_execute()` (L534) | 每次 SQL 执行自动记录 `[SQL_PROFILE] cost=X.XXXs` |

### 2.2 SQL 耗时分析

`_profile_execute()` 在 [price_inquiry.py L534-L545](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py#L534-L545) 中对每条 SQL 记录耗时：

```python
def _profile_execute(cur, sql, params) -> float:
    start = time.perf_counter()
    cur.execute(sql, params)
    elapsed = time.perf_counter() - start
    logger.info("[SQL_PROFILE] cost=%.3fs sql=%s params=%s", elapsed, ...)
    return elapsed
```

**单次查询的 SQL 执行次数**：`_PRICE_DBS` 共 5 个数据库 × 平均每库 5~10 张表 ≈ **25~50 条 SQL**（其中仅语义列匹配的表才会执行）。

**估算典型端到端响应时间**：

| 阶段 | 耗时估算 | 说明 |
|------|----------|------|
| 意图抽取 (LLM) | 0.5~2.0s | DeepSeek API 调用 |
| 表结构加载 | 0.2~0.5s | 首次加载走 `information_schema` 查询（有缓存） |
| FULLTEXT SQL 执行 | 0.01~0.1s/条 | 有索引时，nlist=128 索引扫描 |
| 候选集获取 | 0.5~2.0s | 25~50 条 SQL 并发压力 |
| Python 重排序 | < 0.01s | 纯内存文本匹配 |
| **总计** | **1.5~5.0s** | |

### 2.3 FULLTEXT vs LIKE 性能对比

基于 [explain_sql.py](file://d:\DEMO\zhaotoubiao_demo\test\explain_sql.py) 的 EXPLAIN ANALYZE 诊断能力，可做如下对比：

| 查询方式 | 扫描类型 | 预估耗时（10万行表） | 内存占用 |
|----------|----------|---------------------|---------|
| `LIKE '%关键词%'` | **全表扫描** (type=ALL) | 200~500ms | 全行数据加载 |
| `MATCH(col) AGAINST('+关键词' IN BOOLEAN MODE)` | **全文索引扫描** (type=fulltext) | 5~20ms | 仅索引命中行 |
| 硬过滤 `col = '值'` | **索引查找** (type=ref/eq_ref) | 1~5ms | 单行或极少量行 |

**关键发现**：当 FULLTEXT 索引缺失时，代码有专门的容错处理（L628-L630）：

```python
except Exception as e:
    if "fulltext" in str(e).lower():
        logger.warning("[FULLTEXT_MISSING] db=%s table=%s: %s", db_name, table_name, e)
    else:
        logger.debug("查询 %s.%s 失败: %s", db_name, table_name, e)
    continue
```

这意味着 **缺失 FULLTEXT 索引的表会被静默跳过**，不会退化为 LIKE 全表扫描——这是一种安全但会漏检的设计。

### 2.4 性能瓶颈识别

1. **LLM 意图解析**：占总耗时 30%~50%，是最大瓶颈。虽然有缓存，但新问题仍需调用。
2. **多库多表串行查询**：5 个数据库 × N 张表串行执行 SQL，缺少并行化。
3. **无 SSCursor**：当前使用 `DictCursor`（L622），候选集全部加载到内存。虽已 LIMIT 200 + LEFT 800，但 50 张表 × 200 行仍有内存压力。
4. **schema 缓存粒度**：`_SCHEMA_CACHE` 只在进程生命周期内有效，重启后需重新加载。

---

## 3. 检索准确性评估

### 3.1 混合评分机制分析

**MySQL FULLTEXT 得分**：
- 基于 ngram (token_size=2) 的 TF-IDF 相关性
- 优点：数据库原生计算，效率高，支持布尔操作符
- 局限：双字分词粒度对短关键词（单字）无效；缺乏语义理解

**Python 侧重排序** (`_hybrid_score()` L551-L563)：
- 语义关键词：每命中一次 +1.0 分（线性累加）
- 精确 token：每命中 +10.0 分（高权重）

**综合**：`_hybrid_score_ = _score_ + _hybrid_score()`

### 3.2 准确性问题分析

#### 3.2.1 ngram 分词粒度

`ngram_token_size=2` 意味着中文按每 2 个字切分。例如：

| 关键词 | ngram 切分 | 潜在问题 |
|--------|-----------|---------|
| "皮艺沙发" | `皮艺`, `艺沙`, `沙发` | "艺沙" 是无意义 token，可能误匹配 |
| "招标公告" | `招标`, `标公`, `公告` | "标公" 无意义 |
| "中标" | `中标` (2字) | ✅ 精确匹配 |
| "沙发" | `沙发` (2字) | ✅ 精确匹配 |

短于 2 字的关键词（如 "床"、"桌"）无法被 ngram 索引覆盖。

#### 3.2.2 同义词与变体

招投标领域存在大量同义词和变体：

| 标准词 | 常见变体 | 当前处理 |
|--------|---------|---------|
| 招标公告 | 招标信息、采购公告、招标文件 | ❌ 无同义词扩展 |
| 中标 | 成交、中标结果、中标公示 | ❌ 需 LLM 提取关键词 |
| 询价 | 询价采购、询价比选 | ❌ 无联想 |
| 磋商 | 竞争性磋商、谈判 | ❌ 无关联 |

当前依赖 LLM 在意图解析阶段提取 `semantic_keywords`，但 LLM 可能遗漏变体。

#### 3.2.3 列名匹配的局限性

`_classify_columns()` 基于硬编码的列名模式匹配（如 `_SEMANTIC_PATTERNS` L309-L314）。当数据库表的列名不规范时（如 `col1`, `field_a`），所有文本列会被兜底归入 `semantic` 角色，但这些列的内容可能无业务含义。

#### 3.2.4 排序质量

混合评分存在以下问题：
- **关键词计数偏差**：长文本天然包含更多关键词命中，可能将低质量长文本排在前面
- **缺乏位置权重**：标题命中 vs 正文命中的重要性未区分
- **无文本质量因子**：未考虑数据完整性、时效性

### 3.3 命中率估算

基于现有数据规模：
- 覆盖 5 个数据库，约 125 万行中标相关数据
- 查询"皮艺沙发中标记录"可命中 `tender`、`goods_info`、`ods_tender` 等表
- 覆盖率：约 100%（对于已索引的表）
- 但 47 个数据库中仅检索 5 个，**整体数据覆盖率仅约 11%**

---

## 4. 升级潜力分析

### 4.1 从关键词匹配到 Text-to-SQL

**可行性**：⭐⭐⭐⭐ (高)

当前系统已有 LLM 意图解析能力，升级到 Text-to-SQL 的路径清晰：

```
当前：LLM → SearchIntent (结构化关键词) → 确定性 SQL 模板
升级：LLM → 直接生成 SQL → 执行 → 结果
```

**优势**：
- 可实现跨表 JOIN、聚合统计（如"过去一年中标金额最高的前 10 家公司"）
- 消除硬编码的列分类规则

**风险**：
- LLM 可能生成不安全的 SQL（需严格的权限控制和语法校验）
- 需要向 LLM 提供表结构上下文（当前 schema 缓存可直接复用）
- SQL 生成质量依赖 LLM 对数据库结构的理解

**推荐实施路径**：
1. 先用 `EXPLAIN ANALYZE` 校验生成的 SQL 无全表扫描
2. 使用只读数据库账号，限制 `DROP`/`ALTER`/`DELETE` 权限
3. 保留当前确定性 SQL 模板作为 fallback

### 4.2 向量语义检索

**可行性**：⭐⭐⭐⭐⭐ (非常高)

项目已具备完整的向量检索基础设施（[public_kb/](file://d:\DEMO\zhaotoubiao_demo\public_kb) 模块）：

| 组件 | 文件 | 状态 |
|------|------|------|
| Embedding 模型 | BAAI/bge-large-zh-v1.5 (1024维) | ✅ 已配置 |
| 向量数据库 | Milvus (Docker 本地部署) | ✅ 已部署 |
| 文档切片 | SemanticChunker (400字/块) | ✅ 已实现 |
| RAG 问答链 | LCEL build_qa_chain | ✅ 已实现 |

**MySQL 数据向量化路径**：

```
MySQL 表行 → 拼接文本列 → BGE Embedding → Milvus 集合
     ↑                                            │
     └──────── 检索时回表获取完整字段 ←────────────┘
```

**混合检索架构建议**：

```
用户查询
  │
  ├─→ Milvus 向量检索 (Top 50)    ← 语义相似度
  ├─→ MySQL FULLTEXT (Top 50)     ← 关键词精确匹配
  │
  └─→ RRF (Reciprocal Rank Fusion) 合并排序 → Top 20
```

### 4.3 RAG 融合

**可行性**：⭐⭐⭐⭐⭐

当前 [knowledge_qa](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\knowledge_qa.py) 节点已实现基于 Milvus 的 RAG 问答，[price_inquiry](file://d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py) 节点实现 MySQL 结构化检索。两者可融合：

```
router
  ├─→ knowledge_qa  ← Milvus RAG (法律法规、知识文档)
  ├─→ price_inquiry ← MySQL FULLTEXT (中标记录)
  └─→ fused_search  ← 混合检索（跨源合并）
```

**实现建议**：新增 `fused_search` 节点，同时调用 Milvus 和 MySQL，通过 LLM 对合并结果重排序。

### 4.4 缓存优化

| 策略 | 预期收益 | 实现复杂度 |
|------|----------|-----------|
| Redis 缓存热门查询结果 | 重复查询响应 < 50ms | 低 |
| 表结构 schema 持久化到本地 JSON | 启动免去 schema 加载 | 低（已有扫描工具） |
| LLM 意图解析结果持久化 | 相同问题免 LLM 调用 | 低（已有多级缓存 `_INTENT_CACHE`） |
| 向量索引预热 | 首次查询不冷启动 | 中 |

### 4.5 索引优化建议

基于当前 FULLTEXT 索引覆盖情况：

1. **扩大 _PRICE_DBS 范围**：将 `lin_gang_6_ju_tou_1`、`ifyltek4_2`、`xunfei` 等高价值库纳入检索
2. **复合索引优化**：对时间列 + 文本列建立复合索引（先时间过滤再 FULLTEXT）
3. **覆盖索引**：对高频查询字段（如 `purchaser`、`region`）建立覆盖索引避免回表
4. **ngram_token_size 调整**：评估 `ngram_token_size=1` 的索引膨胀与召回率权衡

---

## 5. 自建分类数据库与路由方案可行性论证

### 5.1 现状分析

当前 MySQL 服务器上存在 **47 个用户数据库**，数据分散且命名不规范（如 `xunfei_202605_01`、`xunfei5`、`xunfei_06`、`tm`、`lin_gang_6_ju_tou_1` 等）。通过 [scan_tables.py](file://d:\DEMO\zhaotoubiao_demo\test\scan_tables.py) 的关键词匹配分析，可识别出以下主要数据类别：

| 数据类别 | 典型表名模式 | 示例表 |
|----------|-------------|--------|
| **招标公告** | `tender`, `notifications`, `procurement_notices`, `ods_tender` | 招标公告正文、项目信息 |
| **中标记录** | `projects`, `goods_info`, `companies` | 中标公司、金额、产品信息 |
| **法律法规** | `laws`, `policy`, `ods_policy`, `ods_policy_regulation_files` | 法规全文、政策文件 |
| **产品信息** | `product`, `ods_products`, `物资商品信息表` | 产品目录、规格、供应商 |
| **企业信息** | `enterprise`, `companies`, `ods_company_detail` | 企业工商信息、资质 |
| **RAG 文档** | `rag_*`, `knowledge_*`, `embedding_*` | 文档片段、向量数据 |

### 5.2 方案设计

#### 目标架构

```
                         ┌──────────────────────┐
                         │    Router (LLM)       │
                         │  意图分类 + 数据源选择  │
                         └──────┬───────────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          ▼                     ▼                     ▼
   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
   │ 招标公告库    │    │ 产品信息库    │    │ 法律法规库    │
   │ bidding_db   │    │ product_db   │    │ law_db       │
   │              │    │              │    │              │
   │ • tenders    │    │ • products   │    │ • laws       │
   │ • notices    │    │ • goods      │    │ • policies   │
   │ • projects   │    │ • suppliers  │    │ • regulations│
   └──────────────┘    └──────────────┘    └──────────────┘
          │                     │                     │
          └─────────────────────┼─────────────────────┘
                                ▼
                     ┌──────────────────────┐
                     │   混合检索引擎        │
                     │  MySQL FULLTEXT       │
                     │  + Milvus Vector      │
                     │  + RRF Fusion         │
                     └──────────────────────┘
```

#### 数据库分类设计

```
bidding_core/          # 招标核心数据
├── tenders            # 招标公告（合并 xunfei_*.ods_tender / bidding_information_dai.notifications）
├── bid_results        # 中标结果（合并 bidding_information_dai.projects）
├── procuring_entities # 采购单位（合并 companies）
└── bid_items          # 标的物清单（合并 goods_info / ods_products）

product_market/        # 产品市场数据
├── product_catalog    # 产品目录
├── supplier_info      # 供应商信息
└── price_history      # 历史价格

legal_kb/              # 法律法规
├── laws               # 法律
├── regulations        # 行政法规
├── policies           # 政策文件
└── local_rules        # 地方性法规

rag_metadata/          # RAG 元数据
├── document_index     # 文档索引
├── chunk_store        # 文档块存储
└── embedding_cache    # 向量缓存
```

### 5.3 技术复杂度评估

| 维度 | 评分 (1-5) | 说明 |
|------|-----------|------|
| **数据迁移** | ⭐⭐⭐⭐ | 47 个源库 → 4 个目标库，需处理表结构差异、数据去重、字段映射 |
| **路由策略** | ⭐⭐ | 已有 LLM 路由基础，增加"数据源选择"维度即可 |
| **索引重建** | ⭐⭐⭐⭐ | 需为合并后的表重新设计 FULLTEXT 和普通索引 |
| **查询重写** | ⭐⭐⭐ | SQL 模板需适配新的库表结构 |
| **向后兼容** | ⭐⭐⭐ | 需保留旧数据源访问能力（过渡期双写/双读） |
| **维护成本** | ⭐⭐⭐ | 新增数据类别→新增库→修改路由→修改查询模板 |

### 5.4 与现有系统的关系

#### 与 Milvus 向量库的关系

| 维度 | MySQL 分类库 | Milvus public_kb |
|------|-------------|------------------|
| 数据类型 | 结构化字段（金额、日期、公司名） | 非结构化文本（法规全文、文档片段） |
| 查询方式 | SQL + FULLTEXT | 向量相似度 |
| 优势 | 精确过滤、聚合统计、排序 | 语义匹配、跨段落理解 |
| 互补性 | 精确查询 + 条件筛选 | 模糊语义 + 知识问答 |

**建议**：MySQL 分类库负责结构化精确检索，Milvus 负责非结构化语义检索，两者通过统一 API 层融合，不做互相替代。

#### 与 public_kb 模块的关系

`public_kb` 模块的 `rag_engine.py` 专注于 PDF 解析入库 + 向量问答，不适合直接管理结构化 MySQL 数据。分类数据库方案应作为 **新的数据层**，与现有 RAG 模块平行：

```
agent/
├── nodes/
│   ├── knowledge_qa.py      ← 对接 Milvus (不变)
│   ├── price_inquiry.py     ← 对接 MySQL 分类库 (改造)
│   └── fused_search.py      ← 新增：跨源融合
│
public_kb/                    ← Milvus RAG 引擎 (不变)
│
classified_db/                ← 新增：分类数据库模块
├── __init__.py
├── config.py                ← 分类库连接配置
├── schema.py                ← 统一表结构定义
├── migration.py             ← 数据迁移脚本
└── query_engine.py          ← 分类库查询引擎
```

### 5.5 实施路径建议

**阶段一：轻量级视图方案（1-2 周）**

不迁移数据，在当前 MySQL 上创建跨库 VIEW 或通过查询引擎动态路由：

```sql
-- 在查询引擎中维护"类别 → 库.表"映射
CATEGORY_MAP = {
    "招标公告": [
        "xunfei_202605_01.ods_tender",
        "bidding_information_dai.notifications",
        "xunfei5.ods_tender",
        # ...
    ],
    "法律法规": [
        "xunfei_06.laws",
        "xunfei5.ods_policy",
        # ...
    ],
}
```

**阶段二：数据整合（3-4 周）**

在新建的 `bidding_core`、`product_market`、`legal_kb` 三个库中：
1. 设计统一表结构（基于现有表的字段分析）
2. 编写 ETL 脚本做数据清洗、去重、字段映射
3. 创建优化后的 FULLTEXT 索引
4. 并行运行新旧系统，对比验证

**阶段三：智能路由（2 周）**

扩展 [router.py](file://d:\DEMO\zhaotoubiao_demo\agent\router.py) 的意图分类，增加数据源选择维度：

```python
# 新增路由维度
DataSources = Literal[
    "bidding_core",      # 招标核心
    "product_market",    # 产品市场
    "legal_kb",          # 法律法规
    "milvus_kb",         # Milvus 知识库
    "all",               # 全部数据源
]
```

### 5.6 风险与缓解

| 风险 | 级别 | 缓解措施 |
|------|------|---------|
| 数据迁移丢失/错位 | 高 | 保留原始库不变，新库作为只读副本；编写数据校验脚本 |
| 查询性能下降 | 中 | 新库独立服务器或独立实例；充分测试索引策略 |
| 路由分类错误 | 中 | 保留 `all` 兜底模式；人工审核日志 |
| 维护复杂度上升 | 中 | 统一表结构减少维护点；自动化 schema 管理 |
| 与现有 RAG 冲突 | 低 | 分层设计，MySQL 管结构化、Milvus 管非结构化 |

---

## 总结

当前 MySQL 检索系统已具备良好的工程架构基础：LLM 意图解析 + 确定性 SQL 生成 + 混合排序的三阶段设计有效平衡了灵活性与安全性。FULLTEXT 索引 + ngram 解析器的组合在中文招投标场景中提供了实用的关键词检索能力。

**核心优势**：
- 清晰的模块边界和职责划分
- 完善的性能诊断工具链（EXPLAIN、Profiler）
- 良好的容错机制（索引缺失静默跳过、LLM 失败回退）

**主要短板**：
- 数据覆盖率低（5/47 数据库，约 11%）
- 缺乏语义检索能力（纯关键词匹配）
- ngram 粒度粗，短词和同义词处理不足
- 缺少查询结果缓存

**推荐优先改进项**（按投入产出比排序）：
1. 扩大 `_PRICE_DBS` 覆盖范围（低成本，高收益）
2. 引入查询结果缓存（低成本，中等收益）
3. 实施分类数据库视图方案（中等成本，高收益）
4. 探索向量语义检索与 FULLTEXT 混合（中等成本，高收益）
5. 长期规划 Text-to-SQL + 分类数据库完整方案（高成本，最高收益）
