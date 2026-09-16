# 引用溯源数据规范 — 测评系统对接文档

> 适用范围：`public_kb` 知识库问答（`knowledge_qa` 分支）返回结果中的引用来源数据。
> 目的：为系统回答的**可信度**（answer credibility）与**出处合规性**（citation compliance）提供自动化评估所需的标准化数据。

---

## 1. 数据获取方式

### 1.1 直接调用检索引擎

```python
from public_kb import PublicKnowledgeRAG
rag = PublicKnowledgeRAG()
rag._store_manager.load_existing()
rag._build_qa_chain()
result = rag.query("评标委员会由哪些人组成？")
```

### 1.2 通过 Agent 全链路（router → knowledge_qa 节点）

```python
from agent import AgentGraph
result = AgentGraph().invoke(question)
# result["business_result"]["data"]["citations"]          ← 标准化引用
# result["business_result"]["data"]["citation_validation"] ← 校验报告
```

### 1.3 批量测评落盘

`scripts/run_three_core_evaluation.py` / `scripts/run_knowledge_citation_eval.py`
已将 `citations` / `citation_validation` 透传到 `test_report/*.jsonl`，每行一条样本，可直接读取评估。

---

## 2. 返回结构

```jsonc
{
  "answer": "评标委员会由招标人代表和有关技术、经济等方面的专家组成……【来源1】【来源2】",
  "sources": [ /* legacy 视图（向后兼容），字段: doc/chapter/chunk_index/content_snippet/score */ ],
  "citations": [
    {
      "context_index": 1,          // 上下文块编号 = 回答中【来源N】的 N（1 起）
      "chunk_id": 173856,          // Milvus 主键 id（行级唯一，回表验证锚点）
      "chunk_uid": "ck-a1b2…",     // 内容派生稳定标识（跨重建稳定，见 §4）
      "doc_name": "中华人民共和国招标投标法",  // 所属文档（数据源位置）
      "chapter": "第四章 开标、评标和中标",     // 章节路径（数据源位置）
      "chunk_index": 0,            // 章节内块序号
      "text": "第三十七条 评标由招标人依法组建的评标委员会负责。……",  // 完整原文片段
      "score": 0.86,               // 检索相关度
      "metadata": {                // 全部附加元数据（动态字段透传）
        "source_file": "xunfei0001_policy_documents_copy2.csv",
        "source_url": "https://…",
        "publish_date": "2024-10-20"
      }
    }
  ],
  "citation_validation": {
    "all_passed": true,            // 启用规则全部通过
    "is_refusal": false,           // 拒答场景：true 且 citations 合法为空
    "context_chunks": 2,           // 进入 LLM 上下文的 chunk 数
    "cited_markers": [1, 2],       // 回答中实际出现的【来源N】编号
    "uncited_chunks": [],          // 未被回答标记引用的上下文块编号
    "unknown_markers": [],         // 无法解析到有效引用的标记编号
    "rules": [
      {
        "rule_id": "R1_chunk_id_present",
        "name": "chunk_id 完整性",
        "description": "每条引用必须携带 Milvus 行级 chunk_id",
        "enabled": true,
        "passed": true,
        "detail": ""
      }
      // … R2-R7 同构
    ]
  }
}
```

## 3. 引用校验规则（R1-R7）

针对法律法规类专业场景的溯源规则集，配置开关见 `public_kb/config.py::CitationRuleConfig`。
所有规则 **fail-soft**：只产出结构化报告，不阻断回答返回。

| 规则 | 含义 | 默认 |
|---|---|---|
| R1 `chunk_id_present` | 每条引用必须携带 Milvus 行级 chunk_id（完整性） | 开 |
| R2 `chunk_uid_present` | 每条引用必须携带内容派生 chunk_uid | 开 |
| R3 `source_location_present` | doc_name/chapter 非空且非占位值（数据源可定位） | 开 |
| R4 `full_text_present` | 原文片段完整非空（未被截断） | 开 |
| R5 `context_fully_cited` | **无遗漏**：进入 LLM 上下文的全部 chunk 必须出现在 citations 中，且无凭空引用 | 开 |
| R6 `no_unknown_markers` | 回答中【来源N】全部解析到有效引用（**无错误关联**/幻觉引用） | 开 |
| R7 `all_context_marked` | 严格模式：上下文 chunk 全部被回答标记引用 | 关（关闭时未标记仅记入 uncited_chunks） |

## 4. chunk 唯一标识规范

- `chunk_id`：Milvus 主键 `id`（INT64 auto_id）。行级唯一、当次集合内稳定；集合重建后会变化，仅用于**回表验证**。
- `chunk_uid`：内容派生稳定标识，格式 `ck-<md5_hex32>`：

```
chunk_uid = "ck-" + md5( doc_name | chapter | chunk_index | md5(normalize(text))[:16] )
normalize(text) = strip + 统一换行（\r\n / \r → \n）
```

  - 入库时由 `public_kb/milvus_store.py` 写入动态字段固化；存量数据在检索侧用同一函数（`public_kb/chunk_ids.py::compute_chunk_uid`）即时计算，**两侧口径一致**；
  - 跨集合重建稳定 → 供测评跨批次对比；
  - 同内容重复行（同 doc/chapter/index/text）共享同一 uid → 供测评**去重检测**。

## 5. 关联校验（防"错误关联"）

测评系统对每条 citation 执行回表验证：

```python
from pymilvus import MilvusClient
client = MilvusClient(uri=…)              # 云端集合
entity = client.get("public_kb", ids=[citation["chunk_id"]], output_fields=["*"])[0]
assert entity["text"] == citation["text"]                                   # 原文一致
assert compute_chunk_uid(entity["text"], entity) == citation["chunk_uid"]    # uid 口径一致
```

失败类型：`missing_chunk_id` / `chunk_not_found` / `text_mismatch` / `chunk_uid_mismatch`。
参考实现：`scripts/run_knowledge_citation_eval.py::_AssociationChecker`。

## 6. 建议评估指标

| 指标 | 定义 | 数据来源 |
|---|---|---|
| 溯源完整率 | all_passed 样本占比 | `citation_validation.all_passed` |
| 单规则通过率 | 各 R1-R7 通过率 | `citation_validation.rules[]` |
| 关联正确率 | 回表校验通过条数 / 引用总条数 | §5 回表验证 |
| 引用覆盖率 | 平均 cited_markers / context_chunks | `citation_validation` |
| 拒答正确率 | 负样本 is_refusal 命中率 | `citation_validation.is_refusal` |
| 引用精确率 | 无 unknown_markers 样本占比 | `citation_validation.unknown_markers` |
