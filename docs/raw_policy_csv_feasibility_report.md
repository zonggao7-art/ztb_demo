# raw_policy 政策性 CSV 文件数据处理可行性分析报告

> **报告日期**：2026-08-12（更新于数据清理后）  
> **分析范围**：`d:\DEMO\zhaotoubiao_demo\raw_policy\` 下全部 50 个 `*_data.csv` 文件  
> **报告性质**：仅分析评估，不修改任何现有代码  
> **变更说明**：原 64 个文件经清理后，移除了 6 个空文件、3 个纯元数据文件（组 B）、2 个表格片段文件（组 D）、3 个冗余/无效政策文件（含 138 万行巨表 `xunfei5_ods_policy_regulation_files`），当前余 50 个有效数据文件。

---

## 一、现有工作流适配性评估

### 1.1 现有工作流全貌

项目已具备一套成熟的 PDF → Milvus 知识库处理管线，核心链路如下：

```
PDF文件 → MinerUParser (magic-pdf CLI解析为Markdown)
       → TextCleaner (去页眉页脚/页码/短行/压缩空行)
       → SemanticChunker (按Markdown标题层级语义分块)
       → _SafeEmbeddings (bge-m3 向量化, 2000字符安全截断)
       → MilvusStoreManager (pymilvus批量入库, IVF_FLAT + COSINE)
```

入口类 `PublicKnowledgeRAG` 通过 `init_knowledge_base(pdf_dir)` 驱动全流程。

### 1.2 各环节适配性逐项核对

| 环节 | 模块 | 能否直接适配 CSV？ | 分析 |
|------|------|-------------------|------|
| **解析** | `MinerUParser` | **不能** | 仅通过 `subprocess` 调用 `magic-pdf` CLI 处理 PDF 文件。CSV 文件无法进入此流程，会直接报 `FileNotFoundError`（非 .pdf 扩展名）或 `RuntimeError`（magic-pdf 无法解析）。 |
| **清洗** | `TextCleaner` | **可复用** | 清洗规则（去页眉页脚、去页码、去短行、压缩空行）对纯文本同样有效。但需注意：CSV 中的 content 是纯文本而非 Markdown，部分规则如"保留 # 开头的标题行"对纯文本无实际效果。 |
| **切片** | `SemanticChunker` | **部分可用，需改造** | 核心切分逻辑依赖 Markdown `#`/`##`/`###` 标题层级。CSV content 中的政策文本存在两类情况：<br>(a) 带结构化标题（如"第一章 总则""第一条 …"）—— 可被正则部分匹配但无法识别"第X章/条"类中文标题；<br>(b) 纯叙述文本无标题 —— 所有内容归入"前言"，超过 2000 字符后按句子边界二次拆分，语义粒度粗糙。 |
| **向量化** | `_SafeEmbeddings` | **可直接使用** | 已有 2000 字符安全截断 + batch_size=100 批量处理，无需修改。 |
| **入库** | `MilvusStoreManager` | **可直接使用** | Schema（id/vector/text + dynamic fields doc_name/chapter/chunk_index）通用，无需修改。 |

### 1.3 title 字段与 content 字段的特殊问题

**title 字段**：大部分含 content 列的 CSV 文件均存在独立的 title/rule_title 列（如"中华人民共和国招标投标法实施条例"），现有流程完全不具备读取 CSV 列并映射为元数据的能力——`MinerUParser` 只返回纯文本，title 信息在管线起点就已丢失。

**content 字段**：content 为未经切分的原始长文本（典型长度 4,000–12,000 字符），且为纯文本格式（无 Markdown 标题标记）。`SemanticChunker` 按 `#` 标题切分的策略对其几乎无效，仅能依赖句子边界二次拆分，导致切分粒度过粗、跨段落粘连。

**结论**：现有工作流从入口 `MinerUParser` 起即与 CSV 数据不兼容，后续 `SemanticChunker` 对无 Markdown 标题的纯文本也缺乏合理的切分策略。需要新增 CSV 专用预处理模块，但 `TextCleaner`、`_SafeEmbeddings`、`MilvusStoreManager` 均可复用。

---

## 二、格式不规范问题梳理

### 2.1 总体概况

50 个 CSV 文件按 Schema 可分为 **5 个不同类别**，按内容性质可分为 **2 个功能组**：

#### 组 A：完整政策文档（title + content，可直接入库）
| Schema | 文件数 | 代表文件 | 行数 |
|--------|--------|---------|------|
| `id, source_url, inserted_time, imple_time, release_time, rule_title, content` | 4 | `lin_gang_6_ju_tou_1_ods_policy_data.csv` | 7,513 |
| `id, title, content, source, publish_time, url, category, project_type, word_count` | 2 | `xunfei0001_policy_documents_copy2_data.csv` | 315 |
| `id, title, content, release_time, imple_time` | 1 | `rag_cleaned_policy_政策全文_data.csv` | 43 |
| `doc_id, doc_type, title, content, date, source_url, created_at` | 2 | `test_db_last_policy_docs_data.csv` | 626 |

#### 组 C：QA 问答对（非原始政策全文）
| Schema | 文件数 | 代表文件 |
|--------|--------|---------|
| `id, question, answer, page_number, chunk_index, created_at` | 37 | `xunfei0002_policy_t_*.csv` |
| `id, question, answer, page_number, chunk_index, created_at` | 4 | `xunfei0003_policy_anli_*.csv` |

> **已移除的组**：组 B（纯元数据文件，3 个）和组 D（表格内容片段，2 个）已从目录中清理，不再纳入分析范围。组 A 中原 `bidding_data_ods_policy_data.csv`（6 行）、`xunfei0001_policy_documents_data.csv`（315 行）以及 `xunfei5_ods_policy_regulation_files_data.csv`（138 万行巨表）已一并移除。

### 2.2 具体格式问题详列

#### 问题 1：UTF-8 BOM 全量污染（50/50 文件，100%）
**现象**：所有 50 个 CSV 文件首字节均为 `\ufeff`（BOM）。  
**影响**：使用 Python `csv.reader` 直接打开时 `utf-8` 编码会将 BOM 混入首列列名（如 `\ufeffid`），需统一使用 `utf-8-sig` 编码。  
**严重程度**：中（有标准修复方式）。

#### 问题 2：Schema 碎片化（5 种不同列结构）
**现象**：同样是政策性数据，标题列在 4 种 Schema 中分别叫 `title`、`rule_title`；发布时间列分别叫 `publish_date`、`release_time`、`publish_time`、`date`。  
**影响**：无法用同一段 SQL/代码读取所有文件，必须逐文件检测列名并做语义映射。  
**严重程度**：高（阻碍批量处理）。

#### 问题 3：Content 列包含多行文本（换行符嵌入）
**现象**：`lin_gang_6_ju_tou_1_ods_policy_data.csv` 和 `test_db_last_policy_docs_data.csv` 的 content 列使用双引号包裹，内容内部包含 `\n` 换行符（标准 CSV 多行字段）。  
**影响**：不能用简单的 `split('\n')` 或 `readlines()` 按行解析，必须使用 `csv.reader` 的引号处理模式。  
**严重程度**：中（标准 CSV 库可处理）。

#### 问题 4：Content 字段前导换行
**现象**：`lin_gang` 系列文件的 content 值以一个 `\n` 开头（即 `"\n中华人民共和国招标..."`），实际文本首字符是换行符。  
**影响**：`SemanticChunker` 会将首个换行符当作空行处理，造成首行文本丢失上下文；`TextCleaner` 可能将其作为空行压缩。  
**严重程度**：低（`.strip()` 可修复）。

#### 问题 5：title 列为 Null 值
**现象**：`test_db_last_policy_docs_v1_data.csv`（11,041 行）中 566 行的 title 列为字符串 `"Null"`，但 content 列包含完整 Markdown 格式的政策文本（含 `#` 标题层级）。  
**影响**：title 元数据丢失，需从 content 的首个 `#` 标题行提取作为替代。  
**严重程度**：中（可从 content 内补全）。

#### 问题 6：41 个 QA 格式文件非原始政策全文
**现象**：37 个 `xunfei0002_policy_t_*` 和 4 个 `xunfei0003_policy_anli_*` 文件的 Schema 为 `(id, question, answer, page_number, chunk_index, created_at)`，存储的是从书籍/政策文档生成的问答对，而非原始政策条文。  
**影响**：这些文件的 answer 字段可提取为知识库内容，但格式与 policy full-text 完全不同，需要单独的抽取逻辑（取 answer 作为 content，用 question 作为辅助元数据）。  
**严重程度**：中（需区分处理路径）。

#### 问题 7：全角空格与特殊 Unicode 字符
**现象**：content 文本中包含全角空格 `\u3000`（如"第一章\u3000总则"）、中文书名号、全角括号等，属于中文政策文本的正常排版。  
**影响**：`SemanticChunker` 按 `#` 匹配标题时不会受全角空格影响；但对嵌入 `TextCleaner` 的短行过滤（规则保留了 `#` 开头的行），含"第一章　总则"格式的纯文本行为可能被误判为"非标题短行"而移除。  
**严重程度**：低（需在清洗阶段保留中文政策文本特有的格式行）。

---

## 三、新增代码模块需求评估

### 3.1 总体判断

**需要新增独立代码模块。** 原因如下：

1. 现有工作流的入口 `MinerUParser` 仅能处理 PDF 文件，与 CSV 数据完全不兼容；
2. 现有 `SemanticChunker` 的 Markdown 标题切分策略无法胜任纯文本政策内容的结构化分块；
3. CSV 数据存在 5 种 Schema、BOM、多行 content、Null title 等多种格式问题，必须引入专门的预处理层；
4. title 等结构化元数据在现有管线中全程丢失，需要从 CSV 列中显式提取并附加到 Document metadata。

### 3.2 新增模块设计建议

建议新增 `public_kb/csv_loader.py` 模块，承担 CSV → 标准化 Document 列表的完整转换职责。

#### 核心功能

| 功能 | 说明 |
|------|------|
| **Schema 自动探测** | 读取 CSV header，按预定义映射表将列名归一化为标准字段（`title`、`content`、`publish_date`、`source_url`） |
| **BOM 透明处理** | 统一使用 `utf-8-sig` 编码打开，消除 BOM 对列名的污染 |
| **多行内容解析** | 使用 `csv.reader` 标准库正确处理被双引号包裹的多行 content 字段 |
| **内容清洗标准化** | 复用 `TextCleaner` 清洗 content 文本。扩展规则：保留"第X章""第X条"等中文法律文本标题行，不被短行过滤器误删 |
| **title 缺失补全** | 对 title 为 Null 的行，从 content 的首个标题行（`#` 开头或"第X章"模式）自动提取 |
| **语义切分配置化** | 调用 `SemanticChunker` 前，对 content 文本进行轻量结构化预处理：识别"第X章""第X节""第X条"模式，转换为 `##` 或 `###` Markdown 标题，使 `SemanticChunker` 能够正确拆分 |
| **QA 格式特殊处理** | 对 `xunfei0002` 和 `xunfei0003` 系列问答文件，提取 answer 字段作为 content，question 字段拼接为辅助元数据 |
| **空值/空行过滤** | 跳过空的 content 行，跳过无数据行文件 |

#### 与现有工作流的衔接逻辑

```
CSV文件 → CsvLoader.load(csv_path)
       → 列名归一化 + BOM处理 + 多行解析
       → TextCleaner.clean(content)           # 复用现有清洗器
       → CsvLoader._structure_plain_text()    # 新增：中文标题转Markdown标题
       → SemanticChunker.chunk(text, doc_name) # 复用现有切片器
       → List[Document] (带 title/publish_date/source_url 元数据)
       → _SafeEmbeddings + MilvusStoreManager  # 复用现有向量化与入库
```

`RagEngine` 层新增一个 `init_knowledge_base_from_csv(csv_dir)` 方法，与现有的 `init_knowledge_base(pdf_dir)` 并列，内部调用 `CsvLoader` + 复用 `TextCleaner` → `SemanticChunker` → `MilvusStoreManager` 链路。

#### 技术实现方向

| 技术点 | 方案 |
|--------|------|
| **列名归一化** | 字典映射表：`{"rule_title": "title", "publish_date": "publish_date", "release_time": "publish_date", "publish_time": "publish_date", "date": "publish_date", ...}` |
| **中文标题识别** | 正则 `r"^第[一二三四五六七八九十百千]+[章节条款]\b"` 匹配后，前缀 `## ` 转为 Markdown 标题，确保 `SemanticChunker` 正确切分 |
| **QA 文件判断** | 检测 header 中同时存在 `question` 和 `answer` 列且无 `content` 列 |
| **编码** | 底层统一 `utf-8-sig`，输出统一 `utf-8` |
| **错误隔离** | 每个 CSV 文件独立 try/except，记录失败清单但不阻断其余文件处理 |

### 3.3 无需新增模块的部分

- **向量化服务**（`_SafeEmbeddings`）：已有 2000 字符截断保护 + batch_size=100，完全满足长政策文本的向量化需求
- **Milvus 入库**（`MilvusStoreManager`）：现有 Schema 利用 `enable_dynamic_field=True`，可直接写入 title、publish_date、source_url 等额外元数据
- **问答链**（`qa_chain.py`）：基于 vector_store 构建，与数据来源无关，无需修改

---

## 四、全流程落地思路规划

### 4.1 处理流程总览

```
┌─────────────────────────────────────────────────────────┐
│  Phase 1: CSV 批量扫描与分类                              │
│  ├─ 遍历 raw_policy/*_data.csv                           │
│  ├─ 按 Schema 自动分类（A/C 两组）                         │
│  └─ 过滤空行                                             │
├─────────────────────────────────────────────────────────┤
│  Phase 2: 逐文件清洗与标准化                               │
│  ├─ BOM 移除 + 列名归一化                                 │
│  ├─ 组A: 提取 title + content + publish_date + source_url │
│  ├─ 组C: 提取 answer 作为 content, question 为辅助元数据     │
│  ├─ title Null 补全（从 content 首个标题行提取）             │
│  └─ content 前导换行裁剪 + 全角空格规范化                    │
├─────────────────────────────────────────────────────────┤
│  Phase 3: 内容结构增强                                    │
│  ├─ 中文标题识别：第X章/第X节/第X条 → ## Markdown标题       │
│  └─ TextCleaner 清洗（去噪 + 保留政策标题行）               │
├─────────────────────────────────────────────────────────┤
│  Phase 4: 语义切分                                        │
│  ├─ SemanticChunker 按 Markdown 标题层级切分               │
│  ├─ 单块 ≤ 2000 字符（与 bge-m3 安全截断阈值一致）           │
│  └─ 超长块按句子边界二次拆分（overlap=100）                  │
├─────────────────────────────────────────────────────────┤
│  Phase 5: 向量化                                          │
│  ├─ _SafeEmbeddings (bge-m3, 1024维)                     │
│  └─ batch_size=100, 自动截断保护                           │
├─────────────────────────────────────────────────────────┤
│  Phase 6: Milvus 入库                                     │
│  ├─ 增量导入模式（add_documents）                          │
│  ├─ 元数据: doc_name(csv文件名), chapter(章节路径),         │
│  │   chunk_index, title(政策名称), publish_date,           │
│  │   source_url, source_file                              │
│  └─ enable_dynamic_field 自动容纳动态字段                   │
└─────────────────────────────────────────────────────────┘
```

### 4.2 分文件组处理策略

#### 组 A：完整政策文档（9 个文件）

**优先级**：最高。这是核心知识来源。

| 子组 | 文件 | 处理要点 |
|------|------|---------|
| A1: ods_policy 系列 | `lin_gang_6_ju_tou_1`, `xunfei_ods`, `xunfei4_ods`, `xunfei5_ods` | 列名映射 `rule_title→title`；content 前导换行 strip；多行 content 用 csv.reader 解析 |
| A2: xunfei0001 系列 | `xunfei0001_policy_documents_copy2`, `xunfei07_rag_db` | 含 `word_count` 列可用于过滤过短/过长文档 |
| A3: 小批量 | `rag_cleaned_policy_政策全文` | 标准处理 |
| A4: test_db 系列 | `test_db_last_policy_docs`, `_v1` | v1 版本 title 大量为 Null，需从 content Markdown 标题提取；content 本身已是 Markdown 格式，`SemanticChunker` 可直接工作 |

**预估入库量**：odp_policy 系列 4 文件 × 7,513 = 约 3 万条，加上 test_db_v1（1.1 万条）及其他文件，共计约 4.5 万条原始记录。经语义切分后每个文档拆分为 2–10 个 chunk，预计总 chunk 数 10 万–20 万。

#### 组 C：QA 问答对（41 个文件）

**优先级**：中。可提取为补充知识。

**处理策略**：
- 提取 `answer` 字段作为 content
- 将 `question` 存入 Document metadata 的 `source_question` 字段
- `page_number` 和 `chunk_index` 保留，用于追溯来源
- 注意去重：多文件可能存在同一政策的不同 QA 视角，入库时保留全部但标记来源文件

### 4.3 向量化质量保障措施

| 措施 | 说明 |
|------|------|
| **切分粒度对齐** | `chunk_max_chars=2000`，与 bge-m3 `_MAX_TEXT_CHARS=2000` 一致，确保每个 chunk 不触发暴力截断 |
| **标题上下文保留** | `SemanticChunker` 输出的每个 Document 携带完整 `chapter` 路径（如"第一章 总则 > 第一条"），使检索结果可定位到具体法条 |
| **中文标题识别增强** | 预处理阶段将"第X章/第X节/第X条"转为 `##` Markdown 标题，使 `SemanticChunker` 沿用法条边界切分 |
| **overlap 设置** | 句子边界拆分时 overlap_chars=100，确保跨句子语义连贯性 |
| **内容去噪** | 复用 `TextCleaner` 去除 CSV 导出过程中混入的格式噪音（如重复表头行、页码残余） |
| **source_url 元数据** | 在 Milvus dynamic field 中保留原始 URL，检索结果可溯源至权威发布页面 |

### 4.4 入库准确性保障

| 措施 | 说明 |
|------|------|
| **幂等性** | 增量导入模式（`add_documents`），支持断点续跑；若需全量重建，先 `clear_collection` 再重新导入 |
| **去重策略** | 按 `(title, source_url)` 组合作为业务主键，在 CsvLoader 阶段标记已入库文档，避免重复导入 |
| **分批提交** | 沿用现有 batch_size=100 策略；对大批量文件以 1000 行为一批逐批读取→清洗→切分→向量化→入库 |
| **失败隔离** | 单个文件处理失败不影响其余文件；单行解析失败跳过该行并记录日志 |
| **验证机制** | 入库后抽样查询，验证 title 字段是否完整写入、chapter 路径是否准确、向量维度是否为 1024 |

### 4.5 实施阶段建议

| 阶段 | 内容 | 预估工作量 |
|------|------|-----------|
| **Phase 1** | 新建 `csv_loader.py`，实现 Schema 探测、列名归一化、BOM 处理、多行解析 | 1–2 天 |
| **Phase 2** | 扩展 `TextCleaner`，新增中文法律文本标题行保留规则；实现中文标题 → Markdown 标题转换 | 0.5 天 |
| **Phase 3** | `RagEngine` 新增 `init_knowledge_base_from_csv()`，串联 CsvLoader → TextCleaner → SemanticChunker → MilvusStoreManager | 0.5 天 |
| **Phase 4** | 组 A（9 个文件）批量导入验证 | 0.5 天 |
| **Phase 5** | 组 C（QA 文件）特殊处理路径开发与导入 | 0.5 天 |
| **Phase 6** | 端到端检索质量评估（抽样 query 验证召回率与准确率） | 0.5 天 |

---

## 五、风险与注意事项

1. **数据冗余**：`xunfei_ods_policy_data.csv`、`xunfei4_ods_policy_data.csv`、`xunfei5_ods_policy_data.csv` 与 `lin_gang_6_ju_tou_1_ods_policy_data.csv` 均含 7,513 行且 Schema 相同，高度疑似同一数据的多份副本。入库前需去重，避免向量库冗余。
2. **bge-m3 限流**：若使用 SiliconFlow/CloseAI 等第三方 API，需关注 RPM（每分钟请求数）限制。约 4.5 万条原始记录 × 每条约 3 chunk = 约 13.5 万次 embedding 调用，在 100 batch_size 下约 1,350 次 API 请求，总体可控。
3. **Milvus 存储**：10–20 万条 1024 维向量约需 0.4–0.8 GB 存储空间（不含索引），存储压力较小。
4. **QA 文件的权威性**：组 C 的问答对来源于书籍的二次加工，非原始法规条文，检索时应标记来源类型以区分权威等级。
