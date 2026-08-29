# 双链路工程化审查报告——离线入库 Pipeline 与在线检索 Pipeline

> 审查日期: 2026-08-29
> 审查范围: public_kb/ 全部 11 个模块 + process_csv.py 编排器（约 2300 行）
> 方法: 全文通读 + 调用图梳理 + 与既有测试/POC 证据交叉验证
> 结论先行: 两组件质量分层明显——原子组件（chunker/cleaner/parser）良好；
>           但两条链路均缺 pipeline 抽象，在线侧 qa_chain.py 599 行承载 7 类职责需要拆分

---

## 一、离线数据向量化入库链路审查

### 1.1 现状调用图（实际存在两条互不共享的路径）

```
路径 A (PDF 源, rag_engine 内私有编排):
  init_knowledge_base(pdf_dir)
    └─ _process_single_pdf()            ← 编排藏在私有方法
         ├─ MinerUParser.parse          (subprocess, 有缓存, 良好)
         ├─ TextCleaner.clean           (纯函数, 良好)
         ├─ SemanticChunker.chunk       (纯函数, 良好)
         └─ MilvusStoreManager.initialize_collection
              (schema+BM25 Function+契约 fail-fast+嵌入+批量插入+flush+load)

路径 B (CSV 源, process_csv.py CLI 编排, 325 行):
  main() → scan_csv_files 分组(A/C)
         → CsvLoader.load_file      (CSV解析+行标准化+清洗+中文标题转MD+切片)
         → save_chunks_to_markdown  (中间产物 DATA/raw_data/*_chunks.md)
         → rag 入库 → validate_markdown_output
```

### 1.2 问题清单（按严重度）

#### P0-1 中间产物丢失行级元数据 —— 已被 POC 证实

`save_chunks_to_markdown` 生成的 `*_chunks.md` 是 CSV→入库的关键交接物，
但其格式为**展示**设计（含"Content 预览"等段落），保留的元数据仅有
`doc_name/chapter/chunk_index`。CSV 行级的 `title/publish_date/source_url`
在 md 落盘时丢失 → 从 md 回灌入库的文档**无法按政策名称/发布时间溯源**
（POC 50 条已实证该现象）。

**结论: md 中间产物不可作为正式全量入库源**。正式入库必须
`CsvLoader → (内存 Documents) → Milvus` 直通, 或为 md 定义带
front-matter 元数据的正式 schema。

#### P0-2 编排器分裂, 同构流程三处实现

- 路径 A 编排: `rag_engine._process_single_pdf`（私有）
- 路径 B 编排: `process_csv.py`（根级 CLI, 混合 IO/统计/报告/流程）
- `CsvLoader.__init__` 又自行组装 cleaner+chunker（第三处组合点）

后果: 新增数据源（如 docx、数据库导出）或调整步骤（如跳过清洗）需改多处;
无统一的阶段级统计/耗时/断点。

#### P1-1 rag_engine 门面职责过载

门面 + PDF 编排 + LLM 装配 + QA 链装配 + 生命周期五合一;
`_create_llm()` 是对 `llm_factory.create_llm` 的无信息转发。

#### P1-2 CsvLoader 双职责

441 行中约 1/3 是 `save_chunks_to_markdown` 的 Markdown 报告排版——
展示逻辑混入数据加载层, 且它是模块级函数（不属于类）, 测试需连带 import。

#### P2 组件层无问题（明确不动）

`chunker.py / text_cleaner.py / mineru_parser.py` 单一职责、纯输入输出、
无全局状态、docstring 完整——**解耦时原样搬移即可, 禁止顺手重写**。

### 1.3 目标形态: 显式 Ingestion Pipeline

```
public_kb/ingestion/
    pipeline.py        # IngestionPipeline: stages=[] 顺序执行
                       #   每阶段产出 StageResult(rows/chunks/耗时/skip)
                       #   失败策略: fail-fast | skip-record（按源文件粒度）
    sources/           # 统一接口 Source.load() -> list[Document]
        pdf_source.py      # 包 MinerUParser（含缓存逻辑）
        csv_source.py      # 包 CsvLoader 的解析/标准化部分
        markdown_source.py # 读中间 md（仅回灌/POC 场景, 文档标注元数据缺失风险）
    sinks/
        milvus_sink.py     # 现 milvus_store 平移（schema/契约校验/入库）
        markdown_sink.py   # save_chunks_to_markdown 独立为可选中间产物 sink
    transforms/            # cleaner / chunker 原样搬移
rag_engine.py          # 瘦身: 仅对外 API + pipeline 装配, ~100 行
```

复用既有契约: 入库前 `contracts.validate_ingestion_documents` 已存在,
天然成为 pipeline 的必经校验 stage。

---

## 二、在线检索链路审查

### 2.1 现状: qa_chain.py 599 行, 10 个职责块

| 行区间 | 职责 | 归属目标模块 |
|--------|------|--------------|
| 41-72 | Prompt 模板 + 内联引用指令 | 生成层 (prompts.py) |
| 73-119 | _format_docs / _build_sources 上下文与来源格式化 | 生成层 (context.py) |
| 120-201 | 命中实体归一化 / _entity_to_doc 溯源映射 | 检索层 (search_ops) |
| 202-262 | search / hybrid_search + output_fields 回退 | 检索层 (search_ops) |
| 267-475 | build_qa_chain: _retrieve 编排闭包(约200行) + _decide_and_answer | 检索层 HybridRetriever 类 |
| 548-613 | _SiliconFlowReranker HTTP 客户端 | reranker/ 包 |
| 615-628 | _adaptive_threshold 档位策略 | 检索层 (strategies) |
| 630-599 | _dense_only_retrieve 降级 | 检索层 (fallback) |

### 2.2 问题清单

#### P0-1 修改放大与测试粒度粗

改 Reranker 超时策略需通读全文件; 现有测试只能整链装配+Fake 注入,
无法单测"降级 score 语义""schema 缓存失效"等中粒度逻辑
（离线测试 6 个 qa_chain 用例全部走 build_qa_chain 全链）。

#### P1-1 闭包承载核心业务(约 200 行)

`_retrieve` 为嵌套闭包, 依赖 nonlocal（`_has_sparse_cache`/`_reranker`）。
功能正确（POC 8/8 已证）, 但逻辑不可单独实例化/复用
（如 price_inquiry 想复用混合检索需复制代码）。

#### P1-2 双数据访问轨并存

`build_qa_chain` 同时接收 langchain `vector_store`（仅旧 schema 方案B用）
与原生 `collection`(MilvusClient)。两套访问路径长期并存必然漂移
（score 语义、字段名、超时行为各自演化）。

#### P1-3 策略硬编码

`_adaptive_threshold` 档位(0.75/0.50→0.40/0.45/0.50)写死;
新参数 `bm25_analyzer_type` 等已配置化, 阈值档位仍散落代码。

#### P2-1 Reranker 无重试

429/瞬时网络错误一次即降级 RRF 排序——检索质量静默下滑
（有 `last_status` 诊断可观测, 但无恢复手段）。嵌入服务已有重试模式可对齐。

#### P2-2 生成层混入检索文件

prompt/引用格式化与 Milvus 无关, 换 LLM/改提示词不应触碰检索模块。

### 2.3 目标形态: 检索与生成分离的 pipeline

```
public_kb/retrieval/
    retriever.py       # class HybridRetriever:
                       #   __init__(client, embeddings, settings, reranker)
                       #   retrieve(question, dense_vec=None)
                       #     -> RetrievalResult(docs, diagnostics)   # contracts 复用
    search_ops.py      # 实体归一化 / full_fields 搜索 helper（平移）
    strategies.py      # AdaptiveThreshold(settings 可配档位)
    fallback.py        # DenseFallback（score 语义统一在此收口）
    reranker/
        protocol.py    # Reranker 协议 + Status（从 contracts 已有 Status 复用）
        siliconflow.py # HTTP 实现（http_client 注入保留 + 重试）
prompts.py             # prompt/内联引用指令
context.py             # _format_docs / _build_sources
qa_chain.py            # 瘦身 ~150 行: LCEL 装配 + _decide_and_answer
```

收益: 检索可被 price_inquiry 等复用; reranker 协议化后 POC 手工注入假
client 的手法成为一等公民; 生成层改动零触碰检索模块。

---

## 三、实施路线（行为不变原则）

回归锚: 离线 37 测试 + POC 8/8 验证脚本, 每期结束必须全绿。

| 期 | 内容 | 规模 | 触发条件 |
|----|------|------|----------|
| 第 1 期 | 在线检索拆分: retrieval/ 包 + qa_chain 瘦身 | ~2 天 | 立即（生产查询路径, 风险最高收益最大） |
| 第 2 期 | ingestion/ pipeline 抽象 + rag_engine/csv_loader 瘦身 | ~2 天 | 全量数据入库之前（否则 P0-1 元数据丢失随全量固化） |
| 第 3 期 | 策略配置化(阈值档位) + Reranker 重试 | ~0.5 天 | 随第 1 期顺带 |

明确不做（避免过度工程）:
- 不引入抽象基类继承树（用 Protocol/回调即可）;
- 不重写 chunker/cleaner/parser 内部实现;
- 不把 langchain wrapper 访问轨立刻删除（保留至旧 2.4 schema 退役）。

## 四、与全量入库的关系（决策提示）

若近期执行全量向量化入库, **必须先做第 2 期或至少绕开 md 中间产物**
（CSV → 内存 Documents → Milvus 直通）, 否则 P0-1 的元数据丢失会随
全量数据固化, 后续修复需要二次全量重建。
