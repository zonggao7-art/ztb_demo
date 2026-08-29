# public_kb Pipeline 重构执行计划

> 制定日期: 2026-08-29  
> 重构范围: `public_kb/` 的在线检索、生成问答、离线向量化入库三条链路  
> 执行原则: 接口先冻结、目录小步迁移、行为默认不变、每步可验证、每步可回退  
> 协作约束: 不推送远端，不修改生产 `.env`，不改 Milvus 2.4 生产集合，不影响组员正在做的协程并发改造入口

---

## 1. 目标与非目标

### 1.1 目标

1. 将 `public_kb` 从平铺大文件结构拆成职责清晰的 Pipeline 结构。
2. 在线检索形成独立、可复用、可单测的 `HybridRetriever`。
3. 离线 PDF 和 CSV 入库统一为显式 `IngestionPipeline`。
4. 保持现有外部入口和返回结构稳定，避免影响 `agent` 与并发改造。
5. 为后续全量数据向量化入库做好准备，避免中间产物继续丢失行级元数据。

### 1.2 非目标

1. 本轮不重写 `MinerUParser`、`TextCleaner`、`SemanticChunker` 内部算法。
2. 本轮不把现有同步检索接口强行改成 async。
3. 本轮不删除旧的 LangChain Milvus wrapper 访问路径，等旧 2.4 schema 退役后再处理。
4. 本轮不引入复杂抽象基类继承树，只使用 Protocol、数据类和显式函数边界。
5. 本轮不直接执行全量数据入库。

---

## 2. 必须冻结的对外契约

以下接口在重构期间保持名称、输入、输出和异常语义稳定：

| 契约 | 说明 |
| --- | --- |
| `PublicKnowledgeRAG` | 对外知识库门面类，`init_knowledge_base()`、`query()`、`add_pdf()`、`clear_kb()` 行为不变 |
| `build_qa_chain()` | 继续返回可执行的 LCEL chain，现有调用不感知内部模块变化 |
| `Settings` | 只允许向后兼容新增字段，默认值必须保持当前行为 |
| `Document.metadata` | 离线入库与在线检索的元数据字段不丢失、不改名 |
| `RetrievalDiagnostics` | 检索模式、Reranker 状态、降级状态等诊断字段兼容 |
| Milvus collection schema | 离线与在线的字段名、维度、analyzer、BM25 Function 定义保持一致 |
| `AgentState` | `agent` 侧状态结构不变，业务节点返回结构不变 |

---

## 3. 总体目标结构

```text
public_kb/
  __init__.py
  __main__.py
  config.py
  contracts.py
  citations.py
  rag_engine.py

  ingestion/
    __init__.py
    pipeline.py
    cli.py
    models.py
    sources/
      __init__.py
      base.py
      pdf_source.py
      csv_source.py
    transforms/
      __init__.py
      cleaner.py
      chunker.py
      chunk_ids.py
    sinks/
      __init__.py
      base.py
      milvus_sink.py
      markdown_sink.py

  retrieval/
    __init__.py
    retriever.py
    milvus_search.py
    entities.py
    strategies.py
    fallback.py
    reranker/
      __init__.py
      protocol.py
      siliconflow.py

  generation/
    __init__.py
    prompts.py
    context.py
    chain.py
```

说明：

- `ingestion/` 负责数据加载、清洗、分块、校验、向量化入库。
- `retrieval/` 负责 dense、BM25、RRF、Reranker、阈值和降级。
- `generation/` 负责 prompt、上下文格式化、引用输出和 LCEL 问答链。
- `rag_engine.py` 最后只保留对外 API、组件组装和生命周期管理。
- 旧模块暂时保留兼容导出，不要求调用方一次性修改 import。

---

## 4. 阶段 0：重构前基线冻结

### 4.1 工作内容

1. 提交当前文档目录调整，形成干净的基线。
2. 记录当前测试结果和 POC 结果。
3. 确认当前实验集合、生产集合、`.env` 指向不被误改。
4. 与并发改造组员确认当前依赖入口。

### 4.2 验证命令

使用当前可用虚拟环境：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test -q
```

至少必须保留以下回归锚点：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test/test_kb_contracts.py test/test_recall_optimization.py test/test_sub_route.py test/test_bug_repairs.py test/test_citation_tracing.py -q
```

Milvus POC 回归：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" scripts/poc_probe_function.py
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" scripts/poc_verify_hybrid.py
```

如需重建小批量样本：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" scripts/poc_ingest_sample.py
```

### 4.3 完成标准

- 离线测试全绿。
- POC 8/8 用例通过。
- `git status` 干净，或只剩明确说明的文档变更。
- 已确认协程组当前不直接依赖 `qa_chain.py` 内部私有函数。

### 4.4 产出

一次本地提交：

```text
docs: 建立 pipeline 重构基线与执行计划
```

---

## 5. 阶段 1：在线检索层拆分

### 5.1 R1.1 建立 `retrieval/` 包骨架

新增：

```text
public_kb/retrieval/__init__.py
public_kb/retrieval/retriever.py
public_kb/retrieval/milvus_search.py
public_kb/retrieval/entities.py
public_kb/retrieval/strategies.py
public_kb/retrieval/fallback.py
public_kb/retrieval/reranker/__init__.py
public_kb/retrieval/reranker/protocol.py
public_kb/retrieval/reranker/siliconflow.py
```

本步只建立目录和空实现/协议，不改变 `qa_chain.py` 行为。

验收：

1. `python -m pytest test -q` 全绿。
2. `from public_kb.qa_chain import build_qa_chain` 继续可用。

建议提交：

```text
refactor(retrieval): add retrieval package skeleton
```

---

### 5.2 R1.2 抽离 Reranker

从 `qa_chain.py` 抽出：

- `_SiliconFlowReranker`
- HTTP 请求逻辑
- 失败降级逻辑

目标：

```python
public_kb/retrieval/reranker/protocol.py
public_kb/retrieval/reranker/siliconflow.py
```

要求：

1. 保留 `http_client` 注入能力。
2. Reranker 失败时继续保留 RRF 排序，不恢复假分 0.5。
3. 诊断信息继续写入 `RetrievalDiagnostics`。
4. `qa_chain.py` 中保留 `_SiliconFlowReranker` 兼容别名。

本步是纯迁移，不新增重试策略。

验收：

1. POC 中 `hybrid_rerank` 用例通过。
2. POC 中 Reranker 故障降级用例通过。
3. 现有离线测试全绿。

建议提交：

```text
refactor(retrieval): extract reranker client
```

---

### 5.3 R1.3 抽离 Milvus 搜索与实体归一化

从 `qa_chain.py` 抽出：

- `_normalize_hit_entity()`
- `_entity_to_doc()`
- `_search_with_full_fields()`
- `_hybrid_search_with_full_fields()`
- dense/BM25 请求构造
- RRF 融合调用

目标：

```python
public_kb/retrieval/entities.py
public_kb/retrieval/milvus_search.py
```

要求：

1. 不修改 Milvus 字段名。
2. 不修改 score 语义。
3. 不修改 output fields 回退逻辑。
4. 不修改 sparse schema 探测缓存行为。
5. `qa_chain.py` 中保留同名私有函数兼容壳。

验收：

1. dense 单路 POC 通过。
2. BM25 单路 POC 通过。
3. hybrid + RRF POC 通过。
4. 现有离线测试全绿。

建议提交：

```text
refactor(retrieval): extract milvus hybrid search
```

---

### 5.4 R1.4 抽离阈值策略与 dense fallback

从 `qa_chain.py` 抽出：

- `_adaptive_threshold()`
- `_dense_only_retrieve()`

目标：

```python
public_kb/retrieval/strategies.py
public_kb/retrieval/fallback.py
```

要求：

1. 本步只迁移逻辑，不改变阈值档位。
2. dense fallback 的 score 语义保持不变。
3. RRF、dense、rerank 三类 score 不混用同一阈值。
4. `qa_chain.py` 保留旧函数名兼容壳。

验收：

1. 拒答用例通过。
2. 引用规则 R1-R7 通过。
3. Reranker 故障降级用例通过。
4. 现有离线测试全绿。

建议提交：

```text
refactor(retrieval): extract threshold strategy and dense fallback
```

---

### 5.5 R1.5 建立 `HybridRetriever`

新增统一入口：

```python
class HybridRetriever:
    def __init__(
        self,
        *,
        client,
        embeddings,
        settings,
        collection_name: str,
        reranker=None,
    ) -> None: ...

    def retrieve(
        self,
        question: str,
        *,
        dense_vec=None,
    ) -> RetrievalResult: ...
```

职责：

1. 编排输入校验。
2. 调用 dense/BM25/hybrid 搜索。
3. 决定是否调用 Reranker。
4. 执行阈值策略。
5. 处理降级。
6. 返回 `RetrievalResult`。

要求：

1. `_retrieve()` 闭包改成调用 `HybridRetriever.retrieve()`。
2. `build_qa_chain()` 的外部行为不变。
3. `qa_chain.py` 不再直接承载 200 行检索业务。
4. 不在本步新增 async 接口。

验收：

1. 全链路 `hybrid_rerank` POC 通过。
2. Reranker 失败降级 POC 通过。
3. 严格模式 POC 通过。
4. 离线测试全绿。

建议提交：

```text
refactor(retrieval): introduce HybridRetriever pipeline
```

---

## 6. 阶段 2：生成层拆分

### 6.1 R2.1 抽离 Prompt

从 `qa_chain.py` 抽出：

- `_build_prompt()`
- inline citation instruction

目标：

```python
public_kb/generation/prompts.py
```

要求：

1. Prompt 文本不改。
2. Prompt 变量名不改。
3. `qa_chain.py` 保留 `_build_prompt()` 兼容导出。

建议提交：

```text
refactor(generation): extract prompt builders
```

---

### 6.2 R2.2 抽离上下文与来源格式化

从 `qa_chain.py` 抽出：

- `_format_docs()`
- `_build_sources()`

目标：

```python
public_kb/generation/context.py
```

要求：

1. 来源字段映射不变。
2. `chunk_uid` 派生规则不变。
3. 引用编号规则不变。

建议提交：

```text
refactor(generation): extract context formatting
```

---

### 6.3 R2.3 瘦身 `qa_chain.py`

目标：

```python
public_kb/generation/chain.py
public_kb/qa_chain.py
```

要求：

1. `generation/chain.py` 承担 `_decide_and_answer()` 和 LCEL 装配。
2. `qa_chain.py` 变成兼容门面，继续导出：
   - `build_qa_chain`
   - `_SiliconFlowReranker`
   - `_adaptive_threshold`
   - `_dense_only_retrieve`
   - 其他既有测试依赖的私有符号
3. 外部调用不需要立即修改 import。

验收：

1. `python -m agent --question "招标方式有哪些？"` 正常返回。
2. 公共知识库独立问答可用。
3. 引用规则 R1-R7 全部通过。
4. 离线测试和 POC 全绿。

建议提交：

```text
refactor(generation): split qa chain from retrieval pipeline
```

---

## 7. 阶段 3：离线入库 Pipeline 拆分

### 7.1 R3.1 建立入库契约与 Pipeline 骨架

新增：

```python
public_kb/ingestion/models.py
public_kb/ingestion/pipeline.py
public_kb/ingestion/sources/base.py
public_kb/ingestion/sinks/base.py
```

核心模型：

```python
@dataclass(frozen=True)
class StageResult:
    name: str
    input_count: int
    output_count: int
    skipped_count: int
    elapsed_ms: float
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class IngestionResult:
    source: str
    chunk_count: int
    inserted_count: int
    stage_results: tuple[StageResult, ...]
    status: str
    error: str | None = None
```

核心接口：

```python
class Source(Protocol):
    def load(self) -> list[Document]: ...


class Sink(Protocol):
    def write(self, documents: list[Document]) -> IngestionResult: ...
```

验收：

1. 不改变现有入库行为。
2. 现有 PDF 初始化和 CSV 入库入口仍可用。

建议提交：

```text
refactor(ingestion): add ingestion pipeline contracts
```

---

### 7.2 R3.2 抽离 transforms

迁移以下模块：

| 旧位置 | 新位置 |
| --- | --- |
| `public_kb/text_cleaner.py` | `public_kb/ingestion/transforms/cleaner.py` |
| `public_kb/chunker.py` | `public_kb/ingestion/transforms/chunker.py` |
| `public_kb/chunk_ids.py` | `public_kb/ingestion/transforms/chunk_ids.py` |

要求：

1. 内部算法不改。
2. 旧路径保留 re-export。
3. `public_kb` 与 `agent` 侧既有 import 不受影响。

建议提交：

```text
refactor(ingestion): move shared transforms
```

---

### 7.3 R3.3 抽离 PDF Source

将 PDF 解析流程封装为：

```python
public_kb/ingestion/sources/pdf_source.py
```

职责：

1. 调用 `MinerUParser`。
2. 调用 `TextCleaner`。
3. 调用 `SemanticChunker`。
4. 输出标准 `Document` 列表。

要求：

1. `rag_engine._process_single_pdf()` 改为调用 `PdfSource`。
2. PDF 缓存逻辑保持不变。
3. 不修改 MinerU API 调用方式。

建议提交：

```text
refactor(ingestion): extract pdf source
```

---

### 7.4 R3.4 抽离 CSV Source 并修复元数据链路

目标：

```python
public_kb/ingestion/sources/csv_source.py
```

要求：

1. CSV 解析、行标准化、正文清洗、分块后直接在内存中生成 `Document`。
2. 正式入库路径不再依赖 `*_chunks.md` 回灌。
3. `title`、`publish_date`、`source_url`、`doc_name`、`chapter`、`chunk_index` 等字段必须完整进入 metadata。
4. Markdown 只作为可选预览或调试产物，不作为正式入库源。

本步是全量入库前的 P0 前置条件。

验收：

1. 抽取 3-5 个 CSV 文件做小批量入库。
2. 检查 Milvus 实体 metadata 中行级溯源字段完整。
3. 用 `chunk_uid` 反查源数据一致。
4. 现有 CSV 入库测试和知识库测试全绿。

建议提交：

```text
refactor(ingestion): extract csv source with lossless metadata
```

---

### 7.5 R3.5 抽离 Milvus Sink 与 Markdown Sink

目标：

```python
public_kb/ingestion/sinks/milvus_sink.py
public_kb/ingestion/sinks/markdown_sink.py
```

要求：

1. `MilvusSink` 封装 `MilvusStoreManager`。
2. 入库前调用 `validate_ingestion_documents()`。
3. MarkdownSink 只负责可选中间产物，不再混入 CSV loader。
4. 批量大小、flush、load 策略保持不变。

建议提交：

```text
refactor(ingestion): extract milvus and markdown sinks
```

---

### 7.6 R3.6 统一编排入口

目标：

```python
public_kb/ingestion/pipeline.py
public_kb/ingestion/cli.py
```

Pipeline 顺序：

```text
Source.load()
  -> validate_ingestion_documents()
  -> transforms.clean
  -> transforms.chunk
  -> validate_ingestion_documents()
  -> Sink.write()
  -> StageResult 统计
```

要求：

1. `rag_engine.init_knowledge_base()` 改为组装 PDF Pipeline。
2. `process_csv.py` 改为兼容 CLI 壳。
3. 新增 CLI 能显式选择：
   - `--source pdf`
   - `--source csv`
   - `--sink milvus`
   - `--sink markdown`
   - `--fail-fast`
   - `--skip-record`
4. 每个阶段输出统计、耗时、失败原因。

建议提交：

```text
refactor(ingestion): unify pdf and csv ingestion pipeline
```

---

## 8. 阶段 4：门面瘦身与策略配置化

### 8.1 R4.1 瘦身 `rag_engine.py`

最终只保留：

1. 对外 API。
2. 单例生命周期。
3. Pipeline 组件组装。
4. QA chain 组装。

移除：

1. 私有 PDF 编排逻辑。
2. 具体清洗、分块逻辑。
3. 具体检索逻辑。
4. 无信息转发函数。

建议提交：

```text
refactor(public_kb): slim public rag facade
```

---

### 8.2 R4.2 阈值策略配置化

要求：

1. `Settings` 新增可选阈值配置字段。
2. 默认值必须等于当前硬编码值，保证行为不变。
3. RRF、dense、rerank 分别定义策略，不共用同一个绝对阈值。
4. 不在 `.env` 中直接修改生产配置。

验收：

1. 不配置新字段时，检索结果与当前一致。
2. 配置新字段后，可覆盖阈值。
3. 测试覆盖默认值和覆盖值两种情况。

建议提交：

```text
feat(retrieval): make adaptive thresholds configurable
```

---

### 8.3 R4.3 Reranker 瞬时错误重试

要求：

1. 只针对网络错误、超时、429、5xx 等瞬时错误重试。
2. 最多 2 次重试，指数退避。
3. 全部失败后仍降级保留 RRF 排序。
4. 诊断中记录重试次数和最终状态。

不建议使用 tenacity 之外再引入新依赖；如果当前没有 tenacity，可先用小函数实现，避免扩大依赖面。

建议提交：

```text
feat(retrieval): retry transient reranker failures
```

---

## 9. 阶段 5：全链路验证

### 9.1 功能验证

必须通过：

1. 离线测试全绿。
2. POC 8/8 全绿。
3. `python -m agent --question "..."` 正常。
4. `python -m public_kb --interactive` 正常。
5. PDF 初始化链路可用。
6. CSV 小批量入库可用。
7. Milvus metadata 溯源字段完整。

### 9.2 检索质量验证

优先使用现有测试集：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" scripts/run_knowledge_citation_eval.py
```

如果 `testset_knowledge.jsonl` 仍缺失，则先用小批量人工标注集验证，不得把未验证结果称为生产达标。

最低观察指标：

| 指标 | 目标 |
| --- | --- |
| Hit@5 | 不低于重构前基线 |
| MRR@10 | 不低于重构前基线 |
| 引用完整率 | 不低于重构前基线 |
| Reranker 故障降级成功率 | 100% 返回 RRF 结果，不出现假分 |
| 拒答准确率 | 无有效证据时不编造答案 |

### 9.3 性能验证

记录：

1. dense 检索耗时。
2. BM25 检索耗时。
3. RRF 融合耗时。
4. Reranker 耗时。
5. 正常链路总耗时。
6. Reranker 故障降级链路总耗时。
7. CSV 小批量入库吞吐。

如果重构后性能下降超过 10%，先定位原因，不继续合并后续功能。

---

## 10. 与协程并发改造的协作规则

### 10.1 本轮保证不变

1. `PublicKnowledgeRAG.query()` 仍是同步入口。
2. `HybridRetriever.retrieve()` 仍是同步方法。
3. `AgentState` 结构不变。
4. 业务节点输入输出结构不变。
5. `build_qa_chain()` 返回的 Runnable 行为不变。

并发组可以继续在外层使用协程、线程池或批量调度，不需要等待本轮完成。

### 10.2 后续再开放的异步边界

等检索层拆分稳定后，再评估是否新增：

```python
async def aretrieve(self, question: str) -> RetrievalResult: ...
```

但本轮不实现。

### 10.3 需要并发组注意的共享资源

| 资源 | 注意点 |
| --- | --- |
| Milvus client | 明确是否进程内复用，避免每次请求重建 |
| Embedding client | 确认 HTTP client 并发安全与限流 |
| Reranker client | 保持注入式设计，便于压测和 mock |
| `PublicKnowledgeRAG` 单例 | 避免并发首次访问时重复初始化 |
| schema 探测缓存 | 必须保证幂等，避免并发首次查询互相覆盖 |
| MySQL 连接池 | 与本解耦无直接关系，但不要因 import 变化影响连接池初始化 |

---

## 11. 风险与回退策略

| 风险 | 影响 | 缓解措施 |
| --- | --- | --- |
| 移动模块导致 import 断裂 | 测试或启动失败 | 旧路径保留 re-export，逐步清理 |
| 检索结果变化 | 排序、引用或拒答变化 | 每步跑 POC，POC 不过不合并 |
| score 语义混淆 | 阈值过滤错误 | RRF、dense、rerank 分别处理 |
| CSV metadata 仍丢失 | 无法溯源 | 全量入库前必须完成 CSV Source 直通 |
| 和并发分支冲突 | 合并成本升高 | 只动 `public_kb`，不改 `AgentState` 和节点返回结构 |
| 误改生产 `.env` | 连接错误集合 | 阶段 0 记录备份，重构期间不改生产配置 |
| 一次性提交过大 | 难以回退 | 每个子阶段单独提交 |

每个阶段必须形成独立本地 commit；如果某一步 POC 或测试失败，优先回退该阶段 commit，不在错误状态上继续堆补丁。

---

## 12. 推荐提交序列

```text
docs: establish pipeline refactor execution plan
docs: organize execution plans under docs/execution_plans
refactor(retrieval): add retrieval package skeleton
refactor(retrieval): extract reranker client
refactor(retrieval): extract milvus hybrid search
refactor(retrieval): extract threshold strategy and dense fallback
refactor(retrieval): introduce HybridRetriever pipeline
refactor(generation): extract prompt builders
refactor(generation): extract context formatting
refactor(generation): split qa chain from retrieval pipeline
refactor(ingestion): add ingestion pipeline contracts
refactor(ingestion): move shared transforms
refactor(ingestion): extract pdf source
refactor(ingestion): extract csv source with lossless metadata
refactor(ingestion): extract milvus and markdown sinks
refactor(ingestion): unify pdf and csv ingestion pipeline
refactor(public_kb): slim public rag facade
feat(retrieval): make adaptive thresholds configurable
feat(retrieval): retry transient reranker failures
test(public_kb): add pipeline regression coverage
```

实际提交数量可以按执行情况合并，但每个阶段的验证点和回退点必须保留。

---

## 13. 总体完成标准

1. `qa_chain.py` 不再承载检索、Reranker、fallback、prompt、context 全部逻辑。
2. `HybridRetriever` 可以被独立构造、独立测试、独立复用。
3. PDF 和 CSV 都通过统一 `IngestionPipeline` 入库。
4. 正式 CSV 入库不再经过丢失 metadata 的 Markdown 回灌。
5. 现有测试、POC、引用规则全部通过。
6. `agent` 和并发改造入口无破坏性变化。
7. 后续全量向量化入库可以直接使用新 `ingestion.pipeline`。

---

## 14. 立即下一步

执行阶段 0：

1. 确认当前文档目录调整。
2. 本地提交当前计划和文档归档。
3. 运行离线测试与 POC，记录基线结果。
4. 停下等待确认后，再进入 R1.1。
