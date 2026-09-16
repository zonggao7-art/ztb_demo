# 第二轮RAG测评报告

- 运行编号：s3_20260911_formal_01
- 检索阶段：s3
- 采集完成：是
- 评分完成：否
- 正式完成：否
- 已完成尝试：200/200

## 六项核心指标

| 指标 | 结果 | 有效数量 |
|---|---:|---:|
| Context Recall（检索内容覆盖率） | 缺失 | 0/200 |
| Context Precision（检索内容精度） | 缺失 | 0/200 |
| Answer Correctness（答案正确性） | 缺失 | 0/200 |
| Faithfulness（忠实度） | 缺失 | 0/200 |
| P50端到端查询耗时 | 1.741秒 | 200 |
| P95端到端查询耗时 | 2.990秒 | 200 |
| 平均每题Token | 55499.89 | 200 |

P50和P95共同构成第五项速度指标。四项质量分、速度和Token不合成为总分。

## 完成与异常

| 状态 | 数量 |
|---|---:|
| success | 140 |
| abnormal_refusal | 0 |
| empty_answer | 0 |
| system_failure | 60 |
| unknown | 0 |

## 精确chunk命中

精确命中只比较 `ground_truth_chunk_ids` 与最终引用的 `chunk_uid`，不对RRF分数应用余弦阈值。

| 指标 | 结果 |
|---|---:|
| 可评题数 | 200 |
| 至少命中一个标准chunk | 134 / 200 |
| 全部标准chunk命中 | 127 / 200 |
| Macro Recall | 0.6525 |
| Micro Recall | 0.6074 |
| 首个命中平均排名 | 1.50 |

## 单跳/多跳分层

| 题型 | 数量 | Context Recall | Answer Correctness | P50 | P95 | 全chunk命中率 |
|---|---:|---:|---:|---:|---:|---:|
| multi_hop | 70 | 缺失 | 缺失 | 1.734秒 | 3.158秒 | 0.4286 |
| single_hop | 130 | 缺失 | 缺失 | 1.741秒 | 2.781秒 | 0.7462 |

## Token来源

- 系统Token总数：11099978
- 查询嵌入：8753（粗略估算）
- Reranker输入：10332568（接口无usage时为粗略估算）
- 回答模型：758657（接口实际usage）
- 真实接口usage与估算次数：{"rough_estimate": 400, "api_actual": 140}
- 无法确认的输出次数：0
- Ragas裁判与评分嵌入Token：0（单独记录，不计入平均每题系统Token）

## 人工审核

本报告不设置自动及格线。请结合六项指标、异常数量和逐题结果决定是否进入下一阶段。
S2及后续阶段会在同目录生成 paired_comparison.json 和 paired_comparison.md。
## Reranker观测

- 有轨迹题数：200
- 结果状态：{"success": 140, "failure": 60}
- Reranker P50：0.662秒
- Reranker P95：1.225秒
- 轨迹只含chunk_uid、重排前后名次和分数，不含候选正文。

