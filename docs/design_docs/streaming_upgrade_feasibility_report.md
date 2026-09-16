# 招投标智能问答系统 — 流式输出升级可行性评估报告

> 评估日期：2026-08-07  
> 评估范围：Agent 意图路由、价格查询分支、PublicKnowledgeRAG 答案生成、通用对话  
> 技术栈：LangChain 0.3+ / LangGraph 1.2+ / Milvus 2.4 / pymysql / DeepSeek API

---

## 目录

1. [需要流式化的模块范围](#1-需要流式化的模块范围)
2. [现有技术栈对流式输出的原生支持程度](#2-现有技术栈对流式输出的原生支持程度)
3. [升级整体思路](#3-升级整体思路)
4. [各模块改造难度评估](#4-各模块改造难度评估)
5. [建议的分步实施路径](#5-建议的分步实施路径)
6. [综合可行性结论与升级价值判断](#6-综合可行性结论与升级价值判断)

---

## 1. 需要流式化的模块范围

### 1.1 当前 LLM 调用点全景扫描

经过对全部源代码的审查，项目中实际存在 LLM 调用的位置如下：

| 序号 | 模块 | 文件 | 调用方式 | 当前是否流式 | 流式价值 |
|------|------|------|----------|:----------:|:--------:|
| ① | **Agent 路由器** | `agent/router.py` | `llm.with_structured_output().invoke()` / `llm.bind_tools().invoke()` | ❌ | 低 |
| ② | **价格查询-意图抽取** | `agent/nodes/price_inquiry.py` | `_INTENT_PROMPT \| llm \| StrOutputParser().invoke()` | ❌ | 低 |
| ③ | **价格查询-结果生成** | `agent/nodes/price_inquiry.py` | **无 LLM 调用**（纯模板拼接 `_format_records`） | N/A | — |
| ④ | **RAG 答案生成** | `public_kb/qa_chain.py` | `prompt \| llm \| StrOutputParser().invoke()` | ❌ | **高** |
| ⑤ | **通用对话** | `agent/nodes/general_chat.py` | **无 LLM 调用**（硬编码兜底文案） | N/A | 中 |

### 1.2 各调用点的流式化需求分析

#### ① Agent 路由器（router.py: L132-L159）

**当前行为：**
- 通过 `with_structured_output(RouterDecision)` 或 `bind_tools(ROUTER_TOOLS)` 做意图分类
- 输出为枚举值 `"knowledge_qa" | "price_inquiry" | "general_chat" | "doc_qa" | "fallback"`
- 耗时极短（通常 < 1s），用户感知弱

**流式化建议：** **不需要流式化**。路由是分类任务，输出仅为 5 种枚举之一，流式输出无实际体验提升。保留 `invoke()` 即可。

---

#### ② 价格查询-意图抽取（price_inquiry.py: L176-L215）

**当前行为：**
- `_parse_intent()` 使用 LCEL 链 `_INTENT_PROMPT | llm | StrOutputParser().invoke()` 将自然语言转为结构化 JSON（`SearchIntent`）
- 输出为 `{hard_filters, semantic_keywords, exact_tokens}` 结构体
- 同样耗时短，结果需完整 JSON 才能被后续 SQL 生成器消费

**流式化建议：** **不需要流式化**。此调用是中间管线环节，下游依赖完整 JSON 做 SQL 拼接，流式输出无意义。

---

#### ③ 价格查询-结果生成（price_inquiry.py: L688-L746）

**当前行为：**
- `node_price_inquiry()` 查询 MySQL 后，调用 `_format_records()` 做纯模板字符串拼接
- **完全没有 LLM 参与结果生成**，仅按固定格式列出字段

**潜在升级方向：** 可在检索完成后增加一个 LLM 摘要生成步骤，将结构化中标记录转为自然语言回答并流式输出。但这属于**功能增强**而非纯流式改造。

---

#### ④ RAG 答案生成（qa_chain.py: L272-L296）⭐ 核心目标

**当前行为：**
- `_decide_and_answer()` 内部构建 `answer_chain = prompt | llm | StrOutputParser()` 并用 `.invoke()` 同步获取完整回答
- 此调用封装在 `PublicKnowledgeRAG.query()` → `self._qa_chain.invoke()` 链路中
- 是整个系统**唯一真正生成面向用户的长文本答案**的地方

**流式化建议：** **这是流式升级的核心目标**。知识问答场景下，回答文本较长（通常 200-800 字），token-by-token 流式输出能显著改善用户等待体验。

---

#### ⑤ 通用对话（general_chat.py: L38-L80）

**当前行为：**
- `node_general_chat()` **未接入 LLM**，直接返回硬编码的功能引导文案
- 注释写明 LLM 应由 graph.py 在构建时注入，但当前未实现

**流式化建议：** 需先补齐 LLM 接入，再考虑流式化。属于**前置改造项**。

---

### 1.3 流式化范围结论

```
需要流式化（核心）: RAG 答案生成（qa_chain.py _decide_and_answer）
需要流式化（次要）: 通用对话（general_chat.py，需先接入 LLM）
不需要流式化:      Agent 路由器、价格查询意图抽取
可选增强:          价格查询结果 LLM 摘要生成（非流式改造范畴）
```

---

## 2. 现有技术栈对流式输出的原生支持程度

### 2.1 LangChain 核心库

| 组件 | 版本 | 流式支持 | 说明 |
|------|------|:--------:|------|
| `langchain-core` | ≥0.3.37 | ✅ 原生 | `Runnable.stream()` / `Runnable.astream()` 支持所有 LCEL 链 |
| `langchain-openai` | ≥1.0.0 | ✅ 原生 | `ChatOpenAI(streaming=True)` 返回生成器，token 级流式 |
| `StrOutputParser` | 内置 | ✅ 原生 | 支持 `stream()` 逐 token 输出字符串 |
| `ChatPromptTemplate` | 内置 | ✅ 原生 | 无状态模板，天然兼容流式 |

**关键发现：** 当前 `qa_chain.py` 已使用纯 LCEL 语法 `prompt | llm | StrOutputParser()`，这是支持流式的最佳实践。只需将 `.invoke()` 替换为 `.stream()` 即可获得 token 级生成器。

```python
# 当前（非流式）
raw_answer: str = answer_chain.invoke({"context": context, "question": question})

# 改为流式（一行改动）
for token in answer_chain.stream({"context": context, "question": question}):
    yield token  # str, 逐 token
```

### 2.2 LangGraph

| 特性 | 支持程度 | 说明 |
|------|:--------:|------|
| `graph.stream()` | ✅ 已使用 | `AgentGraph.stream()` 方法已存在（graph.py: L243-L251） |
| `stream_mode="values"` | ✅ 原生 | 每个节点完成后推送完整 State |
| `stream_mode="updates"` | ✅ 原生 | 仅推送增量更新 |
| `stream_mode="messages"` | ✅ 原生 | 自动捕获节点内 LLM 产生的 token 级消息流 |
| `stream_mode="custom"` | ✅ 原生 | 支持通过 `writer` 发送自定义数据 |
| `astream_events` | ✅ 原生 | v2 事件 API，最细粒度的事件流 |

**关键发现：** `AgentGraph` 已预留 `stream()` 方法，当前使用默认 `stream_mode="values"`（节点级事件）。升级到 token 级流式只需改为 `stream_mode="messages"` 或 `stream_mode="custom"`。

但有一个**架构层面的限制**：`stream_mode="messages"` 要求 LLM 的 token 流经 State 的 `messages` 通道。当前 `node_knowledge_qa` 调用 `rag.query()` 内部完成 LLM 调用后才追加 `AIMessage`——这意味着 graph 层看不到内部 LLM 的 token 流。

### 2.3 DeepSeek API（当前 LLM 后端）

| 特性 | 支持程度 | 说明 |
|------|:--------:|------|
| OpenAI 兼容 SSE 流式 | ✅ 原生 | `stream=True` 参数，返回 `data: {"choices":[{"delta":{"content":"..."}}]}` |
| `ChatOpenAI` 封装 | ✅ 透明 | `streaming=True` 即可，底层自动处理 SSE |

### 2.4 其他组件

| 组件 | 流式相关性 | 说明 |
|------|:--------:|------|
| Milvus / pymilvus | 无关 | 向量检索为同步操作，天然快速（< 200ms），无需流式 |
| pymysql | 无关 | 数据库查询为同步操作，返回结构化数据，无需流式 |
| MinerU PDF 解析 | 无关 | 离线预处理管线，不参与在线问答 |
| BGE-Reranker (SiliconFlow API) | 无关 | 重排序为同步 HTTP 调用 |

### 2.5 前端对接能力

当前项目为 CLI 入口（`agent/__main__.py`），未接入前端。若未来对接 Web 前端，推荐方案：

| 方案 | 适用场景 | 复杂度 |
|------|----------|:------:|
| **FastAPI + SSE** | Web 应用 | 中 |
| **FastAPI + WebSocket** | 双向实时通信 | 高 |
| **Flask + SSE** | 轻量 Web | 低 |

---

## 3. 升级整体思路

### 3.1 总体架构

```
用户问题
    │
    ▼
┌─────────────────────────────────────────────────────┐
│  LangGraph StateGraph (stream_mode="custom")         │
│                                                      │
│  ┌──────────┐   ┌───────────────┐   ┌────────────┐ │
│  │ router   │──▶│ business_node │──▶│    END     │ │
│  │ (invoke) │   │ (内部流式)     │   │            │ │
│  └──────────┘   │               │   └────────────┘ │
│                  │ llm.stream()  │                   │
│                  │ ↓ token-by-tok│                   │
│                  │ writer(token) │                   │
│                  └───────┬───────┘                   │
│                          │                           │
└──────────────────────────┼───────────────────────────┘
                           │ SSE / WebSocket
                           ▼
                    前端逐字渲染
```

**核心原则：** 路由器保持 `invoke()`（同步），业务节点内部用 `llm.stream()` + LangGraph `writer` 机制推送 token。

### 3.2 方案对比

#### 方案 A：LangGraph `stream_mode="messages"`（侵入式）

- **原理：** 让 LLM 在业务节点内将 token 写入 `messages` 通道，graph 自动捕获
- **优点：** 与 LangGraph 框架深度集成，自动去重
- **缺点：** 需要彻底重构 `qa_chain.py` 的 `_decide_and_answer`，把 LLM 调用从 `RunnableLambda` 中拆出来放到节点层面，改动范围大

#### 方案 B：LangGraph `stream_mode="custom"` + `writer`（推荐 ⭐）

- **原理：** 业务节点内部用 `llm.stream()` 获取 token 生成器，通过 `get_stream_writer()` 推送到 graph 外部
- **优点：** 改动集中在各业务节点内部，`qa_chain.py` 只需增加一个 `stream()` 方法，不改 LCEL 链结构
- **缺点：** 需要手动管理 writer 生命周期

#### 方案 C：绕过 LangGraph，直接在 API 层流式

- **原理：** 保持 `graph.invoke()` 不变，在 FastAPI 路由中先获取检索结果，再单独调用 `llm.stream()`
- **优点：** 改动最小
- **缺点：** 绕过了 graph 的状态管理，多轮对话记忆可能丢失，架构不优雅

**推荐方案 B**，兼顾改动量和架构一致性。

### 3.3 核心改造点

```
改造点 1: LLM 配置层
  └─ ChatOpenAI 初始化增加 streaming=True

改造点 2: QA Chain 层
  └─ _decide_and_answer 拆分为 _decide + _answer_stream
  └─ 新增 query_stream() 方法返回 token 生成器

改造点 3: Knowledge QA 节点
  └─ node_knowledge_qa 支持流式路径，使用 get_stream_writer()

改造点 4: General Chat 节点
  └─ 接入 LLM + 流式输出

改造点 5: AgentGraph 层
  └─ stream() 方法改为 stream_mode="custom"，透传 token
```

---

## 4. 各模块改造难度评估

### 4.1 改造难度矩阵

| 模块 | 文件 | 难度 | 预计工时 | 关键风险点 |
|------|------|:----:|:--------:|------------|
| **LLM 配置** | `graph.py` / `config.py` | 🟢 低 | 0.5h | 无风险，单行参数 `streaming=True` |
| **QA Chain 流式改造** | `qa_chain.py` | 🟡 中 | 3-4h | `_decide_and_answer` 是 `RunnableLambda` 内部闭包，需拆分为独立可调用函数；拒答分支无流式内容需处理 |
| **Knowledge QA 节点** | `knowledge_qa.py` | 🟡 中 | 2-3h | 需区分流式/非流式两条路径；`_get_rag()` 单例需支持注入 writer |
| **General Chat 节点** | `general_chat.py` | 🟡 中 | 2-3h | 当前完全未接入 LLM，需从零构建 prompt + llm 链 |
| **AgentGraph 流式入口** | `graph.py` | 🟢 低 | 1-2h | `stream()` 方法已存在，改为 `stream_mode="custom"` + 事件解析 |
| **RAG Engine 接口** | `rag_engine.py` | 🟢 低 | 1h | 新增 `query_stream()` 方法，委托给 qa_chain |
| **前端对接层** | 新建 `api.py` | 🟡 中 | 4-6h | 新建 FastAPI + SSE 端点，需处理连接断开、超时等边界 |
| **价格查询增强** | `price_inquiry.py` | 🔴 高 | 6-8h | 需新增 LLM 摘要逻辑、prompt 设计、流式输出，且记录数多时 token 消耗大 |

### 4.2 关键风险详解

#### 风险 1：`_decide_and_answer` 闭包重构

`qa_chain.py` 中 `_decide_and_answer` 是 `build_qa_chain()` 函数内部的嵌套闭包，无法从外部直接调用或修改。流式化需要将其拆分为两个独立步骤：

```python
# 当前结构（无法部分复用）
def build_qa_chain(...):
    def _decide_and_answer(inputs):  # 闭包
        ...
        answer_chain.invoke(...)     # 同步阻塞
        ...
    chain = {...} | RunnableLambda(_decide_and_answer)
```

**应对策略：** 在 `build_qa_chain` 内部新增一个独立的 `_answer_stream` 生成器函数，保持闭包结构但分离关注点。不改变 `build_qa_chain` 的签名。

#### 风险 2：拒答分支无流式内容

当检索结果为空时，系统返回固定文本 `"抱歉，公共知识库中暂无相关内容..."`，这不产生 LLM token 流。前端需要能处理"瞬间返回完整文本"和"逐 token 流式返回"两种模式。

**应对策略：** 在流式协议中增加一个 `"type": "complete"` vs `"type": "token"` 的消息类型区分。

#### 风险 3：`_with_fallback` 异常兜底与流式不兼容

当前 `_with_fallback` 包装器（graph.py: L51-L92）捕获异常后返回完整 dict，包括 `AIMessage`。如果节点内部正在流式输出 token 时发生异常，前端可能已收到部分 token 后再收到错误消息。

**应对策略：** 在 `writer` 中增加异常信号（如 `{"type": "error", ...}`），让前端能清除已渲染的部分内容并显示错误提示。

#### 风险 4：DeepSeek API 流式兼容性

DeepSeek 的 OpenAI 兼容 API 已实测支持流式，但需确认 `stream_options={"include_usage": True}` 等细节参数。

**应对策略：** 在环境准备阶段做专项流式连通性测试。

---

## 5. 建议的分步实施路径

### Phase 0：环境准备与验证（0.5 天）

```
□ Step 0.1: DeepSeek 流式连通性测试
   └─ 写独立脚本，用 ChatOpenAI(streaming=True).stream("测试") 验证 token 级输出

□ Step 0.2: LangGraph stream_mode 原型验证
   └─ 写最小 demo：一个 StateGraph 节点内用 get_stream_writer() 推 token
   └─ 确认 stream_mode="custom" 的 event 结构

□ Step 0.3: 依赖包版本确认
   └─ 确认 langgraph>=1.2.0 支持 get_stream_writer
   └─ 确认 langchain-openai>=1.0.0 的 streaming 行为
```

### Phase 1：核心流式链路（2-3 天）⭐ 最小可用

```
□ Step 1.1: LLM 配置升级
   文件: agent/graph.py, public_kb/rag_engine.py
   └─ ChatOpenAI 构造参数增加 streaming=True

□ Step 1.2: QA Chain 流式化
   文件: public_kb/qa_chain.py
   └─ 在 build_qa_chain 内部新增 _answer_stream() 生成器
   └─ 保持 _decide_and_answer 不变（向后兼容）
   └─ build_qa_chain 返回的 chain 增加 .stream() 方法或返回 (chain, stream_fn)

□ Step 1.3: RAG Engine 新增 query_stream()
   文件: public_kb/rag_engine.py
   └─ 新增 def query_stream(self, question: str) -> Generator[str, None, dict]
   └─ 先调用检索获取 docs，再流式生成答案
   └─ 最后 return 完整 sources（通过生成器无法传递，改用回调或返回特殊 sentinel）

□ Step 1.4: Knowledge QA 节点支持流式
   文件: agent/nodes/knowledge_qa.py
   └─ 新增 node_knowledge_qa_stream() 或通过参数控制流式/非流式
   └─ 使用 get_stream_writer() 推送 token

□ Step 1.5: AgentGraph stream() 升级
   文件: agent/graph.py
   └─ 新增 stream_qa() 方法，指定 stream_mode="custom"
   └─ 解析 event 并 yield token 字符串
```

### Phase 2：全分支覆盖（1-2 天）

```
□ Step 2.1: General Chat 接入 LLM + 流式
   文件: agent/nodes/general_chat.py
   └─ 将 LLM 注入节点（从 graph 传入或自行创建）
   └─ 实现 token 级流式输出

□ Step 2.2: Price Inquiry 增加 LLM 摘要流式输出（可选）
   文件: agent/nodes/price_inquiry.py
   └─ 检索完成后增加 prompt | llm | StrOutputParser 流式调用
   └─ 将结构化记录转为自然语言摘要

□ Step 2.3: Fallback 节点流式适配
   文件: agent/nodes/fallback.py
   └─ 支持流式输出引导文案（仍为瞬间返回，添加消息类型标记）
```

### Phase 3：前端对接与测试（2-3 天）

```
□ Step 3.1: FastAPI SSE 端点
   新建: api.py 或 agent/api.py
   └─ POST /chat/stream 端点
   └─ 返回 SSE 事件流: data: {"type":"token","content":"..."}
   └─ 事件类型: token | sources | error | done

□ Step 3.2: 端到端集成测试
   └─ 知识问答：验证 token 逐个到达、sources 正确返回
   └─ 通用对话：验证流式正常
   └─ 询价：验证非流式 + 流式摘要两种路径
   └─ 错误场景：检索为空、LLM 超时、网络中断

□ Step 3.3: 性能测试
   └─ 首 token 延迟 (TTFT)：目标 < 2s（含检索时间）
   └─ token 生成速率：确认不高于非流式总耗时 1.2x
   └─ 并发流式连接：至少支持 5 并发无报错
```

### Phase 4：异常处理与优化（1 天）

```
□ Step 4.1: _with_fallback 流式兼容
   └─ 节点内异常时 writer 发送 error 事件
   └─ 前端清除部分渲染内容

□ Step 4.2: 超时与断连处理
   └─ LLM 调用超时（30s）时优雅降级
   └─ 客户端断开 SSE 时取消 LLM 生成

□ Step 4.3: 日志与监控
   └─ 记录每次流式调用的 TTFT、总 token 数、中断率
```

---

## 6. 综合可行性结论与升级价值判断

### 6.1 可行性结论：✅ 高度可行

| 评估维度 | 结论 |
|----------|------|
| **技术栈兼容性** | 优秀。LangChain LCEL、LangGraph、DeepSeek API 均原生支持流式输出，无需引入新依赖 |
| **架构兼容性** | 良好。`AgentGraph.stream()` 方法已预留，`qa_chain.py` 使用纯 LCEL 链天然可 stream |
| **代码改动量** | 可控。核心改动集中在 `qa_chain.py`（~50 行）和 `knowledge_qa.py`（~30 行），总计约 200-300 行净增 |
| **风险可控性** | 中低。主要风险在闭包重构和异常处理，均有明确的应对策略 |
| **向后兼容** | 可保证。`invoke()` 方法保持不变，`stream()` 为新增接口 |

### 6.2 升级价值判断

#### 正面收益

| 收益 | 影响范围 | 重要程度 |
|------|----------|:--------:|
| **用户等待体验质变** | 知识问答是核心功能，回答通常 200-800 字，非流式等待 5-15s，流式可降至首字 2s 内 | ⭐⭐⭐⭐⭐ |
| **产品竞争力提升** | 流式输出是 AI 对话产品的标配体验，ChatGPT / Kimi / 豆包均默认流式 | ⭐⭐⭐⭐ |
| **架构前瞻性** | 为后续多轮对话、Agent 工具调用等场景的流式化打下基础 | ⭐⭐⭐ |
| **技术债清理** | 顺带补齐 general_chat 缺失的 LLM 接入 | ⭐⭐⭐ |

#### 成本与风险

| 成本 | 说明 |
|------|------|
| **开发工作量** | 约 5-8 人天（Phase 0-4 全覆盖），最小可用（Phase 0-1）约 2-3 人天 |
| **LLM Token 消耗** | 流式不增加 token 消耗（同一请求），但价格查询增加 LLM 摘要会新增消耗 |
| **运维复杂度** | SSE 长连接增加网关/反向代理配置需求（如 Nginx `proxy_buffering off`） |

### 6.3 最终建议

```
┌─────────────────────────────────────────────────────────┐
│                                                         │
│   ✅ 建议立即启动流式升级，优先级：Phase 0 → Phase 1    │
│                                                         │
│   核心理由：                                            │
│   1. 技术栈全面支持，无阻塞性障碍                       │
│   2. 核心改造点（qa_chain）改动量小、风险可控           │
│   3. 用户体验提升显著（TTFT 从 5-15s 降至 < 2s）       │
│   4. 可渐进式交付：先上线 RAG 流式 → 再补齐其他分支     │
│                                                         │
│   推荐策略：                                            │
│   • 第 1 周: Phase 0 + Phase 1（RAG 流式上线）          │
│   • 第 2 周: Phase 2 + Phase 3（全分支覆盖 + 前端对接） │
│   • 第 3 周: Phase 4（异常处理 + 压测优化）             │
│                                                         │
└─────────────────────────────────────────────────────────┘
```

---

## 附录 A：流式协议消息类型设计（草案）

```typescript
// SSE 事件类型
type StreamEvent =
  | { type: "token"; content: string }           // LLM 逐 token 输出
  | { type: "sources"; data: Source[] }          // 知识库引用来源（回答完成后推送）
  | { type: "records"; data: Record[] }          // 价格查询原始记录（可选）
  | { type: "intent"; value: string }            // 路由意图（可选，调试用）
  | { type: "error"; message: string }           // 异常降级
  | { type: "done"; intent: string }             // 流结束信号
```

## 附录 B：核心代码改造示意

### B.1 QA Chain 流式化（qa_chain.py）

```python
# ── 新增：流式答案生成器 ──
def _answer_stream(inputs: Dict[str, Any]) -> Generator[str, None, Dict[str, Any]]:
    """流式生成答案，最后 return 完整结果 dict。"""
    docs_with_scores = inputs["docs"]
    question = inputs["question"]

    if not docs_with_scores:
        yield "抱歉，公共知识库中暂无相关内容，无法提供可靠回答。"
        return {"answer": "...", "sources": []}

    context = _format_docs(docs_with_scores)
    sources = _build_sources(docs_with_scores)
    answer_chain = prompt | llm | StrOutputParser()

    full_answer = []
    for token in answer_chain.stream({"context": context, "question": question}):
        full_answer.append(token)
        yield token

    return {"answer": "".join(full_answer).strip(), "sources": sources}
```

### B.2 Knowledge QA 节点流式化（knowledge_qa.py）

```python
from langgraph.config import get_stream_writer

def node_knowledge_qa_stream(state: AgentState) -> dict:
    """专业知识问答节点（流式版）。"""
    writer = get_stream_writer()
    question = str(state["messages"][-1].content)
    rag = _get_rag()

    # 流式生成
    final_result = None
    for token in rag.query_stream(question):
        if isinstance(token, dict):  # 最后返回的完整结果
            final_result = token
        else:
            writer({"type": "token", "content": token})  # 推送 token

    return {
        "business_result": {
            "branch": "knowledge_qa",
            "answer": final_result["answer"],
            "data": {"sources": final_result["sources"]},
        },
        "messages": [AIMessage(content=final_result["answer"])],
    }
```

---

> **文档版本:** v1.0  
> **生成工具:** Qoder AI Coding Assistant  
> **下次评审建议:** Phase 1 完成后，更新实际改造中遇到的问题和工时偏差
