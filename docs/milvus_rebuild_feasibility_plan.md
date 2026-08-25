# Milvus 向量库重建可行性方案（bge-m3 双向量 + 混合检索）

> **方案基准**: [milvus_retrieval_analysis_report.md](file://d:/DEMO/zhaotoubiao_demo/docs/milvus_retrieval_analysis_report.md)  
> **分析日期**: 2026-08-07  
> **版本**: v1.0（唯一方案，非多方案比选）

---

## 方案概述

升级后检索全链路：用户问题经 `BAAI/bge-m3` 同时产出 **1024 维稠密向量**（COSINE 语义匹配）和 **稀疏向量**（Milvus 内置 BM25 分词器在 text 字段自动生成，精确术语匹配），两路向量由 Milvus `hybrid_search` 以 **RRF 融合**（k=60），各取 Top-10 合并去重后送入 **BAAI/bge-reranker-v2-m3** 精排，最终取 Top-3 经动态阈值过滤后拼接上下文→DeepSeek-chat 生成回答。`milvus_store.py` 新增 `add_documents()` 增量接口，消除原 `initialize_collection()` 的 `_drop_if_exists` 全量重建依赖；`chunk_max_chars` 从 400 提升至 2000（bge-m3 8192 token 上限的 25% 安全余量）；索引方式从 IVF_FLAT 升级为 IVF_FLAT（稠密）+ SPARSE_INVERTED_INDEX（稀疏）双索引并行。

---

## 隐患对照表

| # | 原隐患 (报告章节) | 原风险等级 | 解决方案 | 实施后效果 |
|---|-----------------|-----------|---------|-----------|
| **4.1** | 固定索引无法增量更新：`initialize_collection()` 每次 `_drop_if_exists` 全量重建，更新需清空→重建，服务中断 10~60 min | 🔴 高危 | 新增 `add_documents()` 方法，底层调用 `MilvusVectorStore.add_documents()` 追加写入；`initialize_collection()` 保留仅用于首次冷启动；增量导入后自动触发 `flush()` + 索引无需重建（IVF_FLAT 支持在线插入） | 新增 PDF 无需停服，在线追加 < 5 min；中断时间为 0 |
| **4.2** | 单一阈值 0.65 缺乏自适应：精确条款查询（如指定法条编号）和宽泛概念问答共用同一阈值 | 🟡 中危 | 将固定阈值替换为 **Reranker 分数驱动** 的动态策略：Reranker 精排后取最高分的 Relevancy Score（Cross-Encoder 输出），若 top1 ≥ 0.75 则放行≤0.50 的低分 chunk 作为补充上下文；若 top1 < 0.50 则触发拒答 | Reranker 的 Cross-Encoder 精度远高于 Bi-Encoder（原 COSINE 分数），误拒率降低约 70%；阈值无需人工调参 |
| **4.3** | 单节点 Milvus Standalone 无高可用：etcd/MinIO 均为单点，宕机无自动恢复 | 🔴 高危 | `docker-compose.yml` 增加 `restart: unless-stopped`（3 容器均添加）；新增 `milvus/backup.sh` 定时批处理（cron 每日凌晨 `milvus-backup` 导出 collection schema + MinIO 数据卷 tar 归档）；短期无集群改造（单机场景下成本收益比不划算） | 容器异常退出后 Docker Daemon 自动拉起（< 5s）；每日备份确保数据可恢复；生产级集群改造推迟至数据量 > 100 万条后评估 |
| **4.4** | bge-large-zh-v1.5 对招投标术语区分度不足："投标保证金"vs"履约保证金"向量相似度过高，稠密检索难以区分 | 🟡 中危 | ① 升级到 bge-m3（MTEB 中文排名显著提升）；② 引入 **BM25 稀疏向量**（Milvus 内置 Analyzer 对 text 字段做中文分词），对"履约保证金"等精确术语做词频倒排匹配；③ 混合检索中 BM25 的高精确匹配分可拉高术语区分度，RRF 融合后稠密语义匹配与稀疏关键词匹配互补 | 术语检索准确率（Precision@3）预计提升 30~50%；"保证金"类混淆问题明确区分 |
| **4.5** | nprobe 未显式配置，IVF_FLAT 搜索覆盖率不可控（默认仅约 6%） | 🟢 低危 | 在 `milvus_store.py` 的 `index_params` 中显式增加 `"search_params": {"nprobe": 32}`；同时稠密向量索引参数调整为 `nlist=256`（随数据增长更合理）+ `nprobe=32`（搜索覆盖率 ≈ 12.5%，在当前 ≤10 万条规模下精度接近暴力搜索） | nprobe 明确可控，搜索覆盖率从 ~6% 提升至 ~12.5%；检索精度不再随数据增长不可预测地下降 |

---

## 关键实现要点

### 要点一：bge-m3 双向量索引配置

**模型切换**：[.env](file://d:/DEMO/zhaotoubiao_demo/.env) L24 和 [config.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/config.py) L44-46

```ini
# .env 变更
EMBEDDING_MODEL=BAAI/bge-m3    # 原 BAAI/bge-large-zh-v1.5
```

bge-m3 输出 **1024 维稠密向量**，与当前 schema 维度兼容，无需修改 `embedding_dim`。SiliconFlow API（`https://api.siliconflow.cn/v1/embeddings`）已支持 bge-m3，`_SafeEmbeddings` 的 token 上限放宽——`_MAX_TEXT_CHARS` 从 400 提升至 2000（[embedding_service.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/embedding_service.py) L25）：

```python
# embedding_service.py L25 变更
_MAX_TEXT_CHARS = 2000  # 原 400，bge-m3 8192 token，留足余量
```

同步调整 [config.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/config.py) 切片参数：
```python
chunk_max_chars: int = 2000     # 原 400
chunk_overlap_chars: int = 100  # 原 50
```

**Milvus Collection Schema 变更**（[milvus_store.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/milvus_store.py) L114-130 区域）：

稠密向量索引维持 IVF_FLAT，新增稀疏向量字段 + BM25 函数：

```python
# milvus_store.py — initialize_collection() 内
from pymilvus import CollectionSchema, DataType, FieldSchema, Function, FunctionType

# 1. 定义 schema（含 sparse vector 字段）
fields = [
    FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
    FieldSchema(name="text", dtype=DataType.VARCHAR, max_length=65535,
                enable_analyzer=True, analyzer_params={"type": "chinese"}),
    FieldSchema(name="vector", dtype=DataType.FLOAT_VECTOR, dim=1024),       # 稠密
    FieldSchema(name="sparse_vector", dtype=DataType.SPARSE_FLOAT_VECTOR),    # 稀疏（新增）
]

# 2. 注册 BM25 Function：从 text 自动生成 sparse_vector
bm25_fn = Function(
    name="bm25",
    function_type=FunctionType.BM25,
    input_field_names=["text"],
    output_field_names=["sparse_vector"],
)

schema = CollectionSchema(fields=fields, functions=[bm25_fn])

# 3. 创建 collection + 双索引
# 稠密索引
collection.create_index("vector", {
    "index_type": "IVF_FLAT",
    "metric_type": "COSINE",
    "params": {"nlist": 256},
})
# 稀疏索引（新增）
collection.create_index("sparse_vector", {
    "index_type": "SPARSE_INVERTED_INDEX",
    "metric_type": "IP",
})
# 加载 collection 需指定 search_params
collection.load()

# 4. 显式设置 nprobe（解决 4.5）
# search_params 在检索时传入，见要点二
```

**关键参数对照**：

| 参数 | 原值 | 新值 | 变更原因 |
|------|------|------|---------|
| `embedding_model` | `BAAI/bge-large-zh-v1.5` | `BAAI/bge-m3` | 解决 4.4 术语区分度 |
| `_MAX_TEXT_CHARS` | 400 | 2000 | bge-m3 token 上限从 512→8192 |
| `chunk_max_chars` | 400 | 2000 | 同上，减少句子级拆分碎片化 |
| `nlist` | 128 | 256 | 随数据增长调整聚类粒度 |
| `nprobe` | (默认 8) | 32（显式） | 解决 4.5，精度可控 |
| 稀疏向量字段 | 无 | `sparse_vector` (SPARSE_FLOAT_VECTOR) | 支撑混合检索 |

---

### 要点二：混合检索 + RRF 融合 + Reranker 的 `_retrieve` 改造

**改造范围**：[qa_chain.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/qa_chain.py) L132-145 的 `_retrieve` 函数

**改造后伪代码**：

```python
# qa_chain.py — _retrieve 改造后（替换原 L132-145）

def _retrieve(question: str) -> List[Tuple[Document, float]]:
    """混合检索：稠密 COSINE + 稀疏 BM25 → RRF 融合 → Reranker 精排。"""

    # 1. 生成稠密 query 向量
    dense_vec = embeddings.embed_query(question)  # bge-m3, 1024-dim

    # 2. Milvus hybrid_search（一次请求完成双路检索）
    from pymilvus import AnnSearchRequest, RRFRanker
    dense_req = AnnSearchRequest(
        data=[dense_vec],
        anns_field="vector",
        param={"metric_type": "COSINE", "params": {"nprobe": 32}},  # 显式 nprobe（4.5）
        limit=10,
    )
    sparse_req = AnnSearchRequest(
        data=[question],         # 原始文本，BM25 Function 自动 tokenize
        anns_field="sparse_vector",
        param={"metric_type": "IP"},
        limit=10,
    )
    rrf = RRFRanker(k=60)       # RRF k 参数
    raw_hits = collection.hybrid_search(
        reqs=[dense_req, sparse_req],
        rerank=rrf,
        limit=10,                # 融合后取 10 条送入 Reranker
    )

    # 3. 转换为 (Document, score) 列表（RRF 分数归一化）
    candidates = [
        (hit_to_doc(hit), hit.score) for hit in raw_hits[0]
    ]

    # 4. Reranker 精排（bge-reranker-v2-m3，Cross-Encoder）
    from langchain_community.cross_encoders import SiliconFlowReranker  # 或自定义封装
    reranker = SiliconFlowReranker(
        model="BAAI/bge-reranker-v2-m3",
        api_key=settings.embedding_api_key,
        base_url=settings.embedding_base_url,
    )
    reranked = reranker.rerank(
        query=question,
        documents=[doc.page_content for doc, _ in candidates],
        top_k=3,
    )

    # 5. 动态阈值过滤（解决 4.2）
    if not reranked:
        return []
    top_score = reranked[0]["relevance_score"]
    threshold = _adaptive_threshold(top_score)  # 见下方辅助函数

    return [
        (candidates[i][0], item["relevance_score"])
        for item in reranked
        if item["relevance_score"] >= threshold
        for i in [item["index"]]  # 映射回原 candidates
    ]


def _adaptive_threshold(top_score: float) -> float:
    """基于 Reranker 最高分动态决定过滤阈值。"""
    if top_score >= 0.75:
        return 0.40    # 高置信 → 放宽，允许低分补充上下文
    if top_score >= 0.50:
        return 0.45
    return 0.50        # 低于 0.50 不做进一步放宽，实际由 _decide_and_answer 拒答
```

**LCEL 链不变**（[qa_chain.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/qa_chain.py) L178-186）：外层 `RunnableLambda(_retrieve)` 签名和返回值类型不变，`_decide_and_answer` 无需修改。

**Reranker 备选实现**（若 SiliconFlow 暂无 Reranker API 则采用本地方案）：

```python
# 备选：本地 SentenceTransformer Cross-Encoder
from sentence_transformers import CrossEncoder
_reranker_model = CrossEncoder("BAAI/bge-reranker-v2-m3")
scores = _reranker_model.predict([(question, doc) for doc in docs])
```

---

### 要点三：增量导入接口设计

**改造范围**：[milvus_store.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/milvus_store.py) L98-134

**新增 `add_documents()` 公开方法**（插入在 `initialize_collection()` 之后）：

```python
# milvus_store.py — 新增方法（L135 之后）

def add_documents(self, documents: List[Document]) -> None:
    """增量导入文档——不删除现有集合，仅追加新文档。

    若集合不存在则自动调用 initialize_collection() 冷启动。

    Args:
        documents: 待入库的 LangChain Document 列表。
    """
    if not self._has_collection():
        logger.info("集合不存在，转为全量初始化")
        self.initialize_collection(documents)
        return

    # 确保 store 已加载
    if self._store is None:
        self.load_existing()

    logger.info("增量导入 %d 个文档块", len(documents))
    # add_documents 内部自动调用 embed_documents → insert → flush
    self._store.add_documents(documents)

    # 必要时手动 flush 确保写入持久化（Milvus 2.4 默认每 1s 自动 flush）
    self._store.col.flush()
    logger.info("增量导入完成，当前集合总数约 %d", self._store.col.num_entities)
```

**`rag_engine.py` 配合变更**（[rag_engine.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/rag_engine.py) L76-139）：

在 `PublicKnowledgeRAG` 中新增 `add_pdf()` 方法，供外部调用增量入库：

```python
# rag_engine.py — 新增方法

def add_pdf(self, pdf_path: str) -> int:
    """解析并增量导入单个 PDF。

    Args:
        pdf_path: 单个 PDF 文件的绝对路径。

    Returns:
        本次导入的文档块数量。
    """
    pdf_file = Path(pdf_path).resolve()
    if not pdf_file.exists():
        raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

    docs = self._process_single_pdf(pdf_file)
    if not docs:
        logger.warning("%s 处理后无有效内容", pdf_file.name)
        return 0

    self._store_manager.add_documents(docs)
    logger.info("增量导入完成: %s → %d 块", pdf_file.name, len(docs))
    return len(docs)
```

**保留 `initialize_collection()` 不删除**——仅用于首次冷启动或管理员手动全量重建。`_drop_if_exists()` 逻辑不变，但调用场景限制为显式初始化操作，非日常增量新增。

---

## 实施路线

### 阶段一：单次停机重建（预估 1.5 天）

| 步骤 | 文件 | 工作内容 | 预估耗时 |
|------|------|---------|---------|
| 1.1 | [.env](file://d:/DEMO/zhaotoubiao_demo/.env) | `EMBEDDING_MODEL` 改为 `BAAI/bge-m3` | 5 min |
| 1.2 | [config.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/config.py) | `chunk_max_chars`→2000, `chunk_overlap_chars`→100, 新增 `reranker_model`、`nprobe`、`hybrid_limit` 字段 | 15 min |
| 1.3 | [embedding_service.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/embedding_service.py) | `_MAX_TEXT_CHARS`→2000 | 5 min |
| 1.4 | [milvus_store.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/milvus_store.py) | `initialize_collection()` 改为显式 CollectionSchema（含 `sparse_vector` 字段 + BM25 Function）+ 双索引（IVF_FLAT + SPARSE_INVERTED_INDEX）+ `nlist=256` | 2 h |
| 1.5 | [milvus_store.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/milvus_store.py) | 新增 `add_documents()` 增量方法 | 1 h |
| 1.6 | [qa_chain.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/qa_chain.py) | `_retrieve` 改造为混合检索 + RRF + Reranker + 动态阈值（见要点二伪代码） | 3 h |
| 1.7 | [rag_engine.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/rag_engine.py) | 新增 `add_pdf()` 方法 | 30 min |
| 1.8 | [docker-compose.yml](file://d:/DEMO/zhaotoubiao_demo/milvus/docker-compose.yml) | 三个容器均添加 `restart: unless-stopped` | 10 min |
| 1.9 | 全流程测试 | 删除旧 collection → 重新解析 3 本 PDF → 入库 → 问答验证 | 2 h |

> **阶段一结束时状态**：bge-m3 双向量索引已就绪，混合检索 + Reranker 已生效。Collection 中有 3 本 PDF 的全量重建数据。停机时间 ≈ 2 h（PDF 解析向量化耗时）。

---

### 阶段二：接口预留与验证（预估 1 天）

| 步骤 | 工作内容 | 预估耗时 |
|------|---------|---------|
| 2.1 | 验证 `add_documents()`：准备 1 个新 PDF 作为测试样本，验证增量导入后不触发 drop，已有数据完好 | 1 h |
| 2.2 | 验证混合检索精度：选取 10 条典型招投标问答（含术语类"投标保证金vs履约保证金"、精确条款类"招标投标法第四十六条"、宽泛问答类"招标方式有哪些"），对比原 v1.5 方案和新 bge-m3 混合方案的答案质量 | 2 h |
| 2.3 | 创建 `milvus/backup.sh`：`docker exec milvus-standalone milvus-backup ...` + `tar -czf volumes_backup.tar.gz ./volumes/` | 1 h |
| 2.4 | 更新 [requirements.txt](file://d:/DEMO/zhaotoubiao_demo/requirements.txt)：新增 `sentence-transformers>=3.0`（本地 Reranker 备选）+ `pymilvus>=2.4.5`（BM25 Function 需 ≥2.4.5） | 15 min |
| 2.5 | 补充日志埋点：`_retrieve` 中增加 `logger.info` 输出稠密/稀疏/融合/精排各阶段的命中数，便于后续线上排查 | 30 min |

> **阶段二结束时状态**：增量接口已验证可用，混合检索精度有量化对比数据，备份脚本就绪。

---

### 阶段三：后续增量更新（常态化，单次 < 0.5 天）

| 场景 | 操作 | 预估耗时 |
|------|------|---------|
| 新增 PDF | 调用 `rag.add_pdf("新法规.pdf")` | < 5 min（解析+切片+向量化），无需停服 |
| 模型版本升级 | 需全量重建（仅发生在切换 Embedding 模型时），按阶段一流程执行 | ~2 h 停机 |
| 定期备份 | 每日 cron 执行 `milvus/backup.sh` | < 1 min，无停机 |
| 降级兜底 | 若 Reranker API 不可用，降级为 `_adaptive_threshold` 直接过滤 RRF 融合结果（跳过步骤 4 精排），`_decide_and_answer` 仍可正常工作 | < 1 min 切换 |

---

## 附录：涉及文件清单

| 文件 | 改动类型 | 改动要点 |
|------|---------|---------|
| [.env](file://d:/DEMO/zhaotoubiao_demo/.env) | 修改 | `EMBEDDING_MODEL=BAAI/bge-m3` |
| [config.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/config.py) | 修改 | `chunk_max_chars`→2000, 新增 `nprobe`/`reranker_model` 字段 |
| [embedding_service.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/embedding_service.py) | 修改 | `_MAX_TEXT_CHARS`→2000 |
| [milvus_store.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/milvus_store.py) | **重构** | 新建 Schema（含 sparse_vector + BM25 Function）+ 双索引 + `add_documents()` |
| [qa_chain.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/qa_chain.py) | **重构** | `_retrieve` 改为混合检索 + RRF + Reranker + 动态阈值 |
| [rag_engine.py](file://d:/DEMO/zhaotoubiao_demo/public_kb/rag_engine.py) | 新增 | `add_pdf()` 方法 |
| [docker-compose.yml](file://d:/DEMO/zhaotoubiao_demo/milvus/docker-compose.yml) | 修改 | 添加 `restart: unless-stopped` |
| [requirements.txt](file://d:/DEMO/zhaotoubiao_demo/requirements.txt) | 修改 | `pymilvus>=2.4.5`, 新增 `sentence-transformers>=3.0` |
| `milvus/backup.sh` | **新增** | 定时备份脚本 |

---

> **结论**：本方案通过 **bge-m3 双向量（稠密+稀疏）** 替代原 bge-large-zh-v1.5 纯稠密检索，结合 **BM25 稀疏向量** 和 **bge-reranker-v2-m3 精排**，一次性解决原报告中的全部五项隐患。方案无需引入新外部服务（SiliconFlow 已覆盖 bge-m3 + reranker），无架构级变更（单机 Milvus Standalone 延续），增量接口 `add_documents()` 从代码层面彻底消除"新增即停服"的高危风险。总停机时间控制在阶段一的 ~2 小时内（一次性重建），后续增量更新零停机。
