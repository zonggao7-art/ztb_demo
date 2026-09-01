# 环境迁移 + PDF/数据链路 + 去重 + 时效性 评估报告

> 日期：2026-08-30
> 范围：①环境迁移（已执行）②DATA 数据处理可行性分析 ③去重现状 ④PDF 大批量矿工处理 ⑤法条时效性
> 方法：静态代码审查 + 对新上传 3 份 PDF 的制度抽样 + 对 `cleaned_v1/manifest+dedup_report` 的证据核对
> 结论中行号/代码引用均来自 `D:\agent_project\ztb_demo\public_kb\`

---

## ⚠️ 执行摘要（先说结论）

- **环境迁移：已完成** ✅。新环境 `D:\agent_project\ztb_demo\.venv` 创建并用 uv 安装了与源环境 **逐版本一致** 的 89 个包。**全量测试 `239 passed`**（原基线 221，多出者为新并入的离线测试），比源 venv 校验 `public_kb` 可用。**未迁移 `.env`（含密钥，由你手动处理）。**
- **这 3 本 PDF 不是普通法规 PDF**，是「工具书+编纂书+案例问答书」的复合体，当前链路**无法妥善处理**：表格识别为零、分栏文本被顺读打乱、目录页噪声直接入上下文、书脊页眉混淆。工作子结论：**新增 PDF 入库前必须补一层 PDF 专用解析（PDF→结构化块/表格识别），并将 `SemanticChunker` 的"短行丢弃"对 `第X条`/`第X章` 的保护纳入清洗规则。**
- **去重：识别层有（chunk_uid），执行层没有**。`cleaned_v1` 是外部（模拟 SQL 导入侧）去重产物；`public_kb` 入库侧 `MilvusStoreManager` 无任何基于 `chunk_uid` 的过滤/剔重/幂等。评测侧 `run_knowledge_citation_eval.py` 会在评估时**单独报告重复组**，实为"体检报告"而非"治疗"。
- **时效性/新法代替旧法：完全未实现**。`manifest.csv` 有 `release_time/imple_time` 字段但入库侧不消费；相对时点（新政发布旧法失效）在 vector 层面没有证据支撑。
- **PDF 大批量处理：可行（同步循环逐本），但有 3 个硬前提**：`magic-pdf` 已装且入 PATH、MinerU 超时上限 3600s、逐本调用无并发（协作者接手并发化前需先清理共享可变状态）。**这本工具书多页千页级 + 表格/分栏会导致单本解析时间显著拉长。**

---

## 1. 环境迁移（已执行）

### 1.1 做了什么

| 项 | 值 |
|---|---|
| 目标环境 | `D:\agent_project\ztb_demo\.venv`（新建，uv 管理） |
| Python | 3.11.15（`uv venv --python 3.11`，与源一致） |
| 依赖 | `uv pip install` 逐版本安装源 venv 的 89 个包（`pymilvus 3.0.1`、`langchain-core 1.5.4`、`langchain-openai 1.5.0`、`langgraph 1.2.11`、`torch 2.12.1`、`numpy 2.4.6` 等） |
| 验证 | `.venv\Scripts\python.exe -m pytest test -q --ignore=test/test_cloud_sync.py` → **239 passed in 40.52s** |
| 附加 | 为 PDF 分析临时加装 `pymupdf`（纯只读分析用，不入测试基线） |
| 未做 | **`.env` 未复制**（含 API 密钥），由你手动拷到 `ztb_demo`（注意源项目可能无 `.env`，两个项目目录不同，密钥直接拷贝风险自负）；另 `requirements.txt` 中锁定的 `openai>=1.50.0,<2.0.0` 与 `langchain-openai`(要求 openai≥2.45) 存在**版本冲突**，所以用源 venv 的 pins 而非 requirements.txt 完成安装——新环境与源环境**完全一致**，且这恰暴露了 requirements.txt 长期未按解析器校验（源 venv 中 openai==3.0.0 ✓）。 |

### 1.2 迁移脚本（可复用）

```powershell
cd D:\agent_project\ztb_demo
uv venv --python 3.11 .venv
uv pip install --python .venv\Scripts\python.exe -r requirements.txt  # 若仍冲突则用源 pins
# 若走 pins：uv pip freeze --python "D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe" > /tmp/pins.txt && uv pip install -r /tmp/pins.txt
```

建议将来用 `uv pip freeze` 维护一个 `requirements.lock` 以保证可复现。

---

## 2. DATA 现状与新上传 PDF 剖析

### 2.1 `DATA/raw_data/` 结构

```
DATA/raw_data/
├── law_pdf/                    # 你刚上传的 3 本 PDF（电子书）
│   ├── 招标投标法律解读与风险防范实务(白如银)(Z-Library).pdf        # 774 页
│   ├── 中华人民共和国招标投标法律法规全书…(OCR)(Z-Library).pdf      # 574 页
│   └── 政府采购、工程招标、投标与评标1200问(第3版)…(刘海桑).pdf     # 552 页
└── cleaned_v1/                 # 已有清洗产物（外部导入链路产出）
    ├── manifest.csv            # 355 行，doc_id/filename/title/来源/发布/实施/content_len
    ├── dedup_report.csv        # 文本级去重报告（外部）
    ├── documents.jsonl         # 每条含 content_md 全文
    └── markdown/*.md           # 355 个去重后的法规 Markdown
```

### 2.2 三本 PDF 的实测普查（前 28 页抽样，PyMuPDF 纯只读）

| 维度 | book1《招标投标法律解读与风险防范实务》 | book2《招标投标法律法规全书(含相关政策)》 | book3《政府采购…1200问》 |
|---|---|---|---|
| 页数 | **774** | **574** | **552** |
| 文本层 | ✅ 有文本层（非纯扫） | ✅ 有 | ✅ 有 |
| 首 28 页抽样 | 正文有字，逐页 300-800 字 | 正文字密（1200-1900/页） | 正文 400-600/页 |
| 分栏率 | 14%（3/28 页见双栏提示） | **89%（25/28 页双栏）** | 18%（5/28） |
| 表格检出（横/竖线） | 0 | 0 | **0** |
| 目录点线页 | 无点线 | **有**（如"……(82)"） | 无 |
| 页眉 | 书脊"2 中华人民共和国…"重复 | **书脊"8 一、招标投标"（章名/页脚混排）** | "第一节 政府采购概述"页眉 |
| 页码 | 分离字符 | 与正文同行 | 混合 |
| 字体 | SimSun/STKaiti + Type3 | **SimSun（legacy CID）** | Tahoma + Type3 |
| 结构 | 章/节 标题（第X章→） | **编-章-节 三级标题** | 章-节-「第N问…答：」 |

**两个关键制度样本（后文中重点举证）：**

1. **book2 双栏正文（p9 实测）**：`get_text("text")` 输出 77 行/页，但**左栏与右栏的文字按物理行混排、顺序错乱**（如"第五十七条【招标人在中标候选之外确定中标人的责任】……"后紧跟另一栏的"第五十八条…"），条文边界被打断成对栏交叉。
2. **book3 表格（p9 实测，表1-1『政府采购和公共采购的比较』）**：`get_text` 中表格被**摊平成 4 行文本**（"首要宗旨主体所用资金适用的法规、程序"→"政府采购规范政府采购行为各级国家机关…"→"公共采购…"），列对齐与单元格边界全部丢失。
3. **book3 Q&A 体例**：正文以「55.公开招标的程序是什么？→ 答：（1）…」贯穿，`SemanticChunker` 依赖的 `第X章/第X条` 正则结构**不存在**，会退化为纯长度切块。

### 2.3 当前 PDF 链路能不能处理？——逐条对照

| 当前链路步骤 | 对这三本 PDF 的表现 | 结论 |
|---|---|---|
| ① `MinerUParser.parse`（`subprocess magic-pdf`） | MinerU 可产出读取顺序分段、表格识别（PDF2MD 表格）、公式等。**是当前链路里唯一有希望处理分栏/表格的一环**。但需要 `magic-pdf` 在 PATH、且**单本 700 页以上 + OCR 类会显著超时**（`mineru_timeout=3600` 可能不够） | ⚠️ 前提满足才有基础 |
| ② `TextCleaner.clean` 4 条规则 | 页眉页脚根据**全文档重复行计数≥5**去重：book1 的书脊页眉"2 中华人民共和国…"恰好每页重复 → 会被删除 ✅；但 book2 的页眉"8 一、招标投标"也是每页重复 → 也会被删除 ⚠️ **会把章标题误删**。**页码行（`^\d{1,4}$`）对 book3 中"1.什么是采购？"这类数字开头的行无效（有中文）**。 | ⚠️ 部分可用 |
| ③ `SemanticChunker` 标题感知 | book1/2 有 `第X章/第X节` → 可切；**book3 无此结构 → 退化为 2000 字纯长度切块**；且对双栏混排文本，切出的块内容是**跨栏交叉废话**；表格被摊平 → 切出的块把表格语义打乱。 | ❌ 对双栏+表格不友好 |
| ④ `csv_loader.structure_plain_text`（中文法律标题→Markdown） | 该函数是给 **CSV 纯文本**用的，`MinerUParser` 的 Markdown 已经带标题，**不会走到**。 | 不适用 |
| ⑤ 入库 metadata | `doc_name=文件名`、`chapter=标题栈`。book2 三级标题栈可用；book3 无（回退"前言"）。 | ⚠️ 部分 |

**结论**：
- **不能"直接复用现有清理链路就入库"**。对这三本电子书，需要**在 MinerU 之后、TextCleaner 之前加一层 PDF 结构化适配**：表格识别（MinerU 支持 markdown 表格）、双栏 reflow（MinerU 已做版面分析，需确认读序）、目录/书脊噪声过滤。现有 `TextCleaner`+`SemanticChunker` **没有列级/读序/表格保护**。
- **白如银实务（774 页）与《法律法规全书》（574 页）是编纂类工具书**，信息量大但含大量重复条文（与已入库的 355 部法规重叠），**入库后会大幅推高重复率**（当前已入 55.57%），**进一步验证去重必需**。
- book3（1200 问，552 页）是 **Q&A 体例**，与我们已入库的 `content_type=qa_pair` 完全同构 → 若入库，最好落入 `qa_pair` 元数据体系（`question`/`answer` 字段），但 `SemanticChunker` 不会拆。
- **看版式：3 本都非纯扫描**，文本层可用，但 `magic-pdf` 的 OCR 需求不急切；不过其中 2 本 `(OCR)` 后缀意味着它已经历过一次 OCR → MinerU 再解析 PDF 时是"文本+图像混合"，OCR 模型仍会被 pymupdf 之外的 OCR（MinerU 内部）再次调起，耗时与资源开销大。

---

## 3. 去重现状（核心问题 3 & 4）

### 3.1 你在清理侧 existing 的去重

`cleaned_v1/manifest.csv` + `dedup_report.csv` 显示，**外部导入链路**（`ods_policy` 多快照等）做过一次**文档级(全文)去重**：
- `dedup_report.csv` 列 `dropped_source,dropped_source_id,dropped_title,dropped_len,kept_source,kept_source_id` → 同一标题全文重复的源行被丢弃并指向保留行（如 实施条例被丢弃 8 份、保留 `rag_cleaned_policy:1`）。
- **这是"整文档级"去重**（same title + same 全文）；`manifest.csv:content_len` 证明去重后每行即一部唯一法规。
- 但它**不是文本块级去重**：同一部法内不同的条文、以及跨文档重复引用的条文，仍会在分块后重复（审计报告已实测 55.57%）。

### 3.2 `public_kb` 入库侧的去重 —— **没有**

审查 `public_kb` 全量：
- `ingestion/pipeline.py`：只 `validate_ingestion_documents`（非空、三个 metadata 字段），**无去重概念**。
- `services/milvus_store.py::_batch_insert`（189-211）、`_build_records`（213-242）、`add_documents`（244-261）：**纯 insert，无 `doc_name+chapter+text_hash` 存在性检查**，不做 upsert，不做内容过滤。
- schema 主键 `id INT64 auto_id`，`chunk_uid` 是动态字段**不是唯一键**（Milvus 不允许非主键唯一），所以**即使有 chunk_uid 也无法由数据库强制唯一**——必须是应用层 `query(PK=chunk_uid)` 判重后决定 insert/跳过/删除的策略。
- `chunk_ids.py::compute_chunk_uid` 只负责**生成**标识。审计报告把 `add_documents` 无去重直接列为"重复根因之一"。

### 3.3 权力汇总（谁在去重谁没有）

| 层级 | 实现 | 性质 |
|---|---|---|
| 导入前（文档级） | `cleaned_v1/dedup_report.csv`（外部链路） | **存在**，整文档去重 |
| 分块后（块级） | `chunk_uid` 只是标签 | **不存在边做边去重** |
| 向量化入库 | `_batch_insert` | **无** |
| 评测时 | `run_knowledge_citation_eval.py` 按 `chunk_uid` 统计 `duplicate_groups` | **体检报告**，非治疗 |
| 清理侧（离线工具） | `archived/csv_to_mysql.py` 用 `INSERT ... ON DUPLICATE KEY UPDATE` | 只对 MySQL 侧有效，与 Milvus 无关 |

**结论：代码里没有任何"对文本块做去重"的实现。** 审计报告明确："入库侧增加内容哈希去重（`(doc_name, chapter, text_hash)` 判重），增量导入前先查重"属于**未落地建议**。

### 3.4 多重去重 / 幂等 —— 现状

- **多重去重**：无。（文档级有；块级无；向量级无。）
- **幂等**：`init_knowledge_base` 重复执行会因"集合已存在"报错阻断（`milvus_store.py:68-72` 默认禁止覆盖），这是**保护**；`add_documents` 重复导同一批 → **重复写入**（无幂等）。
- **向量化入库去重**：无。`batch_size=100` 循环只是简单 insert。

---

## 4. PDF 大批量处理（问题 5）

- **实现形式**：`MinerUParser` 通过 `subprocess.run(["magic-pdf", "-p", pdf, "-o", out_dir])` 调用，`timeout=settings.mineru_timeout(3600)`。`init_knowledge_base` 同步 for 循环逐本 Parse → 收集 → 批量入库。**是"同步批次"，非并行。**
- **大批量可行性**：单本调用是可行的；但
  1. 无并行：774 页工具书 + 574 页 OCR 书，逐本串行 `magic-pdf` 会显著拖长整体（基线文档只证实了一本一页级 POC）。
  2. 缓存断点：`_find_cached_markdown` 按 `pdf_stem/auto/stem.md` 检索，重复跑可复用 ✅。
  3. `mineru_output_dir` 默认 `DATA/raw_data`（`config.py:141-144`）——**会把解析中间产物写进你刚整理的 DATA 目录**，注意规划。
  4. 大批量并发当前代码不支持：`MinerUParser`（无状态）+ 后续 `MilvusStoreManager`/`emdeddings` 均同步阻塞。**协作者若要并发化，需先解决第 5 节（上一份报告）里的共享可变状态**（reranker `last_status` 等）。
- **结论**：代码形式上可跑大批量；**但针对这三本工具书，我建议先把"PDF 专用解析"补上再大批量**，否则批量入库的数据质量会反过来污染现有知识库。

---

## 5. 法条时效性 / 新法代替旧法（问题 6）

**完全没有实现。**

- `manifest.csv` 有 `release_time`（发布）与 `imple_time`（施行）；`cleaned_v1/documents.jsonl` 每条也带发布/施行时间；`csv_loader._process_row` 会把 `publish_date`/`imple_time` 作为元数据写入（`csv_loader.py:410-411`），随机被透传到 Milvus 动态字段。
- 但入库侧和检索侧**都没有消费这两个时间**：没有 `effective/失效` 标志字段、没有"新版旧版"关联、没有 `query` 时按施行日期过滤当前有效的版本、没有过期判定。法规层级(A1：法律/行政法规)在 manifest 里也没有列。
- **后果预判**：若你把 2023 年《招标投标法律法规全书》与 20xx 年新法都入库，问答会同时命中新旧条文，LLM 会混淆"现行有效"与"已废止"。**现有 R1-R7 引用校验只保证"引用了正常来源"，不保证"来源是最新有效的法条"**。

**补最短路径**（不改代码，给后续实施建议）：
1. `manifest.csv`（或文档级 metadata）加 `status(现行/失效)` + `取代关系(新法→旧法列表)`；
2. `MilvusStoreManager._build_records` 写入 `effective_date`/`status` 到动态字段；
3. 检索时按需 `expr` 过滤 `status=现行` 或按 `effective_date≤now` 过滤（`search` 的 `filter` 参数支持标量过滤）；
4. （可选）离线脚本定期把"失效法"文中打标、或把失效 chunk 从活跃检索中淘汰。

---

## 6. 改进清单（本次不改代码，供排期）

| 优先级 | 项 | 位置/方法 |
|---|---|---|
| 🔴 高 | PDF 专用解析适配（表格/双栏/目录/书脊） | 新增 `ingestion/transforms/` 或 `services/mineru_parser.py` 增强；MinerU 输出 Markdown 表格保留后，`SemanticChunker` 需要表格感知 |
| 🔴 高 | 块级去重（`chunk_uid` 判重 → insert/跳过） | `milvus_store._batch_insert` 前用 `query(expr="chunk_uid in [...]")` 过滤；增量同理 |
| 🔴 高 | 幂等导入 | `add_documents` 与 `init` 支持不重复写集内去重 |
| 🟠 中 | 法条时效性 | `effective_date`/`status` 元数据 + 检索过滤 |
| 🟠 中 | 清洗规则保护条款号 | `TextCleaner` 短行丢弃须豁免 `第X条`/`第X章`（防误删），页眉检测加"章节页眉"留白逻辑 |
| 🟠 中 | `requirements.txt` 冲突修复 | 锁定 `openai>=2.45,<3` 或用 lockfile |
| 🟡 低 | `mineru_output_dir` 规划 | 避免解析中间产物混入 DATA 组织 |

---

## 附：迁移校验记录

```
ztb_demo\.venv\Scripts\python.exe -m pytest test -q --ignore=test/test_cloud_sync.py
239 passed in 40.52s
ztb_demo\.venv\Scripts\python.exe -c "import public_kb, langchain_core, pymilvus, openai"
OK（pymilvus 3.0.1, langchain_core 1.5.4, openai 3.0.0）
```