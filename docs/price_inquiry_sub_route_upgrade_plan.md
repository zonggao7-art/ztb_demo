# 智能询价节点二级路由升级方案

> 版本：v2.2（LLM 调用合并性能优化版）  
> 日期：2026-08-07  
> 关联代码：[agent/nodes/price_inquiry.py](../agent/nodes/price_inquiry.py) | [agent/router.py](../agent/router.py) | [agent/state.py](../agent/state.py) | [agent/graph.py](../agent/graph.py) | [agent/nodes/output_templates.py](../agent/nodes/output_templates.py)（新建）  
> 前置文档：[project_overview.md](./project_overview.md) | [ml_field_selection_feasibility_report.md](./ml_field_selection_feasibility_report.md)

---

## 目录

1. [改造背景与目标](#1-改造背景与目标)
2. [改造思路与整体架构](#2-改造思路与整体架构)
3. [技术栈选择](#3-技术栈选择)
4. [数据库存储方案评估](#4-数据库存储方案评估)
5. [数据源分析：四个 CSV 文件概览](#5-数据源分析四个-csv-文件概览)
6. [数据导入与索引策略](#6-数据导入与索引策略)
7. [三个二级路由的详细实现方案](#7-三个二级路由的详细实现方案)
   - 7.1 [路由一：公司信息查询](#71-路由一公司信息查询-company_query)
   - 7.2 [路由二：产品信息查询](#72-路由二产品信息查询-product_query)
   - 7.3 [路由三：招投标历史交易查询](#73-路由三招投标历史交易查询-bidding_query)
   - 7.4 [统一输出字段框架](#74-统一输出字段框架)
8. [检索逻辑的 SQL 实现细节](#8-检索逻辑的-sql-实现细节)
9. [数据维护与更新接口设计](#9-数据维护与更新接口设计)
10. [实施步骤与风险](#10-实施步骤与风险)
11. [附录：现有代码改动清单](#11-附录现有代码改动清单)

---

## 1. 改造背景与目标

### 1.1 现状

当前系统在 [agent/nodes/price_inquiry.py](../agent/nodes/price_inquiry.py) 中实现了一个**单一的价格查询节点**，覆盖的检索场景为"历史中标记录查询"。其核心机制为：

- **一级路由**：`RouterIntent` 中仅有一个 `price_inquiry` 枚举值，所有结构化数据查询全部落入该分支
- **数据源**：硬编码遍历若干旧 MySQL 数据库，跨库全扫描所有非空表
- **检索策略**：三阶段（LLM 意图抽取 → SQL 硬过滤 + FULLTEXT → Python 混合重排序）
- **局限**：无法区分"查公司""查产品""查中标记录"等不同业务场景；数据源陈旧且混杂，缺乏按业务场景分类的精准检索能力

### 1.2 升级目标

在 `price_inquiry` 节点内部增设 **三个二级意图路由**，分别面向不同的数据表和业务场景：

| 二级路由 | 标识符 | 数据来源 | 典型场景 |
|---------|--------|---------|---------|
| 公司信息查询 | `company_query` | `raw_tables/company_info.csv` + `raw_tables/company_penalty.csv` | 供应商推荐、企业资质查询、不良记录核查 |
| 产品信息查询 | `product_query` | `raw_tables/product_info.csv` | 产品行情价查询、供应商筛选、联系人查找 |
| 招投标历史交易查询 | `bidding_query` | `raw_tables/bid_project.csv` | 历史招标/投标/中标记录查询、金额统计 |

**核心指标**：

| 指标 | 升级前 | 升级后 |
|------|--------|--------|
| 二级路由粒度 | 无（全部混在一起） | 3 个精准二级路由 |
| 单次查询扫描的数据表 | 多库 × N 表（含无关表） | 1~2 张精准表 |
| 数据覆盖 | 旧库历史数据 | 全新 CSV 三类业务数据全覆盖 |
| SQL 执行数 | 数十条（跨多库） | 1~4 条 |
| 候选集噪声率 | 高（公司/产品/招标混杂） | 低（同质数据） |

---

## 2. 改造思路与整体架构

### 2.1 设计原则

1. **最小侵入**：不修改 `RouterIntent` 枚举，不修改 `AgentState` 定义，不修改 Graph 条件边结构
2. **二级路由内聚**：在 `price_inquiry` 节点内部完成"二级意图识别 → 数据源选择 → 检索执行"的全链路，对外仍表现为单一 `price_inquiry` 节点
3. **纯净新库**：所有数据来源于 `raw_tables/` 目录下的四个 CSV 文件，导入一个全新的独立 MySQL 数据库，不依赖任何旧数据库
4. **复用现有三阶段架构**：LLM 意图抽取 → SQL 硬过滤 → 混合重排序的核心管线不变，仅扩展意图 schema 和数据源映射

### 2.2 架构演进对比

**升级前（当前）**：

```
Router (LLM)
  ├── knowledge_qa → Milvus RAG
  ├── price_inquiry → 遍历旧数据库全扫描（公司/产品/招标混杂）
  ├── general_chat  → LLM
  ├── doc_qa        → 占位
  └── fallback      → 兜底
```

**升级后**：

```
Router (LLM)                              ← 一级路由：不变
  ├── knowledge_qa → Milvus RAG
  ├── price_inquiry ─────────────────┐    ← 一级路由：不变
  │   ├── [二级路由] company_query   │    ← 新增：公司信息检索
  │   │     └── ztb_clean.company_info 表
  │   │     └── ztb_clean.company_penalty 表
  │   ├── [二级路由] product_query   │    ← 新增：产品信息检索
  │   │     └── ztb_clean.product_info 表
  │   ├── [二级路由] bidding_query   │    ← 新增：招投标交易检索
  │   │     └── ztb_clean.bid_project 表
  │   └── [兜底]    all              │    ← 兜底：遍历全部 4 张新表
  ├── general_chat  → LLM
  ├── doc_qa        → 占位
  └── fallback      → 兜底
```

### 2.3 关键设计决策：为何采用"二级路由内聚"而非"新增 3 个一级节点"

| 维度 | 方案 A：新增 3 个一级 RouterIntent | 方案 B（推荐）：二级路由内聚 |
|------|----------------------------------|---------------------------|
| RouterIntent 改动 | 新增 3 个枚举值 | **不变** |
| Graph 条件边改动 | 新增 3 条边 | **不变** |
| State 定义改动 | 不变 | **不变** |
| Tool Calling 回退 tools | 新增 3 个 tool | **不变** |
| 路由提示词改动 | 需扩展一级意图分类规则 | 仅扩展二级意图（在 price_inquiry 内部处理） |
| 一级路由准确率影响 | 可能降低（8 分类比 5 分类更难） | 无影响（仍为 5 分类） |
| 代码组织 | 3 个独立节点文件 | 1 个文件内按子路由分模块（高内聚） |
| 扩展性 | 未来每增加一个数据源需新增枚举值 | 在 `_SUB_ROUTE_MAP` 中新增条目即可 |

**结论**：选择方案 B。理由如下——

1. 三个二级路由的数据检索逻辑高度同构（都是 SQL 硬过滤 + FULLTEXT + 混合排序），抽象在同一节点内可最大化代码复用
2. 将"区分公司/产品/招标"的意图分类下沉到 `price_inquiry` 内部，避免一级路由过于臃肿
3. 完全符合 Agent 骨架"可插拔"设计原则——新增业务能力不改 State 和 Graph 核心结构

### 2.4 核心改动点概览

```
agent/
├── router.py                ← 修改：RouterDecision 新增 sub_intent 字段 + 提示词微调
├── state.py                 ← 不变
├── graph.py                 ← 不变
├── nodes/
│   ├── price_inquiry.py     ← 核心改造：
│   │   ├── 新增 _CLEAN_DB 配置（ztb_clean 数据库连接）
│   │   ├── 新增 _SUB_ROUTE_MAP（二级路由 → 表名映射）
│   │   ├── 新增 _UNIFIED_INTENT_PROMPT（合并二级路由分类 + 结构化过滤条件抽取，单次 LLM 调用）
│   │   ├── 新增 _query_company_data() / _query_product_data() / _query_bidding_data()
│   │   ├── 改造 node_price_inquiry() 入口（先分类再分发）
│   │   └── 新增 _query_all_tables() 作为 all 兜底（遍历全部 4 张新表）
```

---

## 3. 技术栈选择

### 3.1 沿用现有技术栈（零新增依赖）

| 技术 | 版本 | 用途 | 选型理由 |
|------|------|------|---------|
| **Python** | 3.12 | 运行环境 | 项目已有，Anaconda 管理 |
| **LangChain Core** | ≥0.3.37 | Prompt 模板、输出解析 | 项目已有，`ChatPromptTemplate` + `StrOutputParser` 用于意图抽取 |
| **LangChain OpenAI** | ≥1.0.0 | LLM API 封装 | 项目已有，DeepSeek `deepseek-chat` 模型 |
| **LangGraph** | ≥1.2.0 | StateGraph Agent 骨架 | 项目已有，节点注册 + 条件路由 |
| **pymysql** | ≥1.1.0 | MySQL 连接驱动 | 项目已有，连接 `192.168.10.120:3306` |
| **pydantic** | ≥2.0 | 数据模型定义 | 项目已有，`RouterDecision` 和新增 `SubIntent` 的结构化输出 |
| **python-dotenv** | ≥1.0.0 | 环境变量管理 | 项目已有，`.env` 配置数据库连接参数 |

### 3.2 新增数据源

| 数据源 | 格式 | 说明 |
|--------|------|------|
| `raw_tables/company_info.csv` | CSV (UTF-8) | 企业信息表，~20MB，含企业名称、法人、注册资本、经营范围等 |
| `raw_tables/company_penalty.csv` | CSV (UTF-8) | 企业处罚表，~338KB，含违法行为、处罚结果等 |
| `raw_tables/product_info.csv` | CSV (UTF-8) | 产品信息表，~6.6MB，含产品名称、供应商、价格、联系人等 |
| `raw_tables/bid_project.csv` | CSV (UTF-8) | 招标项目表，~6.4MB，含项目名称、采购人、中标供应商、金额等 |

### 3.3 LLM 模型配置

| 用途 | 模型 | Temperature | 说明 |
|------|------|-------------|------|
| 一级意图路由 | `deepseek-chat` | 0.0 | 已有，不变 |
| 意图结构化抽取（合并二级路由） | `deepseek-chat` | 0.0 | 新增：统一 Prompt 一次完成 sub_route 判断 + hard_filters 抽取，替代原"二级分类 + 三路 Prompt"两次调用 |
| 结果格式化回答 | `deepseek-chat` | 0.0~0.3 | 已有，不变（各节点可选） |

---

## 4. 数据库存储方案评估

### 4.1 方案定义

本次升级的数据库**完全独立于任何旧数据库**，新建一个纯净的 MySQL 数据库 `ztb_clean`，仅存储从四个 CSV 文件导入的数据。下面评估两种组织方式：

#### 方案 A：单库多表（推荐）

所有数据存入同一个数据库 `ztb_clean`，按业务路由分表存储：

```
ztb_clean/                     # 全新纯净数据库
├── company_info               # 企业工商信息（company_query 路由）
├── company_penalty            # 企业处罚信息（company_query 路由）
├── product_info               # 产品市场行情（product_query 路由）
└── bid_project                # 招标项目交易记录（bidding_query 路由）
```

#### 方案 B：三个独立数据库

```
ztb_company/                   # 公司信息库
├── company_info
└── company_penalty

ztb_product/                   # 产品信息库
└── product_info

ztb_bidding/                   # 招投标交易库
└── bid_project
```

### 4.2 多维对比分析

| 维度 | 方案 A：单库多表 | 方案 B：三个独立库 | 优胜 |
|------|-----------------|-------------------|------|
| **查询性能** | 单连接复用，无跨库开销；单次查询 1~2 张表，WHERE + FULLTEXT 精准命中 | 需维护 3 个 pymysql 连接，跨业务查询（如"某公司中标过什么项目"）需两次查询后 Python 合并 | ✅ A |
| **数据维护便利性** | 一个 `mysqldump` 备份全部；一个 Alembic 迁移链管理所有表 | 3 个独立的备份/恢复流程；3 套 Alembic 配置 | ✅ A |
| **扩展性** | 新增数据表只需在 `ztb_clean` 内 `CREATE TABLE`，路由映射加一行 | 新增数据源需新建数据库 + 新连接配置 + 新路由条目 | ✅ A |
| **纯净性与独立性** | 一个全新的数据库，完全独立于任何旧系统，无历史包袱 | 三个全新的数据库，同样独立，但管理分散 | ✅ A |
| **未来数据扩展** | 后续如需新增业务数据（如政策法规表），直接在 `ztb_clean` 内加表即可，无需新增数据库连接 | 需评估新数据应归入哪个库，或再新建第四个库 | ✅ A |
| **权限管理** | 单库内表级权限（`GRANT SELECT ON ztb_clean.company_info TO ...`） | 3 个库级权限 | 持平 |
| **索引构建 I/O** | 单库集中建索引，I/O 压力集中（可分批串行缓解） | 3 库分别建索引，I/O 分散 | ✅ B（但可缓解） |
| **单点故障风险** | `ztb_clean` 故障 → 所有结构化检索不可用 | 单库故障仅影响对应业务 | ✅ B（但可主从复制缓解） |
| **代码复杂度** | 单连接 + 表名过滤，代码简洁 | 3 连接管理 + 连接池，代码略复杂 | ✅ A |

### 4.3 推荐方案：方案 A（单库多表 `ztb_clean`）

**推荐理由**：

1. **纯净独立**：`ztb_clean` 是一个全新数据库，与任何旧系统零依赖，数据仅来自四个 CSV 文件，架构清晰、无历史包袱
2. **跨业务关联查询天然支持**：例如 "福州怡富电梯有限公司代理过中标金额最大的项目是哪个？" —— 需要在 `company_info` 和 `bid_project` 之间做关联查询。单库内可直接 JOIN 或分步查询，方案 B 则需要两次跨库查询再 Python 合并
3. **数据量可控**：4 张表的数据量均不大（CSV 总大小约 33MB），FULLTEXT 索引构建时间可控（预计 <5 分钟），远未到需要拆库分散 I/O 的量级
4. **运维简单**：一个数据库连接、一套备份策略、一个 Alembic 迁移链，运维成本远低于维护 3 个独立库
5. **单点故障可通过主从复制缓解**：MySQL 主从复制是成熟方案，成本远低于维护 3 个独立库的运维复杂度

**数据库命名**：`ztb_clean` —— "clean" 强调该库的纯净性：仅含 CSV 导入数据，不与任何旧数据库共享实例或混存数据。

**实施要点**：

- 4 张表之间按业务路由完全隔离：`company_query` 仅查 `company_info` + `company_penalty`，`product_query` 仅查 `product_info`，`bidding_query` 仅查 `bid_project`
- 索引构建顺序：先建 BTREE（加速去重），数据写入完成后统一建 FULLTEXT，分批串行执行
- `all` 兜底模式遍历全部 4 张表，确保在意图分类不确定时仍能覆盖所有数据

---

## 5. 数据源分析：四个 CSV 文件概览

### 5.1 `raw_tables/company_info.csv` — 企业信息表

| 字段名 | 中文含义 | 数据类型 | 检索角色 |
|--------|---------|---------|---------|
| `company_name` | 企业名称 | VARCHAR(256) | **语义检索**（FULLTEXT）+ 精确匹配 |
| `legal_person` | 法定代表人 | VARCHAR(128) | 精确匹配 |
| `registered_capital` | 注册资本 | VARCHAR(64) | 范围过滤（需解析数值） |
| `establish_date` | 成立日期 | DATE | 时间过滤 |
| `business_status` | 经营状态 | VARCHAR(64) | 硬过滤（存续/注销等） |
| `province` | 省份 | VARCHAR(64) | **硬过滤**（BTREE） |
| `city` | 城市 | VARCHAR(64) | **硬过滤**（BTREE） |
| `district` | 区县 | VARCHAR(64) | 硬过滤 |
| `industry` | 所属行业 | VARCHAR(128) | **硬过滤**（BTREE）+ 语义匹配 |
| `company_type` | 企业类型 | VARCHAR(64) | 硬过滤 |
| `credit_code` | 统一社会信用代码 | VARCHAR(64) | **精确匹配**（UNIQUE KEY） |
| `address` | 企业地址 | VARCHAR(512) | 语义检索 |
| `credit_rating` | 信用评级 | VARCHAR(64) | 硬过滤 |
| `company_level` | 企业等级 | VARCHAR(64) | 硬过滤（如"中型企业"） |
| `business_scope` | 经营范围 | TEXT | **语义检索**（FULLTEXT） |
| `source_file` | 来源文件 | VARCHAR(256) | 溯源 |

### 5.2 `raw_tables/company_penalty.csv` — 企业处罚表

| 字段名 | 中文含义 | 数据类型 | 检索角色 |
|--------|---------|---------|---------|
| `company_name` | 企业名称 | VARCHAR(256) | **语义检索** + 精确匹配 |
| `credit_code` | 统一社会信用代码 | VARCHAR(64) | 精确匹配（关联 company_info） |
| `penalty_date` | 处罚日期 | DATE | 时间过滤 |
| `law_enforcement_unit` | 执法单位 | VARCHAR(256) | 语义检索 |
| `illegal_behavior` | 违法行为 | TEXT | **语义检索**（FULLTEXT） |
| `penalty_result` | 处罚结果 | TEXT | 语义检索 |
| `source_file` | 来源文件 | VARCHAR(256) | 溯源 |

### 5.3 `raw_tables/product_info.csv` — 产品信息表

| 字段名 | 中文含义 | 数据类型 | 检索角色 |
|--------|---------|---------|---------|
| `product_name` | 产品名称 | VARCHAR(500) | **语义检索**（FULLTEXT） |
| `supplier_name` | 供应商名称 | VARCHAR(256) | **语义检索**（FULLTEXT）+ 精确匹配 |
| `price` | 报价（元） | DECIMAL(20,2) | **范围过滤**（BTREE） |
| `price_unit` | 单位 | VARCHAR(32) | 展示字段 |
| `currency` | 货币 | VARCHAR(16) | 硬过滤（CNY/USD） |
| `category` | 产品大类 | VARCHAR(256) | **硬过滤**（BTREE） |
| `subcategory` | 产品小类 | VARCHAR(256) | 硬过滤 |
| `product_parameters` | 产品参数 | TEXT | **语义检索**（FULLTEXT） |
| `min_order_qty` | 起订量 | INT | 展示字段 |
| `province` | 省份 | VARCHAR(64) | **硬过滤**（BTREE） |
| `city` | 城市 | VARCHAR(64) | 硬过滤 |
| `supplier_address` | 供应商地址 | VARCHAR(512) | 展示字段 |
| `contact_person` | 联系人 | VARCHAR(64) | **精确匹配** + 展示 |
| `contact_info` | 联系方式 | VARCHAR(128) | 展示字段（电话） |
| `source_file` | 来源文件 | VARCHAR(256) | 溯源 |

### 5.4 `raw_tables/bid_project.csv` — 招标项目表

| 字段名 | 中文含义 | 数据类型 | 检索角色 |
|--------|---------|---------|---------|
| `project_number` | 项目编号 | VARCHAR(128) | **精确匹配**（exact token） |
| `project_name` | 项目名称 | VARCHAR(500) | **语义检索**（FULLTEXT） |
| `purchaser` | 采购人/招标单位 | VARCHAR(256) | **硬过滤**（BTREE）+ 语义检索 |
| `agent` | 代理机构 | VARCHAR(256) | 语义检索 |
| `budget_amount` | 预算金额（元） | DECIMAL(20,2) | 范围过滤（BTREE） |
| `winning_amount` | 中标金额（元） | DECIMAL(20,2) | **范围过滤**（BTREE）+ 聚合统计 |
| `successful_bidder` | 中标供应商 | VARCHAR(500) | **语义检索**（FULLTEXT）+ 硬过滤 |
| `winning_date` | 中标日期 | DATE | **时间过滤**（BTREE） |
| `subject_matter` | 标的物 | VARCHAR(500) | **语义检索**（FULLTEXT） |
| `province` | 省份 | VARCHAR(64) | **硬过滤**（BTREE） |
| `city` | 城市 | VARCHAR(64) | 硬过滤 |
| `district` | 区县 | VARCHAR(64) | 硬过滤 |
| `project_category` | 项目类别 | VARCHAR(128) | 硬过滤（政府采购/工程建设等） |
| `project_stage` | 项目阶段 | VARCHAR(64) | **硬过滤**（招标公告/结果公告/更正公告等） |
| `publish_date` | 发布日期 | DATE | 时间过滤 |
| `source_file` | 来源文件 | VARCHAR(256) | 溯源 |
| `source_url` | 来源链接 | VARCHAR(1024) | 溯源（可点击） |

---

## 6. 数据导入与索引策略

### 6.1 MySQL 表结构设计

#### 6.1.1 `company_info`（企业信息表）

```sql
CREATE TABLE `company_info` (
    `id`                 BIGINT AUTO_INCREMENT PRIMARY KEY,
    `company_name`       VARCHAR(256)  NOT NULL COMMENT '企业名称',
    `legal_person`       VARCHAR(128)  DEFAULT NULL COMMENT '法定代表人',
    `registered_capital` VARCHAR(64)   DEFAULT NULL COMMENT '注册资本',
    `establish_date`     DATE          DEFAULT NULL COMMENT '成立日期',
    `business_status`    VARCHAR(64)   DEFAULT NULL COMMENT '经营状态',
    `province`           VARCHAR(64)   DEFAULT NULL COMMENT '省份',
    `city`               VARCHAR(64)   DEFAULT NULL COMMENT '城市',
    `district`           VARCHAR(64)   DEFAULT NULL COMMENT '区县',
    `industry`           VARCHAR(128)  DEFAULT NULL COMMENT '所属行业',
    `company_type`       VARCHAR(64)   DEFAULT NULL COMMENT '企业类型',
    `credit_code`        VARCHAR(64)   DEFAULT NULL COMMENT '统一社会信用代码（主去重键）',
    `address`            VARCHAR(512)  DEFAULT NULL COMMENT '企业地址',
    `credit_rating`      VARCHAR(64)   DEFAULT NULL COMMENT '信用评级',
    `company_level`      VARCHAR(64)   DEFAULT NULL COMMENT '企业等级',
    `business_scope`     TEXT          DEFAULT NULL COMMENT '经营范围',
    `source_file`        VARCHAR(256)  DEFAULT NULL COMMENT '来源文件名',
    `created_at`         DATETIME      DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY `uk_credit_code` (`credit_code`),
    INDEX `idx_province` (`province`),
    INDEX `idx_city` (`city`),
    INDEX `idx_industry` (`industry`),
    INDEX `idx_company_level` (`company_level`),
    INDEX `idx_business_status` (`business_status`),
    FULLTEXT INDEX `ft_company_info` (`company_name`, `business_scope`, `industry`, `address`) WITH PARSER ngram
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='企业工商信息表 — 来源：raw_tables/company_info.csv';
```

#### 6.1.2 `company_penalty`（企业处罚信息表）

```sql
CREATE TABLE `company_penalty` (
    `id`                    BIGINT AUTO_INCREMENT PRIMARY KEY,
    `company_name`          VARCHAR(256)  NOT NULL COMMENT '企业名称',
    `credit_code`           VARCHAR(64)   DEFAULT NULL COMMENT '统一社会信用代码（关联 company_info）',
    `penalty_date`          DATE          DEFAULT NULL COMMENT '处罚日期',
    `law_enforcement_unit`  VARCHAR(256)  DEFAULT NULL COMMENT '执法单位',
    `illegal_behavior`      TEXT          DEFAULT NULL COMMENT '违法行为',
    `penalty_result`        TEXT          DEFAULT NULL COMMENT '处罚结果',
    `source_file`           VARCHAR(256)  DEFAULT NULL COMMENT '来源文件名',
    `created_at`            DATETIME      DEFAULT CURRENT_TIMESTAMP,
    INDEX `idx_company_name` (`company_name`),
    INDEX `idx_credit_code` (`credit_code`),
    INDEX `idx_penalty_date` (`penalty_date`),
    FULLTEXT INDEX `ft_penalty` (`company_name`, `illegal_behavior`, `penalty_result`) WITH PARSER ngram
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='企业处罚信息表 — 来源：raw_tables/company_penalty.csv';
```

#### 6.1.3 `product_info`（产品信息表）

```sql
CREATE TABLE `product_info` (
    `id`                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    `product_name`        VARCHAR(500)  DEFAULT NULL COMMENT '产品名称',
    `supplier_name`       VARCHAR(256)  DEFAULT NULL COMMENT '供应商名称',
    `price`               DECIMAL(20,2) DEFAULT NULL COMMENT '报价（元）',
    `price_unit`          VARCHAR(32)   DEFAULT NULL COMMENT '计价单位',
    `currency`            VARCHAR(16)   DEFAULT 'CNY' COMMENT '货币',
    `category`            VARCHAR(256)  DEFAULT NULL COMMENT '产品大类',
    `subcategory`         VARCHAR(256)  DEFAULT NULL COMMENT '产品小类',
    `product_parameters`  TEXT          DEFAULT NULL COMMENT '产品参数',
    `min_order_qty`       INT           DEFAULT NULL COMMENT '起订量',
    `province`            VARCHAR(64)   DEFAULT NULL COMMENT '省份',
    `city`                VARCHAR(64)   DEFAULT NULL COMMENT '城市',
    `supplier_address`    VARCHAR(512)  DEFAULT NULL COMMENT '供应商地址',
    `contact_person`      VARCHAR(64)   DEFAULT NULL COMMENT '联系人',
    `contact_info`        VARCHAR(128)  DEFAULT NULL COMMENT '联系方式（电话）',
    `source_file`         VARCHAR(256)  DEFAULT NULL COMMENT '来源文件名',
    `created_at`          DATETIME      DEFAULT CURRENT_TIMESTAMP,
    INDEX `idx_product_name` (`product_name`),
    INDEX `idx_supplier_name` (`supplier_name`),
    INDEX `idx_price` (`price`),
    INDEX `idx_category` (`category`),
    INDEX `idx_province` (`province`),
    FULLTEXT INDEX `ft_product` (`product_name`, `supplier_name`, `product_parameters`, `category`) WITH PARSER ngram
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='产品市场行情表 — 来源：raw_tables/product_info.csv';
```

#### 6.1.4 `bid_project`（招标项目表）

```sql
CREATE TABLE `bid_project` (
    `id`                 BIGINT AUTO_INCREMENT PRIMARY KEY,
    `project_number`     VARCHAR(128)  DEFAULT NULL COMMENT '项目编号',
    `project_name`       VARCHAR(500)  DEFAULT NULL COMMENT '项目名称',
    `purchaser`          VARCHAR(256)  DEFAULT NULL COMMENT '采购人/招标单位',
    `agent`              VARCHAR(256)  DEFAULT NULL COMMENT '代理机构',
    `budget_amount`      DECIMAL(20,2) DEFAULT NULL COMMENT '预算金额（元）',
    `winning_amount`     DECIMAL(20,2) DEFAULT NULL COMMENT '中标金额（元）',
    `successful_bidder`  VARCHAR(500)  DEFAULT NULL COMMENT '中标供应商',
    `winning_date`       DATE          DEFAULT NULL COMMENT '中标日期',
    `subject_matter`     VARCHAR(500)  DEFAULT NULL COMMENT '标的物',
    `province`           VARCHAR(64)   DEFAULT NULL COMMENT '省份',
    `city`               VARCHAR(64)   DEFAULT NULL COMMENT '城市',
    `district`           VARCHAR(64)   DEFAULT NULL COMMENT '区县',
    `project_category`   VARCHAR(128)  DEFAULT NULL COMMENT '项目类别',
    `project_stage`      VARCHAR(64)   DEFAULT NULL COMMENT '项目阶段（结果公告/招标公告/更正公告等）',
    `publish_date`       DATE          DEFAULT NULL COMMENT '发布日期',
    `source_file`        VARCHAR(256)  DEFAULT NULL COMMENT '来源文件名',
    `source_url`         VARCHAR(1024) DEFAULT NULL COMMENT '来源链接',
    `created_at`         DATETIME      DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY `uk_project_number` (`project_number`),
    INDEX `idx_purchaser` (`purchaser`),
    INDEX `idx_successful_bidder` (`successful_bidder`),
    INDEX `idx_winning_date` (`winning_date`),
    INDEX `idx_winning_amount` (`winning_amount`),
    INDEX `idx_province` (`province`),
    INDEX `idx_project_stage` (`project_stage`),
    INDEX `idx_publish_date` (`publish_date`),
    INDEX `idx_project_category` (`project_category`),
    FULLTEXT INDEX `ft_bid_project` (`project_name`, `purchaser`, `successful_bidder`, `subject_matter`) WITH PARSER ngram
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='招标项目交易记录表 — 来源：raw_tables/bid_project.csv';
```

### 6.2 字符集选择

**统一使用 `utf8mb4` + `utf8mb4_unicode_ci` 排序规则**，理由：

- CSV 数据含中文企业名称、产品名称、地址等，`utf8mb3` 无法覆盖部分生僻字和 emoji
- `utf8mb4_unicode_ci` 支持更准确的中文排序和比较（相比 `utf8mb4_general_ci`）
- MySQL 8.0 默认字符集即为 `utf8mb4`，无需额外配置

### 6.3 索引策略总结

| 表名 | FULLTEXT 索引 | BTREE 索引 | 去重键 |
|------|-------------|-----------|--------|
| `company_info` | `ft_company_info`: company_name, business_scope, industry, address | province, city, industry, company_level, business_status | `uk_credit_code` |
| `company_penalty` | `ft_penalty`: company_name, illegal_behavior, penalty_result | company_name, credit_code, penalty_date | —（按 id 自增） |
| `product_info` | `ft_product`: product_name, supplier_name, product_parameters, category | product_name, supplier_name, price, category, province | —（按 id 自增） |
| `bid_project` | `ft_bid_project`: project_name, purchaser, successful_bidder, subject_matter | purchaser, successful_bidder, winning_date, winning_amount, province, project_stage, publish_date, project_category | `uk_project_number` |

### 6.4 CSV 数据导入流程

```python
# 伪代码：csv_to_mysql.py（新建脚本）

import csv
import pymysql

TABLE_MAP = {
    "raw_tables/company_info.csv":    "company_info",
    "raw_tables/company_penalty.csv": "company_penalty",
    "raw_tables/product_info.csv":    "product_info",
    "raw_tables/bid_project.csv":     "bid_project",
}

def import_csv_to_mysql(csv_path: str, table_name: str, conn: pymysql.Connection):
    """流式读取 CSV，批量 INSERT 到 MySQL。"""
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        batch = []
        for row in reader:
            # 字段清洗：空字符串 → None；金额列去除千分位逗号
            cleaned = clean_row(row, table_name)
            batch.append(cleaned)
            if len(batch) >= 5000:
                insert_batch(conn, table_name, batch)
                batch = []
        if batch:
            insert_batch(conn, table_name, batch)

def clean_row(row: dict, table_name: str) -> dict:
    """清洗单行数据。"""
    cleaned = {}
    for k, v in row.items():
        if v is None or (isinstance(v, str) and v.strip() == ""):
            cleaned[k] = None
        elif k in ("price", "budget_amount", "winning_amount", "min_order_qty"):
            # 金额列：移除千分位逗号后转数值
            cleaned[k] = float(str(v).replace(",", "")) if v else None
        else:
            cleaned[k] = str(v).strip()
    return cleaned
```

**导入顺序建议**：

1. 先创建表结构（DDL），**不建 FULLTEXT 索引**（仅建 BTREE 和 UNIQUE KEY）
2. 流式导入 CSV 数据（每批 5000 条 INSERT）
3. 数据全部写入后，统一 `ALTER TABLE ... ADD FULLTEXT INDEX`（避免逐行写入期间的索引维护开销）
4. 对 `company_info` 按 `credit_code` 去重（`INSERT ... ON DUPLICATE KEY UPDATE`）

---

## 7. 三个二级路由的详细实现方案

### 7.1 路由一：公司信息查询（`company_query`）

#### 7.1.1 业务场景

- 供应商推荐：按地区 + 行业 + 规模筛选企业，如 "安徽软件信息行业中型及以上企业"
- 业务领域匹配：按经营范围推荐供应商，如 "日用百货领域公司"
- 不良记录核查：查询企业是否有行政处罚，如 "河源市赞爷餐饮管理服务有限公司是否有不良记录？"
- 企业资质查询：按企业名称查询详细信息

#### 7.1.2 意图识别 Prompt 设计

> **v2.2 性能优化**：原独立的 `_COMPANY_INTENT_PROMPT` 已合并至统一意图解析 Prompt `_UNIFIED_INTENT_PROMPT`（详见 [§8.5](#85-统一意图解析与二级路由分发)），通过一次 LLM 调用同时完成二级路由判断与全部结构化过滤条件抽取，消除串行 LLM 调用瓶颈。

`company_query` 路由在统一 Prompt 中对应 `sub_route = "company_query"`，`query_type` 支持 `supplier_recommend` / `penalty_check` / `company_detail` / `mixed`。专属过滤字段：`company_name`、`credit_code`、`industry`、`company_level`、`company_type`、`business_status`、`credit_rating`；条件标志 `need_penalty_check` 控制不良记录联查。同时填写通用字段 `province`、`city`（如有地区筛选需求）。

#### 7.1.3 检索逻辑

**三阶段检索（复用现有框架）**：

| 阶段 | 说明 | 实现要点 |
|------|------|---------|
| ① LLM 意图抽取 | 调用 `_UNIFIED_INTENT_PROMPT`（合并二级路由 + 结构化抽取）→ `SearchIntent` | 扩展 `SearchIntent` 增加 `query_type` 和 `need_penalty_check` 字段 |
| ② SQL 硬过滤 + FULLTEXT | 对 `company_info` 表执行 SQL | 地区/行业/等级用 BTREE 精确匹配；公司名/经营范围用 FULLTEXT；必要时联查 `company_penalty` |
| ③ 混合重排序 | Python 侧重排序 | 复用 `_hybrid_score()` 和 `_rank_records()`，优先返回高匹配记录 |

**特殊处理：不良记录查询**：

当 `need_penalty_check=True` 时，执行**两阶段关联查询**：

```sql
-- 阶段 1：先查 company_info 确认企业存在
SELECT * FROM company_info
WHERE MATCH(company_name) AGAINST ('+河源市赞爷餐饮管理服务有限公司' IN BOOLEAN MODE)
LIMIT 5;

-- 阶段 2：用 credit_code 联查处罚表
SELECT cp.* FROM company_penalty cp
INNER JOIN company_info ci ON ci.credit_code = cp.credit_code
WHERE ci.credit_code = '<从阶段1获取>'
ORDER BY cp.penalty_date DESC;
```

#### 7.1.4 输出字段设计

`company_query` 路由采用三层字段分级体系（详见 [§7.4](#74-统一输出字段框架)），由 `query_type` 驱动输出字段的动态选择：

**字段注册表**（`company_query` 全部可用字段）：

| 字段 key | 显示名 | 来源表.列 | 优先级 | 空值 | 截断 |
|---------|--------|----------|--------|------|------|
| `company_name` | 企业名称 | company_info.company_name | ★★★ 必出 | 显示"未提供" | — |
| `credit_code` | 统一社会信用代码 | company_info.credit_code | ★★★(penalty_check) / ★★☆(其他) | 显示"未提供" | — |
| `legal_person` | 法定代表人 | company_info.legal_person | ★★☆ 可选 | 隐藏 | — |
| `registered_capital` | 注册资本 | company_info.registered_capital | ★★☆ 可选 | 隐藏 | — |
| `establish_date` | 成立日期 | company_info.establish_date | ★★☆ 可选 | 隐藏 | — |
| `business_status` | 经营状态 | company_info.business_status | ★★☆(company_detail必出) | 显示"未知" | — |
| `industry` | 所属行业 | company_info.industry | ★★★(supplier_recommend必出) | 显示"未提供" | — |
| `company_type` | 企业类型 | company_info.company_type | ★☆☆ 可选 | 隐藏 | — |
| `company_level` | 企业等级 | company_info.company_level | ★★★(supplier_recommend必出) | 隐藏 | — |
| `credit_rating` | 信用评级 | company_info.credit_rating | ★★☆ 可选 | 隐藏 | — |
| `province` | 省份 | company_info.province | ★★★(supplier_recommend必出) | 显示"未提供" | — |
| `city` | 城市 | company_info.city | ★★★(supplier_recommend必出) | 隐藏 | — |
| `address` | 企业地址 | company_info.address | ★☆☆ 可选 | 隐藏 | 100 字 |
| `business_scope` | 经营范围 | company_info.business_scope | ★★☆ 条件出 | 隐藏 | 200 字 |
| `penalty_date` | 处罚日期 | company_penalty.penalty_date | ★★★(penalty_check必出) | 显示"未提供" | — |
| `illegal_behavior` | 违法行为 | company_penalty.illegal_behavior | ★★★(penalty_check必出) | 显示"未提供" | 200 字 |
| `penalty_result` | 处罚结果 | company_penalty.penalty_result | ★★★(penalty_check必出) | 显示"未提供" | 200 字 |
| `law_enforcement_unit` | 执法单位 | company_penalty.law_enforcement_unit | ★★☆ 可选 | 隐藏 | — |

**各 query_type 输出模板**：

```python
# agent/nodes/output_templates.py（新建，配置模型定义见 §7.4.2）

_COMPANY_OUTPUT_TEMPLATES = {
    "supplier_recommend": OutputTemplate(
        query_type="supplier_recommend",
        required=["company_name", "industry", "company_level", "province", "city"],
        conditional={
            "intent.need_penalty_check": ["penalty_date", "illegal_behavior",
                                           "penalty_result"],
        },
        optional=["legal_person", "registered_capital", "establish_date",
                  "credit_rating", "business_scope", "address", "business_status",
                  "company_type", "credit_code"],
        display_order=["company_name", "industry", "company_level", "province",
                       "city", "registered_capital", "establish_date",
                       "credit_rating", "business_scope", "address",
                       "business_status", "legal_person", "company_type",
                       "credit_code"],
    ),
    "penalty_check": OutputTemplate(
        query_type="penalty_check",
        required=["company_name", "credit_code", "penalty_date",
                  "illegal_behavior", "penalty_result"],
        optional=["law_enforcement_unit"],
        display_order=["company_name", "penalty_date", "illegal_behavior",
                       "penalty_result", "law_enforcement_unit", "credit_code"],
    ),
    "company_detail": OutputTemplate(  # 新增：企业详情查询
        query_type="company_detail",
        required=["company_name", "credit_code", "business_status"],
        conditional={
            "intent.need_penalty_check": ["penalty_date", "illegal_behavior",
                                           "penalty_result"],
        },
        optional=["legal_person", "registered_capital", "establish_date",
                  "industry", "company_type", "company_level", "credit_rating",
                  "province", "city", "address", "business_scope"],
        display_order=["company_name", "credit_code", "legal_person",
                       "registered_capital", "establish_date", "business_status",
                       "industry", "company_type", "company_level",
                       "credit_rating", "province", "city", "address",
                       "business_scope"],
    ),
    "mixed": _merge_templates("supplier_recommend", "penalty_check"),
}
```

> **说明**：`supplier_recommend` 默认不输出 `penalty_*` 系列字段（避免冗余），仅当 `need_penalty_check=True` 时通过 `conditional` 动态追加。`company_detail` 默认展示 `company_info` 全量字段，用于身份验证场景。`mixed` 模式合并 `supplier_recommend` 与 `penalty_check` 的 required 字段并集。

---

### 7.2 路由二：产品信息查询（`product_query`）

#### 7.2.1 业务场景

- 市场行情查询：如 "电剪刀的市场行情价怎么样？"
- 供应商筛选：如 "找几个防水涂料的供应商，要价格便宜的，顺便给我联系人电话和地址"
- 产品搜索：按产品名称/类别搜索

#### 7.2.2 意图识别 Prompt 设计

> **v2.2 性能优化**：原独立的 `_PRODUCT_INTENT_PROMPT` 已合并至统一意图解析 Prompt `_UNIFIED_INTENT_PROMPT`（详见 [§8.5](#85-统一意图解析与二级路由分发)），通过一次 LLM 调用同时完成二级路由判断与全部结构化过滤条件抽取。

`product_query` 路由在统一 Prompt 中对应 `sub_route = "product_query"`，`query_type` 支持 `price_inquiry` / `supplier_search` / `product_detail` / `mixed`。专属过滤字段：`product_name`、`category`、`subcategory`、`supplier_name`、`price_range`；排序字段 `sort_by` 支持 `price_asc` / `price_desc`；条件标志 `need_contact` 控制联系人信息输出。

#### 7.2.3 检索逻辑

| 阶段 | 说明 | 与现有 price_inquiry 的差异 |
|------|------|--------------------------|
| ① LLM 意图抽取 | 调用 `_UNIFIED_INTENT_PROMPT`（合并二级路由 + 结构化抽取） | `SearchIntent` 扩展 `sort_by` 和 `need_contact` 字段 |
| ② SQL 硬过滤 + FULLTEXT | 对 `product_info` 表执行 SQL | 额外支持 `ORDER BY price ASC/DESC` 排序 |
| ③ 混合重排序 | Python 侧重排序 | 复用时对 `price` 列做归一化处理，结合文本匹配得分 |

**价格排序增强**：当 `sort_by="price_asc"` 时，在 SQL 层直接 `ORDER BY price ASC`（利用 BTREE 索引），再在 Python 层按混合得分微调顺序，确保"价格便宜"优先。

#### 7.2.4 输出字段设计

`product_query` 路由采用三层字段分级体系（详见 [§7.4](#74-统一输出字段框架)）：

**字段注册表**（`product_query` 全部可用字段）：

| 字段 key | 显示名 | 来源表.列 | 优先级 | 空值 | 截断 |
|---------|--------|----------|--------|------|------|
| `product_name` | 产品名称 | product_info.product_name | ★★★ 必出 | 显示"未提供" | — |
| `supplier_name` | 供应商名称 | product_info.supplier_name | ★★★ 必出 | 显示"未提供" | — |
| `price` | 报价 | product_info.price | ★★★(price_inquiry) / ★★☆(其他) | 显示"未提供" | — |
| `price_unit` | 计价单位 | product_info.price_unit | ★★★(price_inquiry) / ★★☆(其他) | 隐藏 | — |
| `category` | 产品大类 | product_info.category | ★★☆ 可选 | 隐藏 | — |
| `subcategory` | 产品小类 | product_info.subcategory | ★☆☆ 可选 | 隐藏 | — |
| `product_parameters` | 产品参数 | product_info.product_parameters | ★★☆(product_detail必出) | 隐藏 | 300 字 |
| `province` | 省份 | product_info.province | ★★☆ 可选 | 隐藏 | — |
| `city` | 城市 | product_info.city | ★★☆ 可选 | 隐藏 | — |
| `contact_person` | 联系人 | product_info.contact_person | ★★★(need_contact=true) | 隐藏 | — |
| `contact_info` | 联系方式 | product_info.contact_info | ★★★(need_contact=true) | 隐藏 | — |
| `supplier_address` | 供应商地址 | product_info.supplier_address | ★★☆ 可选 | 隐藏 | 100 字 |
| `min_order_qty` | 起订量 | product_info.min_order_qty | ★☆☆ 可选 | 隐藏 | — |

**各 query_type 输出模板**：

```python
_PRODUCT_OUTPUT_TEMPLATES = {
    "price_inquiry": OutputTemplate(  # 新增：行情询价
        query_type="price_inquiry",
        required=["product_name", "price", "price_unit", "supplier_name"],
        conditional={
            "intent.need_contact": ["contact_person", "contact_info"],
        },
        optional=["category", "subcategory", "product_parameters",
                  "province", "city", "min_order_qty"],
        display_order=["product_name", "price", "price_unit", "supplier_name",
                       "category", "subcategory", "product_parameters",
                       "province", "city", "contact_person", "contact_info",
                       "min_order_qty"],
    ),
    "supplier_search": OutputTemplate(
        query_type="supplier_search",
        required=["supplier_name", "product_name", "price", "price_unit"],
        conditional={
            "intent.need_contact": ["contact_person", "contact_info"],
        },
        optional=["category", "subcategory", "product_parameters",
                  "province", "city", "supplier_address", "min_order_qty"],
        display_order=["supplier_name", "product_name", "price", "price_unit",
                       "category", "subcategory", "product_parameters",
                       "province", "city", "supplier_address",
                       "contact_person", "contact_info", "min_order_qty"],
    ),
    "product_detail": OutputTemplate(  # 新增：产品详情
        query_type="product_detail",
        required=["product_name", "supplier_name", "product_parameters"],
        conditional={
            "intent.need_contact": ["contact_person", "contact_info"],
        },
        optional=["price", "price_unit", "category", "subcategory",
                  "province", "city", "supplier_address", "min_order_qty"],
        display_order=["product_name", "supplier_name", "product_parameters",
                       "price", "price_unit", "category", "subcategory",
                       "province", "city", "supplier_address",
                       "contact_person", "contact_info", "min_order_qty"],
    ),
    "mixed": _merge_templates("supplier_search", "price_inquiry"),
}
```

> **说明**：`contact_person`/`contact_info` 仅当 `need_contact=True` 时激活（用户明确要求联系信息），避免主动暴露隐私。`price_inquiry` 以价格字段为核心输出（`price` + `price_unit` 升级为必出），`product_detail` 以产品参数为核心。

---

### 7.3 路由三：招投标历史交易查询（`bidding_query`）

#### 7.3.1 业务场景

- 采购方视角：如 "福建师范大学招标过什么项目？都是谁中标了？中标金额多少？"
- 供应商视角：如 "福州怡富电梯有限公司代理过中标金额最大的项目是哪个项目？"
- 时间范围查询：如 "福州怡富电梯有限公司 2024 年都中标了什么项目？"
- 聚合统计：如 "2024 年福建省中标金额 TOP10 的项目"

#### 7.3.2 意图识别 Prompt 设计

> **v2.2 性能优化**：原独立的 `_BIDDING_INTENT_PROMPT` 已合并至统一意图解析 Prompt `_UNIFIED_INTENT_PROMPT`（详见 [§8.5](#85-统一意图解析与二级路由分发)），通过一次 LLM 调用同时完成二级路由判断与全部结构化过滤条件抽取。

`bidding_query` 路由在统一 Prompt 中对应 `sub_route = "bidding_query"`，`query_type` 支持 `purchaser_query` / `bidder_query` / `project_detail` / `aggregation` / `mixed`。专属过滤字段：`purchaser`、`successful_bidder`、`agent`、`project_number`、`project_category`、`project_stage`、`winning_amount_range`、`time_range`；排序与聚合字段 `sort_by`（`amount_desc` / `amount_asc` / `date_desc` / `date_asc`）、`aggregation`（`max_amount` / `count` / `sum`）、`top_n`。

#### 7.3.3 检索逻辑

**三阶段检索**：

| 阶段 | 说明 | 特殊处理 |
|------|------|---------|
| ① LLM 意图抽取 | 调用 `_UNIFIED_INTENT_PROMPT`（合并二级路由 + 结构化抽取） | 扩展 `aggregation` 和 `top_n` 字段 |
| ② SQL 硬过滤 + FULLTEXT | 对 `bid_project` 表执行 SQL | 支持 `ORDER BY winning_amount DESC`；聚合查询走专用 SQL |
| ③ 混合重排序 | Python 侧重排序 | 金额查询时弱化文本得分权重，强化金额排序 |

**聚合查询特殊处理**：当 `aggregation` 不为 null 时，跳过 FULLTEXT 检索，直接走聚合 SQL：

```sql
-- 示例：福州怡富电梯有限公司 2024 年中标金额最大的项目
SELECT project_name, purchaser, successful_bidder, winning_amount, winning_date
FROM bid_project
WHERE successful_bidder LIKE '%福州怡富电梯有限公司%'
  AND winning_date >= '2024-01-01' AND winning_date <= '2024-12-31'
  AND project_stage = '结果公告'
ORDER BY winning_amount DESC
LIMIT 1;
```

#### 7.3.4 输出字段设计

`bidding_query` 路由采用三层字段分级体系（详见 [§7.4](#74-统一输出字段框架)）：

**字段注册表**（`bidding_query` 全部可用字段）：

| 字段 key | 显示名 | 来源表.列 | 优先级 | 空值 | 截断 |
|---------|--------|----------|--------|------|------|
| `project_name` | 项目名称 | bid_project.project_name | ★★★ 必出 | 显示"未提供" | — |
| `project_number` | 项目编号 | bid_project.project_number | ★★★ 必出 | 显示"未提供" | — |
| `purchaser` | 采购人 | bid_project.purchaser | ★★★(bidder_query) / ★★☆(其他) | 显示"未提供" | — |
| `successful_bidder` | 中标供应商 | bid_project.successful_bidder | ★★★(purchaser_query) / ★☆☆(bidder_query) | 显示"未提供" | — |
| `winning_amount` | 中标金额/元 | bid_project.winning_amount | ★★★ 必出 | 显示"未提供" | — |
| `winning_date` | 中标日期 | bid_project.winning_date | ★★★ 必出 | 显示"未提供" | — |
| `subject_matter` | 标的物 | bid_project.subject_matter | ★★☆ 可选 | 隐藏 | 200 字 |
| `agent` | 代理机构 | bid_project.agent | ★★☆ 可选 | 隐藏 | — |
| `project_stage` | 项目阶段 | bid_project.project_stage | ★★☆ 可选 | 隐藏 | — |
| `project_category` | 项目类别 | bid_project.project_category | ★★☆ 可选 | 隐藏 | — |
| `budget_amount` | 预算金额/元 | bid_project.budget_amount | ★☆☆ 可选 | 隐藏 | — |
| `province` | 省份 | bid_project.province | ★★☆ 可选 | 隐藏 | — |
| `city` | 城市 | bid_project.city | ★★☆ 可选 | 隐藏 | — |
| `publish_date` | 发布日期 | bid_project.publish_date | ★☆☆ 可选 | 隐藏 | — |
| `source_url` | 来源链接 | bid_project.source_url | ★☆☆ 可选 | 隐藏 | — |

**各 query_type 输出模板**：

```python
_BIDDING_OUTPUT_TEMPLATES = {
    "purchaser_query": OutputTemplate(
        query_type="purchaser_query",
        required=["project_name", "project_number", "successful_bidder",
                  "winning_amount", "winning_date"],
        optional=["purchaser", "subject_matter", "agent", "project_stage",
                  "project_category", "budget_amount", "province", "city",
                  "publish_date", "source_url"],
        display_order=["project_name", "project_number", "successful_bidder",
                       "winning_amount", "winning_date", "purchaser",
                       "subject_matter", "agent", "project_stage",
                       "project_category", "budget_amount", "province",
                       "city", "publish_date", "source_url"],
    ),
    "bidder_query": OutputTemplate(
        query_type="bidder_query",
        # 核心差异：successful_bidder 降为可选（已知条件），purchaser 升级为必出
        required=["project_name", "project_number", "purchaser",
                  "winning_amount", "winning_date"],
        optional=["successful_bidder", "subject_matter", "agent",
                  "project_stage", "project_category", "budget_amount",
                  "province", "city", "publish_date", "source_url"],
        display_order=["project_name", "project_number", "purchaser",
                       "winning_amount", "winning_date", "successful_bidder",
                       "subject_matter", "agent", "project_stage",
                       "project_category", "budget_amount", "province",
                       "city", "publish_date", "source_url"],
    ),
    "project_detail": OutputTemplate(  # 新增：按项目编号精确查询
        query_type="project_detail",
        required=["project_name", "project_number", "project_stage",
                  "publish_date"],
        optional=["purchaser", "successful_bidder", "winning_amount",
                  "winning_date", "subject_matter", "agent",
                  "project_category", "budget_amount", "province",
                  "city", "source_url"],
        display_order=["project_name", "project_number", "project_stage",
                       "purchaser", "successful_bidder", "winning_amount",
                       "winning_date", "subject_matter", "agent",
                       "project_category", "budget_amount", "publish_date",
                       "province", "city", "source_url"],
    ),
}
```

**聚合统计场景（`aggregation`）**— 不走常规字段模板，输出结构化聚合结果：

```python
# aggregation 输出格式（不经 _apply_output_template 处理）
aggregation_result = {
    "aggregation_type": "max_amount",  # max_amount | count | sum
    "aggregation_value": 12345678.90,
    "aggregation_label": "最大中标金额",
    "detail_records": [  # 当 top_n 有值时附带明细
        {"project_name": "...", "winning_amount": ..., "winning_date": "..."},
    ],
}
```

> **说明**：`bidder_query` 中 `successful_bidder` 降级为可选（用户已知该供应商），`purchaser` 升级为必出（"都是哪些采购人采购的？"）。`aggregation` 完全走独立输出路径，不套用常规记录模板。`project_detail` 新增覆盖按项目编号精确查询场景。

---

### 7.4 统一输出字段框架

#### 7.4.1 设计动机与评估结论

**现状差距**：

| 问题 | 现状 | 影响 |
|------|------|------|
| query_type 覆盖不全 | 仅 5/11 个子场景有输出字段定义 | `company_detail`、`price_inquiry`、`product_detail`、`project_detail`、`bidder_query`(含混)、`mixed` 共 6 个缺失 |
| 格式不一致 | 三个 §7.x.4 使用不同表格格式 | 新增路由时开发者无所适从 |
| 无动态字段控制 | `need_contact` 标注了条件优先级但无运行时机制 | 实现阶段可能被遗漏 |
| 无 NULL/截断约束 | 未定义空值展示和超长文本处理规则 | 前端可能直接 crash 或数据不可读 |

**独立 vs 统一框架评估**：

| 维度 | 保持独立结构 | 统一配置框架 | 结论 |
|------|------------|-------------|------|
| 业务贴合度 | ✅ 高：天然匹配场景 | ⚠️ 需额外映射层 | 独立优于统一 |
| 扩展成本 | ❌ 每增一个 query_type 手写一套 | ✅ 新增模板配置即可 | 统一优于独立 |
| NULL 处理 | ❌ 零约束 | ✅ 集中管控 | 统一优于独立 |
| 维护一致性 | ❌ 多处分摊 | ✅ 一处定义全局生效 | 统一优于独立 |

**结论：混合方案** — 每路由保留独立的字段注册表（表结构不同），但使用 **统一的 `FieldDescriptor` + `OutputTemplate` 配置模型** 和 **统一的 `_apply_output_template()` 运行时引擎** 来管控空值处理、截断和字段上限。

#### 7.4.2 配置模型定义

```python
# agent/nodes/output_templates.py（新建文件）

from dataclasses import dataclass, field
from typing import Optional

@dataclass
class FieldDescriptor:
    """单个输出字段描述符 — 全系统统一。"""
    key: str                         # 机器名，如 "company_name"
    label: str                       # 中文显示名，如 "企业名称"
    source_table: str                # 来源表名
    source_col: str                  # 来源列名
    default_priority: str = "optional"  # "required" | "conditional" | "optional"
    null_behavior: str = "hide"      # "hide" | "show_placeholder" | "keep_null"
    max_chars: Optional[int] = None  # 截断阈值（char），超长则截断 + "…"
    group: str = "default"           # 字段分组标签（预留：未来按组批量引用）


@dataclass
class OutputTemplate:
    """输出模板 — 定义某 query_type 的字段筛选规则。"""
    route: str                       # "company_query" | "product_query" | "bidding_query"
    query_type: str                  # "supplier_recommend" | "price_inquiry" | ...
    required: list[str]              # ★★★ 必出字段 key 列表
    conditional: dict[str, list[str]] = field(default_factory=dict)
                                     # 条件字段：{条件表达式 → [字段key]}
                                     # 如 {"intent.need_contact": ["contact_person", "contact_info"]}
    optional: list[str] = field(default_factory=list)
                                     # ★☆☆ 可选字段 key 列表
    display_order: list[str] = field(default_factory=list)
                                     # 字段展示顺序


# ── 全局字段注册表 ──
_FIELD_REGISTRY: dict[str, FieldDescriptor] = {}

def _register(fd: FieldDescriptor) -> FieldDescriptor:
    """注册字段描述符，全局唯一。"""
    if fd.key in _FIELD_REGISTRY:
        raise ValueError(f"字段 key '{fd.key}' 重复注册")
    _FIELD_REGISTRY[fd.key] = fd
    return fd


# ===== company_query 字段 =====
_register(FieldDescriptor("company_name", "企业名称", "company_info", "company_name",
    default_priority="required", null_behavior="show_placeholder"))
_register(FieldDescriptor("credit_code", "统一社会信用代码", "company_info", "credit_code",
    null_behavior="show_placeholder"))
_register(FieldDescriptor("legal_person", "法定代表人", "company_info", "legal_person"))
_register(FieldDescriptor("registered_capital", "注册资本", "company_info", "registered_capital"))
_register(FieldDescriptor("establish_date", "成立日期", "company_info", "establish_date"))
_register(FieldDescriptor("business_status", "经营状态", "company_info", "business_status",
    null_behavior="show_placeholder"))
_register(FieldDescriptor("industry", "所属行业", "company_info", "industry",
    null_behavior="show_placeholder"))
_register(FieldDescriptor("company_type", "企业类型", "company_info", "company_type"))
_register(FieldDescriptor("company_level", "企业等级", "company_info", "company_level"))
_register(FieldDescriptor("credit_rating", "信用评级", "company_info", "credit_rating"))
_register(FieldDescriptor("province", "省份", "company_info", "province",
    null_behavior="show_placeholder"))
_register(FieldDescriptor("city", "城市", "company_info", "city"))
_register(FieldDescriptor("address", "企业地址", "company_info", "address", max_chars=100))
_register(FieldDescriptor("business_scope", "经营范围", "company_info", "business_scope",
    max_chars=200))
# penalty 字段（来源 company_penalty 表）
_register(FieldDescriptor("penalty_date", "处罚日期", "company_penalty", "penalty_date",
    default_priority="conditional", null_behavior="show_placeholder"))
_register(FieldDescriptor("illegal_behavior", "违法行为", "company_penalty", "illegal_behavior",
    default_priority="conditional", null_behavior="show_placeholder", max_chars=200))
_register(FieldDescriptor("penalty_result", "处罚结果", "company_penalty", "penalty_result",
    default_priority="conditional", null_behavior="show_placeholder", max_chars=200))
_register(FieldDescriptor("law_enforcement_unit", "执法单位", "company_penalty",
    "law_enforcement_unit"))

# ===== product_query 字段 =====
_register(FieldDescriptor("product_name", "产品名称", "product_info", "product_name",
    default_priority="required", null_behavior="show_placeholder"))
_register(FieldDescriptor("supplier_name", "供应商名称", "product_info", "supplier_name",
    default_priority="required", null_behavior="show_placeholder"))
_register(FieldDescriptor("price", "报价", "product_info", "price",
    null_behavior="show_placeholder"))
_register(FieldDescriptor("price_unit", "计价单位", "product_info", "price_unit"))
_register(FieldDescriptor("category", "产品大类", "product_info", "category"))
_register(FieldDescriptor("subcategory", "产品小类", "product_info", "subcategory"))
_register(FieldDescriptor("product_parameters", "产品参数", "product_info", "product_parameters",
    max_chars=300))
_register(FieldDescriptor("contact_person", "联系人", "product_info", "contact_person",
    default_priority="conditional"))
_register(FieldDescriptor("contact_info", "联系方式", "product_info", "contact_info",
    default_priority="conditional"))
_register(FieldDescriptor("supplier_address", "供应商地址", "product_info", "supplier_address",
    max_chars=100))
_register(FieldDescriptor("min_order_qty", "起订量", "product_info", "min_order_qty"))

# ===== bidding_query 字段 =====
_register(FieldDescriptor("project_name", "项目名称", "bid_project", "project_name",
    default_priority="required", null_behavior="show_placeholder"))
_register(FieldDescriptor("project_number", "项目编号", "bid_project", "project_number",
    default_priority="required", null_behavior="show_placeholder"))
_register(FieldDescriptor("purchaser", "采购人", "bid_project", "purchaser",
    null_behavior="show_placeholder"))
_register(FieldDescriptor("successful_bidder", "中标供应商", "bid_project", "successful_bidder",
    null_behavior="show_placeholder"))
_register(FieldDescriptor("winning_amount", "中标金额", "bid_project", "winning_amount",
    default_priority="required", null_behavior="show_placeholder"))
_register(FieldDescriptor("winning_date", "中标日期", "bid_project", "winning_date",
    default_priority="required", null_behavior="show_placeholder"))
_register(FieldDescriptor("subject_matter", "标的物", "bid_project", "subject_matter",
    max_chars=200))
_register(FieldDescriptor("agent", "代理机构", "bid_project", "agent"))
_register(FieldDescriptor("project_stage", "项目阶段", "bid_project", "project_stage"))
_register(FieldDescriptor("project_category", "项目类别", "bid_project", "project_category"))
_register(FieldDescriptor("budget_amount", "预算金额", "bid_project", "budget_amount"))
_register(FieldDescriptor("publish_date", "发布日期", "bid_project", "publish_date"))
_register(FieldDescriptor("source_url", "来源链接", "bid_project", "source_url"))
# province/city 复用 company_query 中同名字段（不重复注册）
```

#### 7.4.3 运行时字段筛选引擎

```python
def _apply_output_template(
    records: list[dict],
    intent: SearchIntent,
    template: OutputTemplate,
    field_registry: dict[str, FieldDescriptor] = _FIELD_REGISTRY,
    max_fields_per_record: int = 12,
) -> list[dict]:
    """根据 OutputTemplate 筛选并格式化输出字段。

    处理流程：
    ① 确定活跃字段集（required + 条件满足的 conditional + optional）
    ② 逐字段应用空值处理（hide / show_placeholder / keep_null）
    ③ 逐字段应用截断（max_chars → 追加 "…"）
    ④ 按 display_order 排序
    ⑤ 单记录字段数超 max_fields_per_record 时，从 optional 尾部裁剪
    """
    # ① 活跃字段
    active_keys: set[str] = set(template.required)

    for cond_expr, keys in template.conditional.items():
        if _eval_condition(cond_expr, intent):
            active_keys.update(keys)

    active_keys.update(template.optional)

    # ②③④ 逐记录格式化
    formatted = []
    for raw in records:
        row = {}
        for key in template.display_order:
            if key not in active_keys:
                continue
            fd = field_registry.get(key)
            if fd is None:
                continue

            value = raw.get(fd.source_col)

            # 空值处理（②）
            if value is None or (isinstance(value, str) and value.strip() == ""):
                if fd.null_behavior == "show_placeholder":
                    row[fd.label] = "未提供"
                elif fd.null_behavior == "keep_null":
                    row[fd.label] = None
                else:  # "hide"
                    continue
            else:
                # 截断（③）
                if fd.max_chars and isinstance(value, str) and len(value) > fd.max_chars:
                    value = value[:fd.max_chars] + "…"
                row[fd.label] = value

        # ⑤ 字段上限裁剪
        if len(row) > max_fields_per_record:
            overflow = len(row) - max_fields_per_record
            keys_in_order = [k for k in template.display_order if k in row]
            for k in reversed(keys_in_order):
                if overflow <= 0:
                    break
                k_label = field_registry.get(k, FieldDescriptor(k, k, "", "")).label
                if k_label in row and k not in template.required:
                    del row[k_label]
                    overflow -= 1

        if row:  # 全 NULL 的记录不输出
            formatted.append(row)

    return formatted


def _eval_condition(expr: str, intent: SearchIntent) -> bool:
    """安全的条件求值（仅支持 intent.xxx 布尔字段）。"""
    if expr.startswith("intent."):
        attr = expr[len("intent."):]  # "need_contact"
        return bool(getattr(intent, attr, False))
    return False


def _merge_templates(*query_types: str) -> OutputTemplate:
    """合并多个 query_type 的 required 字段并集（用于 'mixed' 回退）。"""
    # 具体实现：遍历各模板取 required 并集，display_order 拼接
    ...
```

#### 7.4.4 NULL 与缺失数据处理规则

| 字段优先级 | NULL 行为 | 示例 | 设计理由 |
|-----------|----------|------|---------|
| `required` | 显示占位文本 `"未提供"` | `"所属行业": "未提供"` | 用户期望看到该字段，隐藏会造成"漏字段"的错觉 |
| `conditional` | 隐藏该字段 | `contact_person=NULL` → 不输出 | 非核心关注点，隐藏更干净 |
| `optional` | 隐藏该字段 | `law_enforcement_unit=NULL` → 不输出 | 可选信息，有则锦上添花，无则不打扰 |
| 整条记录全 NULL | 从 `formatted` 中移除 | 空行不计入 `displayed_hits` | 避免输出无意义的数据行 |

#### 7.4.5 字段溢出与上限控制

| 规则 | 阈值 | 触发条件 | 处理方式 |
|------|------|---------|---------|
| 文本截断 | 见各字段 `max_chars`（100~300 字） | `len(text) > max_chars` | 截断 + 追加 "…"（U+2026），不修改 DB 原始数据 |
| 单记录字段上限 | 12 个 | `len(row) > 12` | 从 `optional` 尾部开始逐字段裁剪，不裁剪 `required` |
| 结果集上限 | 20 条 | SQL `LIMIT` 层面控制 | `SELECT ... LIMIT 20`，不在输出层处理 |

#### 7.4.6 与 `AgentState.business_result` 的衔接

`business_result` 保持现有泛型 `dict` 结构（[state.py L37](../agent/state.py#L37)），**零改动**。升级后的 `price_inquiry` 节点填充如下：

```python
# node_price_inquiry() 最终构造 business_result
business_result = {
    "branch": "price_inquiry",              # 不变
    "sub_route": sub_route,                 # 新增："company_query"|"product_query"|"bidding_query"|"all"
    "query_type": intent.query_type,        # 新增："supplier_recommend"|"price_inquiry"|...
    "answer": llm_formatted_answer,         # 不变：LLM 生成的最终自然语言回答
    "data": {
        "records": formatted_records,       # _apply_output_template() 处理后的干净数据
        "meta": {
            "total_hits": len(raw_records),
            "displayed_hits": len(formatted_records),
            "displayed_fields": [fd.label for fd in active_fields],
            "skipped_empty_records": len(raw_records) - len(formatted_records),
            "truncated_fields": [...],      # 被截断过的字段名列表
        },
    },
}
```

**兼容性保证**：
- `state.py` — 零改动（`business_result` 仍是 `dict`）
- `graph.py` — 零改动（条件边只检查 `router_intent`）
- 其他节点 — 零影响（`knowledge_qa` / `general_chat` 等不感知新增字段）
- LLM 兜底 — 极端场景下回退 `{"branch": "price_inquiry", "answer": "未找到匹配记录，请调整查询条件后重试。", "data": None}`

#### 7.4.7 扩展性设计

**新增数据表**（如未来接入 `bid_content`）：
1. 在 `_FIELD_REGISTRY` 中 `_register()` 新字段
2. 在对应路由的 `_OUTPUT_TEMPLATES` 中添加 `OutputTemplate`
3. 在 `_SUB_ROUTE_MAP` 中增加表映射
4. ✅ 无需修改 `_apply_output_template()` / `_eval_condition()` 核心逻辑

**新增 query_type**（如 `company_query` 下新增 `financial_check`）：
1. 在 Intent Prompt 中增加 `query_type` 枚举值
2. 在对应 `_OUTPUT_TEMPLATES` 字典中添加新 `OutputTemplate`
3. ✅ 无需修改字段注册表和格式化引擎

**新增条件表达式**（如 `intent.sort_by_price`）：
1. 在 `_eval_condition()` 的 `intent.xxx` 路径下自动支持（基于 `getattr`）
2. ✅ 无需新增代码

> **设计参考**：此配置驱动架构借鉴了 ML 特征工程中的 Feature Registry 模式（Feast / Tecton），确保新增能力只需修改声明式配置，核心引擎代码保持稳定。

---

## 8. 检索逻辑的 SQL 实现细节

### 8.1 统一数据模型扩展

在现有 `price_inquiry.py` 的基础上，扩展 `SearchIntent` 和 `HardFilters` 以支持三个二级路由的差异化需求：

```python
@dataclass
class HardFilters:
    """硬过滤条件 — 扩展以支持三类数据源。"""
    # ── 通用 ──
    time_range: Optional[dict[str, str]] = None        # {start, end}
    budget_range: Optional[dict[str, float]] = None    # {min, max}
    purchaser: Optional[str] = None
    region: Optional[str] = None
    province: Optional[str] = None                     # 新增：省份
    city: Optional[str] = None                         # 新增：城市
    status: Optional[str] = None

    # ── 公司专用 ──
    company_name: Optional[str] = None
    credit_code: Optional[str] = None
    industry: Optional[str] = None
    company_level: Optional[str] = None
    company_type: Optional[str] = None
    business_status: Optional[str] = None

    # ── 产品专用 ──
    product_name: Optional[str] = None
    category: Optional[str] = None
    supplier_name: Optional[str] = None
    price_range: Optional[dict[str, float]] = None     # {min, max}

    # ── 招标专用 ──
    successful_bidder: Optional[str] = None
    agent: Optional[str] = None
    project_number: Optional[str] = None
    project_category: Optional[str] = None
    project_stage: Optional[str] = None
    winning_amount_range: Optional[dict[str, float]] = None


@dataclass
class SearchIntent:
    """结构化查询意图 — 扩展。"""
    hard_filters: HardFilters
    semantic_keywords: list[str] = field(default_factory=list)
    exact_tokens: list[str] = field(default_factory=list)
    original_question: str = ""

    # ── 新增：路由与排序 ──
    sub_route: str = "all"                             # company_query | product_query | bidding_query | all
    query_type: str = "mixed"                          # 查询类型（路由专用）
    sort_by: Optional[str] = None                      # price_asc | price_desc | amount_desc | date_desc | relevance
    aggregation: Optional[str] = None                   # max_amount | count | sum
    top_n: Optional[int] = None
    need_penalty_check: bool = False                   # 是否需要不良记录查询
    need_contact: bool = False                         # 是否需要联系人信息
```

### 8.2 列分类规则扩展

在现有 `_classify_columns()` 的基础上，增加针对三类新表的**硬编码快速路径**，跳过 `information_schema` 查询，直接使用已知列结构：

```python
# 新增：单库模式下的硬编码 schema（快速路径，跳过 information_schema 查询）
_HARDCODED_SCHEMA = {
    "company_info": {
        "id": ["id"],
        "semantic": ["company_name", "business_scope", "industry", "address"],
        "time": ["establish_date"],
        "region": ["province", "city", "district"],
        "exact": ["credit_code"],
        "text": ["company_name", "business_scope", "industry", "address",
                 "legal_person", "registered_capital"],
    },
    "company_penalty": {
        "id": ["id"],
        "semantic": ["company_name", "illegal_behavior", "penalty_result"],
        "time": ["penalty_date"],
        "exact": ["credit_code"],
        "text": ["company_name", "illegal_behavior", "penalty_result",
                 "law_enforcement_unit"],
    },
    "product_info": {
        "id": ["id"],
        "semantic": ["product_name", "supplier_name", "product_parameters", "category"],
        "budget": ["price"],
        "region": ["province", "city"],
        "text": ["product_name", "supplier_name", "product_parameters",
                 "category", "subcategory", "supplier_address"],
    },
    "bid_project": {
        "id": ["id"],
        "semantic": ["project_name", "purchaser", "successful_bidder", "subject_matter"],
        "time": ["winning_date", "publish_date"],
        "budget": ["winning_amount", "budget_amount"],
        "purchaser": ["purchaser"],
        "region": ["province", "city", "district"],
        "status": ["project_stage"],
        "exact": ["project_number"],
        "text": ["project_name", "purchaser", "successful_bidder", "subject_matter",
                 "agent", "project_category"],
    },
}
```

### 8.3 SQL 生成器适配

现有 `_build_candidate_sql()` 方法（[price_inquiry.py L481-L528](../agent/nodes/price_inquiry.py#L481-L528)）的核心逻辑高度可复用。针对三个二级路由的差异，在 `_build_hard_conditions()` 中扩展过滤条件：

```python
def _build_hard_conditions_extended(
    table: str,
    classification: dict[str, list[str]],
    intent: SearchIntent,
) -> tuple[list[str], list[Any]]:
    """扩展版硬过滤条件构造 — 支持三类数据源的专用过滤。"""
    conditions, params = _build_hard_conditions(table, classification, intent)  # 复用原有

    hf = intent.hard_filters

    # ── 公司专用过滤 ──
    if hf.company_name:
        conditions.append("`company_name` = %s")
        params.append(hf.company_name)
    if hf.credit_code:
        conditions.append("`credit_code` = %s")
        params.append(hf.credit_code)
    if hf.industry:
        conditions.append("`industry` = %s")
        params.append(hf.industry)
    if hf.company_level:
        conditions.append("`company_level` = %s")
        params.append(hf.company_level)
    if hf.business_status:
        conditions.append("`business_status` = %s")
        params.append(hf.business_status)

    # ── 产品专用过滤 ──
    if hf.category:
        conditions.append("`category` = %s")
        params.append(hf.category)
    if hf.price_range:
        if hf.price_range.get("min") is not None:
            conditions.append("`price` >= %s")
            params.append(hf.price_range["min"])
        if hf.price_range.get("max") is not None:
            conditions.append("`price` <= %s")
            params.append(hf.price_range["max"])

    # ── 招标专用过滤 ──
    if hf.successful_bidder:
        conditions.append("`successful_bidder` = %s")
        params.append(hf.successful_bidder)
    if hf.project_number:
        conditions.append("`project_number` = %s")
        params.append(hf.project_number)
    if hf.project_category:
        conditions.append("`project_category` = %s")
        params.append(hf.project_category)
    if hf.project_stage:
        conditions.append("`project_stage` = %s")
        params.append(hf.project_stage)
    if hf.winning_amount_range:
        if hf.winning_amount_range.get("min") is not None:
            conditions.append("`winning_amount` >= %s")
            params.append(hf.winning_amount_range["min"])
        if hf.winning_amount_range.get("max") is not None:
            conditions.append("`winning_amount` <= %s")
            params.append(hf.winning_amount_range["max"])

    # ── 地区过滤增强：支持 province + city ──
    if hf.province:
        conditions.append("`province` = %s")
        params.append(hf.province)
    if hf.city:
        conditions.append("`city` = %s")
        params.append(hf.city)

    return conditions, params
```

### 8.4 排序逻辑适配

现有 `_build_candidate_sql` 中的 `ORDER BY` 直接使用 FULLTEXT `_score_`，需扩展为支持业务排序：

```python
def _build_order_clause(intent: SearchIntent) -> str:
    """根据意图返回 ORDER BY 子句。"""
    sort_by = intent.sort_by

    if sort_by == "price_asc":
        return "ORDER BY `price` ASC"
    elif sort_by == "price_desc":
        return "ORDER BY `price` DESC"
    elif sort_by == "amount_desc":
        return "ORDER BY `winning_amount` DESC"
    elif sort_by == "amount_asc":
        return "ORDER BY `winning_amount` ASC"
    elif sort_by == "date_desc":
        return "ORDER BY `winning_date` DESC"
    elif sort_by == "date_asc":
        return "ORDER BY `winning_date` ASC"
    else:
        # 默认：按 FULLTEXT 得分排序（复用原有逻辑）
        return "ORDER BY `_score_` DESC"
```

**混合排序得分公式**（保持不变）：

```
综合得分 = MySQL FULLTEXT _score_（ngram TF-IDF）
        + Python _hybrid_score()
            ├── 语义关键词命中 +1.0/次 × count
            └── 精确 token 命中 +10.0/次
```

当 `sort_by` 为金额/时间排序时，**降低 Python 关键词得分权重**（乘以 0.3），确保业务排序优先。

### 8.5 统一意图解析与二级路由分发

#### 8.5.1 性能瓶颈与合并策略

**v2.1 原方案**存在严重的 LLM 串行调用延迟问题：

```
用户请求 → Router (LLM #1, ~1.5s)
         → _classify_sub_intent (LLM #2, ~1.5s)   ← 仅判断 sub_route
         → _XXX_INTENT_PROMPT  (LLM #3, ~1.5s)     ← 仅抽取该路由的结构化意图
         → Answer 格式化       (LLM #4, ~1.5s)
─────────────────────────────────────────────────
合计：4 次 LLM 调用，~6s 纯 LLM 延迟
```

**v2.2 优化方案**：将 LLM #2（二级路由判断）与 LLM #3（结构化意图抽取）合并为一次 LLM 调用，通过统一 Prompt 同时输出 `sub_route` 与完整的 `hard_filters` + `query_type` + 条件标志：

```
用户请求 → Router (LLM #1, ~1.5s)
         → _UNIFIED_INTENT_PROMPT (LLM #2, ~1.5s)  ← sub_route + 全路由结构化意图
         → Answer 格式化          (LLM #3, ~1.5s)
─────────────────────────────────────────────────
合计：3 次 LLM 调用，~4.5s 纯 LLM 延迟（节省 ~25%）
```

#### 8.5.2 统一意图解析 Prompt

```python
_UNIFIED_INTENT_PROMPT = ChatPromptTemplate.from_messages([
    ("system", """你是招投标领域的智能查询意图解析专家。
请一次性完成两项任务：① 判断二级路由（sub_route）；② 提取结构化过滤条件。

输出 JSON 格式：
{
  "sub_route": "company_query" | "product_query" | "bidding_query" | "all",
  "query_type": "...",
  "hard_filters": {
    // === 三路由共用字段 ===
    "province": "省份 或 null",
    "city": "城市 或 null",

    // === company_query 专用（sub_route="company_query" 时填写）===
    "company_name": "企业名称 或 null",
    "credit_code": "统一社会信用代码 或 null",
    "industry": "所属行业（如 软件信息、日用百货）或 null",
    "company_level": "企业等级（如 中型企业、大型企业）或 null",
    "company_type": "企业类型 或 null",
    "business_status": "经营状态 或 null",
    "credit_rating": "信用评级 或 null",

    // === product_query 专用（sub_route="product_query" 时填写）===
    "product_name": "产品名称（如 电剪刀、防水涂料）或 null",
    "category": "产品大类 或 null",
    "subcategory": "产品小类 或 null",
    "supplier_name": "供应商名称 或 null",
    "price_range": {"min": number, "max": number} 或 null,

    // === bidding_query 专用（sub_route="bidding_query" 时填写）===
    "purchaser": "采购人/招标单位 或 null",
    "successful_bidder": "中标供应商 或 null",
    "agent": "代理机构 或 null",
    "project_number": "项目编号 或 null",
    "project_category": "项目类别 或 null",
    "project_stage": "项目阶段（结果公告/招标公告/更正公告）或 null",
    "time_range": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"} 或 null,
    "winning_amount_range": {"min": number, "max": number} 或 null
  },
  "semantic_keywords": ["业务关键词"],
  "exact_tokens": ["精确的公司名、项目编号等"],
  "sort_by": "price_asc"|"price_desc"|"amount_desc"|"amount_asc"|"date_desc"|"date_asc"|"relevance"|null,
  "aggregation": "max_amount"|"count"|"sum"|null,
  "top_n": number|null,
  "need_penalty_check": true/false,
  "need_contact": true/false
}

=== sub_route 判断规则 ===
- 查询涉及企业/供应商信息（行业、等级、信用、不良记录等）→ "company_query"
- 查询涉及产品/物资/价格/行情/供应商联系方式 → "product_query"
- 查询涉及招标/投标/中标/采购项目历史 → "bidding_query"
- 语义模糊同时涉及多类 或 无法明确归类 → "all"

=== 各 sub_route 的 query_type 枚举 ===
【company_query】:"supplier_recommend"|"penalty_check"|"company_detail"|"mixed"
  - 推荐/找几个/哪些公司/供应商 → "supplier_recommend"
  - 是否有不良记录/处罚/违法 → "penalty_check"，同时 need_penalty_check=true
  - 某具体公司的详细信息 → "company_detail"

【product_query】:"price_inquiry"|"supplier_search"|"product_detail"|"mixed"
  - 行情价/多少钱/价格 → "price_inquiry"，sort_by 按价格
  - 找供应商/推荐供应商 → "supplier_search"
  - 要联系人电话/地址 → need_contact=true

【bidding_query】:"purchaser_query"|"bidder_query"|"project_detail"|"aggregation"|"mixed"
  - 招标过什么/采购了什么 → "purchaser_query"
  - 中标了什么/代理过什么 → "bidder_query"
  - 金额最大/最高/TOP → aggregation="max_amount"，sort_by="amount_desc"
  - 提到具体年份如"2024年" → 提取到 time_range
  - 项目阶段默认"结果公告"（已中标），除非明确要查招标公告

=== 重要规则 ===
- 仅填写 sub_route 对应的专用字段，其他路由字段统一设为 null
- 行业关键词提取词元（如"软件信息"→["软件","信息"]）
- semantic_keywords 去除无意义词（推荐/查询/找几个），保留业务实体
- price_range / winning_amount_range 中 min/max 使用纯数字，不要带单位
- sort_by: price_asc/price_desc 用于 product；amount_desc/amount_asc/date_desc/date_asc 用于 bidding
- need_penalty_check 和 need_contact 仅当用户明确表达对应需求时设为 true
"""),
    ("user", "用户查询：{question}\n请仅输出 JSON。"),
])
```

> **设计说明**：统一 Prompt 将三个独立路由的意图抽取规则合而为一，LLM 先判断 `sub_route` 再填充对应的 `hard_filters` 和 `query_type`，无关路由字段设为 `null`。`SearchIntent` 的解析器（`with_structured_output`）对所有字段做容错处理（`null` → `None` / `[]` / `false`），保证下游代码零改动。

#### 8.5.3 二级路由分发入口（合并后）

```python
# ── 二级路由 → 表名 + 查询函数 映射（v2.2：移除 intent_prompt，查询函数接收预解析的 SearchIntent）──
_SUB_ROUTE_MAP: dict[str, dict] = {
    "company_query": {
        "tables": ["company_info", "company_penalty"],
        "query_fn": "_query_company_data",
    },
    "product_query": {
        "tables": ["product_info"],
        "query_fn": "_query_product_data",
    },
    "bidding_query": {
        "tables": ["bid_project"],
        "query_fn": "_query_bidding_data",
    },
    "all": {
        "tables": ["company_info", "company_penalty", "product_info", "bid_project"],
        "query_fn": "_query_all_tables",
    },
}


def _parse_unified_intent(question: str, llm: ChatOpenAI) -> SearchIntent:
    """统一意图解析：一次 LLM 调用同时完成 sub_route 判断 + structured filters 抽取。

    替代旧方案中的 _classify_sub_intent() + 三个独立 Intent Prompt，
    将 2 次串行 LLM 调用合并为 1 次。
    """
    chain = _UNIFIED_INTENT_PROMPT | llm.with_structured_output(SearchIntent)
    return chain.invoke({"question": question})


def node_price_inquiry(state: AgentState) -> dict:
    """智能询价节点 — v2.2 合并版（统一意图解析 + 二级路由分发）。"""
    messages = state.get("messages", [])
    if not messages:
        return { ... }

    question = str(messages[-1].content)
    llm = _build_llm()

    # Step 1：统一意图解析（一次 LLM 调用完成 sub_route + hard_filters + query_type）
    intent = _parse_unified_intent(question, llm)

    # Step 2：根据 intent.sub_route 分发到对应的查询函数（不再传 llm，不再做二次意图解析）
    route_config = _SUB_ROUTE_MAP.get(intent.sub_route, _SUB_ROUTE_MAP["all"])
    query_fn = getattr(sys.modules[__name__], route_config["query_fn"])
    query_result = query_fn(intent)  # 查询函数接收预解析的 SearchIntent，直接执行 SQL

    # Step 3：格式化结果 + 输出字段筛选
    # ... 同现有逻辑

    # 性能日志（建议记录）
    # logger.info(f"sub_route={intent.sub_route} query_type={intent.query_type} "
    #             f"tables={route_config['tables']}")
```

> **关键变更**：
> - 移除 `_classify_sub_intent()` 函数
> - 移除 `_SUB_ROUTE_MAP` 中的 `intent_prompt` 字段（统一 Prompt 在外部调用一次）
> - 查询函数签名从 `query_fn(question, llm)` 变更为 `query_fn(intent: SearchIntent)`，不再内部调 LLM
> - 新增 `_parse_unified_intent()` 作为唯一的意图解析入口
> - `_query_all_tables()` 仍遍历全部 4 张新表，但不依赖任何旧数据库
> - 旧 `_PRICE_DBS` 及相关的 `_query_price_data()` 逻辑将被完全移除

#### 8.5.4 对意图分类准确率的影响评估

| 维度 | v2.1（串行两阶段） | v2.2（统一单阶段） | 结论 |
|------|-------------------|-------------------|------|
| sub_route 判断准确率 | 独立 Prompt 专注分类 | 统一 Prompt 同时分类 + 抽取 | **持平或略优**：LLM 在完整上下文中判断 sub_route 可能更准（先看到过滤字段需求再反推路由） |
| hard_filters 抽取完整度 | 路由特定 Prompt，字段集小 | 统一 Prompt，字段集大但仅填相关部分 | **持平**：LLM 明确知道"仅填当前路由字段，其余 null" |
| 边界歧义场景（如 "找防水涂料供应商，看看有没有不良记录"） | 先分类为 product_query，丢失 penalty 需求 | 统一 Prompt 在解析时同时感知两种意图，可设为 `sub_route="all"` 或 `need_penalty_check=true` | **v2.2 更优** |
| Prompt 长度 | ~400 tokens × 3 个 = 1200 tokens（总） | ~900 tokens × 1 个（实际只用 1 个） | **v2.2 更省**（总 token 消耗减少 ~25%） |

> **结论**：合并后的统一 Prompt 在准确率上不劣于原方案，在边界歧义场景下反而有优势。唯一风险是 Prompt 较长可能导致 LLM 遗漏个别字段，建议在 `SearchIntent` 解析层增加默认值回填逻辑（见 §8.5.5）。

#### 8.5.5 容错与默认值回填

```python
# 在 _parse_unified_intent() 返回后，增加默认值回填确保健壮性
def _safe_parse_intent(raw: SearchIntent) -> SearchIntent:
    """容错回填：防止 LLM 遗漏字段导致下游 NullPointer。"""
    if not raw.sub_route or raw.sub_route not in _SUB_ROUTE_MAP:
        raw.sub_route = "all"
    if not raw.query_type:
        raw.query_type = "mixed"
    if raw.hard_filters is None:
        raw.hard_filters = HardFilters()
    if raw.semantic_keywords is None:
        raw.semantic_keywords = []
    if raw.exact_tokens is None:
        raw.exact_tokens = []
    return raw
```

---

## 9. 数据维护与更新接口设计

### 9.1 数据更新策略总览

| 更新方式 | 适用场景 | 频率 | 说明 |
|---------|---------|------|------|
| **CSV 全量替换** | 初始导入、外部系统导出的数据更新 | 按需（周/月） | 从 `raw_tables/` 目录读取最新 CSV，全量替换目标表 |
| **API 接口** | 实时数据录入/修正 | 实时 | FastAPI 端点，供管理后台或外部系统调用 |

### 9.2 CSV 全量替换流程

```
┌──────────────────┐     ┌─────────────────┐     ┌─────────────────────┐
│ raw_tables/ 目录  │ ──→ │ csv_to_mysql.py │ ──→ │ ztb_clean 目标表     │
│ 更新后的 CSV 文件  │     │ (全量 REPLACE)   │     │ (TRUNCATE + LOAD)   │
└──────────────────┘     └─────────────────┘     └─────────────────────┘
```

**脚本**：新建 `scripts/csv_to_mysql.py`

```python
# scripts/csv_to_mysql.py 核心逻辑
import csv
import pymysql

# 目标数据库（全新纯净库）
_CLEAN_DB = "ztb_clean"

TABLE_MAP = {
    "raw_tables/company_info.csv":    "company_info",
    "raw_tables/company_penalty.csv": "company_penalty",
    "raw_tables/product_info.csv":    "product_info",
    "raw_tables/bid_project.csv":     "bid_project",
}

def import_csv_to_mysql(csv_path: str, table_name: str, conn: pymysql.Connection):
    """流式读取 CSV，批量 INSERT 到 ztb_clean。"""
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        batch = []
        for row in reader:
            cleaned = clean_row(row, table_name)
            batch.append(cleaned)
            if len(batch) >= 5000:
                insert_batch(conn, table_name, batch)
                batch = []
        if batch:
            insert_batch(conn, table_name, batch)

def full_replace(csv_path: str, table_name: str, conn: pymysql.Connection):
    """TRUNCATE + 批量 INSERT（适用于全量替换场景）。"""
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE `{table_name}`")
    conn.commit()
    import_csv_to_mysql(csv_path, table_name, conn)
```

**风险提示**：`TRUNCATE` 不可逆，建议在维护窗口执行；可先导入到 `{table_name}_staging` 临时表，验证通过后 `RENAME TABLE` 原子切换。

### 9.3 后续数据更新方式

`ztb_clean` 作为纯净的独立数据库，数据更新来源仅限于：

1. **CSV 文件更新**：`raw_tables/` 目录下的 CSV 文件被外部系统刷新后，重新运行 `csv_to_mysql.py` 执行全量替换
2. **API 增量修改**：通过 FastAPI 端点对单条记录进行增删改（见 §9.4）
3. **手动维护**：通过 DBeaver/Navicat 等 MySQL 客户端直接操作

> **注意**：`ztb_clean` 不执行任何从外部数据库拉取数据的 ETL 操作，确保数据的纯净可追溯——所有数据变更都可追溯到 CSV 文件版本或 API 操作日志。

### 9.4 API 接口设计（FastAPI）

为支持管理后台或其他系统对数据的增删改查，在 `agent/__main__.py` 或新建 `api/` 模块中扩展 FastAPI 端点：

```python
# api/data_maintenance.py（新建）

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="招投标数据维护 API")

# ── 企业信息 ──
@app.get("/api/company/{credit_code}")
async def get_company(credit_code: str):
    """按统一社会信用代码查询企业信息。"""

@app.post("/api/company")
async def create_company(company: CompanyCreate):
    """新增企业信息。"""

@app.put("/api/company/{credit_code}")
async def update_company(credit_code: str, company: CompanyUpdate):
    """更新企业信息。"""

# ── 产品信息 ──
@app.get("/api/product/search")
async def search_products(keyword: str, category: str = None, limit: int = 20):
    """关键词搜索产品。"""

# ── 招标项目 ──
@app.get("/api/bid_project/{project_number}")
async def get_bid_project(project_number: str):
    """按项目编号查询招标项目。"""

# ── 数据统计 ──
@app.get("/api/stats/overview")
async def get_data_overview():
    """返回四张表的行数统计和最后更新时间。"""
```

### 9.5 管理后台（轻量方案）

| 方案 | 适用场景 | 推荐度 |
|------|---------|--------|
| **Flask-Admin / SQLAdmin** | 快速搭建 CRUD 后台，自动生成表单 | ⭐⭐⭐ (推荐) |
| **Streamlit** | 数据探索 + 手动修正，适合内部运维 | ⭐⭐ |
| **直接 MySQL 客户端**（DBeaver/Navicat） | 开发/测试阶段手动维护 | ⭐⭐⭐ (当前阶段) |

**推荐**：初期直接使用 DBeaver 等工具手动维护；待数据量增长后，通过 `api/` 模块的 FastAPI 端点 + SQLAdmin 搭建轻量管理后台。

---

## 10. 实施步骤与风险

### 10.1 实施阶段总览

```
阶段一：数据准备       阶段二：检索逻辑改造     阶段三：测试与上线
   (2~3天)              (3~4天)                 (2~3天)
  ┌──────────┐       ┌──────────────┐       ┌──────────────┐
  │ CSV 分析  │  ──→  │ Intent Prompt │  ──→  │ 单元测试     │
  │ DDL 设计  │       │ SQL 生成器    │       │ 端到端测试   │
  │ 数据导入  │       │ 二级路由分发  │       │ 灰度上线     │
  │ 索引构建  │       │ 结果格式化    │       │ 文档交付     │
  └──────────┘       └──────────────┘       └──────────────┘
```

### 10.2 阶段一：数据准备（预计 2~3 天）

| 任务 | 产出物 | 预估工时 |
|------|--------|---------|
| 1.1 分析四个 CSV 文件的数据质量（空值率、重复率、编码问题） | 数据质量报告 | 0.5 天 |
| 1.2 编写并执行 DDL（4 张表结构） | `scripts/schema.sql` | 0.5 天 |
| 1.3 编写 `csv_to_mysql.py` 导入脚本 | `scripts/csv_to_mysql.py` | 1 天 |
| 1.4 执行数据导入，处理异常行 | 数据就绪 | 0.5 天 |
| 1.5 构建 FULLTEXT + BTREE 索引，验证 `EXPLAIN` | 索引就绪 | 0.5 天 |

### 10.3 阶段二：检索逻辑改造（预计 3~4 天）

| 任务 | 产出物 | 预估工时 |
|------|--------|---------|
| 2.1 扩展 `SearchIntent` / `HardFilters` 数据模型 | 修改 `agent/nodes/price_inquiry.py` | 0.5 天 |
| 2.2 设计并实现统一意图解析 Prompt `_UNIFIED_INTENT_PROMPT`（合并二级路由判断 + 结构化过滤条件抽取，单次 LLM 调用） | 同上 | 0.5 天 |
| 2.3 实现 `_parse_unified_intent()` 统一意图解析（替代 `_classify_sub_intent`，直接返回完整 `SearchIntent`） | 同上 | 0.5 天 |
| 2.4 实现三个专用查询函数（`_query_company_data` 等，接收预解析的 `SearchIntent`，不再内部调 LLM） | 同上 | 1 天 |
| 2.5 扩展 `_build_hard_conditions_extended()` + 排序逻辑 | 同上 | 0.5 天 |
| 2.6 改造 `node_price_inquiry()` 入口（统一意图解析 → sub_route 分发 → 各查询函数） | 同上 | 0.5 天 |
| 2.7 扩展 `RouterDecision` 增加 `sub_intent` (可选) | 修改 `agent/router.py` | 0.5 天 |

### 10.4 阶段三：测试与上线（预计 2~3 天）

| 任务 | 产出物 | 预估工时 |
|------|--------|---------|
| 3.1 编写二级意图分类测试用例（≥30 条标注样本 × 3 类） | `test/test_sub_route.py` | 0.5 天 |
| 3.2 端到端集成测试（三类路由各 15 条真实用户问题，验证检索结果准确性） | 测试报告 | 0.5 天 |
| 3.3 典型场景集成测试（三类路由各 10 条真实问题） | 测试报告 | 0.5 天 |
| 3.4 性能基准测试（对比 4 表精准查询性能） | 性能报告 | 0.5 天 |
| 3.5 灰度上线 + 日志监控 | 线上观察 | 1 天 |

### 10.5 风险与缓解措施

| 风险 | 级别 | 影响 | 缓解措施 |
|------|------|------|---------|
| **二级意图分类准确率低** | 🔴 高 | 公司查询被路由到产品表，返回不相关内容 | ① `all` 兜底模式自动回退遍历全部 4 张表 ② 标注 ≥90 条样本训练 prompt ③ 二级路由日志记录分类理由供审核 |
| **CSV 数据质量问题**（空值、编码、格式不一致） | 🟡 中 | 查询结果不完整或报错 | ① `csv_to_mysql.py` 内置字段清洗逻辑 ② 导入前 `csvstat` 预检 ③ 异常行记录到 `error_rows.log` |
| **FULLTEXT 索引不生效** | 🟡 中 | 关键词检索回退到 LIKE '%...%' 全表扫描 | ① `EXPLAIN` 验证索引命中 ② ngram_token_size=2 必须配置 ③ MySQL `my.cnf` 中 `ft_min_word_len=1` |

---

## 11. 附录：现有代码改动清单

### 11.1 需要修改的文件

| 文件 | 改动行数 | 改动性质 |
|------|---------|---------|
| `agent/nodes/price_inquiry.py` | +250 行 / -80 行 | 核心改造：移除旧 `_PRICE_DBS` 和 `_query_price_data()`；新增 `_CLEAN_DB` 配置、3 个 Intent Prompt、3 个查询函数、二级路由分发、排序适配 |
| `agent/router.py` | +5 行（可选） | `RouterDecision` 新增 `sub_intent` 字段（若不改 RouterDecision，则完全不动此文件） |
| `.env` | 更新 | 新增 `MYSQL_CLEAN_DB=ztb_clean` 数据库名配置项 |

### 11.2 需要新建的文件

| 文件 | 用途 |
|------|------|
| `agent/nodes/output_templates.py` | 统一输出字段配置模型（`FieldDescriptor` + `OutputTemplate`）及运行时筛选引擎 `_apply_output_template()`（详见 [§7.4](#74-统一输出字段框架)） |
| `scripts/schema.sql` | 四张新表的 DDL（目标库 `ztb_clean`） |
| `scripts/csv_to_mysql.py` | CSV → MySQL 批量导入脚本（纯 CSV 数据导入，不涉及外部数据库） |
| `test/test_sub_route.py` | 二级路由意图分类测试用例 |

### 11.3 不需要修改的文件

以下文件**零改动**：

| 文件 | 原因 |
|------|------|
| `agent/state.py` | `AgentState` 只含 `messages`/`router_intent`/`business_result`，二级路由信息在 `business_result.data` 内部传递 |
| `agent/graph.py` | Graph 节点和条件边完全不变 |
| `agent/nodes/__init__.py` | 不新增节点，导出不变 |
| `agent/__main__.py` | CLI 入口不变 |
| `agent/checkpointer.py` | 与检索逻辑无关 |
| `public_kb/` 全部 | Milvus RAG 与 MySQL 结构化检索独立 |
| `agent/nodes/knowledge_qa.py` | 不相关 |
| `agent/nodes/general_chat.py` | 不相关 |
| `agent/nodes/fallback.py` | 不相关 |
| `agent/nodes/doc_qa.py` | 不相关 |

---

## 附录 B：关键代码路径速查

| 想了解... | 看这里 |
|----------|--------|
| 当前三阶段检索核心流程 | [agent/nodes/price_inquiry.py L589-L671](../agent/nodes/price_inquiry.py#L589-L671) |
| 列名分类规则 | [agent/nodes/price_inquiry.py L309-L394](../agent/nodes/price_inquiry.py#L309-L394) |
| SQL 生成器 | [agent/nodes/price_inquiry.py L481-L528](../agent/nodes/price_inquiry.py#L481-L528) |
| 混合重排序 | [agent/nodes/price_inquiry.py L551-L583](../agent/nodes/price_inquiry.py#L551-L583) |
| 路由决策模型 | [agent/router.py L29-L41](../agent/router.py#L29-L41) |
| Graph 构建与节点注册 | [agent/graph.py L108-L181](../agent/graph.py#L108-L181) |
| AgentState 定义 | [agent/state.py L19-L37](../agent/state.py#L19-L37) |
| 项目全览 | [docs/project_overview.md](./project_overview.md) |
| CSV 字段说明 | [raw_tables/字段说明.txt](../raw_tables/字段说明.txt) |

---

> **文档状态**：待评审  
> **下一步**：评审通过后进入阶段一（数据准备），开始 DDL 编写与 CSV 数据导入。
