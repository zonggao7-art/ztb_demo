# P1 级优化实施总结报告

> **项目名称**：招投标智能助手 `zhaotoubiao_demo`  
> **执行依据**：[p0_fix_and_p1_feasibility_report.md](./p0_fix_and_p1_feasibility_report.md)  
> **执行日期**：2026-08-08  
> **报告版本**：v1.0  
> **本次核心改动文件**：[agent/nodes/price_inquiry.py](../agent/nodes/price_inquiry.py)

---

## 1. 工作概述

本次工作严格对照 `p0_fix_and_p1_feasibility_report.md` 中定义的 3 项 P1 优化任务执行，目标是将 `price_inquiry` 节点从“P0 可用”提升到“具备更强口语化容错、分级降级召回、语义检索接入能力”的状态。

本次实际完成情况可概括为：

1. **P1-2：LLM 关键词改写与去噪** 已完成代码落地；
2. **P1-1：多级降级检索链** 已完成代码落地；
3. **P1-3：向量语义检索作为 MySQL 语义层** 已完成代码接入与集合生命周期管理，但**全量语义集合构建与效果回归未在本次执行窗口内完成**。

> 结论上，本次已经完成 **全部 P1 的代码级改造**，但 **P1-3 的运行级初始化任务** 仍需继续推进。

---

## 2. 执行步骤梳理

### 2.1 第一步：核对 P1 原始要求与当前代码状态

首先对 [p0_fix_and_p1_feasibility_report.md](./p0_fix_and_p1_feasibility_report.md) 进行了逐段比对，并复核当前 [price_inquiry.py](../agent/nodes/price_inquiry.py) 的实现状态，确认：

- P0 三项修复已经存在；
- P1 三项优化在代码中均未完整落地；
- 当前可直接复用的基础设施主要有：
  - `public_kb.config.Settings`
  - `public_kb.embedding_service.create_embeddings`
  - Milvus / PyMilvus 依赖
  - 现有 `_query_tables()` 与 `_rank_records()` 查询骨架

### 2.2 第二步：落地 P1-2 关键词改写与去噪

围绕意图解析链进行了两层补强：

1. **Prompt 层增强**
   - 在 `_UNIFIED_INTENT_SYSTEM` 中新增口语化查询约束；
   - 明确要求 `semantic_keywords` 只保留业务实体词，去除“最近、有没有、关于、方面、帮我、查一下、哪些、什么”等噪音词。

2. **后处理层增强**
   - 新增 `_normalize_token()`、`_dedupe_keep_order()`、`_denoise_keywords()`；
   - 新增 `_post_process_intent()`；
   - 在 `_parse_unified_intent()` 与 `_safe_parse_intent()` 中统一接入；
   - 调整 `_extract_keywords()`，去掉误伤业务语义的旧停用词策略。

对应关键位置：

- [_post_process_intent()](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/price_inquiry.py#L386)
- [_build_search_term()](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/price_inquiry.py#L828)

### 2.3 第三步：落地 P1-1 多级降级检索链

在保留现有 MySQL 查询主路径的基础上，对查询执行链做了结构化改造，新增 `_execute_recall_chain_for_table()`，将单次查询扩展为五级召回策略：

1. **Level 1：OR 语义 FULLTEXT**
2. **Level 2：AND 语义 FULLTEXT**
3. **Level 3：LIKE 通配回退**
4. **Level 4：逐关键词拆分重试（单关键词 FULLTEXT / LIKE）**
5. **Level 5：全表扫描兜底（保留 LIMIT 与硬过滤）**

同时补充了以下能力：

- `_build_full_scan_sql()`：用于最终兜底；
- `_build_candidate_sql()`：支持 `search_mode`、`keyword_override` 等参数化能力；
- `_merge_result_record()`：用于多级召回结果去重合并；
- `_clean_result_row()`：保留 `_score_` / `_vector_score_` / `_recall_stage_` 等元信息；
- `_rank_records()`：引入 `recall_stage` 权重衰减。

对应关键位置：

- [_execute_recall_chain_for_table()](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/price_inquiry.py#L1307)
- [_query_tables()](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/price_inquiry.py#L1445)

### 2.4 第四步：落地 P1-3 Milvus 语义检索接入

本次未新增独立模块，而是在 `price_inquiry.py` 内直接补齐了结构化语义层所需的最小能力集：

1. **Embedding 客户端懒加载**
   - `_get_embeddings()`

2. **Milvus 连接与集合管理**
   - `_connect_semantic_milvus()`
   - `_create_mysql_semantic_collection()`
   - `_rebuild_mysql_semantic_collection()`
   - `_ensure_mysql_semantic_collection()`

3. **MySQL 行文本化与向量化**
   - `_semantic_columns()`
   - `_build_semantic_document_text()`
   - `_fetch_semantic_source_rows()`

4. **语义召回与回表**
   - `_semantic_recall_candidates()`
   - `_build_vector_recall_sql()`
   - `_query_semantic_rows()`

5. **非阻塞初始化优化**
   - 将语义集合初始化改为**后台自举**，避免首次查询同步构建全量向量索引而卡住主流程。

对应关键位置：

- [_ensure_mysql_semantic_collection()](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/price_inquiry.py#L727)
- [_semantic_recall_candidates()](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/price_inquiry.py#L760)

### 2.5 第五步：执行基础验证

本次完成了以下验证：

1. `python -m py_compile d:\DEMO\zhaotoubiao_demo\agent\nodes\price_inquiry.py`
   - 结果：**通过**

2. 关键词去噪烟测
   - 输入：`['最近', '保温材料', '相关信息']`
   - 输出：`['保温材料']`

3. MySQL 查询链烟测（关闭语义层自举）
   - 测试语句：`最近有没有关于保温材料方面的中标项目啊`
   - 结果：`{'total_found': 20, 'sql_count': 1}`

4. 语义召回入口烟测（关闭语义层自举）
   - 结果：返回 `{}`，说明在集合不存在时可**平稳降级**，不会打断主查询链

---

## 3. 核心思路与方案选型依据

### 3.1 问题拆解思路

本次 P1 并不是简单加几个函数，而是围绕“为什么口语化查询仍然不稳定”拆成了三个层次：

| 层次 | 问题 | 对应方案 |
|------|------|---------|
| 意图层 | LLM 会输出噪音关键词，污染检索词 | P1-2：Prompt + 后处理双层去噪 |
| 召回层 | 一条查询链一旦失效就直接空结果 | P1-1：多级降级检索链 |
| 语义层 | MySQL FULLTEXT 无法覆盖同义词与口语化表达 | P1-3：Milvus 语义召回 + MySQL 回表 |

### 3.2 方案选型依据

#### 方案一：优先做“轻量补强”，不推翻现有主链

没有重写 `node_price_inquiry()`，也没有把 SQL 检索整体替换为纯向量方案，而是在现有代码上做“增量增强”。原因是：

- 当前 P0 已经恢复了主流程可用性；
- `price_inquiry.py` 的入口、二级路由、输出模板均已稳定；
- 在已有骨架上扩展，可以降低改动面和回归风险。

#### 方案二：语义层采用“Milvus 召回 ID → MySQL 回表”

没有直接把结构化数据完全迁入向量数据库，而是采用：

> Milvus 负责“找到可能相关的主键”，MySQL 负责“返回真实结构化记录”

这样选的原因是：

- 避免向量库直接承载结构化展示逻辑；
- 输出模板仍然围绕 MySQL 字段运行，不破坏现有格式化体系；
- 便于后续做增量同步和结果融合。

#### 方案三：语义集合初始化改为后台自举

这是本次实现中比较关键的一点。原因在于：

- 4 张表全量约 7.7 万条记录；
- 全量 Embedding 构建显著依赖外部 API 吞吐；
- 如果同步构建，会直接拖慢首个用户请求。

因此本次将 `_ensure_mysql_semantic_collection()` 设计为：

- 集合存在：直接启用；
- 集合不存在：后台启动自举线程，主查询先走 SQL 路径，不阻塞用户。

---

## 4. 技术栈清单

### 4.1 运行时技术栈

| 技术 | 版本 | 应用场景 |
|------|------|---------|
| Python | 3.12.7 | 主运行时 |
| PyMySQL | 2.2.8 | MySQL 查询与回表 |
| PyMilvus | 3.0.1 | Milvus 集合管理、向量检索 |
| LangChain Core | 1.5.1 | Prompt / Runnable / 消息抽象 |
| LangChain OpenAI | 1.4.1 | LLM 与 Embedding 客户端 |
| LangGraph | 1.1.10 | Agent 图编排 |
| langchain-milvus | 0.4.0 | 项目已有 Milvus 集成依赖 |

### 4.2 配置与模型

| 项目 | 当前值 | 应用场景 |
|------|--------|---------|
| LLM 模型 | `deepseek-chat` | 统一意图解析 |
| Embedding 模型 | `BAAI/bge-m3` | 结构化数据语义向量化 |
| MySQL 数据库 | `ztb_clean` | 主结构化检索源 |
| Milvus 地址 | `localhost:19530` | 语义向量集合存储 |

### 4.3 代码中新增/强化的技术能力

- 正则与规则去噪
- 多级召回策略调度
- 向量检索与结构化回表融合
- 异步后台自举
- 结果级去重与分层打分

---

## 5. 已完成的 P1 级优化清单

### 5.1 对照原始要求的完成情况

| 原始要求 | 完成状态 | 实施结果 |
|---------|---------|---------|
| P1-1 多级降级检索链 | ✅ 已完成 | 5 级召回链已接入 `_query_tables()` |
| P1-2 LLM 关键词改写与去噪 | ✅ 已完成 | Prompt 规则增强 + `_post_process_intent()` 后处理已落地 |
| P1-3 引入向量语义检索作为 MySQL 语义层 | ✅ 代码已完成 | Milvus 集合管理、语义召回、MySQL 回表、后台自举均已接入 |

### 5.2 本次实际改动点

1. 新增意图去噪与归一化逻辑
2. 扩展 FULLTEXT 查询串构造函数为多模式
3. 新增全表兜底 SQL 构造器
4. 新增多级检索链调度函数
5. 新增结果去重与合并逻辑
6. 新增 Milvus 语义集合生命周期管理
7. 新增语义召回 + MySQL 回表融合逻辑
8. 新增后台语义集合自举逻辑
9. 修改 `_rank_records()`，引入召回层级权重和向量分加权

---

## 6. 未完成内容、原因与后续方案

### 6.1 未完成项一：MySQL 语义集合的全量构建未完成

**状态**：未完成运行级初始化  
**影响范围**：不影响 SQL 主查询链，但会导致 Milvus 语义召回在集合未建成前暂时不可用

**未完成原因**：

1. 当前语义集合需要对 4 张表约 7.7 万条记录做全量向量化；
2. 全量 Embedding 构建依赖外部模型服务吞吐与额度；
3. 在本次执行窗口内，优先级更高的是先完成代码接入与不阻塞主流程的设计。

**后续可落地方案**：

1. 增加一次性的“全量语义集合初始化”运维执行窗口；
2. 使用批量脚本或管理命令显式触发 `_rebuild_mysql_semantic_collection()`；
3. 构建完成后做 10~20 条口语化查询回归，重点验证：
   - 同义词
   - 超长描述
   - 模糊口语表达

### 6.2 未完成项二：P1-3 的真实召回效果尚未做端到端回归

**状态**：未完成  
**原因**：

- 由于全量语义集合未完成构建，本次仅验证了“入口可执行、集合不存在时可平滑降级”，尚未验证真实语义命中质量。

**后续可落地方案**：

1. 在语义集合构建完成后，补充一轮对照测试：
   - 仅 SQL
   - SQL + 语义召回
2. 对比指标建议：
   - 空结果率
   - Top 5 贴题率
   - 平均响应时延

### 6.3 未完成项三：PyMilvus ORM 风格接口的弃用警告尚未治理

**状态**：未完成  
**原因**：

- 本次优先保证功能接入与项目现有 `public_kb` 风格保持一致；
- 当前代码可运行，但运行时会出现 `PyMilvusDeprecationWarning`。

**后续可落地方案**：

1. 将 `connections.connect / utility.has_collection / Collection(...)` 逐步迁移到 `MilvusClient` 新 API；
2. 同步评估 `public_kb/milvus_store.py` 的统一改造，避免双套风格并存。

---

## 7. 本次产出结论

本次 P1 优化工作已经把 `price_inquiry` 的检索能力从“单链路 SQL 检索”提升为“去噪 + 多级召回 + 语义层接入”的增强版结构，核心代码已经落地并通过基础验证。

从交付视角看：

- **代码级目标**：已完成  
- **运行级目标**：部分完成  
- **剩余重点**：全量语义集合构建与真实业务回归验证

> 建议下一步直接进入“语义集合全量构建 + P1 回归测试”阶段，这样才能把本次代码接入真正转化为线上可感知的召回提升。

