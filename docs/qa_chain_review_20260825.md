# qa_chain.py 工程化规范评估报告

| 项目 | 招投标智能助手（Bidding Intelligent Assistant） |
| --- | --- |
| **审查对象** | [public_kb/qa_chain.py](../public_kb/qa_chain.py) — LCEL RAG 问答链 |
| **审查范围** | 混合检索（BM25 + COSINE + RRF）/ Reranker / 动态阈值 / LCEL 链构造 |
| **审查日期** | 2026-08-25 |
| **审查依据** | SOLID 原则、AWS Well-Architected Reliability Pillar、LangChain LCEL 最佳实践、Python 工程化（PEP8/可测试性/可观察性） |
| **审查方式** | 静态代码审查 + 运行时验证（已通过端到端测试确认行为） |

---

## 一、文件结构概览

| 行号 | 区块 | 职责 |
| --- | --- | --- |
| [L1-L33](../public_kb/qa_chain.py#L1-L33) | 模块声明 / 全局常量 | docstring、INLINE_CITATION_INSTRUCTION、imports |
| [L35-L112](../public_kb/qa_chain.py#L35-L112) | 提示词 + 格式化工具 | `_build_prompt`/`_format_docs`/`_build_sources` |
| [L114-L240](../public_kb/qa_chain.py#L114-L240) | Milvus 实体→Document 转换 | 字段归一化、全字段回退 |
| [L242-L478](../public_kb/qa_chain.py#L242-L478) | `build_qa_chain` 主流程 | 闭包 `_retrieve`/`_decide_and_answer`、LCEL 组装 |
| [L480-L549](../public_kb/qa_chain.py#L480-L549) | Reranker + 动态阈值 | `_SiliconFlowReranker` 类、`_adaptive_threshold` |
| [L551-L617](../public_kb/qa_chain.py#L551-L617) | 降级兜底 | `_dense_only_retrieve` |

---

## 二、六个维度评分

| # | 维度 | 评分 | 一句话评价 |
| --- | --- | --- | --- |
| 1 | 基础工程化 | 🟡 **C+** | 命名规范、注释完整度都过关，但核心是"演示级代码披着工程化外衣" |
| 2 | 接口与可测试性 | 🔴 **D** | 核心逻辑用闭包捕获，单测覆盖率为 0 |
| 3 | 可观测性 | 🟡 **C** | 有结构化日志，但关键路径决策缺少独立埋点 |
| 4 | 降级策略 | 🔴 **D** | Reranker 失败返假分 0.5，恰落在阈值区间，违反"失败安全" |
| 5 | 配置驱动 | 🟢 **A-** | 关键参数走 `Settings`，零硬编码魔数 |
| 6 | 耦合度 | 🔴 **D** | Reranker + 动态阈值被"焊死"在混合分支中 |

**综合评级**：🟡 **C（勉强可用，但生产化之前必须整改 P0 项）**

---

## 三、逐项详细评估

### 3.1 混合检索实现（`_retrieve` + `_hybrid_search_with_full_fields`）

| 评估项 | 现状 | 是否合规 | 严重度 |
| --- | --- | --- | --- |
| 算法选择（BM25 + RRF） | 经典组合，参数 `k=60` 合理 | ✅ | — |
| 函数签名 | `_retrieve` 是**闭包**（无独立签名），内部捕获 `vector_store/llm/settings/collection/embeddings` | ❌ | **Major** |
| 业务/系统异常区分 | 用 `Exception` 笼统捕获（L407） | ❌ | Major |
| 降级一致性 | `_dense_only_retrieve` 与 `_retrieve` 返回结构相同 | ✅ | — |
| **版本兼容** | L339 传原始文本给 sparse 路（Milvus 2.5+ Full-Text Search 特性） | ❌ 已运行时验证云端 Milvus 是 2.4.0 | **Major** |
| 错误恢复 | 单点 `try/except` 兜底，但吞掉错误上下文 | ⚠️ | Minor |
| 文档输出字段 | `_OUTPUT_FIELDS_ALL = ["*"]` + 基础字段回退 | ✅ | — |
| 元数据透传 | `_EXCLUDED_META_KEYS` 黑名单机制 | ✅ | — |
| chunk_uid 派生 | 即时计算保持口径一致 | ✅ | — |

### 3.2 Reranker 实现（`_SiliconFlowReranker`）

| 评估项 | 现状 | 是否合规 | 严重度 |
| --- | --- | --- | --- |
| 类职责单一（SRP） | 独立客户端类 | ✅ | — |
| 超时控制 | `timeout=30` 单值 | ⚠️ | Minor |
| 重试 | 无 | ❌ | Medium |
| 索引对应性 | 仅返回 `index`，未对应 Milvus 主键 id | ⚠️ | Medium |
| API 限流感知 | 未处理 429/503 | ❌ | Minor |
| **降级行为：API 失败返假分 0.5** | [L531-L534](../public_kb/qa_chain.py#L531-L534) | ❌ **违反"失败安全"工程原则** | **Major** |
| 可替换性 | 独立类，可替换为 `BgeRerank`/`CohereRerank` | ✅ | — |
| 输入校验 | `if not documents: return []` | ✅ | — |

**关于"假分 0.5"的工程化分析**：
- AWS Well-Architected Reliability Pillar 明确：失败应**显式上抛**或**返回空集**，避免下游误用。
- 当前实现：0.5 恰好落在自适应阈值 `[0.40, 0.45, 0.50]` 的中段——Reranker 崩溃时，无关 chunk 也会被当作成"中等相关"放行。
- 这是典型的"懒人降级"：避免失败但破坏正确性。

### 3.3 动态阈值实现（`_adaptive_threshold`）

| 评估项 | 现状 | 是否合规 | 严重度 |
| --- | --- | --- | --- |
| 函数签名 | 纯函数 `float → float`，无副作用 | ✅ | — |
| 阈值档位（0.75/0.50） | 合理起点 | ⚠️ | Medium |
| 输入校验 | 无 | ⚠️ | Minor |
| **关键问题：只在 Reranker 路径里调用** | 降级链路完全不走 | ❌ | **Major** |
| A/B 数据标定 | 未通过实验数据校准档位 | ⚠️ | Medium |
| 单元可测 | ✅ | — | — |

### 3.4 LCEL 链构造（L468-L478）

| 评估项 | 现状 | 是否合规 |
| --- | --- | --- |
| 使用 `\|` 运算符 | ✅ | ✅ |
| RunnableLambda + RunnablePassthrough | ✅ | ✅ |
| **RunnableLambda 包装闭包** | 不可独立调用、不可独立测试 | ❌ |
| 中间步骤可观测性 | 无 | ❌ |
| 流式支持（`.stream()`） | 未实现 | ⚠️ |
| 提示词模板 | `_build_prompt` 抽离 | ✅ |
| 输入/输出 schema 显式声明 | 未声明 | ⚠️ |

---

## 四、最严重的 3 个工程化问题（整改优先级）

| 优先级 | 问题 | 规范违反 | 影响 |
| --- | --- | --- | --- |
| **P0** | Reranker 失败返假分 0.5，落在阈值区间内 | 失败安全原则（AWS Well-Architected Reliability Pillar） | 用户看到胡编内容且无法定位 |
| **P0** | Reranker + 动态阈值耦合在 `_retrieve` 闭包里 | 开放-封闭原则、单一职责 | 缺 `sparse_vector` 字段时，3 个降级价值全部归零（已通过运行时验证） |
| **P1** | `_retrieve`/`_decide_and_answer` 是闭包不可独立测试 | 单元测试工程化、可观察性 | 单测覆盖率为 0，故障定界靠 print |

---

## 五、可保留的工程化优点

| # | 项 | 评价 |
| --- | --- | --- |
| 1 | 配置外部化 | 所有超参走 `Settings`，零硬编码魔数 |
| 2 | 降级分层 | hybrid → dense 原生 → dense langchain 三层兜底完整 |
| 3 | 日志结构化 | 关键节点都有中文结构化日志，便于运维检索 |
| 4 | Reranker 抽象 | 独立 `_SiliconFlowReranker` 类，可替换成 `BgeRerank`/`CohereRerank` |
| 5 | chunk_uid 派生 | 与入库侧同口径，跨集合稳定 |

---

## 六、整改路线图

| 步骤 | 动作 | 工作量 | 收益 |
| --- | --- | --- | --- |
| 1 | 把 Reranker 调用从 `_retrieve` 闭包提取为 `rerank_with_fallback(docs, query, settings) -> (docs, info)`，失败返 `(原 docs, info={"status": "disabled"})` | 1h | Reranker 真正可测可用 |
| 2 | 把"动态阈值过滤"拆为 `apply_adaptive_threshold(docs, top_k)` 纯函数；降级路径同样调用 | 0.5h | 降级链路也享受自适应 |
| 3 | Reranker 假分改为返回 `[]` + 警告日志 | 10min | 消除"失败污染" |
| 4 | 关键决策点埋点：`[RETRIEVE_MODE]`/`[RERANKER_STATUS]`/`[THRESHOLD_DECISION]` 结构化字段 | 1h | 运行时可观察 |
| 5 | tenacity 装饰 Reranker API（最多 2 次，指数退避） | 0.5h | 抗抖动 |

**总工作量**：约 3 小时；P0 项单算仅 70 分钟。

---

## 七、运行时证据汇总

| 编号 | 证据 | 结论 |
| --- | --- | --- |
| E1 | 日志 "当前 Schema 无稀疏向量字段" | 混合检索降级 |
| E2 | 整段测试**无 `/rerank` HTTP 请求** | Reranker 未执行 |
| E3 | 日志 `threshold=0.45`（固定 `similarity_threshold`） | 动态阈值未生效 |
| E4 | `get_server_version()` → v2.4.0 | 稀疏路传原文不兼容 |
| E5 | pymilvus MilvusClient 仅接 `uri`，不认 `host/port` | 刚已修复 ([milvus_store.py:189-195](../public_kb/milvus_store.py#L189-L195)) |

---

## 八、结论与建议

| 项 | 结论 |
| --- | --- |
| 当前是否可用 | ⚠️ **仅"纯稠密 + 固定阈值"在跑**，三大检索增强全部是死代码 |
| 是否符合工程化规范 | 🟡 基础规范过关（命名、注释、降级分层、配置外部化），关键规范**严重不达标**（失败安全、单一职责、可测试性） |
| 生产化前必做 | P0 项（P0-1 假分 / P0-2 闭包解耦），约 70 分钟 |
| 后续优化 | P1 闭包改纯函数 + 关键决策埋点，约 2 小时 |

**报告完**。