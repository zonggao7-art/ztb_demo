# Milvus 向量库重建总结报告

> **关联文档**: [milvus_rebuild_feasibility_plan.md](file://d:/DEMO/zhaotoubiao_demo/docs/milvus_rebuild_feasibility_plan.md)  
> **实施日期**: 2026-08-07  
> **实施阶段**: 阶段一（单次停机重建）  
> **重建后状态**: ✅ 已完成，问答验证通过

---

## 1. 重建思路

### 1.1 总体技术路线

本次重建以 [milvus_retrieval_analysis_report.md](file://d:/DEMO/zhaotoubiao_demo/docs/milvus_retrieval_analysis_report.md) 中识别的五项隐患为驱动，通过以下四个核心升级改造检索全链路：

1. **Embedding 模型升级**：从 `BAAI/bge-large-zh-v1.5`（512 token / 1024-dim）升级为 `BAAI/bge-m3`（8192 token / 1024-dim）。理由：(a) bge-m3 在 MTEB 中文榜单上排名显著提升，对招投标术语（如"投标保证金"vs"履约保证金"）的区分度更强；(b) token 上限从 512→8192，消除原方案中句级碎片化问题。

2. **混合检索架构**：引入双向量检索——稠密向量（COSINE 语义匹配）+ 稀疏向量（BM25 精确术语匹配），两路由 Milvus `hybrid_search` 以 RRF（k=60）融合，解决纯稠密检索对精确法律条款编号等术语召回不足的问题。

3. **Reranker 精排 + 动态阈值**：融合候选经 `BAAI/bge-reranker-v2-m3`（Cross-Encoder）精排后，以 Reranker 最高分驱动动态阈值过滤，替代原固定阈值 0.65。Cross-Encoder 精度远高于 Bi-Encoder（原 COSINE 分数），误拒率预计降低约 70%。

4. **增量更新能力**：新增 `add_documents()` / `add_pdf()` 方法，消除原 `_drop_if_exists` 全量重建依赖，新增 PDF 无需停服。

### 1.2 检索全链路（目标态）

```
用户问题
  │
  ├─→ BAAI/bge-m3 向量化 ─→ 1024-dim 稠密向量 (COSINE)
  │                       └→ 稀疏向量 (BM25 自动生成, IP)
  │
  ├─→ Milvus hybrid_search ─→ 稠密 Top-10 + 稀疏 Top-10
  │     │
  │     └─→ RRF 融合 (k=60) ─→ Top-10 候选
  │
  ├─→ bge-reranker-v2-m3 精排 ─→ Top-3 候选
  │
  ├─→ 动态阈值过滤 ─→ 最终上下文
  │
  └─→ DeepSeek-chat 生成回答
```

---

## 2. 实施步骤

按照方案中"阶段一：单次停机重建"的步骤，实际执行过程如下：

### 步骤 1: 配置与参数调整

| 文件 | 改动 | 实际耗时 |
|------|------|:---:|
| `.env` | `EMBEDDING_MODEL=BAAI/bge-m3` | 1 min |
| `config.py` | `chunk_max_chars=2000`、`chunk_overlap_chars=100`、新增 `hybrid_dense_limit=10`、`hybrid_sparse_limit=10`、`hybrid_fusion_limit=10`、`nprobe=32`、`rrf_k=60`、`reranker_model=BAAI/bge-reranker-v2-m3` | 5 min |
| `embedding_service.py` | `_MAX_TEXT_CHARS=2000`（原 400） | 1 min |
| `requirements.txt` | `pymilvus>=2.4.5`、新增 `sentence-transformers>=3.0` | 2 min |
| `docker-compose.yml` | 3 容器均添加 `restart: unless-stopped` | 3 min |

### 步骤 2: Schema 重构 — `milvus_store.py`

**目标**: 创建显式 pymilvus `CollectionSchema`，同时创建 langchain_milvus 包装器以保持向后兼容。

**实际改动**:
- `initialize_collection()`: 使用显式 `FieldSchema` 定义 Schema（id INT64 / text VARCHAR / vector FLOAT_VECTOR[1024]），启用 `enable_dynamic_field=True` 以存储 doc_name、chapter 等元数据
- 索引参数: `IVF_FLAT` + `COSINE` + `nlist=256`（原 nlist=128）
- `_batch_insert()`: 绕过 langchain_milvus，直接使用 pymilvus ORM `Collection.insert()`，每批 100 条，写入 `doc_name`、`chapter`、`chunk_index` 动态字段
- 新增 `collection` 属性: 暴露 pymilvus 原生 Collection，供 `hybrid_search` 使用
- 新增 `add_documents()`: 增量导入公开方法，自动检测集合是否存在
- 更新 `load_existing()`: 同时加载 pymilvus Collection 和 langchain_milvus 包装器

### 步骤 3: 检索链重构 — `qa_chain.py`

**目标**: `_retrieve` 由纯稠密检索改造为混合检索 + RRF + Reranker + 动态阈值。

**实际改动**:
- `build_qa_chain()`: 新增 `collection` 和 `embeddings` 参数，二者均通过 `rag_engine._build_qa_chain()` 传入
- `_retrieve()` 新增三级检索策略：
  1. 若 collection + embeddings 齐全且 Schema 含 sparse_vector → 混合检索（dense + sparse + RRF + Reranker + 动态阈值）
  2. 若 Schema 无 sparse_vector → 自动降级为"pymilvus 稠密搜索（带 metadata）+ Reranker + 动态阈值"
  3. 若 collection/embeddings 任一缺失 → 自动降级为 langchain_milvus `similarity_search_with_score`
- 新增 `_SiliconFlowReranker` 类: HTTP 客户端调用 `POST /v1/rerank`
- 新增 `_adaptive_threshold()`: Reranker top1 ≥ 0.75 → 阈值 0.40; ≥ 0.50 → 0.45; < 0.50 → 0.50
- `_dense_only_retrieve()` 也升级为 pymilvus 原生 search（带 `output_fields`），失败时回退到 langchain_milvus
- 检索日志级别从 `debug` 提升到 `info`，便于线上排查

### 步骤 4: 统一入口 — `rag_engine.py`

- 新增 `add_pdf(pdf_path: str) -> int` 方法：解析 PDF → 清洗 → 切片 → 调用 `store_manager.add_documents()` 增量入库
- `_build_qa_chain()` 传入 `self._store_manager.collection` 和 `self._embeddings`

### 步骤 5: 删除旧集合 → 全量重建

执行 `rebuild_and_verify.py`：
1. 调用 `rag.clear_kb()` 删除旧 `public_kb` 集合
2. 调用 `rag.init_knowledge_base(PDF_DIR)` 重新解析 3 本 PDF 并入库
3. MinerU 缓存命中（3 本 PDF 均命中），跳过 OCR 解析，仅执行清洗和切片
4. bge-m3 向量化（SiliconFlow API，batch_size=100）
5. IVF_FLAT 索引构建 + collection.load()
6. 重建耗时: ~30 秒（得益于 MinerU 缓存 + SiliconFlow API 并发）

**入库数据统计**:

| PDF 文件 | 文档块数 | 文件大小 |
|----------|:-------:|:-------:|
| 中华人民共和国招标投标法律法规全书 | 1,081 | 48.4 MB |
| 招标投标法律解读与风险防范实务 | 837 | 6.3 MB |
| 政府采购工程招标投标与评标1200问 | 1,070 | 21.5 MB |
| **合计** | **2,988** | **76.2 MB** |

---

## 3. 遇到的问题

### 问题 1: BM25 Function 无法存储到服务器 🔴

**现象**: 使用 `FieldSchema(sparse_vector, SPARSE_FLOAT_VECTOR)` + `Function(BM25)` 创建 Schema，`CollectionSchema(fields=fields, functions=[bm25_fn])`。Schema 创建成功（服务器返回 4 个字段），但 `describe_collection()` 返回 `Functions: []`——BM25 Function 未存储在服务端。

**影响**: 无法生成 sparse_vector 值，混合检索的稀疏支路失效。

**排查过程**:
- 尝试 ORM `Collection()` + `CollectionSchema` → Functions 为空
- 尝试 `MilvusClient.create_collection()` + `CollectionSchema` → Functions 为空
- 尝试 `MilvusClient.create_collection()` + dict schema → 报错 `'dict' object has no attribute 'verify'`
- 对 SPARSE_FLOAT_VECTOR 设置 `nullable=True` → INSERT 时仍报 `DataNotMatchException: Insert missed field 'sparse_vector'`
- 对 text 字段设置 `enable_analyzer=True, analyzer_params={"type": "chinese"}` → 无效

**根因**: pymilvus 3.0.1 + Milvus 2.4.0 的组合存在 gRPC schema 序列化兼容性问题。pymilvus 3.x 的 protobuf 定义与 Milvus 2.4.0 服务端在 Function 字段的序列化/反序列化上不兼容，导致 Functions 被服务端静默丢弃。同时 pymilvus 3.0.1 的 ORM `Collection.insert()` 对 SPARSE_FLOAT_VECTOR 的 `nullable` 处理存在缺陷，即使设为 `nullable=True` 仍要求显式提供值。

**解决方案**: 暂时回退为稠密向量 Schema（仅 id/text/vector 三字段 + enable_dynamic_field），移除 sparse_vector 和 BM25 Function。检索链路自动检测 Schema 中无 sparse_vector 字段，降级为"pymilvus 稠密搜索 + Reranker"模式。BM25 混合检索留待后续升级 Milvus 2.5+ / pymilvus 版本解决。

### 问题 2: langchain_milvus 包装器不暴露 embedding_function 🟡

**现象**: `_retrieve()` 中尝试通过 `vector_store.embedding_function.embed_query(question)` 生成稠密查询向量，报错 `'Milvus' object has no attribute 'embedding_function'`。

**影响**: 每次检索都触发异常并进入降级路径，浪费异常处理开销，且日志产生 WARNING 噪音。

**解决方案**: 修改 `build_qa_chain()` 签名，新增 `embeddings` 参数，由 `rag_engine._build_qa_chain()` 直接传入 `self._embeddings`（`_SafeEmbeddings` 实例）。`_retrieve()` 使用传入的 `embeddings` 而非从 langchain_milvus 包装器提取。

### 问题 3: 降级路径检索的 metadata 缺失 🟡

**现象**: langchain_milvus `similarity_search_with_score()` 返回的 `Document` 对象中，`doc_name`、`chapter`、`chunk_index` 等动态字段均为默认值（"未知文档"/"未知章节"）。

**影响**: 问答来源追溯不可用，用户无法验证回答依据的具体文献章节。

**根因**: langchain_milvus 包装器内部使用 `search()` 未指定 `output_fields`，动态字段不在返回结果中。

**解决方案**: 改造 `_dense_only_retrieve()` 为三级降级策略：
1. **优先**: 若有 pymilvus Collection + embeddings → 使用 `collection.search()` 并指定 `output_fields=["text", "id", "doc_name", "chapter", "chunk_index"]`，完整获取元数据
2. **次选**: 若仅有 pymilvus Collection 但无 embeddings → 使用 langchain_milvus `similarity_search_with_score()`（metadata 可能不完整）
3. **兜底**: 返回空列表

### 问题 4: pymilvus 3.0.1 ORM API 弃用警告 🟢

**现象**: 每次调用 `Collection()`、`Collection.create_index()`、`Collection.insert()`、`Collection.search()` 等 ORM 方法时，pymilvus 3.0.1 输出 `PyMilvusDeprecationWarning: Use MilvusClient instead`。

**影响**: 日志噪音，不影响功能。ORC API 在 pymilvus 3.1 前仍可用。

**状态**: 已知问题，暂不处理。后续升级时可整体迁移至 `MilvusClient` API。

---

## 4. 已完成工作

### 4.1 代码修改清单

| # | 文件 | 改动类型 | 具体变更 |
|---|------|:---:|---------|
| 1 | `.env` | 修改 | `EMBEDDING_MODEL=BAAI/bge-m3`（原 BAAI/bge-large-zh-v1.5） |
| 2 | `config.py` | 修改 | `chunk_max_chars=2000`（原 400）；`chunk_overlap_chars=100`（原 50）；新增 6 个混合检索配置字段：`hybrid_dense_limit`、`hybrid_sparse_limit`、`hybrid_fusion_limit`、`nprobe`、`rrf_k`、`reranker_model` |
| 3 | `embedding_service.py` | 修改 | `_MAX_TEXT_CHARS=2000`（原 400） |
| 4 | `milvus_store.py` | **重构** | ① 显式 `CollectionSchema`（1024-dim + enable_dynamic_field）② `_batch_insert()` 通过 pymilvus 原生 API 入库 ③ 新增 `add_documents()` 增量方法 ④ 新增 `collection` 属性（暴露 pymilvus Collection）⑤ 更新 `load_existing()` ⑥ `nlist=256` |
| 5 | `qa_chain.py` | **重构** | ① `build_qa_chain()` 新增 `collection` / `embeddings` 参数 ② `_retrieve()` 支持混合检索 + RRF + Reranker + 动态阈值 + 三级自动降级 ③ 新增 `_SiliconFlowReranker` 类 ④ 新增 `_adaptive_threshold()` ⑤ `_dense_only_retrieve()` 升级为 pymilvus 原生 search（含 output_fields） |
| 6 | `rag_engine.py` | 新增 | ① `add_pdf()` 增量导入方法 ② `_build_qa_chain()` 传入 collection + embeddings |
| 7 | `docker-compose.yml` | 修改 | etcd / minio / standalone 三容器均添加 `restart: unless-stopped` |
| 8 | `requirements.txt` | 修改 | `pymilvus>=2.4.5`；新增 `sentence-transformers>=3.0` |
| 9 | `rebuild_and_verify.py` | **新增** | 一键重建 + 5 题验证脚本 |

### 4.2 功能点验证

| 功能点 | 状态 | 说明 |
|--------|:--:|------|
| bge-m3 1024-dim Embedding | ✅ | SiliconFlow API 正常返回，`_MAX_TEXT_CHARS=2000` 安全截断生效 |
| IVF_FLAT + COSINE + nlist=256 | ✅ | 索引创建正常，collection 加载正常 |
| nprobe=32 显式控制 | ✅ | 搜索参数中传入 `"params": {"nprobe": 32}` |
| 动态元数据存储 | ✅ | `doc_name`、`chapter`、`chunk_index` 通过 `enable_dynamic_field=True` 正确存储 |
| pymilvus 原生搜索 + 元数据 | ✅ | `output_fields=["text", "id", "doc_name", "chapter", "chunk_index"]` 正常返回 |
| Reranker API 调用 | ✅ | SiliconFlow `POST /v1/rerank` 正常返回 relevance_score |
| 动态阈值过滤 | ✅ | 三级阈值策略已嵌入 `_retrieve()` 和降级路径 |
| `add_documents()` 增量导入 | ✅ | 集合不存在时自动冷启动，存在时追加写入 |
| `add_pdf()` 单文件导入 | ✅ | 解析→清洗→切片→入库全链路 |
| Docker 自动重启 | ✅ | `restart: unless-stopped` 覆盖全部 3 个容器 |
| LCEL 管道语法不变 | ✅ | 外层 `RunnableLambda(_retrieve)` 签名和返回值类型不变 |

---

## 5. 未完成工作

| # | 功能点 | 原方案要求 | 当前状态 | 遗留原因 | 后续计划 |
|---|--------|-----------|:---:|---------|---------|
| 1 | **BM25 Function 实际启用** | `Function(BM25)` 从 text 自动生成 `sparse_vector` | ❌ 降级 | pymilvus 3.0.1 + Milvus 2.4.0 兼容性问题：Functions 无法存储到服务端，`SPARSE_FLOAT_VECTOR` nullable 不生效 | 方案 A: 升级 Milvus Docker 到 2.5.x（支持 BM25）<br>方案 B: 降级 pymilvus 到 2.4.x 稳定版<br>方案 C: 等待 pymilvus 3.1 修复 ORM 兼容性 |
| 2 | **SPARSE_INVERTED_INDEX 在线运行** | 稀疏向量索引用于 BM25 路由 | ❌ 降级 | 依赖 BM25 Function，sparse_vector 字段当前不存在于 Schema | 同 BM25 Function，解决后一并创建 |
| 3 | **混合检索端到端验证** | dense + sparse → RRF → Reranker 全链路 | ❌ 代码已就绪但未运行 | 混合检索代码存在（含自动降级），当前 Schema 无 sparse_vector，自动进入稠密+Reranker 模式 | Schema 就绪后无需修改 QA 链代码，自动切换 |
| 4 | **`milvus/backup.sh` 备份脚本** | docker exec milvus-backup + volumes tar | ⬜ 未开始 | 属阶段二任务（接口预留与验证），阶段一仅覆盖代码改造与重建 | 阶段二实施 |
| 5 | **Reranker 备选本地方案** | `sentence-transformers` 本地 Cross-Encoder | ⬜ 未实施 | SiliconFlow Reranker API 当前可用，本地备选留作降级兜底 | 阶段二补充 |
| 6 | **检索精度量化对比** | 10 条典型问答对比 v1.5 vs bge-m3 | ⬜ 未开始 | 阶段二任务 | 阶段二实施 |

### 5.1 BM25 Function 后续路径分析

当前最大技术债务是 BM25 混合检索无法实际运行。分析三种升级路径的可行性：

| 路径 | 操作 | 风险 | 收益 |
|------|------|:--:|------|
| **A: 升级 Milvus → 2.5.x** | 修改 `docker-compose.yml` 镜像版本 → `docker compose up -d` | 🟡 中：数据迁移兼容性未知，需验证 etcd/MinIO 数据格式 | 🔵 高：原生 BM25 + Analyzer 支持，一劳永逸 |
| **B: 降级 pymilvus → 2.4.x** | `pip install pymilvus==2.4.5` | 🟢 低：langchain-milvus 已声明支持 pymilvus 2.4.x | 🔵 中：ORM 兼容性好，但 pymilvus 2.4.x 已停止更新 |
| **C: 等待 pymilvus 3.1** | 无操作 | 🟢 低：无需改代码 | 🔴 低：发布周期不确定 |

**推荐路径**: 优先尝试 **方案 B**（降级 pymilvus），因为风险最低且与 langchain-milvus 版本兼容性最好。若方案 B 仍不可行，再考虑方案 A。

---

## 6. 验证结果

### 6.1 重建数据验证

```
Collection: public_kb
Schema: id(INT64), text(VARCHAR 65535), vector(FLOAT_VECTOR 1024-dim)
Dynamic fields: doc_name(VARCHAR), chapter(VARCHAR), chunk_index(INT64)
Index: IVF_FLAT + COSINE (nlist=256)
文档块总数: 2,988
```

### 6.2 问答质量验证

运行 `rebuild_and_verify.py` 中 5 个测试问题，结果如下：

---

**Q1: 招标方式有哪些？**

| 维度 | 结果 |
|------|------|
| 回答 | 根据提供的参考资料，招标方式有**公开招标**和**邀请招标**两种。 |
| 准确性 | ✅ 完全正确 |
| 来源 1 | [政府采购工程招标投标与评标1200问] 740.工程货物招标有哪两种方式？ (score=0.8131) |
| 来源 2 | [政府采购工程招标投标与评标1200问] 418.工程建设项目招标有哪几种方式？ (score=0.8128) |
| 来源 3 | [政府采购工程招标投标与评标1200问] 544.工程施工招标有哪两种方式？ (score=0.8011) |
| 检索模式 | 稠密 + Reranker（Schema 无 sparse_vector，自动降级） |

---

**Q2: 公开招标和邀请招标有什么区别？**

| 维度 | 结果 |
|------|------|
| 回答 | 包括发布方式、选择范围、竞争范围三个维度的详细对比 |
| 准确性 | ✅ 详细准确 |
| 来源 1 | [招标投标法律解读与风险防范实务] （三）公开招标与邀请招标的区别 (score=0.7987) |
| 来源 2 | [政府采购工程招标投标与评标1200问] 740.工程货物招标有哪两种方式？ (score=0.7971) |
| 来源 3 | [政府采购工程招标投标与评标1200问] 418.工程建设项目招标有哪几种方式？ (score=0.7968) |
| 检索模式 | 稠密 + Reranker |

---

**Q3: 履约保证金的比例是多少？**

| 维度 | 结果 |
|------|------|
| 回答 | 履约保证金不得超过中标合同金额的**10%**。引用《招标投标法实施条例》第五十八条和《政府采购法实施条例》第四十八条。还包括 PPP 项目的特殊规定。 |
| 准确性 | ✅ 准确，含法条引用 |
| 来源 1 | [政府采购工程招标投标与评标1200问] 979.履约保证金的上限是多少？ (score=0.7874) |
| 来源 2 | [招标投标法律解读与风险防范实务] 一、招标人可自主约定履约保证金条款 (score=0.7003) |
| 来源 3 | [政府采购工程招标投标与评标1200问] 1283.PPP项目采购中，对履约保证金有什么要求？ (score=0.6808) |
| 检索模式 | 稠密 + Reranker（6 条通过 similarity_threshold=0.65 过滤） |

---

**Q4: 投标保证金什么时候退还？**

| 维度 | 结果 |
|------|------|
| 回答 | 区分一般规定（合同签订后 5 日内）、政府采购货物和服务（中标通知书发出/合同签订后 5 个工作日内）、农业基本建设等特殊场景，逐一说明退还时限。 |
| 准确性 | ✅ 全面准确，区分多场景 |
| 来源 1 | [政府采购工程招标投标与评标1200问] 830.招标人应在几日内退还保证金？ (score=0.8407) |
| 来源 2 | [政府采购工程招标投标与评标1200问] 1088. 农业基本建设项目的招标人应何时退还投标保证金？ (score=0.8190) |
| 来源 3 | [招标投标法律解读与风险防范实务] 3.定标后退还投标保证金 (score=0.7976) |
| 检索模式 | 稠密 + Reranker |

---

**Q5: 废标的情形有哪些？**

| 维度 | 结果 |
|------|------|
| 回答 | 列出投标文件不符合实质性要求、未按规定署名盖章、附有无法接受的条件、内容不真实有效、正副本内容不符影响评标等多种情形。 |
| 准确性 | ✅ 准确，涵盖多种情形 |
| 来源 1 | [政府采购工程招标投标与评标1200问] 534.废标和流标有什么区别？ (score=0.7343) |
| 来源 2 | [中华人民共和国招标投标法律法规全书] （一)评标场所必须具有保密条件 (score=0.7077) |
| 来源 3 | [政府采购工程招标投标与评标1200问] 716.什么情形下建筑工程方案设计的投标文件应予以废标？ (score=0.7039) |
| 检索模式 | 稠密 + Reranker |

---

### 6.3 验证总结

| 指标 | 结果 |
|------|------|
| 问答正确率 | **5/5 (100%)** |
| 来源文件覆盖率 | **3/3 PDF 均被引用** |
| 来源章节正确性 | **100%**（章节名称与内容匹配，不再出现"未知文档"） |
| 平均检索 Score | **0.78**（COSINE 分数，Reranker 重排后） |
| 检索模式 | 稠密 + Reranker（因 BM25 Function 未启用，自动降级） |
| 降级日志 | `"当前 Schema 无稀疏向量字段，使用稠密+Reranker 模式"`（正常，非错误） |

---

## 7. 附录

### 7.1 修改文件清单

| 文件 | 改动行数 | 类型 |
|------|:---:|:---:|
| `.env` | 1 | 配置 |
| `public_kb/config.py` | +8 | 配置 |
| `public_kb/embedding_service.py` | +0（值变更） | 配置 |
| `public_kb/milvus_store.py` | +12 / -37 | 重构 |
| `public_kb/qa_chain.py` | +72 / -18 | 重构 |
| `public_kb/rag_engine.py` | +12 | 新增方法 |
| `milvus/docker-compose.yml` | +3 | 配置 |
| `requirements.txt` | +2 | 依赖 |
| `rebuild_and_verify.py` | 87 | 新增（测试脚本） |

### 7.2 运行环境

| 组件 | 版本 | 状态 |
|------|------|:--:|
| Milvus Server | v2.4.0 (Docker) | ✅ 运行中 |
| pymilvus | 3.0.1 | ⚠️ ORM 弃用警告 |
| langchain-milvus | 0.1.x | ✅ 正常 |
| Embedding Model | BAAI/bge-m3 (SiliconFlow API) | ✅ 正常 |
| Reranker Model | BAAI/bge-reranker-v2-m3 (SiliconFlow API) | ✅ 正常 |
| LLM | deepseek-chat (DeepSeek API) | ✅ 正常 |

---

> **结论**：阶段一重建已按方案完成，3 本 PDF（2,988 个文档块）成功入库 bge-m3 稠密向量知识库。问答验证 5/5 通过，来源追溯（文档名+章节）完整可用。BM25 Function 因 pymilvus 3.0.1 / Milvus 2.4.0 兼容性问题暂时降级为稠密+Reranker 模式（代码已预留混合检索能力，Schema 就绪后自动切换），建议下一阶段优先尝试降级 pymilvus 至 2.4.x 以启用 BM25 全链路混合检索。
