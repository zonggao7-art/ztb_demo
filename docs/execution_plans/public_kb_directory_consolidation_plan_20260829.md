# public_kb 目录收敛执行计划

> 计划日期：2026-08-29
> 范围：`public_kb` 目录结构收敛、兼容层治理、离线入库与在线检索回归验证
> 原则：先冻结行为，再做物理移动；每个阶段独立本地提交；每阶段必须通过验证后才允许进入下一阶段

---

## 1. 目标

1. 消除“真实实现留在包根 + 新目录只放转发壳”的过渡状态。
2. 将离线索引构建、在线检索、生成引用、共享基础服务分成清晰的稳定边界。
3. 保持 `PublicKnowledgeRAG`、`build_qa_chain()`、Agent 节点返回结构、Milvus schema、检索诊断结构不变。
4. 不影响协程并发改造；本轮仍只维护同步入口，不新增 async API。
5. 全量向量化入库前，先完成结构收敛和小批量验证。

---

## 2. 目标目录结构

```text
public_kb/
├── __init__.py
├── __main__.py
├── config.py
├── contracts.py
├── chunk_ids.py
├── rag_engine.py
│
├── services/
│   ├── embeddings.py
│   ├── llm.py
│   ├── milvus_store.py
│   └── mineru_parser.py
│
├── ingestion/
│   ├── cli.py
│   ├── pipeline.py
│   ├── models.py
│   │
│   ├── sources/
│   │   ├── base.py
│   │   ├── csv_loader.py
│   │   ├── csv_source.py
│   │   ├── document_source.py
│   │   └── pdf_source.py
│   │
│   ├── transforms/
│   │   ├── cleaner.py
│   │   └── chunker.py
│   │
│   └── sinks/
│       ├── base.py
│       ├── markdown_sink.py
│       └── milvus_sink.py
│
├── retrieval/
│   ├── retriever.py
│   ├── milvus_search.py
│   ├── entities.py
│   ├── fallback.py
│   ├── strategies.py
│   └── reranker.py
│
└── generation/
    ├── chain.py
    ├── context.py
    ├── prompts.py
    └── citations.py
```

`rag_engine.py` 保留在包根，因为 `PublicKnowledgeRAG` 是稳定对外门面。
`config.py`、`contracts.py`、`chunk_ids.py` 保留在包根，因为它们是跨链路共享契约和工具。
`qa_chain.py` 在阶段 6A 继续保留兼容门面；阶段 6B 再评估删除。
`process_csv.py` 在阶段 6A 暂时保留；等 `ingestion/cli.py` 具备能力等价后再合并。

---

## 3. 阶段 0：结构冻结与守卫测试

### 执行内容

1. 记录当前 Git 状态，确认无未提交代码。
2. 记录当前基线：
   - 离线测试：目标至少 `221 passed`；
   - Milvus POC：目标 `8/8 PASS`；
   - CSV 小批量验证：目标 `PASS`。
3. 新增结构守卫测试 `test/test_public_kb_layout.py`，覆盖：
   - 新目录和目标模块可以 import；
   - 旧路径 re-export 与新路径对象一致；
   - `ingestion/` 内部不再直接依赖旧包根实现模块；
   - `rag_engine.py` 只依赖新服务模块；
   - 在线链路只依赖 `retrieval/reranker.py` 新模块；
   - `qa_chain.py` 暴露的兼容符号仍然存在。

### 验收标准

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test -q --ignore=test/test_cloud_sync.py
```

期望：全部通过，新增 layout 测试也通过。

建议提交：

```text
test(public_kb): add package layout guard
```

---

## 4. 阶段 1：收敛共享基础服务到 `services/`

### 4.1 文件迁移

| 当前位置 | 目标位置 | 兼容策略 |
| --- | --- | --- |
| `public_kb/embedding_service.py` | `public_kb/services/embeddings.py` | 旧路径保留 re-export |
| `public_kb/llm_factory.py` | `public_kb/services/llm.py` | 旧路径保留 re-export |
| `public_kb/milvus_store.py` | `public_kb/services/milvus_store.py` | 旧路径保留 re-export |
| `public_kb/mineru_parser.py` | `public_kb/services/mineru_parser.py` | 旧路径保留 re-export |

### 4.2 内部引用更新

至少更新以下内部调用方：

- `public_kb/rag_engine.py`
- `public_kb/ingestion/cli.py`
- `public_kb/ingestion/sources/pdf_source.py`

`scripts/` 下的 POC 脚本可以在阶段 6A 暂时继续走旧路径；阶段 6B 再统一更新。

### 4.3 验证测试

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test/test_public_kb_layout.py test/test_milvus_store_offline.py test/test_public_kb_offline_gate.py test/test_ingestion_pipeline.py -q
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test -q --ignore=test/test_cloud_sync.py
```

补充冒烟：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -c "from public_kb.services.embeddings import create_embeddings; from public_kb.services.llm import create_llm; from public_kb.services.milvus_store import MilvusStoreManager; from public_kb.services.mineru_parser import MinerUParser; print('SERVICE_IMPORT: PASS')"
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -c "from public_kb.embedding_service import create_embeddings as old_embed; from public_kb.services.embeddings import create_embeddings as new_embed; assert old_embed is new_embed; print('LEGACY_SERVICE_ALIAS: PASS')"
```

### 验收标准

1. 新旧 import 路径都能使用。
2. 旧路径与新路径指向同一对象。
3. 全量离线测试通过。
4. 不修改 Milvus schema、embedding 逻辑、LLM 构造逻辑。

建议提交：

```text
refactor(public_kb): consolidate shared services package
```

---

## 5. 阶段 2：收敛离线入库 Source 与 Transform

### 5.1 文件迁移

| 当前位置 | 目标位置 | 兼容策略 |
| --- | --- | --- |
| `public_kb/chunker.py` | `public_kb/ingestion/transforms/chunker.py` | 旧包根路径保留 re-export |
| `public_kb/text_cleaner.py` | `public_kb/ingestion/transforms/cleaner.py` | 旧包根路径保留 re-export |
| `public_kb/csv_loader.py` | `public_kb/ingestion/sources/csv_loader.py` | 旧包根路径保留 re-export |

同时删除以下纯转发壳：

```text
public_kb/ingestion/transforms/chunker.py
public_kb/ingestion/transforms/cleaner.py
```

注意：这里不是简单新建转发文件，而是把包根真实实现移动到目标位置，再让旧包根路径反向兼容。

`public_kb/ingestion/transforms/chunk_ids.py` 直接删除。
`chunk_ids` 属于 ingestion、retrieval、citations 共用能力，继续保留在 `public_kb/chunk_ids.py`。

### 5.2 内部引用更新

至少更新：

- `public_kb/csv_loader.py` 移动后的内部导入；
- `public_kb/ingestion/sources/csv_source.py`；
- `public_kb/ingestion/sources/pdf_source.py`；
- `public_kb/ingestion/sinks/markdown_sink.py`；
- `public_kb/rag_engine.py`；
- `public_kb/process_csv.py`；
- `public_kb/ingestion/cli.py`。

### 5.3 验证测试

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test/test_public_kb_layout.py test/test_ingestion_pipeline.py test/test_citation_tracing.py -q
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test -q --ignore=test/test_cloud_sync.py
```

补充导入与别名验证：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -c "from public_kb.ingestion.transforms.chunker import SemanticChunker; from public_kb.ingestion.transforms.cleaner import TextCleaner; from public_kb.ingestion.sources.csv_loader import CsvLoader; print('INGESTION_IMPORT: PASS')"
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -c "from public_kb.chunker import SemanticChunker as OldChunker; from public_kb.ingestion.transforms.chunker import SemanticChunker as NewChunker; assert OldChunker is NewChunker; print('LEGACY_CHUNKER_ALIAS: PASS')"
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -c "from public_kb.text_cleaner import TextCleaner as OldCleaner; from public_kb.ingestion.transforms.cleaner import TextCleaner as NewCleaner; assert OldCleaner is NewCleaner; print('LEGACY_CLEANER_ALIAS: PASS')"
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -c "from public_kb.csv_loader import CsvLoader as OldLoader; from public_kb.ingestion.sources.csv_loader import CsvLoader as NewLoader; assert OldLoader is NewLoader; print('LEGACY_CSV_LOADER_ALIAS: PASS')"
```

### 验收标准

1. `ingestion/transforms/` 内是真实实现，不再是转发壳。
2. CSV 解析真实实现位于 `ingestion/sources/csv_loader.py`。
3. Markdown preview、CSV metadata、chunk index 行为不变。
4. `process_csv.py --help` 和 `python -m public_kb.ingestion.cli --help` 均可用。

建议提交：

```text
refactor(ingestion): move transforms and csv loader into pipeline package
```

---

## 6. 阶段 3：收敛引用校验与 Reranker

### 6.1 文件迁移

| 当前位置 | 目标位置 | 兼容策略 |
| --- | --- | --- |
| `public_kb/citations.py` | `public_kb/generation/citations.py` | 旧包根路径保留 re-export |
| `public_kb/retrieval/reranker/protocol.py` | `public_kb/retrieval/reranker.py` | 内部 import 直接更新 |
| `public_kb/retrieval/reranker/siliconflow.py` | `public_kb/retrieval/reranker.py` | 内部 import 直接更新 |

`retrieval/reranker/` 是当前内部实现目录，未作为稳定对外契约使用；可以收敛为单文件 `retrieval/reranker.py`。

### 6.2 内部引用更新

更新：

- `public_kb/generation/chain.py`
- `public_kb/__main__.py`
- `public_kb/qa_chain.py`
- `public_kb/retrieval/retriever.py`
- 引用 `citations` 的测试与脚本

### 6.3 验证测试

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test/test_public_kb_layout.py test/test_citation_tracing.py test/test_qa_chain_offline.py test/test_kb_contracts.py -q
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test -q --ignore=test/test_cloud_sync.py
```

补充导入验证：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -c "from public_kb.generation.citations import CitationValidator, build_citations, format_citations; print('CITATION_IMPORT: PASS')"
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -c "from public_kb.citations import CitationValidator as OldValidator; from public_kb.generation.citations import CitationValidator as NewValidator; assert OldValidator is NewValidator; print('LEGACY_CITATION_ALIAS: PASS')"
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -c "from public_kb.retrieval.reranker import Reranker, SiliconFlowReranker; print('RERANKER_IMPORT: PASS')"
```

### 验收标准

1. Reranker 失败时仍返回 RRF 原始排序，不注入假分。
2. Citation R1-R7 回归全部通过。
3. `PublicKnowledgeRAG.query()` 返回结构不变。
4. `qa_chain.py` 继续暴露 `_SiliconFlowReranker`、`_dense_only_retrieve`、`build_qa_chain` 等兼容符号。

建议提交：

```text
refactor(public_kb): consolidate citations and reranker modules
```

---

## 7. 阶段 4：端到端结构验证

### 7.1 离线测试

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test -q --ignore=test/test_cloud_sync.py
```

最低通过线：

```text
221 passed
```

如果新增 layout 测试后数量增加，则要求新增测试全部通过。

### 7.2 Milvus 功能探针

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" scripts/poc_probe_function.py
```

期望：

```text
PROBE: PASS
```

确认服务端仍具备：

```text
fields: sparse_vector
functions: text_bm25_emb
```

### 7.3 混合检索 POC

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" scripts/poc_verify_hybrid.py
```

期望：

```text
8/8 PASS
```

重点确认：

| 用例 | 必须保持 |
| --- | --- |
| dense-only | 可召回 |
| bm25-only | 可召回 |
| hybrid-rrf(raw) | dense + sparse 融合 |
| full-chain(reranker real) | mode 正常 |
| reranker-failure fallback | 返回 RRF，不注入假分 |
| irrelevant -> refusal | 无证据时拒答 |
| citation R1-R7 | 全部通过 |
| strict-mode e2e | 无静默降级 |

### 7.4 CSV 小批量入库

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" scripts/poc_validate_csv_ingestion.py --refresh
```

期望：

```text
CSV-INGESTION-CHECK: PASS
```

检查项：

1. 实验集合独立，不影响生产集合。
2. `chunk_count == inserted_count == row_count == queried_count`。
3. metadata 缺失列表为空。
4. `title`、`publish_date`、`source_url`、`source_file`、`doc_name`、`chapter`、`chunk_index`、`chunk_uid` 全部可回查。
5. `sparse_vector` 字段和 `text_bm25_emb` Function 存在。
6. `vector`、`sparse_vector` 索引存在。

### 7.5 CLI 冒烟

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m public_kb --help
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m public_kb.ingestion.cli --help
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m public_kb.process_csv --help
```

期望：三个入口均可正常输出帮助，不触发初始化、清空集合或生产写入。

建议提交：

```text
test(public_kb): verify consolidated package layout
```

---

## 8. 阶段 5：合并 CSV 批处理入口

### 8.1 当前差异

`public_kb/ingestion/cli.py` 目前只支持单个 CSV：

```text
--csv-path
--markdown-output-dir
--mode initialize|append
```

`public_kb/process_csv.py` 还支持批量能力：

```text
--csv-dir
--group A|C
--no-import
```

因此不能直接删除 `process_csv.py`。

### 8.2 执行内容

1. 将 `process_csv.py` 的目录扫描、A/C 分组、失败统计、Markdown 校验能力迁移到 `ingestion/cli.py`。
2. 统一命令参数：
   - `--csv-path`：单文件；
   - `--csv-dir`：批量目录；
   - `--group`：可选 `A`、`C`；
   - `--no-import`：只生成 preview，不写 Milvus；
   - `--markdown-output-dir`；
   - `--mode initialize|append`。
3. `process_csv.py` 阶段 6A 改成薄兼容入口，内部转发 `public_kb.ingestion.cli`。
4. 阶段 6B 再删除 `process_csv.py`，或保留一行废弃提示。

### 8.3 新增测试

新增 `test/test_ingestion_cli.py`，使用 mock 覆盖：

1. 单 CSV 调用参数；
2. 目录扫描和 A/C 分组；
3. `--group A` 只处理 A；
4. `--group C` 只处理 C；
5. `--no-import` 不创建 MilvusSink 或不触发 Milvus 写入；
6. Markdown preview sink 参数传递正确；
7. 失败文件计数和汇总统计正确；
8. 旧入口 `process_csv.py` 能转发到新 CLI。

### 8.4 验证测试

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test/test_ingestion_cli.py test/test_ingestion_pipeline.py -q
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test -q --ignore=test/test_cloud_sync.py
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m public_kb.ingestion.cli --help
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m public_kb.process_csv --help
```

建议提交：

```text
refactor(ingestion): unify csv cli entrypoints
```

---

## 9. 阶段 6B：删除兼容层

该阶段默认不与阶段 6A 同步执行，必须等待：

1. 离线测试通过；
2. Milvus POC 通过；
3. CSV 小批量验证通过；
4. 协程并发改造分支完成合并或明确确认不依赖旧路径；
5. 文档中不再引导使用旧路径。

### 删除清单

| 路径 | 处理 |
| --- | --- |
| `public_kb/qa_chain.py` | 确认外部不再依赖后删除 |
| `public_kb/process_csv.py` | CLI 合并完成后删除或改为废弃提示 |
| `public_kb/embedding_service.py` | 删除旧 alias |
| `public_kb/llm_factory.py` | 删除旧 alias |
| `public_kb/milvus_store.py` | 删除旧 alias |
| `public_kb/mineru_parser.py` | 删除旧 alias |
| `public_kb/chunker.py` | 删除旧 alias |
| `public_kb/text_cleaner.py` | 删除旧 alias |
| `public_kb/csv_loader.py` | 删除旧 alias |
| `public_kb/citations.py` | 删除旧 alias |

### 删除前验证

全仓库搜索旧路径：

```powershell
rg -n "public_kb\.embedding_service|public_kb\.llm_factory|public_kb\.milvus_store|public_kb\.mineru_parser|public_kb\.chunker|public_kb\.text_cleaner|public_kb\.csv_loader|public_kb\.citations|public_kb\.process_csv|public_kb\.qa_chain"
```

允许命中位置只包括：

1. 历史 docs；
2. archive；
3. 明确标记 deprecated 的兼容层自身；
4. 明确保留的兼容测试。

删除后必须再次执行：

```powershell
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" -m pytest test -q --ignore=test/test_cloud_sync.py
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" scripts/poc_verify_hybrid.py
& "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" scripts/poc_validate_csv_ingestion.py
```

建议提交：

```text
refactor(public_kb): remove legacy compatibility modules
```

---

## 10. 每阶段通用验收门

| 门 | 标准 |
| --- | --- |
| 行为门 | `PublicKnowledgeRAG.query()` 返回结构不变 |
| 离线入库门 | Document metadata、chunk_uid、chunk_index 不变 |
| Milvus 门 | schema、dense index、sparse index、BM25 Function 不变 |
| 检索门 | dense、sparse、RRF、reranker、fallback 语义不变 |
| 引用门 | R1-R7 全部通过 |
| 并发协作门 | 不修改 `AgentState`、节点签名、`build_qa_chain()` 外部行为，不新增 async |
| 配置门 | 不修改生产 `.env` |
| 数据门 | 不连接生产 Milvus 集合，不执行全量入库 |

---

## 11. 回滚策略

1. 每个阶段独立本地提交。
2. 任一阶段验证失败，先回滚该阶段 commit，不在失败状态上叠加修复。
3. 阶段 1、2、3 可以分别回滚。
4. 阶段 4 只验证，不产生业务代码变更。
5. 阶段 5 CLI 合并必须先补齐 mock 测试，再改入口。
6. 阶段 6B 删除兼容层前必须全仓库搜索旧路径。

---

## 12. 最终完成标准

1. 目标目录结构落地。
2. `ingestion/transforms/` 内不再有指向包根实现的转发壳。
3. `services/` 承载 embedding、LLM、Milvus、MinerU 基础服务。
4. `generation/citations.py` 承载引用构造与校验。
5. `retrieval/reranker.py` 承载 Reranker 协议和实现。
6. CSV 单文件和批量入口统一到 `ingestion/cli.py`。
7. `pytest`、POC、CSV metadata 验证全部通过。
8. 输出一份目录收敛验证报告，包含测试结果、耗时、文件迁移清单、兼容层状态。

---

## 13. 当前建议执行顺序

```text
阶段 0：结构冻结 + layout guard 测试
阶段 1：services/
阶段 2：ingestion sources + transforms
阶段 3：generation citations + retrieval reranker
阶段 4：端到端验证
阶段 5：CSV CLI 合并
阶段 6B：等待确认后删除兼容层
```

每个阶段执行完成后暂停，等待用户确认。
