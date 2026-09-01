# 知识库引用溯源全量测评报告

- 生成时间: 自动生成
- 测试集: testset_knowledge.jsonl
- 总样本数: 106

## 1. 总览

- 全部规则通过（all_passed）: **101/101**（有校验报告的样本，即 knowledge_qa 分支）
- 分支分布: fallback=5, knowledge_qa=101

## 2. 引用校验规则通过率（R1-R7）

| 规则 | 启用次数 | 通过次数 | 通过率 |
|---|---|---|---|
| R1_chunk_id_present | 101 | 101 | 100.0% |
| R2_chunk_uid_present | 101 | 101 | 100.0% |
| R3_source_location_present | 101 | 101 | 100.0% |
| R4_full_text_present | 101 | 101 | 100.0% |
| R5_context_fully_cited | 101 | 101 | 100.0% |
| R6_no_unknown_markers | 101 | 101 | 100.0% |
| R7_all_context_marked | 0 | 101 | N/A（未启用） |

## 3. 关联校验（回表 Milvus，防错误关联）

- 引用总条数: 505
- 校验通过: 505
- 存在失败记录数: 0
- 同内容重复 chunk 组（同一 chunk_uid 多行）: 46

## 4. 拒答正确率（负样本）

- 期望拒答样本: 10
- 正确拒答: 5
  - 硬拒答（检索为空, is_refusal=true）: 0
  - 语义拒答（检索非空但 LLM 明确拒答，附引用支撑）: 5
- 漏拒答: 0
- 路由分流（未进入 knowledge_qa，router 兜底）: 5

## 5. 引用覆盖率

- 平均内联标记覆盖率（cited_markers / context_chunks）: 60.9%
