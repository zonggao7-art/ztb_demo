# PDF 法规书三档路由执行计划

> 计划日期：2026-08-31  
> 文档性质：独立执行计划，不修改 `pdf_routing_pipeline_plan_20260831.md`。  
> 关系说明：本计划吸收原两层路由方案与《企业级 PDF 高性能多级解析与 Markdown 重构技术方案》中可取的部分，但以当前 3 本法规电子书的真实语料特征为准。若进入实现阶段，以本文件作为最新执行基线。

## 1. 结论与定位

### 1.1 核心判断

这批 PDF 的主体是法规条文和解释性文字，其中一本书为双栏排版，但双栏内容仍以线性条文为主；表格和图片只是局部现象。因此不宜把“双栏”一刀切为复杂页，也不宜把 MinerU 当成所有复杂页的唯一入口。

本计划采用三档路由：

| 层级 | 页面类型 | 处理器 | 定位 |
| --- | --- | --- | --- |
| Tier A | 单栏纯文本页、双栏条文页 | PyMuPDF 坐标感知快路径 | 处理主体条文页 |
| Tier B | 规整有框表格、图文关系清楚的局部页 | pdfplumber / PyMuPDF Table + 校验 | CPU 处理可结构化表格 |
| Tier C | 无框表、嵌套表、跨页复杂表、公式、扫描件、低置信度页 | MinerU | 深度版面解析兜底 |

### 1.2 与原计划的主要差异

| 原计划 | 本计划 |
| --- | --- |
| simple / complex 二分 | text / table / complex 三档 |
| 双栏页强制 complex | 双栏条文页优先走坐标感知快路径 |
| 快路径直接抽取纯文本 | 快路径抽取 block、坐标、字体，重建标题与段落 |
| 表格页全部 MinerU | 规整表格先走 CPU 表格抽取，失败才升级 MinerU |
| 复杂页只按连续范围聚合 | 连续复杂页聚合，并在疑似跨页表时扩展边界页 |
| 人工抽检 20-30 页 | 增加法规专项 golden set、路由报告和检索回归 |

## 2. 目标与非目标

### 2.1 目标

1. 保持 `PublicKnowledgeRAG.init_knowledge_base/add_pdf`、`PdfSource.load()`、`SourceResult`、`Document.metadata` 必填字段和下游 `TextCleaner / PdfStructure / SemanticChunker / Milvus` 契约不变。
2. 新增可开关的三档路由，默认关闭，关闭时完全回退现有全量 MinerU / M1 行为。
3. 让单栏与双栏条文页尽量不依赖 GPU，保留章节、条款编号和段落阅读顺序。
4. 让规整表格优先由 CPU 提取，减少 MinerU 调用量。
5. 为每页留下路由理由、置信度和解析器来源，便于后续质量回查。

### 2.2 非目标

1. 不引入 PaddleOCR 作为独立解析层。
2. 不在本计划中重构在线 `doc_qa`。
3. 不引入 async API，编排保持同步；快路径允许使用 `ThreadPoolExecutor`。
4. 不承诺所有页面在无 MinerU 环境下都能完整解析。若存在 Tier C 页且 MinerU 不可用，默认 fail-fast，只有显式允许部分成功时才跳过 Tier C 页。
5. 不把 MinerU 的 Markdown 直接当作唯一事实源；输出需进入统一校验和装配层。

## 3. 总体链路

```text
PDF
 |
 v
T1. PdfPageProfile + LegalPageClassifier
 |     - 文本层、block 坐标、栏间隙、字体统计
 |     - 表格候选、图片面积、公式/扫描特征
 |     - 输出: text | two_col_text | table_regular | table_complex | visual_or_scan | uncertain
 |
 +-- text / two_col_text ------------> T2A. LegalFastTextExtractor
 |                                      - get_text("dict") / blocks
 |                                      - 双栏列序重建
 |                                      - 标题/条款/段落结构化
 |
 +-- table_regular ------------------> T2B. RegularTableExtractor
 |                                      - pdfplumber / PyMuPDF Table
 |                                      - Markdown 表格 + 校验
 |                                      - 校验失败升级 Tier C
 |
 +-- table_complex / visual_or_scan /
 +-- uncertain / Tier A-B 校验失败 --> T3. MinerUComplexPageParser
                                        - 聚合连续复杂页
                                        - 疑似跨页表扩展边界页
                                        - 子 PDF + 内容哈希缓存
 |
 v
T4. PageMarkdownAssembler
 |     - 按页序缝合
 |     - 标题级别规范化
 |     - 去重页眉页脚
 |     - 保留 page/route/parser 元数据
 v
TextCleaner -> PdfStructure -> SemanticChunker -> Milvus
```

## 4. 对外部方案的采纳与修正

### 4.1 采纳

1. **三级轻量化分流**：把表格从 MinerU 中拆出来，先尝试 CPU 表格抽取。
2. **双栏不再等于复杂**：双栏条文页可基于 block 坐标重建阅读顺序。
3. **坐标感知抽取**：不使用 `get_text("text")` 作为最终产物，而是使用 block / span 信息。
4. **表格置信度与自动升级**：表格抽取后做行列、空单元格、错位、文本连续性校验，失败则升级 MinerU。
5. **Markdown 装配层独立化**：标题对齐、页序缝合、页眉页脚清理不应散落在解析器里。

### 4.2 修正

1. 外部方案用固定 `0.49W / 0.51W` 切分左右栏，过度依赖页宽比例。本计划改为从 block 坐标统计栏间隙，并结合跨栏标题、跨栏表格、块交错置信度判断。
2. 外部方案把“无框表 / 嵌套表”简单归入 Tier C，但没有处理跨页表边界。本计划增加边界页扩展与按页去重。
3. 外部方案没有定义 MinerU 不可用时的行为。本计划明确：Tier C 默认 fail-fast，避免静默丢内容。
4. 外部方案缺少法规书质量指标。本计划增加条款编号、章节标题、跨页表格和检索效果验收。
5. 外部方案的耗时估计偏理想化。本计划不在实现前承诺固定耗时，先以页面画像统计和样张实测为准。

## 5. 数据契约

### 5.1 保留不变

| 契约 | 要求 |
| --- | --- |
| `PublicKnowledgeRAG.init_knowledge_base/add_pdf` | 签名、返回结构和调用方式不变 |
| `PdfSource.load()` | 仍返回 `SourceResult(documents=...)` |
| `Document.metadata` 必填字段 | `doc_name`、`chapter`、`chunk_index`、`chunk_uid` 不变 |
| 下游结构链路 | `TextCleaner -> PdfStructure -> SemanticChunker` 不做破坏性重写 |
| `Settings` | 只允许向后兼容新增字段 |

### 5.2 新增中间结构

新增页面级中间结构，不直接进入对外 API：

```python
@dataclass(frozen=True)
class PageRouteDecision:
    page_idx: int
    page_label: str
    tier: str              # A / B / C
    reason: str
    confidence: float
    parser: str            # fast_text / table_extractor / mineru
    features: dict[str, float]


@dataclass(frozen=True)
class ParsedPage:
    page_idx: int
    markdown: str
    parser: str
    route: PageRouteDecision
    warnings: tuple[str, ...] = ()
```

`Document.metadata` 可选新增：

- `pdf_page: int`
- `pdf_route: str`
- `pdf_parser: str`

这些字段只能作为增强信息，不能成为下游必填依赖。

## 6. 模块设计

### T1 — 页面画像与三档分类

**新增文件**

```text
public_kb/ingestion/transforms/pdf_page_profile.py
public_kb/ingestion/transforms/pdf_legal_page_classifier.py
```

**特征**

| 特征 | 来源 | 用途 |
| --- | --- | --- |
| 文本字符数 | `page.get_text("text")` | 判断无文本层 / 扫描页 |
| block 数量与坐标 | `page.get_text("blocks")` | 判断单栏、双栏、跨栏标题 |
| 栏间隙宽度 | block x 坐标分布 | 识别双栏条文页 |
| 跨栏标题数 | block 宽度和位置 | 避免简单按左右栏切开 |
| 字号分布 | `page.get_text("dict")` | 推断章节标题 |
| 图片数量与面积 | `page.get_images()` / image bbox | 识别图片密集页 |
| 表格线 / 表格候选 | PyMuPDF 表格探测 | 区分普通页与表格页 |
| 公式字符 / 字体 | span 字体与符号统计 | 公式页进 Tier C |
| 条文号密度 | `第X章 / 第X条 / 一、 / （一）` | 法规文本结构校验 |

**分类输出**

```text
text            单栏文本页
two_col_text    双栏文本页，但列序置信度足够
table_regular   有明显表格结构，且可能是规整有框表
table_complex   无框表、嵌套表、跨页表候选、图片密集表格
visual_or_scan  无文本层、图片占比过高、OCR 依赖强
uncertain       特征冲突或置信度低
```

**原则**

1. `uncertain` 默认进入 Tier C。
2. 双栏只有在列序置信度达标时才进入 Tier A。
3. 只要存在图片密集、公式、扫描、低文本层特征，不进入 Tier A。
4. 分类结果必须写 manifest，不要求下游强制消费。

### T2A — 法规书结构化快路径

**新增文件**

```text
public_kb/ingestion/transforms/pdf_fast_text.py
public_kb/ingestion/transforms/pdf_two_column_reflow.py
```

**职责**

1. 使用 `get_text("dict")` 或 `get_text("blocks")` 获取 block / line / span。
2. 先剔除页眉、页脚、独立页码；剔除规则需要结合重复频次和页面位置。
3. 对单栏页按 block 坐标排序。
4. 对双栏页：
   - 统计 block x 坐标分布，识别主栏间隙；
   - 判断是否有跨栏标题、跨栏表格、通栏插图；
   - 先输出左栏，再输出右栏；
   - 对被栏边界切断的条款块做连续性校验。
5. 使用字体大小、加粗状态、居中程度和法规标题模式生成 Markdown 标题：
   - `第一章`、`第一节`
   - `第一条`、`第二条`
   - `一、`、`（一）`
   - 1200 问类书籍的问题/答案体例
6. 输出轻量 Markdown，而不是 plain text。

**测试**

- 单栏 block 排序稳定。
- 双栏左栏 -> 右栏顺序正确。
- 跨栏标题不参与左右栏重复输出。
- 页眉页脚不进入正文。
- 章节标题、条款编号进入 Markdown。
- 低置信度双栏页返回 `uncertain`，升级 Tier C。
- 每个线程独立打开 PDF 文档，不跨线程共享 `Document` / `Page`。

### T2B — 规整表格抽取

**新增文件**

```text
public_kb/ingestion/transforms/pdf_table_extractor.py
public_kb/ingestion/transforms/pdf_table_validator.py
```

**职责**

1. 对 `table_regular` 页使用 pdfplumber 或 PyMuPDF Table 抽取表格。
2. 表格转换为 Markdown 表格，并在表格上方保留附近标题或说明文字。
3. 表格块作为原子块进入下游，避免 `SemanticChunker` 拆碎表格。
4. 抽取后校验：
   - 行数 / 列数是否与检测区域匹配；
   - 首行表头是否完整；
   - 空单元格比例是否异常；
   - 数字、日期、金额列是否保持格式；
   - 单元格文本是否被异常截断；
   - 相邻行列是否存在明显错位。
5. 校验失败时把该页升级为 Tier C，并在 manifest 中记录原因。

**测试**

- 标准 2 列、3 列、4 列表格转 Markdown 正确。
- 表头缺失 / 空单元格密度过高 / 列数漂移时校验失败。
- 表格上下文说明文字不丢失。
- 校验失败页面能进入 MinerU 队列。

### T3 — MinerU 深度解析

**复用与新增**

```text
public_kb/ingestion/transforms/pdf_complex_range.py
public_kb/ingestion/transforms/pdf_mineru_router.py
```

**职责**

1. 将 `table_complex`、`visual_or_scan`、`uncertain`、Tier A/B 校验失败页聚合成连续范围。
2. 对疑似跨页表、跨页条文图、跨页说明的区域，向前后扩展最多 1 页边界页。
3. 扩展后的范围整体送 MinerU；范围内页统一使用 MinerU 产物，避免同一页同时出现快路径和 MinerU 内容。
4. 使用 PyMuPDF `select() + save()` 生成子 PDF。
5. 缓存 key 必须包含：
   - 源 PDF 内容哈希；
   - 页范围；
   - MinerU 解析器类型；
   - MinerU / 模型版本；
   - 关键解析参数。
6. 输出按页切分困难时，允许返回范围级 Markdown，但必须记录范围页号和装配边界。

**失败语义**

- MinerU 未安装或服务不可达：若文档包含 Tier C 页，默认抛出明确异常。
- 若 `pdf_tiered_allow_partial = true`：跳过 Tier C 页并生成 warning，但不得静默丢弃。
- MinerU 输出为空、页数异常或明显缺章节：记录失败并支持人工重跑，不自动把低质量结果入库。

### T4 — Markdown 装配

**新增文件**

```text
public_kb/ingestion/transforms/pdf_markdown_assembler.py
```

**职责**

1. 按原始页序缝合 Tier A/B/C 产物。
2. 保留页级标记 `<!-- page: N -->`，但该标记只用于排查和溯源，不承诺进入 chunk metadata。
3. 规范化标题层级：
   - 书名 / 部 / 编 -> `#`
   - 章 -> `#` 或 `##`
   - 节 -> `##` 或 `###`
   - 条 -> `###`
   - 具体层级由样张统计后确定，避免全文档标题层级漂移。
4. 去重重复页眉、页脚、目录点线行。
5. 每个文档输出 manifest：
   - 页面分类结果；
   - 每页使用的解析器；
   - 置信度与触发原因；
   - 警告信息；
   - 缓存路径；
   - 解析器版本；
   - 耗时统计。

## 7. 配置

新增配置统一放在 `Settings`，默认关闭：

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `pdf_tiered_routing_enabled` | `false` | 总开关；关闭时保持现有 M1 行为 |
| `pdf_tiered_two_col_confidence` | `0.80` | 双栏页进入快路径的最低置信度 |
| `pdf_tiered_min_text_chars` | `80` | 低于该值进入 Tier C |
| `pdf_tiered_table_min_rows` | `2` | 表格候选最低行数 |
| `pdf_tiered_table_max_empty_ratio` | `0.30` | 表格空单元格比例上限 |
| `pdf_tiered_image_area_ratio` | `0.35` | 图片面积占比超过该值进入 Tier C |
| `pdf_tiered_expand_boundary_pages` | `1` | 疑似跨页结构时扩展的边界页数 |
| `pdf_tiered_fast_max_workers` | `4` | 快路径线程数 |
| `pdf_tiered_allow_partial` | `false` | 是否允许 MinerU 不可用时跳过 Tier C |
| `pdf_tiered_manifest_dir` | `DATA/raw_data/_pdf_tiered_manifest` | 路由与质量报告目录 |

依赖引入约束：

1. `pymupdf`、`pdfplumber` 只作为离线 ingestion 依赖引入，不在在线问答链路强依赖。
2. 引入前确认 PyMuPDF 许可形态对当前产品的分发方式是否可接受；若面向外部闭源分发，需要评估商业授权或替换实现。
3. 依赖应在实现分支单独更新并说明版本。

## 8. 验收方案

### 8.1 页面级 golden set

从 3 本 PDF 中选取 120-200 页，覆盖：

| 类型 | 最低样本量 |
| --- | --- |
| 单栏法规条文 | 40 页 |
| 双栏法规条文 | 40 页 |
| 跨页条文 / 跨页段落 | 10 页 |
| 有框表 | 15 页 |
| 无框 / 复杂表 | 5 页 |
| 图文混排 | 10 页 |
| 目录 / 页眉页脚密集页 | 10 页 |

每页记录期望结果：

1. 章节标题是否存在；
2. 条款编号是否连续；
3. 表格行列是否完整；
4. 页眉页脚是否清除；
5. 阅读顺序是否正确；
6. 应该路由到哪一档。

### 8.2 页面质量指标

| 指标 | 初始目标 |
| --- | --- |
| 章节标题保留率 | >= 98% |
| 条款编号连续率 | >= 98% |
| 页眉页脚误入正文比例 | <= 1% |
| 规整表格完整率 | >= 95% |
| 疑难页路由到 MinerU 的召回率 | >= 98% |
| 简单页误入 MinerU 比例 | <= 10%，以性能换质量的收紧策略可接受 |

### 8.3 路由质量指标

输出路由混淆矩阵：

```text
actual \\ predicted: text | two_col_text | table_regular | table_complex | visual_or_scan | uncertain
```

重点观察：

1. `two_col_text -> table_complex`：说明双栏误判严重；
2. `table_regular -> text`：会造成表格丢失，属于高风险；
3. `uncertain` 比例：持续偏高时应调整阈值，而不是放宽校验；
4. 每本书的 Tier 分布和耗时。

### 8.4 端到端回归

1. 使用现有知识库测试题跑检索和引用评测。
2. 对比三个结果：
   - 现有全量 MinerU；
   - 原两层路由 mock/实测结果；
   - 三档路由结果。
3. 至少确认：
   - 答案准确率不低于现有基线；
   - 引用 chunk 可定位；
   - 章节元数据不劣化；
   - 表格相关问题不退步；
   - 双栏条文页的阅读顺序不劣化。

### 8.5 发布门槛

满足以下条件才允许默认开启：

1. golden set 指标达标；
2. 3 本书各完成一次端到端样张核验；
3. manifest 可解释每个页面的路由来源；
4. MinerU 不可用时的 fail-fast 行为有测试覆盖；
5. 关闭开关后的行为与当前 M1 全量链路一致。

## 9. 里程碑

| 里程碑 | 内容 | 验证 |
| --- | --- | --- |
| L0 | 3 本 PDF 页面画像采样 | 输出每本书的 Tier 分布、双栏置信度和表格候选统计 |
| L1 | 页面分类器 | 分类器单测 + 120-200 页 golden set 初始混淆矩阵 |
| L2 | 结构化快路径 | 单栏 / 双栏 / 跨栏标题 / 法规标题样张通过 |
| L3 | 规整表格抽取 | 有框表格 Markdown 和校验测试通过 |
| L4 | 复杂页聚合与 MinerU 接入 | 连续复杂页子 PDF、缓存、fail-fast 测试通过 |
| L5 | Markdown 装配与 manifest | 页序、标题、去重、警告和路由报告通过 |
| L6 | 配置接线与端到端 | 开关关闭回退旧链路；开关开启后检索 / 引用回归不劣化 |

L1-L3 可以先使用 mock 页面对象开发；L4 依赖 MinerU 实装；L6 前必须有真实样张验收。

## 10. 风险与缓解

| 风险 | 缓解 |
| --- | --- |
| 双栏法规条文被左右栏简单切开，导致条款断裂 | 不只按固定中线切分；使用栏间隙、跨栏标题、条款连续性三重校验 |
| 表格被误判为普通文本 | 表格候选页不进入快路径；无框表和低置信度页直接 Tier C |
| 跨页表边界丢失 | 连续复杂页范围扩展边界页，并由装配层避免重复拼接 |
| MinerU 输出不稳定 | 缓存 key 带 parser/model 版本；输出为空或页数异常不入库 |
| PyMuPDF 多线程竞争 | 每个 worker 独立打开 PDF，不共享 `Document` / `Page` |
| 新旧开关同时存在造成行为漂移 | 新总开关默认关闭；关闭时不得执行任何三档路由逻辑 |
| 质量指标被性能压力挤掉 | 先跑 golden set 和混淆矩阵，再谈性能；高风险页宁可进 Tier C |

## 11. 执行建议

1. 先做 L0 页面画像采样，确认三本书的真实 Tier 分布，不预先设定加速倍数。
2. L1 分类器先保证“高风险页不漏进快路径”，宁可多送 MinerU，也不要让表格页误入纯文本路径。
3. L2 快路径优先服务单栏和双栏条文页，这是这批语料的主要收益来源。
4. L3 表格层先支持规整有框表；无框表和嵌套表暂不强行 CPU 解析。
5. L4 之前先固定 MinerU 协议与失败语义。
6. L6 的默认开关只在验收通过后调整，当前保持 `false`。

---

## 附录：执行状态（2026-08-31）

### L0 — 页面画像采样 ✅ 完成
- 脚本：`scripts/pdf_l0_profile.py`；产物 `DATA/raw_data/_pdf_tiered_manifest/l0_profile.json`。
- 结论：
  - book1(774页)：text 93.4% / 双栏 6.3% / 扫描 0.3%
  - book2(574页)：**双栏条文页 98.1%** / 扫描 0.9%
  - book3(552页)：text 79.3% / 双栏 16.5% / 扫描+低文本 4.2%
  - **表格候选页全量为 0**（三本书均无有框表格）→ 表格基本落 Tier C；**Tier B 降级为可选/后置**，先打通 Tier A 双栏快路径 + Tier C MinerU。
  - 修复：全页背景图（水印/OCR 底图）不计入图片占比，避免 book2 被整体误判为扫描页。

### L1 — 页面分类器 ✅ 完成（11 单测 + 全量 295 passed）
- 新增：
  - `public_kb/ingestion/transforms/pdf_page_profile.py`（PageProfile + build_page_profile，纯特征，鸭子类型页面对象，不强依赖 pymupdf）
  - `public_kb/ingestion/transforms/pdf_legal_page_classifier.py`（LegalPageClassifier + PageRouteDecision，A/B/C 三档 + parser/reason/confidence）
  - `test/test_pdf_legal_page_classifier.py`（11 用例：单栏→A、双栏置信度达标→A、失衡→C、规整表→B、复杂表→C、扫描/图片/公式→C、阈值边界）
- 分类原则已落地：uncertain 一律 C；双栏列序置信度达标才 A；图片密集/公式/低文本不进 A。
- 已知待校准（记入 L2）：实页 smoke 显示 book3 第 8/9 页双栏被判 `uncertain→C`（左右栏块失衡），当前偏保守，宁可多送 MinerU——这正是计划 §11「高风险页不漏进快路径」的预期行为，L2 快路径打磨时用 golden set 校准置信度。


### L2 — 结构化快路径 ✅ 完成（9 单测 + 全量 304 passed）
- 新增：
  - `public_kb/ingestion/transforms/pdf_fast_text.py`（TextLine + iter_lines / remove_page_numbers / remove_header_footer / sort_single_column / generate_markdown / extract_page_markdown）
  - `public_kb/ingestion/transforms/pdf_two_column_reflow.py`（is_full_width_line / reflow_two_columns / reflow_page_markdown）
  - `test/test_pdf_fast_text.py`（9 用例：单栏排序 / 页码剔除 / 页眉页脚剔除+法律标题保留 / 标题层级生成 / 双栏左→右重建 / 跨栏标题只输出一次 / 通栏判定阈值）
- 关键行为已落地：
  - 双栏切分用 `PageProfile.two_col_split_x`（栏间隙中心），非固定 0.49/0.51 页宽比例（对齐计划 §4.2 修正 1）；
  - 跨栏通栏行（行宽/页宽 ≥0.7）优先于左右栏整体输出一次；
  - 页眉页脚按位置剔除，但法律标题（第X章/条）即使位于页眉区也保留（避免误删章节）；
  - 标题层级由字号+居中+法规标题模式共同判定（编章→##、节→###、条→####、一、/（一）/数字项→列表级）。
- 实页 smoke（book2 第20页双栏）：左→右列序重建正确，条文号正确标为 #### 标题；右栏 OCR 原文本自身存在跨页断句（上游 OCR 质量问题，非重建引入）。
- 已知待校准：L1 的双栏置信度偏保守（book3 第8/9页被判 uncertain→C），留待 L5/L6 golden set 校准阈值。


---

## 附录 B：T4 装配层 + T2B 表格抽取/校验 正式任务卡（2026-08-31 补）

> 说明：本附录把 §6 的 T4、T2B 从"设计描述"细化为"编号任务卡"，并补充表格位置标签与图文表混排还原的落地契约。表格处理走「简单表本地 → 校验不合格/复杂表 → MinerU」两级判定。

### B.1 表格与混排还原的核心契约（先定，后实现）

| 契约 | 定义 |
| --- | --- |
| `PageBlock` | 页内原子块：`order_key`(块顶 y 坐标) / `kind`(text\|table\|heading) / `content`(文本或 Markdown 表格) / `bbox`(溯源) |
| 页级主键 | `page_idx`（0 基）；装配层用 `OrderedDict[page_idx → markdown]` 按升序 join |
| 表格占位标签 | `<!-- table: page=N, id=tN_m, bbox=(x0,y0,x1,y1) -->` 仅排查/溯源用，**不进 chunk metadata** |
| 表格原子性 | 本地表格与 MinerU 表格统一输出为 `|` Markdown 块，下游 `PdfStructure` 已有表格原子块保护（M1） |
| 升级语义 | Tier B 本地抽取 + 校验失败 → 该页 `tier=C, parser=mineru`，并入 MinerU 页集，reason 写 manifest |

### B.2 T4 — Markdown 装配层（下一顺位，不依赖 MinerU）

| 编号 | 任务 | 涉及文件 | 验收标准 |
| --- | --- | --- | --- |
| T4-1 | `ParsedPage` 扩展为含 `blocks: List[PageBlock]`（或单 markdown + 位置元数据） | `pdf_page_profile.py` 或新 `pdf_markdown_assembler.py` | 每页产物带 `page_idx/parser/route`，块带 `order_key/kind/bbox` |
| T4-2 | 页序缝合：`OrderedDict[page_idx]` 按升序 join，穿插页还原 | `pdf_markdown_assembler.py` | 构造 30 文本 + 10 MinerU + 60 文本穿插用例，还原后页号连续不丢不重 |
| T4-3 | 页内块排序：`blocks.sort(key=order_key)`，表格块原子插入 bbox 对应位置 | 同上 | 图文表混排样例：表格块出现在正确文字之间 |
| T4-4 | 表格占位标签生成与解析（不进入下游 chunk metadata） | 同上 | 标签可回查 page/bbox；chunk metadata 无 table 标签泄漏 |
| T4-5 | 标题级别规范化（编→#、章→#/##、节→##/###、条→###，按样张定级） | 同上 | golden set 中章节标题级别无漂移 |
| T4-6 | 去重页眉页脚 + 目录点线行 + manifest（每页 route/parser/置信度/耗时/版本/警告） | 同上 | manifest 可解释每页路由来源 |
| T4-7 | 单测 + 回归 | `test/test_pdf_markdown_assembler.py` | 全绿；全量 pytest 不破 |

### B.3 T2B — 规整表格抽取与校验（可选/后置，业务有框表接入时启用）

| 编号 | 任务 | 涉及文件 | 验收标准 |
| --- | --- | --- | --- |
| T2B-1 | `extract_table(page)` → Markdown 表格（复用 pdfplumber 或 PyMuPDF Table） | `pdf_table_extractor.py` | 2/3/4 列有框表转 Markdown 正确 |
| T2B-2 | `validate_table(...) → (ok, reason, confidence)` | `pdf_table_validator.py` | 行列匹配/表头完整/空单元格比/格式/截断/错位 六项判定 |
| T2B-3 | 校验失败 → 升级 Tier C（tier/parser 改写 + reason 写 manifest） | 路由层接线 | 失败页进入 MinerU 队列，manifest 记录原因 |
| T2B-4 | 表格上下文说明文字不丢失 | `pdf_table_extractor.py` | 表格上方标题/说明保留 |
| T2B-5 | 单测 + 回归 | `test/test_pdf_table_extractor.py` | 全绿 |

### B.4 表格处理两级判定的代码流（任务卡对应关系）

```
build_page_profile → table_candidate（L1 已实现）
   ↓
LegalPageClassifier → table_regular（Tier B 本地）/ table_complex（Tier C MinerU）（L1 已实现）
   ↓
Tier B: T2B-1 extract_table → T2B-2 validate_table
         ├─ ok    → PageBlock(kind="table", content=Markdown 表格)（T2B-4）
         └─ fail  → T2B-3 升级 Tier C（并入 MinerU 页集）
   ↓
Tier C: MinerU 整页解析（表格由 MinerU 版面分析还原，不再单独排）
   ↓
T4-3 页内块排序 → T4-4 表格占位标签 → T4-2 页序缝合
```

### B.5 执行顺序（更新后）

```
已完成：L0 画像 → L1 分类器 → L2 快路径
下一顺位（不依赖 MinerU）：
  1. T4 装配层（T4-1..T4-7）—— 顺序还原 + 表格标签 + manifest，先打通"无 MinerU 也能完整装配快路径"
  2. T2B 表格抽取/校验骨架（T2B-1..T2B-5）—— 因本批有框表≈0，按"骨架+单测"交付，不追求真实表格全通过
待服务器支线（内网 IP/SSH/端口/镜像 tag 确认）：
  3. L4 MinerU 接入（R4 子 PDF + 缓存 + fail-fast）
  4. L6 接线 + golden set + 端到端
```

> 结论：先做 T4 装配层（顺序还原是 L4 接入 MinerU 的前置，因为 MinerU 产物必须插回正确 page_idx 槽位），再补 T2B 骨架，最后等服务器支线做 L4/L6。


### T4 — Markdown 装配层 ✅ 完成（10 单测 + 全量 314 passed）
- 新增：
  - `public_kb/ingestion/transforms/pdf_markdown_assembler.py`（PageBlock / ParsedPage / assemble_page_blocks / assemble_pages / normalize_heading_levels / dedup_repeated_lines / clean_assembled_document / build_manifest / 表格占位标签 table_placeholder + parse_table_placeholder）
  - `test/test_pdf_markdown_assembler.py`（10 用例）
- 关键契约落地（对齐附录 B.1）：
  - 页级主键 page_idx，`assemble_pages` 乱序输入按 page_idx 升序缝合 → 穿插页还原（前2文本+中1MinerU+后1文本用例通过）；
  - 页内块按 order_key(y) 排序，表格块整体插入 bbox 对应位置，表格占位标签 `<!-- table: page=N, id=..., bbox=... -->` 仅溯源不进 chunk metadata；
  - 标题规范化：编→#、章→##、节→###、条→####、款→#####、项→######，非法规标题不改；
  - 页眉页脚去重豁免法律标题；目录点线行复用 `pdf_structure.strip_toc_noise`；
  - manifest 输出逐页 route/parser/置信度 + tier/parser 分布摘要。
- 至此"无 MinerU 也能完整装配快路径产物"的链路已打通：L1 分类 → L2 快路径 → T4 装配 → manifest。

