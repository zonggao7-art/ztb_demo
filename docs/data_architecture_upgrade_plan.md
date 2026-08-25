# 数据架构升级方案：从多库分散到单库集中 + 双层检索

> 版本：v3.0（现状对齐版，2026-08-15）
> 前置文档：[project_overview.md](./project_overview.md) | [milvus_rebuild_feasibility_plan.md](./milvus_rebuild_feasibility_plan.md)
> 关联代码：[agent/router.py](../agent/router.py) | [agent/nodes/price_inquiry/](../agent/nodes/price_inquiry/) | [public_kb/](../public_kb/) | [test_report/evaluation_report.md](../test_report/evaluation_report.md)
> 关联测评：[三大核心业务测评报告](../test_report/evaluation_report.md)（1500 条，99.464% 字段召回率 / 99.533% 准确率）

---

## 目录

1. [当前架构现状（2026-08 真实状态）](#1-当前架构现状2026-08-真实状态)
2. [升级目标与背景](#2-升级目标与背景)
3. [升级潜力分析](#3-升级潜力分析)
4. [升级难度评估](#4-升级难度评估)
5. [详细升级方案](#5-详细升级方案)
   - 5.1 [MySQL 数据层：从「多库分散」到「单库集中」](#51-mysql-数据层从多库分散到单库集中)
   - 5.2 [公共知识库：从「稠密降级」到「混合检索 + 精排」](#52-公共知识库从稠密降级到混合检索--精排)
   - 5.3 [业务库语义镜像：从「冷启自举」到「自动维护」](#53-业务库语义镜像从冷启自举到自动维护)
   - 5.4 [路由与意图层：保持骨架稳定，扩展二级路由](#54-路由与意图层保持骨架稳定扩展二级路由)
6. [实施路径与里程碑](#6-实施路径与里程碑)
7. [技术栈与工具清单](#7-技术栈与工具清单)
8. [风险与缓解措施](#8-风险与缓解措施)
9. [附录：关键代码路径速查](#附录关键代码路径速查)

---

## 1. 当前架构现状（2026-08 真实状态）

> **本节为对齐说明**：v2.0 文档基于"47 个用户库分散存储 + 计划迁移到 `ztb_data` 单库 + `bidding_* / company_* / product_*` 表前缀 + LIST 分区"的设想。当前真实状态与 v2.0 设想的差异较大，本版（v3.0）按真实情况重写。

### 1.1 已落地的简化单库架构

v2.0 设想的 ETL 整合已经在早期阶段完成，**实际产物是 `ztb_clean` 单库 + 3 张核心业务表**：

| 维度 | v2.0 设想 | 当前真实状态（2026-08） | 差异说明 |
| --- | --- | --- | --- |
| 数据库数量 | 47 个源库 → 1 个 `ztb_data` | **1 个 `ztb_clean`**（已落地） | 库名是 `ztb_clean` 而非 `ztb_data` |
| 核心业务表 | `bidding_tender_notices` / `bidding_bid_results` / `company_companies` / `product_products` | **`bid_project` / `company_info` / `company_penalty`**（3 张而非 4 张） | product_* 表组**未保留**（产品询价已下线，见节点 `_build_capability_boundary_answer`） |
| 表前缀分组 | `bidding_* / company_* / product_*` 命名 | **无前缀命名**，按业务语义直接命名 | 路由通过 `_SUB_ROUTE_MAP` 显式声明 |
| 分区策略 | LIST 分区按 `category` | **未启用分区**（单库 3 表数据量未达分区阈值） | 18k + 39k + 1.8k 行规模无需分区 |
| 数据量级 | 估 580 万行合并 | 实际 **58,458 行**（详 §1.2） | 原设想严重高估，是评估阶段的占位值 |

### 1.2 当前数据规模（实测）

| 表 | 用途 | 行数 | 子路由 | query_type |
| --- | --- | --- | --- | --- |
| `ztb_clean.company_info` | 企业工商信息 | 38,911 | `company_query` | `company_detail` / `company_industry` |
| `ztb_clean.company_penalty` | 企业失信惩戒 | 1,805 | `company_query` | `penalty_check` |
| `ztb_clean.bid_project` | 招投标项目中标 | 17,742 | `bidding_query` | `project_detail` / `bidder_query` |
| **合计** | | **58,458** | | |

业务路由 + query_type 共**5 种**：company_detail / company_industry / penalty_check / project_detail / bidder_query（与本次 1500 条测评完全对应）。

### 1.3 当前完整数据层架构

```
┌─────────────────────────────────────────────────────────┐
│  业务结构化数据（MySQL）                                  │
│  ────────────────────────                                │
│  ztb_clean（单库）                                        │
│   ├─ company_info（38,911 行，10 个字段）                  │
│   ├─ company_penalty（1,805 行，6 个字段）                │
│   └─ bid_project（17,742 行，13 个字段）                  │
│                                                          │
│  连接：[db.py] 连接池复用（_pool_connections）             │
│  Schema：[schema.py] _HARDCODED_SCHEMA 硬编码字段分类       │
└─────────────────────────────────────────────────────────┘
              ↑                  ↑
              │精确回表/精确匹配  │语义召回（仅返回 id）
              │                  │
┌─────────────────────────────────────────────────────────┐
│  Milvus 向量数据库                                        │
│  ────────────────────────                                │
│  ① public_kb（29,729 条，稠密 COSINE，临时本地实例）         │
│     用途：法规 / 文档问答（本次测评 0 命中，业务线未触达）    │
│                                                          │
│  ② mysql_price_semantic（77,597 条，COSINE 稠密）          │
│     用途：MySQL 三表的"行级语义镜像"，配合 SQL 回表          │
└─────────────────────────────────────────────────────────┘
              ↑                  ↑
              │Embedding (SiliconFlow bge-large-zh-v1.5 / 计划 bge-m3)
              │
┌─────────────────────────────────────────────────────────┐
│  LangGraph Agent                                          │
│  ────────────────────────                                │
│  router.py：RouterDecision 4 类意图                       │
│   ├─ knowledge_qa → public_kb（实际 0 流量）               │
│   ├─ price_inquiry → MySQL ztb_clean（1500/1500 流量）     │
│   ├─ general_chat / fallback / doc_qa（本次未测）          │
│                                                          │
│  price_inquiry 节点内部二级路由：                           │
│   ├─ _SUB_ROUTE_MAP：company_query / bidding_query / all  │
│   ├─ 8 种 query_type 模板（answer_templates.py）            │
│   └─ 5 路查询执行（queries.py + recall.py）                 │
└─────────────────────────────────────────────────────────┘
```

### 1.4 v2.0 → v3.0 文档修订要点

| v2.0 内容 | v3.0 处理 | 理由 |
| --- | --- | --- |
| 47 个源库迁移 ETL | **删除**（已落地，文档归档即可） | 历史工作已完成 |
| `ztb_data` 库名 + LIST 分区 + `category` 分区键 | **改为现状**：`ztb_clean` 单库 + 无分区 + 按业务子表命名 | 与代码事实对齐 |
| `_PRICE_DBS` 5 库遍历 | **删除**（不存在该变量） | 当前是 `_CLEAN_DB` 单库 + `_SUB_ROUTE_MAP` 路由到具体表 |
| 4 张核心表（含 `product_*`） | **改为现状**：3 张核心表（`bid_project` / `company_info` / `company_penalty`） | 产品询价已下线 |
| 引用 `agent/router.py` L29-L41 的 `data_source` 字段 | **删除**（不存在该字段） | RouterDecision 当前无 `data_source` 维度 |
| 引用 `agent/nodes/price_inquiry.py` L589-L671 `_query_price_data` | **改为现状**：`_query_company_data` / `_query_bidding_data` / `_query_penalty_by_company_name` | 函数已拆包到 `queries.py` |
| 引用 `test/recommended_indexes.sql` / `preview_candidates.py` | **标注"已废弃"**，指向当前测评产物 | 旧测试脚本已归档 |
| 增量 `_TABLE_GROUP_MAP` + `USE_CLASSIFIED_DB` 开关 | **改为 §5.4 未来扩展**：在 `_SUB_ROUTE_MAP` 上增加表组维度 | 路由增强作为远期项 |
| 性能预估"端到端 0.5~1.5s" | **改为 §5 实际基线**：本次测评 avg=2.76s、P99=3.685s | 用真实测评数字替代估算 |
| 分区管理 `REORGANIZE PARTITION` 命令 | **删除**（未启用分区） | 与事实对齐 |

---

## 2. 升级目标与背景

### 2.1 升级背景：本次测评揭示的 5 类系统性问题

[三大核心业务测评报告](../test_report/evaluation_report.md) 的 1500 条全流程实测，量化暴露了如下问题（按代码事实，本版升级方案要逐条解决）：

| # | 问题 | 量化证据 | 涉及升级章节 |
| --- | --- | --- | --- |
| 1 | **public_kb 永远走稠密降级路径** | 日志 `Schema 无稀疏向量字段`；混合检索 + Reranker + 动态阈值代码就绪但不可达 | §5.2 |
| 2 | **MySQL FULLTEXT 索引缺失** | 日志持续 `Can't find FULLTEXT index matching the column list`；Level 1 检索每次失败，降级 LIKE/全表扫描 | §5.1.4 |
| 3 | **7 条失败用例集中在「实体边界识别」」」 | report §9.2：5 条实体名带注释/后缀 + 2 条中标历史检索漏召 | §5.4.3 |
| 4 | **LLM 串行调用是延迟主因** | node_elapsed 平均 1.76s 占端到端 64%；5 种固定格式本可走确定性快路径 | §5.4.4 |
| 5 | **`public_kb` 业务无测试集覆盖** | 本次测评 1500 条全部命中 `price_inquiry`，法规问答 0 流量 | §5.2.5 |

### 2.2 升级目标（量化对照表）

| 指标 | 当前值（实测） | 目标值 |
| --- | --- | --- |
| 必填字段整体召回率 | 99.464% | ≥99.7%（减少剩余 7 条失败中的 5 条） |
| 系统输出整体准确率 | 99.533% | ≥99.8% |
| 单条查询平均耗时 | 2.76s | ≤1.5s（确定性格快路径命中 5 种固定格式） |
| P99 耗时 | 3.685s | ≤2.5s |
| FULLTEXT 索引缺失告警 | 每条用例持续报 `[FULLTEXT_MISSING]` | 0（建 ngram FULLTEXT 后 Level 1 走通） |
| `public_kb` 混合检索激活 | ❌ 当前仅稠密 + 固定阈值 0.45 | ✅ 激活双向量 + RRF + Reranker + 动态阈值 |
| 法规问答测试集覆盖 | 0 条 | ≥50 条 |

### 2.3 不在本期升级范围

明确**不做**的事，避免范围蔓延：

- ❌ 跨库数据源扩展（当前 `ztb_clean` 已包含全部测评数据）
- ❌ LIST 分区（数据量 58k 行，无需分区）
- ❌ 通用 text2sql 自由生成 SQL（前期测评已证明召回率 2.787%，本系统采用 schema 固定 + 参数动态的中间路线）
- ❌ 多语言 Embedding（业务域限定中文）
- ❌ 引入图数据库 / ES 等新组件
- ❌ 商业 LLM 切换（deepseek-chat 在测试中表现稳定）

---

## 3. 升级潜力分析

### 3.1 代码骨架兼容性：⭐⭐⭐⭐⭐（非常高）

LangGraph Agent 骨架对升级天然友好：

- **`agent/state.py`**：`AgentState` 仅含 `messages` / `router_intent` / `business_result` 三个通用字段，**无业务专用字段**——升级不需改 State。
- **`agent/graph.py` L108-L181**：声明式节点注册 + `_with_fallback` 全局异常兜底，业务节点可独立升级不影响 Graph。
- **`public_kb/` 独立模块**：与 `agent/` 通过 `agent.invoke(question, thread_id=...)` 对接，**完全可以单独升级混合检索 + Reranker**，对 LangGraph 主链路零侵入。
- **`price_inquiry/` 已拆包**：从单一 `price_inquiry.py` 拆成 11 个子模块（`db/intent/queries/recall/semantic/sql_builders/schema/...`），**职责清晰、改动定位明确**。

### 3.2 路由机制扩展性：⭐⭐⭐⭐（高）

`agent/router.py` 当前 `RouterDecision` 字段：

```python
class RouterDecision(BaseModel):
    intent: RouterIntent          # knowledge_qa / price_inquiry / general_chat / doc_qa / fallback
    reason: str                   # 分类理由
    # 扩展点（v3.0 升级目标）：data_source 字段（bidding/company/all），见 §5.4.3
```

`price_inquiry/_SUB_ROUTE_MAP`（`node.py:31-44`）：

```python
_SUB_ROUTE_MAP = {
    "company_query": {"tables": ["company_info", "company_penalty"], "query_fn": "_query_company_data"},
    "bidding_query": {"tables": ["bid_project"], "query_fn": "_query_bidding_data"},
    "all":            {"tables": ["company_info", "company_penalty", "bid_project"], "query_fn": "_query_all_tables"},
}
```

**扩展点**：`_SUB_ROUTE_MAP` 已经支持表级别路由；如需新增表组（如未来加 `supplier_*`），只需在 map 中追加条目 + 对应 query 函数。

### 3.3 数据可获取性：⭐⭐⭐⭐⭐（全部就绪）

本次测评已验证：
- **MySQL `ztb_clean`**：Docker 容器化部署（`docker/mysql/`），3 张表数据完整，行数与报告一致；
- **Milvus `public_kb`**：29,729 条已迁移到云端 `8.130.174.43:19530`（本次测评用本地实例，详见 [milvus_rebuild_feasibility_plan.md](./milvus_rebuild_feasibility_plan.md)）；
- **Milvus `mysql_price_semantic`**：77,597 条（3 表行级语义镜像，启动时按需自举，见 `semantic.py:_rebuild_mysql_semantic_collection`）；
- **Embedding 通道**：SiliconFlow API（`bge-large-zh-v1.5` 当前默认，计划升级 `bge-m3`）；
- **大模型通道**：deepseek-chat（temperature=0，60s 超时，1 次重试）。

### 3.4 三层检索链路适配性：⭐⭐⭐⭐（高）

当前 `price_inquiry` 节点链路（已在本次 1500 条测评中验证）：

| 阶段 | 实现文件 | 升级改动 | 改动量 |
| --- | --- | --- | --- |
| ① 意图抽取 | `intent.py` | 加噪声词 + 同义词表（修 7 条失败中的 5 条实体边界识别） | 极小 |
| ② SQL 召回 | `sql_builders.py` + `recall.py` | 建 ngram FULLTEXT 索引；Level 1 走通，去除 LIKE 兜底依赖 | 0（仅运维 DDL）|
| ③ 语义召回 | `semantic.py` | 升级到 bge-m3；语义查询文本用实体词，不用原疑问句 | 极小 |
| ④ 混合重排 | `recall.py:_rank_records` | 无需改动 | 0 |
| ⑤ 渲染 | `answer_templates.py` | 无需改动（5 种模板已在 1500 条测评中验证） | 0 |

`public_kb` 节点链路（本次测评 0 命中，仅作未来扩展参考）：

| 阶段 | 实现文件 | 升级改动 | 改动量 |
| --- | --- | --- | --- |
| ① schema 重建 | `milvus_store.py:initialize_collection` | 加 sparse_vector 字段 + BM25 Function + 双索引 | 中 |
| ② 切片参数 | `config.py:chunk_max_chars` | 400 → 2000（已配置，未生效） | 极小 |
| ③ 混合检索 | `qa_chain.py:_retrieve` | `has_sparse=True` 自动激活，无需改代码 | 0 |
| ④ 精排 | `qa_chain.py:_SiliconFlowReranker` | 已写好，自动激活 | 0 |
| ⑤ 动态阈值 | `qa_chain.py:_adaptive_threshold` | 已写好，自动激活 | 0 |

### 3.5 与现有 LangGraph 分支的协同兼容性：⭐⭐⭐⭐⭐（完全兼容）

- **`knowledge_qa` 分支**：独立 Milvus 模块，本次 0 流量不影响；
- **`general_chat / doc_qa / fallback`**：纯 LLM / 占位分支，零侵入；
- **`price_inquiry` 主链路**：所有改动均在内部子模块内完成，外部 `agent.invoke()` 接口不变。

---

## 4. 升级难度评估

| 维度 | 难度评级 | 依据与风险分析 |
| --- | --- | --- |
| **MySQL FULLTEXT 索引补齐** | 🟢 低 | 纯 DDL 操作，3 张表加 ngram FULLTEXT 索引；已有 [test/create_fulltext_indexes.py](../test/create_fulltext_indexes.py) 可复用 |
| **实体边界识别优化** | 🟡 中 | 涉及意图解析 prompt 调优 + 同义词表；不破坏现有逻辑，仅追加规则 |
| **bge-m3 Embedding 升级** | 🟡 中 | 需触发 `mysql_price_semantic` 全量重建（77,597 条），耗时约 30-60 分钟；chunk_max_chars 同步生效需重新切 `public_kb` 29,729 条 |
| **public_kb 双向量 schema 升级** | 🟡 中 | 加 `sparse_vector` + BM25 Function + 双索引；增量 `add_documents()` 已实现，但**冷启动需 drop+重建**（约 2h 停机） |
| **测试集扩到法规问答** | 🟡 中 | 需生成 50-100 条法规问题 + 标准答案；用现有 `generate_test_sets.py` 模板扩展 |
| **路由扩 `data_source` 维度** | 🟢 低 | RouterDecision 加 1 字段 + 提示词 3-5 行；State 不动 |
| **确定性格快路径** | 🟡 中 | 对 5 种固定格式走确定性 SQL，跳过 LLM 意图解析；需要单独的快路径开关 + 兼容性测试 |
| **综合评级** | 🟡 中 | 风险集中在数据重建（bge-m3 全量 + public_kb 重建），不涉及业务逻辑大改 |

---

## 5. 详细升级方案

### 5.1 MySQL 数据层：从「多库分散」到「单库集中」

> **现状说明**：v2.0 设想的 47 库 ETL 整合已经落地为 `ztb_clean` 单库。本节聚焦**单库内的索引 / 表结构 / 检索优化**——这是当前系统真正的瓶颈所在（详见测评报告 §10.2 FULLTEXT 缺失项）。

#### 5.1.1 当前数据规模与字段分类

`schema.py:_HARDCODED_SCHEMA` 写死的 3 张核心表的字段分类：

```python
_HARDCODED_SCHEMA = {
    "company_info": {
        "id":       ["id"],
        "semantic": ["company_name", "business_scope", "industry", "address"],
        "time":     ["establish_date"],
        "region":   ["province", "city", "district"],
        "exact":    ["credit_code"],
        "text":     ["company_name", "business_scope", "industry", "address",
                     "legal_person", "registered_capital"],
    },
    "company_penalty": {
        "id":       ["id"],
        "semantic": ["company_name", "illegal_behavior", "penalty_result"],
        "time":     ["penalty_date"],
        "exact":    ["credit_code"],
        "text":     ["company_name", "illegal_behavior", "penalty_result",
                     "law_enforcement_unit"],
    },
    "bid_project": {
        "id":       ["id"],
        "semantic": ["purchaser", "successful_bidder"],
        "time":     ["winning_date", "publish_date"],
        "budget":   ["winning_amount", "budget_amount"],
        "purchaser":["purchaser"],
        "region":   ["province", "city", "district"],
        "status":   ["project_stage"],
        "exact":    ["project_number"],
        "text":     ["purchaser", "successful_bidder"],
    },
}
```

**约束规则**：
- bid_project 仅开放 `project_number` 精确匹配 + `purchaser` / `successful_bidder` 语义匹配；
- 永久屏蔽 `project_name / subject_matter / agent / project_category` 等非授权字段（详见 `schema.py` 注释 P0-11）；
- 注释中明确要求 `bid_project` 表具备 `FULLTEXT(purchaser, successful_bidder)` 索引，**当前未满足**。

#### 5.1.2 升级目标：ngram FULLTEXT 索引补齐

**这是本次升级最核心、ROI 最高的 DDL 操作**。

```sql
-- ============================================================
-- ztb_clean 单库：3 张表全部加 ngram FULLTEXT 索引
-- 解决测评日志中持续报错的 [FULLTEXT_MISSING] 问题
-- ============================================================
USE ztb_clean;

-- 1. bid_project（schema.py 注释明确要求的索引）
ALTER TABLE bid_project 
    ADD FULLTEXT INDEX ft_semantic (purchaser, successful_bidder) 
    WITH PARSER ngram;

-- 2. company_info
ALTER TABLE company_info 
    ADD FULLTEXT INDEX ft_company (company_name, business_scope, industry, address) 
    WITH PARSER ngram;

-- 3. company_penalty
ALTER TABLE company_penalty 
    ADD FULLTEXT INDEX ft_penalty (company_name, illegal_behavior, penalty_result, law_enforcement_unit) 
    WITH PARSER ngram;
```

**预期收益**：

| 维度 | 升级前（实测） | 升级后（预估） |
| --- | --- | --- |
| FULLTEXT 命中 | 0%（每次报 1191，降级 LIKE） | ≥80%（简单查询 + 多关键词查询） |
| 单次 SQL 耗时（Level 1） | ~30ms（全表扫描） | ~2ms（倒排索引） |
| `[FULLTEXT_MISSING]` 日志 | 每条用例都报 | 0 |
| 多关键词查询召回率 | 漏召严重（LIKE 仅字面匹配） | 显著提升（ngram 分词 + 倒排） |
| `winning_date` / `project_number` 等字段召回率 | 98.545% | 预期 ≥99.5%（消除项目编号带"号"后缀的漏召） |

**风险与缓解**：

- 🔴 ngram FULLTEXT 索引构建需要扫全表 + 分词 + 建倒排；58k 行预计 5-15 分钟；
- 建议在低峰期执行 + `ALTER TABLE ... ALGORITHM=INPLACE, LOCK=NONE`；
- 升级失败回滚：`ALTER TABLE ... DROP INDEX ft_xxx`（无副作用，因为这是新增索引）。

#### 5.1.3 BTREE 索引现状审查

检查现有 BTREE 索引是否覆盖常见 WHERE 条件：

```sql
SHOW INDEX FROM ztb_clean.company_info;
SHOW INDEX FROM ztb_clean.company_penalty;
SHOW INDEX FROM ztb_clean.bid_project;
```

**预期现状**：
- `company_info.credit_code` 已建 UNIQUE 索引 ✅
- `company_penalty.company_name` 普通索引（精确匹配用）✅
- `bid_project.project_number` 普通索引 ✅
- `company_info.company_name` 普通索引 ✅

如果 `company_name` 不是 BTREE 索引，会导致 `queries.py:_query_penalty_by_company_name` 的 `WHERE company_name = %s` 走全表扫描（详见测评报告 9.2 关于"政财通（安 徽）"失败用例的归因）。建议补齐。

#### 5.1.4 单库连接池复用（已实现，无需改动）

`db.py:_get_connection()` 已经实现连接池（`_pool_connections` + `_pool_in_use` + `_pool_lock`），多业务节点共享连接——这是 v2.0 设想的"单库单连接"目标，**已落地**。

不需要建新的连接管理代码。

---

### 5.2 公共知识库：从「稠密降级」到「混合检索 + 精排」

> **现状说明**：`milvus_rebuild_feasibility_plan.md` 已详细规划 `sparse_vector` + BM25 + Reranker + 动态阈值的完整升级路径。本节是该方案在 v3.0 数据架构下的**执行检查清单**——代码已就绪，激活只差 schema 升级 + 测评基线。

#### 5.2.1 当前状态：混合检索代码就绪但不可达

`public_kb/qa_chain.py:304-326`：

```python
collection_info = collection.describe_collection(settings.collection_name)
field_names = [f.get("name", "") for f in collection_info.get("fields", [])]
has_sparse = "sparse_vector" in field_names

if not has_sparse:
    logger.info("当前 Schema 无稀疏向量字段，使用稠密+Reranker 模式")
    return _dense_only_retrieve(question, vector_store, settings, collection, embeddings)
```

**当前 `_dense_only_retrieve` 实际行为**（`qa_chain.py:552-616`）：
1. pymilvus 原生 search，COSINE，nprobe=32，limit=30
2. 用 `settings.similarity_threshold=0.45` 固定阈值过滤
3. 截断到 `settings.retrieval_top_k=5`
4. **没有 Reranker、没有动态阈值**（注释撒谎了，"稠密+Reranker 模式"名不副实）

#### 5.2.2 升级目标：双向量 + RRF + 精排 + 动态阈值

完整链路（代码已实现，等待 schema 升级触发）：

```
用户问题
   ↓ bge-m3 Embedding
1024 维稠密向量 + 稀疏向量（BM25 Function 自动从 text 生成）
   ↓
Milvus hybrid_search:
   ├─ 稠密：AnnSearchRequest(vector, COSINE, nprobe=32, limit=30)
   ├─ 稀疏：AnnSearchRequest(text, IP, limit=30)
   └─ RRFRanker(k=60) 融合 → Top-30
   ↓ bge-reranker-v2-m3 精排
Top-30 按 relevance_score 降序
   ↓ _adaptive_threshold
top1 ≥ 0.75 → threshold=0.40（宽松）
top1 ≥ 0.50 → threshold=0.45（中等）
top1 < 0.50 → 拒答（unified_guidance）
   ↓
Top-3 ~ Top-5 拼上下文 → DeepSeek 生成回答
```

#### 5.2.3 实施步骤（与 milvus_rebuild_feasibility_plan.md 对齐）

| 步骤 | 任务 | 产出 | 依赖 |
| --- | --- | --- | --- |
| 1 | 准备新链路（备份 + API 配额验证） | 备份 collection；bge-m3 + bge-reranker-v2-m3 SiliconFlow 配额验证 | 0 |
| 2 | 一次性停机重建 | drop 旧 collection → 新 schema（text + vector + sparse_vector） + BM25 Function + 双索引 + bge-m3 全量重建 29,729 条 | 步骤 1 |
| 3 | 激活 + 验证 | describe_collection 看到 sparse_vector；qa_chain._retrieve 走混合分支；新增 50-100 条法规测试集验证 | 步骤 2 |
| 4 | 量化对比 | 激活前后字段召回率、动态阈值案例、Reranker 改善幅度对比报告 | 步骤 3 |

#### 5.2.4 关键参数

`public_kb/config.py` 当前值（已与方案对齐）：

```python
chunk_max_chars: int = 2000      # 当前 400（旧存量数据），升级时全量重建生效
chunk_overlap_chars: int = 100
embedding_dim: int = 1024       # bge-m3 维度
nprobe: int = 32                # 显式 nprobe（替代默认 8）
hybrid_dense_limit: int = 30
hybrid_sparse_limit: int = 30
hybrid_fusion_limit: int = 30
rrf_k: int = 60
reranker_model: str = "BAAI/bge-reranker-v2-m3"
retrieval_top_k: int = 5        # 当前 5，方案建议 3-5
similarity_threshold: float = 0.45  # 降级路径固定阈值（激活后不再使用）
```

#### 5.2.5 缺失的测试集：法规问答

**关键缺失**：当前 `public_kb` 业务线**没有任何量化测试集**——本次 1500 条测评 0 命中 `knowledge_qa`，意味着激活混合检索后**无法量化收益**。

**升级期间必须同步补 50-100 条法规问答测试集**：
- 来源：[DATA/raw_data/](../DATA/raw_data/) 下的 3 本 PDF（招标投标法、政府采购1200问、政策全文）；
- 格式：参考 `generate_test_sets.py` 已有的 5 种模板，扩展为 `law_clause / law_concept / law_definition / case_query / doc_lookup`；
- 评测：复用 `scripts/generate_three_core_report.py` 的字段召回率计算 + 引用溯源校验。

---

### 5.3 业务库语义镜像：从「冷启自举」到「自动维护」

#### 5.3.1 当前实现

`semantic.py` 实现了**MySQL 三表到 Milvus `mysql_price_semantic` 集合的语义镜像**：

| 维度 | 当前值 |
| --- | --- |
| 集合名称 | `mysql_price_semantic` |
| 文档数 | 77,597 条（= 38,911 + 17,742 + 1,805 + 字段文本化扩展） |
| Embedding 模型 | `bge-large-zh-v1.5`（SiliconFlow） |
| 索引 | IVF_FLAT, COSINE, nlist=256 |
| 自举 | `_ensure_mysql_semantic_collection()` 启动时检查 + 后台异步重建 |
| 检索 | Top-64 COSINE，按 source_table 分桶每表最多 24，阈值 0.3 |

#### 5.3.2 升级路径

- **Embedding 模型升级**：从 bge-large-zh-v1.5 → bge-m3；
  - 触发全量重建 `_rebuild_mysql_semantic_collection()`；
  - 重建期间 `mysql_price_semantic` 不可用 → 自动回退到无语义召回路径（仅 SQL）；
  - 重建耗时 ≈ 77,597 / 100 batch × 1s = 13 分钟（无 429 限流）；
- **索引参数升级**：nprobe 显式化（当前 Milvus 默认 8，建议 32，与 `public_kb` 对齐）；
- **增量同步**：当前只有全量重建，**未来扩展**可加增量接口（监听 MySQL binlog / 定时对比）；
- **schema 升级**：当前仅 `pk / source_table / source_id / text / vector` 5 字段（稠密 + 文本），未来可加 `sparse_vector`（与 public_kb 一致），但因 MySQL 文本字段本就结构化，**当前阶段**稀疏向量的边际收益待评估，**不强制**与 public_kb 对齐。

---

### 5.4 路由与意图层：保持骨架稳定，扩展二级路由

#### 5.4.1 路由骨架（无需改动）

```
用户问题
   ↓ agent/router.py（RouterDecision，LLM structured output）
router_intent ∈ {knowledge_qa, price_inquiry, general_chat, doc_qa, fallback}
   ↓
State['router_intent'] 注入
   ↓ graph.py 条件边分发
对应业务节点执行
```

#### 5.4.2 二级路由（已实现）

`price_inquiry/_SUB_ROUTE_MAP` 已经支持二级路由，无需扩展：

```python
_SUB_ROUTE_MAP = {
    "company_query": {"tables": ["company_info", "company_penalty"], "query_fn": "_query_company_data"},
    "bidding_query": {"tables": ["bid_project"], "query_fn": "_query_bidding_data"},
    "all":           {"tables": ["company_info", "company_penalty", "bid_project"], "query_fn": "_query_all_tables"},
}
```

子路由通过 `intent.py:_UNIFIED_INTENT_SYSTEM` 的 LLM 一次性输出 + `node.py:_get_query_fn` 路由。

#### 5.4.3 未来扩展：实体边界识别优化（针对本次测评 5 条失败）

测评报告 §9.2 已归类 5 条失败：

> 1. **实体名带注释/后缀（5 条）**：公司名含「（曾用名：…）」、项目编号含「（政府采购任务书编号）」/「号」等注释后缀时，意图解析提取到的实体串无法与库中字段精确匹配，触发空结果/统一引导。

**优化方向**：

```python
# intent.py 追加：实体名后处理规则
def _post_process_entity(entity: str, table: str) -> str:
    """实体名后处理：剥离括号注释、统一空格、处理尾部"号"后缀。
    
    用于处理 "中国移动安徽有限公司（曾用名：XX）" → "中国移动安徽有限公司"
    用于处理 "AH2024-001号" → "AH2024-001"（如果在 bid_project 表）
    """
    # 1. 去除括号注释（中英文括号）
    entity = re.sub(r"[\(（][^\)）]*[\)）]", "", entity).strip()
    # 2. 统一多空格
    entity = re.sub(r"\s+", "", entity)
    # 3. bid_project 表：剥离尾部"号"
    if table == "bid_project":
        entity = entity.rstrip("号").rstrip("、").strip()
    return entity
```

**预期收益**：5 条失败用例中的 4-5 条可修复，字段召回率从 99.464% → ≥99.7%。

#### 5.4.4 未来扩展：5 种固定格式的确定性快路径

**这是延迟优化的核心**——当前 node_elapsed 平均 1.76s 中 90%+ 是 LLM 意图解析。

**思路**：对 5 种固定问题模板（详见测评测试集）跳过 LLM，直接走规则化 SQL。

```python
# 新增 node.py:_fast_path_match(question: str) -> Optional[dict]
def _fast_path_match(question: str) -> Optional[dict]:
    """对 5 种固定格式匹配则跳过 LLM，直接构造 SearchIntent。
    
    格式示例：
    - "项目编号为 XXX 的项目中标情况怎样？"
    - "XXX 的中标历史？"
    - "查询 XXX 的工商信息。"
    - "查询 XXX 的经营范围。"
    - "查询 XXX 的不良记录/处罚记录。"
    """
    # 正则匹配 → 提取实体 → 构造 SearchIntent
    # ...
```

**预期收益**：命中快路径时跳过 LLM 意图解析 ~1.5s，**单条耗时从 2.76s → ~1.2s**。

**实施成本**：低，但要小心边界——只有"格式完全匹配"才走快路径，否则反而引入歧义。

#### 5.4.5 未来扩展：RouterDecision 新增 `data_source` 维度

仅作为远期项（v3.0 不实施）：

```python
DataSourceTarget = Literal["bidding", "company", "all"]

class RouterDecision(BaseModel):
    intent: RouterIntent
    data_source: DataSourceTarget = "all"  # 新增，默认 "all" 兜底
    reason: str
```

**实施时机**：未来业务域进一步细分（如新增 `supplier_query` 表组）时再启用。

---

## 6. 实施路径与里程碑

### 阶段一：DDL 与索引补齐（预计 1~2 天）

| 任务 | 产出 | 验证标准 |
| --- | --- | --- |
| 1.1 审查 `ztb_clean` 现有索引 | `index_baseline.md` | `SHOW INDEX` 输出 + 字段映射表 |
| 1.2 建 3 张表的 ngram FULLTEXT 索引 | DDL 执行回执 | `SHOW INDEX` 包含 `ft_*` FULLTEXT 项 |
| 1.3 验证 Level 1 FULLTEXT 走通 | 跑 10 条样本用例 | 日志无 `[FULLTEXT_MISSING]`；`winning_date` / `project_number` 等字段召回率提升 |
| 1.4 补 `company_info.company_name` BTREE 索引（如缺失） | DDL 执行回执 | `EXPLAIN` 显示索引命中 |

**阶段一风险**：
- 索引构建期间锁表（用 `ALGORITHM=INPLACE, LOCK=NONE` 缓解）
- 全文索引大小膨胀（ngram 分词增加存储 ≈ 30%）

### 阶段二：实体边界识别优化（预计 2~3 天）

| 任务 | 产出 | 验证标准 |
| --- | --- | --- |
| 2.1 实现 `_post_process_entity` 后处理函数 | `intent.py` 补丁 | 单元测试覆盖 4 种典型变体 |
| 2.2 实体边界识别测试集（20 条边界用例） | `test_entity_boundary.jsonl` | 边界用例 100% 命中 |
| 2.3 重跑 1500 条测评，对比激活前后 | `metrics_v3_entity.json` | 字段召回率 99.464% → ≥99.7% |
| 2.4 失败用例归因更新 | 测评报告 §9.2 修订 | 实体边界类失败从 5 条降到 ≤2 条 |

### 阶段三：public_kb 混合检索激活（预计 3~5 天，含 2h 停机）

| 任务 | 产出 | 验证标准 |
| --- | --- | --- |
| 3.1 备份 `public_kb` 旧 collection | 备份文件（>5 GB） | 备份校验通过 |
| 3.2 验证 SiliconFlow bge-m3 + bge-reranker-v2-m3 配额 | API 配额报告 | ≥1500 次/小时 配额 |
| 3.3 编写新 schema 重建脚本（按 [milvus_rebuild_feasibility_plan.md](./milvus_rebuild_feasibility_plan.md) §4.1） | `rebuild_public_kb.py` | 脚本可执行，可回滚 |
| 3.4 停机执行：drop → 新 schema → bge-m3 全量重建 → 验证 | 新 collection 就绪 | describe_collection 看到 `sparse_vector` |
| 3.5 新增 50-100 条法规测试集 | `testset_law_qa.jsonl` | 5 类法规问题模板 |
| 3.6 跑法规测评，记录激活前后指标 | `metrics_public_kb.json` | Reranker 命中 / 动态阈值触发次数等 |
| 3.7 灰度切换 + 监控（2 天观察期） | 灰度报告 | 无 P0/P1 故障 |

**阶段三风险**：
- 🔴 2h 停机窗口；
- 🔴 重建后 `chunk_uid` 变化导致存量引用溯源失效（详见 [milvus_rebuild_feasibility_plan.md §5.2](./milvus_rebuild_feasibility_plan.md)）；
- 🟡 bge-m3 SiliconFlow API 调用限流（语义.py 已有 6 次重试 + 指数退避）。

### 阶段四：Embedding 升级 + 性能优化（预计 2~3 天）

| 任务 | 产出 | 验证标准 |
| --- | --- | --- |
| 4.1 bge-m3 Embedding 升级 + 全量重建 `mysql_price_semantic` | 新语义镜像 | 行数 ≥ 77,597 |
| 4.2 确定性格快路径实现 + 兼容性测试 | `node.py` 补丁 + 测试 | 5 种固定格式命中快路径，avg 耗时 ≤1.5s |
| 4.3 重跑 1500 条测评，端到端性能对比 | `metrics_v3_perf.json` | avg ≤1.5s、P99 ≤2.5s |
| 4.4 输出 v3.0 完整测评报告 | `evaluation_report_v3.md` | 含 v2.0 → v3.0 对比 |

### 阶段五：清理与文档化（预计 1 天）

| 任务 | 产出 |
| --- | --- |
| 5.1 清理临时脚本（`_smoke_test*.py` / `_env_check.py` 等） | 工作区干净 |
| 5.2 更新 [project_overview.md](./project_overview.md) | 反映 v3.0 架构 |
| 5.3 输出实施报告（停机时长 / 重建耗时 / 前后指标对比 / 失败用例归因） | `docs/v3_implementation_report.md` |

**总周期预估**：10-15 天（含 2h 停机 + 灰度期）。

---

## 7. 技术栈与工具清单

### 7.1 已就绪（无需新增）

| 工具 | 用途 | 备注 |
| --- | --- | --- |
| **Python 3.12.13** | 全栈运行环境 | `.conda` |
| **pymysql 2.2.8** | MySQL 客户端 | 已用连接池（db.py） |
| **pymilvus 3.0.1** | Milvus 客户端 | `public_kb/milvus_store.py` |
| **langgraph / langchain** | Agent 框架 | router.py + graph.py |
| **deepseek-chat** | LLM | temperature=0，60s 超时 |
| **bge-large-zh-v1.5**（当前）/ **bge-m3**（计划） | Embedding | SiliconFlow API |
| **bge-reranker-v2-m3** | Reranker | 激活条件：schema 含 sparse_vector |
| **Docker MySQL 8.0** | 业务库 | `docker/mysql/` |
| **Milvus 2.4** | 向量库 | 旧本地 / 新云端 8.130.174.43 |

### 7.2 新增 / 复用脚本

| 脚本 | 路径 | 用途 |
| --- | --- | --- |
| `_db_index_baseline.py`（新建） | `scripts/` | 跑 SHOW INDEX 输出对比基线 |
| `_rebuild_public_kb_v3.py`（新建） | `scripts/` | 双向量 schema 重建脚本 |
| `generate_law_testset.py`（新建） | `scripts/` | 法规问答测试集生成（参考 `generate_test_sets.py`） |
| `run_three_core_evaluation.py`（复用） | `scripts/` | 跑测主入口，断点续跑 |
| `generate_three_core_report.py`（复用） | `scripts/` | 指标计算 + HTML/Markdown 报告 |
| `_verify_fails.py`（复用） | `scripts/` | 失败用例源数据核对 |

### 7.3 测试覆盖

| 测试类型 | 工具 | 覆盖 |
| --- | --- | --- |
| 路由意图分类 | `test/test_router.py`（复用） | 5 种 query_type × N 条 |
| SQL 构建 | `test/test_sql_builders.py`（复用） | 边界条件 |
| 字段召回率 | 1500 条测评 | 已有 |
| 边界用例 | 20 条人工标注 | 新增 |
| 法规召回 | 50-100 条法规问答 | 新增 |

---

## 8. 风险与缓解措施

| 风险 | 级别 | 影响 | 缓解措施 |
| --- | --- | --- | --- |
| **public_kb 重建期间服务中断** | 🔴 高 | 法规问答 2h 不可用 | ① 提前2h 公告 ② 备份旧 collection ③ 重建失败回滚脚本 |
| **chunk_uid 变更导致引用失效** | 🟡 中 | 引用溯源校验失败 | ① 重建时同步生成新 chunk_uid 映射表 ② 临时关闭 R1/R2 严格校验 ③ 历史引用标记 "已迁移" |
| **bge-m3 API 限流** | 🟡 中 | 重建失败 / 超时 | ① 复用 `semantic.py:_embed_semantic_batch` 的 6 次重试 + 指数退避 ② 准备 bge-large-zh-v1.5 降级方案 |
| **ngram FULLTEXT 索引构建失败** | 🟡 中 | Level 1 检索仍降级 LIKE | ① DDL 备份（无副作用 ALTER）② 失败 DROP INDEX 回滚 ③ 不影响 Level 2/3 兜底 |
| **5 种快路径引入歧义** | 🟢 低 | 误命中导致 SQL 错 | ① 严格正则匹配 ② 模糊匹配仍走 LLM ③ 1000 条样本回归 |
| **mysql_price_semantic 全量重建期间语义召回不可用** | 🟡 中 | 测评字段召回率短暂下降 | ① 重建期间 `_semantic_recall_candidates` 返回空，自动走纯 SQL 路径 ② 报告 §7.2 SQL 兜底仍能拿到 80%+ 字段 |
| **`_post_process_entity` 误剥离有效字符** | 🟢 低 | 实体名残缺导致 SQL 错 | ① 仅剥离括号注释 + 尾部"号"，其他不动 ② 单元测试 100% 覆盖 |
| **整体升级回退复杂度** | 🟡 中 | 升级失败需回滚 | ① DDL 都是 ADD，drop 即可回滚 ② `chunk_max_chars=400` 旧值保留在 `.env` 备份 ③ 所有改动通过 feature flag 开关 |

---

## 附录：关键代码路径速查

| 想了解... | 看这里 |
| --- | --- |
| **MySQL 连接池与 Settings 单例** | [agent/nodes/price_inquiry/db.py](../agent/nodes/price_inquiry/db.py) L22-L100 |
| **字段分类硬编码** | [agent/nodes/price_inquiry/schema.py](../agent/nodes/price_inquiry/schema.py) L3-L38 |
| **二级路由表 + 子路由函数** | [agent/nodes/price_inquiry/node.py](../agent/nodes/price_inquiry/node.py) L31-L44 |
| **统一意图解析 prompt** | [agent/nodes/price_inquiry/intent.py](../agent/nodes/price_inquiry/intent.py) L40-L115 |
| **SQL 构建器族** | [agent/nodes/price_inquiry/sql_builders.py](../agent/nodes/price_inquiry/sql_builders.py) |
| **多级降级召回链** | [agent/nodes/price_inquiry/recall.py](../agent/nodes/price_inquiry/recall.py) L181-L285 |
| **混合重排序** | [agent/nodes/price_inquiry/recall.py](../agent/nodes/price_inquiry/recall.py) L64-L89 |
| **MySQL 语义镜像构建** | [agent/nodes/price_inquiry/semantic.py](../agent/nodes/price_inquiry/semantic.py) |
| **public_kb Milvus schema + 增量** | [public_kb/milvus_store.py](../public_kb/milvus_store.py) L44-L149 |
| **public_kb 混合检索 + Reranker** | [public_kb/qa_chain.py](../public_kb/qa_chain.py) L304-L405 |
| **public_kb 动态阈值策略** | [public_kb/qa_chain.py](../public_kb/qa_chain.py) L537-L549 |
| **public_kb 切片器** | [public_kb/chunker.py](../public_kb/chunker.py) |
| **LangGraph 路由决策模型** | [agent/router.py](../agent/router.py) L29-L41 |
| **Graph 构建** | [agent/graph.py](../agent/graph.py) L108-L181 |
| **1500 条测评主入口** | [scripts/run_three_core_evaluation.py](../scripts/run_three_core_evaluation.py) |
| **测评指标计算 + 报告** | [scripts/generate_three_core_report.py](../scripts/generate_three_core_report.py) |
| **本次测评报告** | [test_report/evaluation_report.md](../test_report/evaluation_report.md) |
| **public_kb 双向量 + BM25 + 精排方案** | [milvus_rebuild_feasibility_plan.md](./milvus_rebuild_feasibility_plan.md) |
| **3 本政策 PDF 原文** | [DATA/raw_data/](../DATA/raw_data/) |
| **1500 条测试集生成脚本** | [generate_test_sets.py](../generate_test_sets.py) |

---

> **结论**：v3.0 升级方案以本次 1500 条全流程测评的量化指标（99.464% / 99.533% / 2.76s）为基线，聚焦三大瓶颈——**MySQL FULLTEXT 缺失**、**public_kb 混合检索未激活**、**实体边界识别错误**——通过 4 阶段渐进式升级（DDL → 实体优化 → public_kb 重建 → Embedding 升级 + 快路径）实现字段召回率 ≥99.7%、准确率 ≥99.8%、平均耗时 ≤1.5s 的目标。所有改动基于代码事实（`db.py / schema.py / qa_chain.py / milvus_store.py` 等），与 LangGraph 骨架解耦，回滚成本可控。