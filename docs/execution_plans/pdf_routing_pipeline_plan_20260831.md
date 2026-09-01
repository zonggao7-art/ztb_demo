# PDF 解析链路 — 多步分层路由执行计划

> 计划日期：2026-08-31
> 上游决策：
> - 方案讨论确认（2026-08-30）：**三层路由**（页面预检 → 简单页快路径 / 复杂页 MinerU），**不引入 PaddleOCR 层**（3 本 PDF 均有文本层，MinerU 自带 OCR，外挂 OCR 冗余），**不做 MinerU 二次兜底**（路由层级过多，拼接成本高）
> - 依赖已交付：M1（`pdf_structure.py` 表格原子块/目录过滤/双栏打标）、M4（清洗保护）
> - 前置专项：P-1（MinerU 接入）— 本计划 R4 依赖其产出，R1–R3 可独立开发（mock MinerU）
> 执行模型：分模块独立实现 → 独立验证 → 独立提交；R4 之前 R1–R3 不依赖 MinerU 实装
> 协作约束：不推送远端、不修改生产 `.env`、不改生产 Milvus 集合、不引入 async API（协程并发小组入口保持不动；快路径用 `ThreadPoolExecutor` 线程池属允许范围）

---

## 1. 目标与非目标

### 1.1 目标

1. 建立"页面级路由"的 PDF 解析流水线：简单页走轻量快路径（PyMuPDF 直接抽文本，多线程），复杂页走 MinerU，按页序拼接为统一 Markdown 中间态，再进入既有 `TextCleaner → PdfStructure → SemanticChunker` 链路。
2. 在保证复杂页（表格/双栏/图文混排/公式）解析质量不降的前提下，显著缩短 3 本电子书（约 1900 页）的解析耗时。
3. 路由可开关（关闭时回退 M1 的"全量 MinerU"行为），保证既有无 MinerU 环境也能跑通（快路径纯 PyMuPDF，不依赖 GPU）。

### 1.2 非目标

1. 不引入 PaddleOCR（决策见上游）。
2. 不做"OCR 兜底 → MinerU 二次兜底"的多级降级（决策见上游）。
3. 不重写 `MinerUParser` 的 subprocess 调用方式（P-1 专项范围）。
4. 不引入 async API。

---

## 2. 架构与数据流

```
PDF
 │
 ▼
[R1] PdfPageClassifier（PyMuPDF 页面画像，复用既有分析逻辑）
 │   per-page: 文本字符数 / 表格线计数 / 图片数 / 分栏 x 坐标间隙 / 页眉噪声
 │   分类: simple | complex
 ▼
[R3] PdfRouter 编排
 ├─ 简单页（纯文本、单栏、无表无图）→ [R2] FastPath 抽文本（PyMuPDF .get_text, ThreadPool 多线程）
 └─ 复杂页（表格/双栏/图文/公式）→ 聚合为**连续页范围子 PDF**（PyMuPDF select+save）
                                        → [R4] MinerU 解析该子 PDF → 每范围一段 Markdown
 ▼
[R3] 按页序拼接 → 统一 Markdown 中间态
 ▼
既有 M1 链路: TextCleaner → PdfStructure(表格原子块/目录过滤/双栏打标) → SemanticChunker → Milvus
```

**关键工程点：MinerU 只送"连续复杂页范围"的子 PDF，不送整本。**
- 简单页占多数时，MinerU 处理的页数大幅减少 → 耗时可从 2.5–5h 量级降到以复杂页为主；
- 子 PDF 分段可断点续跑（MinerU 输出按范围缓存）；
- PyMuPDF `select()` + `save()` 生成子 PDF 是毫秒级、无重渲染。

---

## 3. 必须冻结的对外契约

| 契约 | 冻结内容 |
| --- | --- |
| `PublicKnowledgeRAG.init_knowledge_base/add_pdf` | 签名与返回不变 |
| `PdfSource.load()` | 返回 `SourceResult(documents=...)` 不变；路由开关关闭时行为与 M1 完全一致 |
| `Settings` | 只允许向后兼容新增字段（本计划新增 `pdf_routing_*`，默认值 = 关闭路由回退全量 MinerU） |
| `Document.metadata` | `doc_name/chapter/chunk_index/chunk_uid` 必填；路由侧新增 `pdf_page` 等可空字段 |
| 既有 M1 链路（`TextCleaner/PdfStructure/SemanticChunker`） | 不改动，作为路由产出的下游消费者 |
| `test_public_kb_layout.py` AST 守卫 | 不引入 legacy 导入路径 |

---

## 4. 模块划分与依赖

```
R1（页面分类器）→ R2（快路径抽取）→ R3（路由编排+装配）→ R5（配置/接线/总体验证）
                                        ↑
                                   R4（MinerU 复杂页解析，依赖 P-1 产出；可用 mock 先行）
```

- **R1/R2/R3 不依赖 MinerU 实装**：R3 中 MinerU 侧用 mock（返回按页范围的占位 Markdown），先行把路由/拼接/装配跑通。
- **R4 依赖 P-1**（MinerU 接入）：接入真实 `magic-pdf`/`MinerUApiParser` 后，R3 切到真实实现并做 3 本 PDF 样张核验。

---

## 5. 模块设计（每模块含：改动点 / 契约影响 / 测试验证）

### R1 — PdfPageClassifier 页面分类器（0.5–1 人日）

**改动点**
| 文件 | 改动 |
| --- | --- |
| `public_kb/ingestion/transforms/pdf_page_classifier.py` **新增** | `PageProfile` dataclass（page_idx/text_chars/table_lines/images/two_col/score）+ `classify_page()` + `classify_document()`；复用已论证的启发式：文本层字符数、横/竖线计数（表格）、图片数、x 坐标间隙 > 页宽 15%（双栏）；输出 `simple/complex` |
| `config.py` | 新增 `pdf_routing_enabled`(默认 True)/`pdf_simple_max_table_lines`(默认 5)/`pdf_simple_max_images`(默认 1)/`pdf_simple_min_chars`(默认 100)/`pdf_two_col_gap_ratio`(默认 0.15) |

**契约影响**：无对外变化；新增 `PageProfile` 为纯数据类。

**验证（新增测试）**
- `test/test_pdf_page_classifier.py`：合成页面画像（纯文本页/含表格线页/含图页/双栏页）→ 分类正确；阈值边界（恰好 5 条线/0 图）判定正确；`pdf_routing_enabled=False` 时返回"全 complex"（触发全量 MinerU 回退）。

### R2 — FastPath 快路径文本抽取（0.5–1 人日）

**改动点**
| 文件 | 改动 |
| --- | --- |
| `public_kb/ingestion/transforms/pdf_fast_text.py` **新增** | `extract_page_text(page) -> str`（PyMuPDF `get_text("text")`）+ `extract_pages_fast(pdf_path, page_indices, max_workers=4)`（`ThreadPoolExecutor` 多线程）；纯函数、无共享状态（满足并发约束） |

**契约影响**：无对外变化。

**验证（新增测试）**
- `test/test_pdf_fast_text.py`：mock PyMuPDF 页面 → 抽取文本正确；并发结果与串行逐页结果一致（等幂）；异常页（无文本层）→ 返回空串不抛错。

### R3 — PdfRouter 路由编排 + 装配（1.5–2 人日，核心）

**改动点**
| 文件 | 改动 |
| --- | --- |
| `public_kb/ingestion/transforms/pdf_routing.py` **新增** | `PdfRouter`：输入 PDF 路径 → ①R1 分类 ②简单页索引交给 R2、复杂页聚合成连续范围 ③每范围用 PyMuPDF `select()+save()` 生成子 PDF → 交给解析回调（R4）④按页序拼接为统一 Markdown（页间用 `<!-- page: N -->` 标记，供下游定位） |
| `public_kb/ingestion/sources/pdf_source.py` | 可选：`load()` 在 `enable_pdf_routing` 时走 `PdfRouter`，否则走既有 M1 全量路径 |
| 新增 `pdf_parser_protocol.py`（或复用 P-1 的 `MinerUApiParser` 接口） | 定义"输入子 PDF → 输出 Markdown"的解析回调协议，便于 mock 与真实 MinerU 切换 |

**契约影响**：`PdfSource.load()` 行为在开关开启时变化（产出经路由），返回结构不变；开关关闭时与 M1 完全一致。

**验证（新增测试）**
- `test/test_pdf_routing.py`：mock 解析回调（简单页走 fast、复杂页走 mock MinerU）→ 断言①每页产物按页序拼接、页号不丢不重 ②复杂页范围子 PDF 生成正确（页索引与子 PDF 内容对应）③全简单页 → 不触发 MinerU（回调调用 0 次）④全复杂页 → 单范围一次 MinerU ⑤路由关闭 → 回退全量路径（回调收到整本）。

### R4 — MinerU 复杂页解析（依赖 P-1，1 人日）

**改动点**
| 文件 | 改动 |
| --- | --- |
| 复用 P-1 的 `MinerUApiParser`（HTTP 服务）或 `MinerUParser`（本地 CLI） | 适配 `PdfRouter` 的解析回调协议：输入子 PDF 路径 → 返回该范围的 Markdown 文本 |
| 子 PDF 解析结果缓存 | 按子 PDF 内容哈希缓存到 `DATA/raw_data/_pdf_router_cache/`（断点续跑） |

**契约影响**：无对外变化；依赖 P-1 交付。

**验证**
- 接真实 MinerU 后，对 3 本 PDF 各抽 20-30 页跑 `PdfRouter`，人工核验：简单页快路径文本正确、复杂页 MinerU 表格/双栏达标、拼接后页序完整（L2 样张）。

### R5 — 配置接线 + 总体验证（0.5–1 人日）

**改动点**
| 文件 | 改动 |
| --- | --- |
| `config.py` | 汇总 R1 新增开关；`pdf_fast_path_max_workers`(默认 4) |
| `rag_engine.py` / `pdf_source.py` | 确保 `enable_pdf_routing` 接线完整；关闭时回退 M1 行为 |
| 文档 | `docs/execution_plans/` 更新本计划状态 |

**验证**
- 全量 `pytest test -q --ignore=test/test_cloud_sync.py` 全绿；
- 3 本 PDF 各抽 20-30 页 L2 样张核验（R4 完成后）；
- 若可行：小批量全流程（路由 → 入库实验集合）走通 V-3 同款端到端。

---

## 6. 性能预期

| 路径 | 单页耗时 | 说明 |
| --- | --- | --- |
| 快路径（简单页） | ~5–20ms/页 | PyMuPDF 直接抽文本，多线程 |
| MinerU（复杂页） | ~5–20s/页 | GPU 串行 |
| **3 本 PDF（约 1900 页）** | 复杂页占比 70–80%（book2 双栏多） | 路由后约 **30–90 分钟**（vs 全量 MinerU 2.5–5h） |

> 说明：简单页占比取决于分类阈值；book2 双栏率 89%，故其复杂页占比高，加速主要体现在 book1/book3。若后续发现快路径误分类劣化质量，可收紧阈值（简单页判定从严）。

---

## 7. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 快路径误分类：双栏页被当简单页 → 双栏错乱 | 双栏检测（x 间隙）作为强制 complex 条件，不参与阈值放宽；L2 样张抽检 |
| 简单页直接抽文本丢表格/读序 | 判定从严：有表格线/图/多栏一律 complex；只有纯文本单栏才走快路径 |
| 子 PDF 边界语义割裂（一条目跨简单/复杂页） | 页边界拼接后由下游 `PdfStructure`/`SemanticChunker` 处理；`<!-- page -->` 标记便于人工核对 |
| 路由增加复杂度 | 开关默认开、关闭回退全量 MinerU；R1–R3 可 mock 先行独立验证 |

---

## 8. 里程碑

| 里程碑 | 内容 | 验证 |
| --- | --- | --- |
| R1 | 页面分类器 | `test_pdf_page_classifier.py` 全绿 |
| R2 | 快路径抽取 | `test_pdf_fast_text.py` 全绿 |
| R3 | 路由编排+装配（mock MinerU） | `test_pdf_routing.py` 全绿 |
| R4 | MinerU 真实接入（依赖 P-1） | 3 本 PDF 各 20-30 页 L2 样张核验 |
| R5 | 接线+全量回归 | 全量 `pytest` 全绿 |

每里程碑独立提交、可回退；R4 之前 R1–R3 不阻塞推进。

---

*本计划为文档产出，未改动任何代码。执行顺序建议：R1→R2→R3（mock）→ 等待 P-1 后 R4 → R5。*
