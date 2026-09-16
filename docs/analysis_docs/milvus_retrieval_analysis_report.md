# 招投标智能助手 — public_kb 模块 Milvus 向量检索逻辑技术分析报告

> **分析日期**: 2026-08-07  
> **分析范围**: `public_kb/` 模块全部核心文件  
> **核心组件**: Milvus Standalone 2.4.0 · BAAI/bge-large-zh-v1.5 · LangChain LCEL 链

---

## 目录

1. [检索逻辑完整拆解](#1-检索逻辑完整拆解)
2. [技术栈梳理](#2-技术栈梳理)
3. [检索速度与准确性分析](#3-检索速度与准确性分析)
4. [潜在隐患识别](#4-潜在隐患识别)
5. [升级空间评估](#5-升级空间评估)

---

## 1. 检索逻辑完整拆解

### 1.1 全链路总览

用户自然语言问题输入后，系统经历 **四个阶段** 完成问答：

```
用户问题 (question)
    │
    ▼
┌──────────────────────────────────────────────┐
│ 阶段 1: 文本向量化                             │
│   embedding_service.py:_SafeEmbeddings       │
│   模型: BAAI/bge-large-zh-v1.5 (1024维)       │
│   安全截断: ≤400 字符                          │
└──────────────────┬───────────────────────────┘
                   │ query_vector[1024]
                   ▼
┌──────────────────────────────────────────────┐
│ 阶段 2: 向量相似度检索                          │
│   qa_chain.py:_retrieve()                    │
│   → vector_store.similarity_search_with_score │
│   Top-K = 3, 度量 = COSINE                    │
└──────────────────┬───────────────────────────┘
                   │ [(Doc, score), ...] 最多 3 条
                   ▼
┌──────────────────────────────────────────────┐
│ 阶段 3: 相似度阈值过滤                          │
│   score >= similarity_threshold (0.65)        │
│   全部低于 0.65 → 触发拒答                      │
└──────────────────┬───────────────────────────┘
                   │ [(Doc, score), ...] 0~3 条
                   ▼
┌──────────────────────────────────────────────┐
│ 阶段 4: 上下文拼接 + LLM 生成                   │
│   qa_chain.py:_decide_and_answer()           │
│   → _format_docs() 拼接来源                    │
│   → Prompt | ChatOpenAI(deepseek-chat)        │
│   → StrOutputParser → 最终回答 + sources       │
└──────────────────────────────────────────────┘
```

### 1.2 阶段 1：文本向量化

**关键文件**: [embedding_service.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/embedding_service.py)

系统通过 `create_embeddings()` 工厂函数创建 `_SafeEmbeddings` 实例——这是 `OpenAIEmbeddings` 的子类，核心差异在于内建了 **超长文本安全截断机制**：

```python
# embedding_service.py L25
_MAX_TEXT_CHARS = 400

class _SafeEmbeddings(OpenAIEmbeddings):
    def embed_query(self, text: str) -> list[float]:
        safe = text[:_MAX_TEXT_CHARS] if len(text) > _MAX_TEXT_CHARS else text
        return super().embed_query(safe)
```

**设计依据**：
- BAAI/bge-large-zh-v1.5 模型的 token 上限为 **512 token**
- 中文约 1 token/字，取 **400 字符** 留 20% 余量
- 同时设置 `check_embedding_ctx_length=False`（embedding_service.py L72），避免 LangChain 将文本转为 token ID 再发送——因为 SiliconFlow 等第三方 API 只接受原始文本，不接受 token ID 格式

**API 路由**：实际调用链路为 `_SafeEmbeddings.embed_query()` → `OpenAIEmbeddings._embed()` → HTTP POST 到 `https://api.siliconflow.cn/v1/embeddings`，model 参数为 `BAAI/bge-large-zh-v1.5`（config.py L44-46，.env L22-24）。

### 1.3 阶段 2：向量相似度检索

**关键文件**: [qa_chain.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/qa_chain.py) L132-145

```python
def _retrieve(question: str) -> List[Tuple[Document, float]]:
    raw = vector_store.similarity_search_with_score(
        question, k=settings.retrieval_top_k  # k=3
    )
    filtered = [
        (doc, score) for doc, score in raw
        if score >= settings.similarity_threshold  # 0.65
    ]
    return filtered
```

**检索执行路径**：
1. `similarity_search_with_score(question, k=3)` 由 `langchain_milvus.Milvus` 提供（[milvus_store.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/milvus_store.py) L25 导入）
2. 内部流程：LangChain 对 question 调用 `self.embedding_function.embed_query(question)` → 获得 1024 维向量 → 通过 pymilvus 执行 `Collection.search()`，参数为 `metric_type=COSINE, limit=3`
3. COSINE 相似度分数范围为 **[-1, 1]**，1 表示完全同向。实际中对于 BGE 模型产生的归一化向量，分数通常落在 [0, 1] 区间

**Top-K 选取规则**：
- `retrieval_top_k = 3`（[config.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/config.py) L109），即每次检索最多返回 **3 条**最相似文档块
- 该值偏保守——对于复杂法律问题，3 条参考资料可能不足以覆盖多角度的法规条款

### 1.4 阶段 3：相似度阈值过滤与拒答机制

**阈值配置**: `similarity_threshold = 0.65`（config.py L110）

过滤逻辑（qa_chain.py L137-140）：
- 仅保留 `score >= 0.65` 的结果
- **全部被过滤**（即 `filtered` 为空列表）→ 触发拒答

**拒答流程**（qa_chain.py L148-159）：
```python
def _decide_and_answer(inputs):
    docs_with_scores = inputs["docs"]   # ← 已经被 _retrieve 过滤后
    question = inputs["question"]
    if not docs_with_scores:
        return {
            "answer": "抱歉，公共知识库中暂无相关内容，无法提供可靠回答。",
            "sources": [],
        }
```

当检索结果全部低于 0.65 时，**不调用 LLM**，直接返回拒答文案。这避免了 LLM 基于低质量或不相关内容"编造"答案（即减少幻觉）。

### 1.5 阶段 4：上下文拼接与 LLM 生成

**关键文件**: [qa_chain.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/qa_chain.py) L57-74, L148-175

**上下文格式化**（`_format_docs`）：
```python
def _format_docs(docs_with_scores):
    parts = []
    for i, (doc, score) in enumerate(docs_with_scores, 1):
        doc_name = doc.metadata.get("doc_name", "未知文档")
        chapter = doc.metadata.get("chapter", "未知章节")
        parts.append(
            f"[来源{i}] 文档: {doc_name} | 章节: {chapter} | 相关度: {score:.2%}\n"
            f"{doc.page_content}"
        )
    return "\n\n---\n\n".join(parts)
```

每条检索结果携带完整的元数据标注（文档名、章节路径、相似度百分比），拼接后的上下文以 `---` 分隔线区分不同来源，注入到 Prompt 的 `{context}` 占位符中。

**Prompt 模板**（qa_chain.py L35-42）：
- System: "你是一个招投标领域的专业顾问，基于权威的公共知识库资料回答问题。请严格依据下方提供的参考资料作答，不要添加任何资料中没有的信息。如果参考资料不足以回答问题，请明确告知用户无法回答。"
- User: `参考资料：\n{context}\n\n用户问题：{question}`

**LLM 调用**：`prompt | ChatOpenAI(model="deepseek-chat", temperature=0.0) | StrOutputParser()`，temperature=0.0 确保回答的确定性，避免创造性发挥。

**LCEL 链结构**（qa_chain.py L178-186）：
```python
chain = (
    {
        "docs": RunnableLambda(_retrieve),
        "question": RunnablePassthrough(),
    }
    | RunnableLambda(_decide_and_answer)
)
```

这是一个 **并行检索 + 串行决策** 的 LCEL 图：`_retrieve` 和 `RunnablePassthrough` 并发执行，结果合并为 dict 后传入 `_decide_and_answer`。

---

## 2. 技术栈梳理

### 2.1 核心组件一览

| 组件 | 版本/型号 | 角色 | 配置来源 |
|------|----------|------|----------|
| **Milvus** | v2.4.0 (Standalone) | 向量数据库 | [docker-compose.yml](file://d:/DEMO/zhaotoubiao_demo/milvus/docker-compose.yml) L45 |
| **etcd** | v3.5.5 | Milvus 元数据存储 | docker-compose.yml L10 |
| **MinIO** | RELEASE.2023-03-20 | Milvus 对象存储 | docker-compose.yml L27 |
| **pymilvus** | ≥2.4.0 | Python Milvus 驱动 | [requirements.txt](file://d:/DEMO/zhaotoubiao_demo/requirements.txt) L41 |
| **langchain-milvus** | ≥0.1.10, <0.2.0 | LangChain Milvus 集成 | requirements.txt L38 |
| **langchain-core** | ≥0.3.37, <0.4.0 | LCEL/Runnable 基础设施 | requirements.txt L36 |
| **langchain-openai** | ≥0.2.0, <0.4.0 | OpenAI 兼容 Embedding/LLM | requirements.txt L37 |
| **BAAI/bge-large-zh-v1.5** | 1024维, 512 token | 文本向量化模型 | [config.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/config.py) L44-63 |
| **DeepSeek-chat** | - | 问答生成 LLM | config.py L72-73, [.env](file://d:/DEMO/zhaotoubiao_demo/.env) L4-5 |
| **SiliconFlow API** | - | Embedding 推理服务商 | .env L18-19 |

### 2.2 索引与检索配置参数

| 参数 | 当前值 | 定义位置 | 说明 |
|------|--------|----------|------|
| `index_type` | `IVF_FLAT` | [milvus_store.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/milvus_store.py) L122 | 倒排文件索引，检索时对聚类中心做近似搜索 |
| `metric_type` | `COSINE` | milvus_store.py L123 | 余弦相似度度量 |
| `nlist` | `128` | milvus_store.py L124 | IVF 聚类中心数 |
| `embedding_dim` | `1024` | config.py L64 | 向量维度，与 BGE 模型输出一致 |
| `retrieval_top_k` | `3` | config.py L109 | 每次检索返回的最大候选数 |
| `similarity_threshold` | `0.65` | config.py L110 | 相似度最低阈值，低于此值拒答 |
| `chunk_max_chars` | `400` | config.py L103 | 单块最大字符数 |
| `chunk_overlap_chars` | `50` | config.py L104 | 句子级切分的重叠字符数 |
| `nprobe` | 未显式设置 (Milvus 默认) | - | IVF 检索时搜索的聚类单元数，影响精度与速度 |

### 2.3 参数影响分析

**IVF_FLAT + nlist=128**：
- IVF_FLAT 将全量向量聚类为 128 个单元，检索时只搜索与查询向量最近的 `nprobe` 个单元
- **速度优势**：相比暴力搜索（FLAT），搜索空间缩小约 128/nprobe 倍
- **精度代价**：若查询向量落在聚类边界附近，可能遗漏相邻单元中的高相似度向量；nprobe 默认值通常为 8~16，搜索覆盖率约 6%~12%
- **适用场景**：当前知识库文档量级（3 本 PDF，估计数千条向量），暴力搜索也完全可行，IVF_FLAT 在当前规模下主要是为未来扩展预留

**COSINE 度量**：
- BGE 模型输出的向量经 L2 归一化后，余弦相似度等价于内积（IP）
- 对中文语义匹配效果良好，但对法律条文的精确措辞匹配不如关键词检索敏感

**retrieval_top_k=3 + similarity_threshold=0.65**：
- 检索 3 条候选中只要有一条 ≥0.65 即进入生成阶段
- 对于需要综合多条款回答的复杂问题（如"公开招标和邀请招标的适用条件分别是什么？"），仅 3 条参考资料可能覆盖不全

---

## 3. 检索速度与准确性分析

### 3.1 耗时分布评估

全链路检索耗时主要由以下环节构成：

```
总耗时 ≈ T_embed + T_milvus_network + T_filter + T_llm
```

| 环节 | 估算耗时 | 瓶颈分析 |
|------|---------|---------|
| **T_embed** (Embedding API 调用) | 200~800ms | SiliconFlow 云端 API，取决于网络延迟和并发负载。单次 query 只向量化一段文本，开销可控 |
| **T_milvus_network** (Milvus 查询) | 5~50ms | 本地 Docker 部署，localhost 通信，延迟极低。IVF_FLAT 搜索在当前数据量下几乎是瞬时完成 |
| **T_filter** (Python 内存过滤) | <1ms | 仅对 3 条结果做阈值比较，可忽略 |
| **T_llm** (DeepSeek-chat 生成) | 1~5s | 占比最大。DeepSeek API 响应时间取决于输出长度和平台负载 |

**结论**：当前耗时瓶颈在 LLM 生成阶段（T_llm），向量检索本身延迟很低。如果未来接入 Agent 多轮对话，Embedding 调用频次增加可能成为次瓶颈。

### 3.2 文档切片策略对召回率的影响

**关键文件**: [chunker.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/chunker.py)

当前采用 **两级切片策略**：

```
第一级（标题级）: 按 #、##、### Markdown 标题拆分
      │
      │ 单块 > 400 字符？
      │
      ├─ 否 → 直接作为 Document
      │
      └─ 是 → 第二级（句子级）: 按中文标点（。！？；\n）二次拆分
                带 50 字符 overlap
```

**优势**：
- **标题级切分保留了语义完整性**——同一标题下的内容不会被拆散到多个块中，"第一章 总则"的全部条款作为一个整体被索引
- 每个 Document 携带 `doc_name`、`chapter`（如 "第一章 > 第一节 > 招标方式"）和 `chunk_index` 元数据，LLM 回答时可精确引用来源

**劣势**：
- **硬字符上限破坏长段落的连贯性**：`chunk_max_chars=400` 意味着一个 600 字的段落会被拆成两块（400 + 200），第二块开头虽有 50 字符 overlap，但重叠仅覆盖末尾 50 字，可能丢失关键的上下文衔接
- **句子级切分不保证语义独立性**：按标点符号机械拆分，不检查是否为完整语义单元（如一个法条可能被截断）
- **无滑动窗口策略**：当前 overlap 仅在句子级拆分时生效，标题级切分无 overlap，意味着跨章节边界的相邻 chunks 之间语义断裂

**对召回率的影响**：
- 当用户查询匹配到某个 chunk 的高相似度片段时，相邻的补充信息（如同一法条的"但书"条款）可能散落在另一个 chunk 中
- 如果另一个 chunk 的语义向量与查询不够接近（<0.65），则会被丢弃，导致 LLM 只能看到不完整的法规条文
- BGE 模型的 512 token 上限限制了单 chunk 的容量——即使将 `chunk_max_chars` 提升到 1000，Embedding 阶段也会被截断到 400 字符

### 3.3 相似度阈值（0.65）的合理性评价

**阈值 0.65 的含义**：
- COSINE 相似度在 BGE 模型语义空间中，0.65 属于中等偏低的相关性水平
- 典型 BGE 检索场景中，高度相关的文档对分数通常在 0.80~0.95，中度相关在 0.60~0.80，低度相关 <0.55

**合理性分析**：

| 维度 | 评价 |
|------|------|
| **防止幻觉** | ✅ 合理。0.65 能有效过滤语义不相关的噪音，配合 System Prompt 中"不要添加资料中没有的信息"进一步约束 LLM |
| **漏召风险** | ⚠️ 存在风险。招投标法律条文中的专业术语（如"暂估价""工程量清单""不平衡报价"）在 BGE 通用语料中可能缺乏充分训练，导致相关 chunk 的相似度偏低（0.55~0.65），被误拒 |
| **误拒风险** | ⚠️ 中等。当用户使用口语化表达查询专业术语时（如"那个开标前要公示几天？"），查询向量与知识库向量的语义距离可能偏大 |
| **业务适配性** | ⚠️ 缺乏区分度。所有问题类型使用同一阈值，但事实型查询（"招标文件应包括哪些内容？"）和信息检索型查询（"招标投标法第几条？"）对相似度的敏感度不同 |

**总结**：0.65 作为统一阈值在当前小规模知识库下可接受，但缺乏自适应能力——对不同问题类型和不同文档来源无法动态调整。

---

## 4. 潜在隐患识别

### 4.1 🔴 高危：固定索引无法支持在线增量更新

**风险描述**：
当前索引构建在 `MilvusStoreManager.initialize_collection()` 中一次性完成（[milvus_store.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/milvus_store.py) L102-134）：

```python
def initialize_collection(self, documents):
    self._drop_if_exists()        # ① 删除旧集合
    self._store = MilvusVectorStore(...)  # ② 重建并创建 IVF_FLAT 索引
    self._store.add_documents(documents) # ③ 全量导入
```

任何新增 PDF 都需要 **全量重建**（drop → recreate → re-index），流程为：
1. 清空已有集合（所有向量丢失）
2. 重新解析全部 PDF（MinerU 耗时数分钟到数十分钟）
3. 重新向量化并建索引

**风险程度**：🔴 **高危**
- **检索服务中断**：重建期间（可能 10~60 分钟），知识库完全不可用
- **资源浪费**：仅新增 1 个 PDF 也需要重新处理所有已有文档
- **不适合生产环境**：法律法规会持续修订发布，知识库需要定期更新。当前方案意味着每次更新都是"停服重建"

**受影响代码**：`milvus_store.py:initialize_collection()` → `_drop_if_exists()` + 全量导入。

---

### 4.2 🟡 中危：单一相似度阈值缺乏自适应能力

**风险描述**：
`similarity_threshold = 0.65` 是一刀切的全局参数（config.py L110），无法区分：

- **查询类型差异**：精确条款查询 vs. 宽泛概念问答
- **文档质量差异**：OCR 扫描版 PDF（识别错误多）vs. 电子版 PDF（文字准确）
- **领域差异**：法律法规条文（措辞精确，要求高阈值）vs. 政策解读（语义宽松，可适当降低阈值）

**具体场景**：
- 用户问"招标投标法第四十六条规定的履约保证金比例是多少？"——这是一个精确匹配需求，即使最高分 chunk 的相似度仅为 0.60（因为查询用词与原文不完全一致），也应当展示给 LLM
- 当前阈值 0.65 会导致此类场景被拒答，而实际上知识库中确实包含相关条款

**风险程度**：🟡 **中危**
- 导致漏召（false negative），降低系统可用性
- 用户感知为"系统答不上来"，损害信任

---

### 4.3 🔴 高危：单节点 Milvus Standalone 无高可用与容灾

**风险描述**：
部署架构为单机 Docker 三件套（[docker-compose.yml](file://d:/DEMO/zhaotoubiao_demo/milvus/docker-compose.yml)）：

```
milvus-standalone (v2.4.0)
    ├── etcd (v3.5.5)         ← 元数据，单节点
    └── minio (2023-03-20)    ← 向量存储，单节点
```

**故障场景**：
| 故障类型 | 影响 |
|----------|------|
| Docker 服务停止 | 检索完全不可用 |
| etcd 数据损坏 | Milvus 元数据丢失，需重建全部索引 |
| MinIO 数据损坏 | 向量数据丢失，需重新向量化入库 |
| 宿主机宕机 | 所有服务中断，无自动恢复 |
| 内存溢出 (OOM) | Milvus 查询失败，无降级方案 |

**风险程度**：🔴 **高危**
- 当前没有数据备份策略
- 没有健康检查与自动重启机制（docker-compose 虽有 `restart: unless-stopped` 但未配置）
- 检索是 Agent 核心能力之一，不可用的影响面是全局的

---

### 4.4 🟡 中危：Embedding 模型对招投标专业术语的语义覆盖不足

**风险描述**：
BAAI/bge-large-zh-v1.5 是通用中文预训练模型，训练语料以互联网文本为主。招投标领域的以下术语在其语义空间中可能缺乏充分区分度：

| 术语对 | 语义差异 | BGE 覆盖风险 |
|--------|---------|-------------|
| "投标保证金" vs. "履约保证金" | 完全不同概念 | 向量可能高度相似（都含"保证金"） |
| "公开招标" vs. "邀请招标" | 不同采购方式 | 可能区分度不足 |
| "废标" vs. "流标" | 不同法律后果 | 通用模型可能混淆 |
| "暂估价" vs. "暂列金额" | 造价专业术语 | 很可能缺乏训练覆盖 |

**实际影响**：
- 当用户查询"履约保证金什么时候退还？"，检索结果可能返回关于"投标保证金"的条款（因为"保证金"关键词拉高了向量相似度）
- 这是语义检索的固有问题——对精确术语匹配不如关键词检索（BM25 / FULLTEXT）

**风险程度**：🟡 **中危**
- 可通过混合检索缓解（见第 5 章升级方案）

---

### 4.5 🟢 低危：nprobe 未显式配置导致精度不可控

**风险描述**：
IVF_FLAT 索引的 `nprobe` 参数（搜索时探测的聚类单元数）未在代码中显式设置（[milvus_store.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/milvus_store.py) L121-125 只设置了 `nlist=128`）。Milvus 默认 `nprobe` 值较小（通常 8），意味着只搜索约 6% 的聚类空间。

在当前小数据量下影响不大，但随着文档增多，可能遗漏高质量匹配。此外，`nprobe` 不可控意味着检索精度随数据增长而不可预测地下降。

**风险程度**：🟢 **低危**（当前数据量下），随数据增长可能升级为 🟡 中危

---

## 5. 升级空间评估

### 5.1 升级方向总览

| 升级方向 | 技术可行性 | 升级难度 | 优先级 | 预期收益 |
|----------|-----------|---------|--------|---------|
| **混合检索（向量 + FULLTEXT/BM25）** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | 🔴 高 | 大幅提升术语检索精度 |
| **索引增量更新 / 自动刷新** | ⭐⭐⭐⭐ | ⭐⭐ | 🔴 高 | 消除停服重建，支持持续更新 |
| **检索结果重排序 (Reranker)** | ⭐⭐⭐⭐ | ⭐⭐ | 🟡 中 | 提升 Top-3 精度 |
| **升级 Embedding 模型** | ⭐⭐⭐ | ⭐⭐ | 🟡 中 | 改善语义覆盖 |
| **动态阈值 / 自适应阈值** | ⭐⭐⭐⭐ | ⭐⭐ | 🟡 中 | 减少漏召和误拒 |
| **引入缓存机制** | ⭐⭐⭐⭐⭐ | ⭐ | 🟢 低 | 减少重复 Embedding 调用 |
| **Milvus 高可用部署** | ⭐⭐⭐ | ⭐⭐⭐⭐ | 🟢 低 | 消除单点故障 |

---

### 5.2 方向一：混合检索（向量 + 关键词）

**技术方案**：
在现有 `_retrieve` 阶段并行执行两路检索，然后融合结果：

```
question
    ├──→ Milvus COSINE 向量检索 (k=5)  ──→ [向量结果]
    │
    └──→ MySQL FULLTEXT / BM25 关键词检索 (k=5) ──→ [关键词结果]
                  │
                  ▼
            RRF (Reciprocal Rank Fusion) 融合去重
                  │
                  ▼
            取 Top-3 进入阈值过滤
```

**实施路径**：
1. 在 Milvus collection 上启用 **Sparse Vector**（Milvus 2.4 已原生支持 BM25 稀疏向量）
2. 或利用项目已有的 MySQL FULLTEXT 检索能力（已有索引配置记忆支持），在 `qa_chain.py` 的 `_retrieve` 中增加一条关键词检索分支
3. 使用 RRF 算法融合两路分数：`RRF_score(d) = Σ 1/(k + rank_i(d))`

**可行性评估**：
- ⭐⭐⭐⭐⭐ 技术完全可行，`langchain_milvus` 支持 Hybrid Search
- ⭐⭐⭐ 升级难度中等——需要修改 `_retrieve` 和 `_decide_and_answer` 函数，增加融合逻辑
- 项目已有 MySQL FULLTEXT 检索经验（参考记忆 `MySQL FULLTEXT检索技术栈`），可直接复用

---

### 5.3 方向二：索引增量更新

**技术方案**：
放弃当前的 `_drop_if_exists` 全量重建模式，改用：

```python
def add_documents(self, documents: List[Document]):
    """增量导入——不删除现有集合，仅追加新文档并触发索引重建。"""
    if not self._has_collection():
        self.initialize_collection(documents)
    else:
        self.load_existing()
        self._store.add_documents(documents)
        # 可选：手动触发索引重建（Milvus 2.4 支持 create_index 增量）
```

同时实现定时任务：
- 扫描 `raw_pdfs/` 目录，检测新增/修改的 PDF
- 自动触发增量解析 → 清洗 → 切片 → 入库

**可行性评估**：
- ⭐⭐⭐⭐ 技术可行，`langchain_milvus` 的 `add_documents` 本身支持增量写入
- ⭐⭐ 升级难度较低——主要是修改 `milvus_store.py` 的管理逻辑，增加 `add_documents` 方法和文件变更检测

---

### 5.4 方向三：检索结果重排序 (Reranker)

**技术方案**：
在 `_retrieve` 返回结果后、阈值过滤前，插入一个 Reranker 节点：

```
_retrieve (k=10, 放宽阈值) → Reranker 精排 → Top-3 + 阈值过滤 → LLM
```

可选的 Reranker 方案：
- **BAAI/bge-reranker-v2-m3**：专门的中文重排序模型，在 MTEB 中文榜单上排名前列
- **Cross-Encoder**：以 (query, document) 对作为输入进行精细语义匹配，精度远高于 Bi-Encoder（BGE Embedding 模型）

**可行性评估**：
- ⭐⭐⭐⭐ 技术可行，SiliconFlow 提供 `BAAI/bge-reranker-v2-m3` API（或本地部署）
- ⭐⭐ 升级难度较低——Reranker 作为一个 `RunnableLambda` 插入 LCEL 链即可
- 代码预留了扩展点：[qa_chain.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/qa_chain.py) L10-11 注释 `"可在 RunnableLambda 前插入 Reranker 节点"`

---

### 5.5 方向四：升级 Embedding 模型

**技术方案对比**：

| 模型 | 维度 | Token 上限 | 中文 MTEB 排名 | 优势 |
|------|------|-----------|---------------|------|
| `BAAI/bge-large-zh-v1.5` (当前) | 1024 | 512 | 中等 | 成熟稳定 |
| `BAAI/bge-m3` | 1024 | 8192 | 高 | 多语言、长文本、支持稀疏+稠密双向量 |
| `BAAI/bge-large-zh-v2.0` | 1024 | 512 | 较高 | v1.5 的改进版 |
| `stella-base-zh-v3-1792d` | 1792 | 512 | 高 | 中文专用，维度更高 |

**推荐路线**：
1. 短期：升级到 `BAAI/bge-large-zh-v2.0`（模型名替换即可，维度不变，SiliconFlow 已支持）
2. 中期：评估 `BAAI/bge-m3`——支持 sparse embedding，天然适配混合检索，且 8192 token 上限可消除当前的 400 字符安全截断限制

**可行性评估**：
- ⭐⭐⭐ 中等可行——需修改 .env 中 `EMBEDDING_MODEL` 并重建索引（全量重新向量化入库）
- ⭐⭐ 升级难度较低——代码改动极小，但需要停机重建

---

### 5.6 方向五：动态阈值

**技术方案**：
将固定的 `similarity_threshold=0.65` 替换为基于查询特征的自适应策略：

```python
def _adaptive_threshold(question: str, docs_with_scores: list) -> float:
    """基于检索结果分布动态计算阈值。"""
    if not docs_with_scores:
        return 0.65
    scores = [s for _, s in docs_with_scores]
    max_score = max(scores)
    # 若最高分很高（>0.85），放宽对后续 chunk 的要求
    if max_score > 0.85:
        return 0.55
    # 若整体分数偏低，也适当放宽避免漏召
    if max_score < 0.70:
        return max_score - 0.10
    return 0.65
```

**可行性评估**：
- ⭐⭐⭐⭐ 技术可行——纯 Python 逻辑，只需修改 `_retrieve` 中的阈值判断
- ⭐ 升级难度很低——改动范围局限于 `qa_chain.py` 一个文件

---

### 5.7 方向六：缓存机制

**技术方案**：
对高频相同或相似问题缓存 Embedding 向量和检索结果：

```python
from functools import lru_cache

@lru_cache(maxsize=256)
def _cached_embed_query(question: str) -> tuple:
    """缓存 query 的向量化结果（注意：tuple 可哈希，list 不可）。"""
    return tuple(embeddings.embed_query(question))
```

进一步可引入语义缓存：对语义相似的问题（向量距离 < 0.02）直接复用之前的结果。

**可行性评估**：
- ⭐⭐⭐⭐⭐ 极易实现
- ⭐ 升级难度极低——单文件改动，5 行代码

---

### 5.8 优先级推荐路线图

```
阶段一（立即，低成本）
  ├── 动态阈值 (方向五)              ← 1天
  ├── 缓存机制 (方向六)              ← 0.5天
  └── 显式设置 nprobe               ← 0.5天

阶段二（1-2周，中成本）
  ├── 混合检索 (方向一)              ← 3-5天
  ├── 索引增量更新 (方向二)          ← 2-3天
  └── Reranker 插入 (方向三)        ← 2-3天

阶段三（按需，较高成本）
  ├── Embedding 模型升级 (方向四)    ← 1-2天 + 重建时间
  └── Milvus 高可用 (方向七)         ← 1-2周
```

---

## 附录 A：关键文件索引

| 文件 | 核心职责 |
|------|---------|
| [public_kb/qa_chain.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/qa_chain.py) | LCEL 问答链：`_retrieve` 检索 + `_decide_and_answer` 决策 |
| [public_kb/milvus_store.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/milvus_store.py) | Milvus 连接管理、ORM 兼容补丁、集合创建/加载/清空 |
| [public_kb/config.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/config.py) | 全局配置中心（阈值、Top-K、模型参数等） |
| [public_kb/chunker.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/chunker.py) | Markdown 标题级 + 句子级两级语义切片 |
| [public_kb/embedding_service.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/embedding_service.py) | Embedding 接口封装 + 安全截断 |
| [public_kb/rag_engine.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/rag_engine.py) | RAG 引擎统一入口（init/query/clear） |
| [public_kb/text_cleaner.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/text_cleaner.py) | Markdown 后处理去噪（页眉页脚、页码、短行） |
| [milvus/docker-compose.yml](file://d:/DEMO/zhaotoubiao_demo/milvus/docker-compose.yml) | Milvus Standalone 容器化部署配置 |
| [requirements.txt](file://d:/DEMO/zhaotoubiao_demo/requirements.txt) | Python 依赖版本约束 |
| [.env](file://d:/DEMO/zhaotoubiao_demo/.env) | 环境变量（API Key、模型名、Base URL） |

## 附录 B：检索链路伪代码

```python
def full_retrieval_pipeline(question: str) -> dict:
    # 阶段 1: 向量化
    query_vector = embed_query(question[:400])  # 1024-dim

    # 阶段 2: Milvus 检索
    raw_results = milvus.similarity_search_with_score(
        query_vector, k=3, metric="COSINE"
    )  # [(Document, score), ...]

    # 阶段 3: 阈值过滤
    filtered = [(doc, score) for doc, score in raw_results if score >= 0.65]

    # 阶段 4: 决策 + 生成
    if not filtered:
        return {"answer": "抱歉，无法回答。", "sources": []}

    context = format_docs(filtered)  # 拼接来源
    prompt = build_prompt(context, question)
    answer = llm.invoke(prompt)      # DeepSeek-chat, temperature=0

    return {"answer": answer, "sources": build_sources(filtered)}
```

---

> **报告结论**：当前 `public_kb` 模块的 Milvus 检索链路设计合理、代码质量高（LCEL 规范、异常兜底完善、安全截断到位），但在 **索引更新机制**、**单一阈值策略**、**单节点高可用** 三方面存在明确的高风险点。建议按阶段一→阶段二的优先级逐步实施升级，优先解决停服重建和漏召问题，再引入混合检索和重排序进一步提升召回精度。
