# 第二轮RAG测评报告

- 运行编号：s2_20260910_formal_01
- 检索阶段：s2
- 采集完成：是
- 评分完成：是
- 正式完成：是
- 已完成尝试：200/200

## 六项核心指标

| 指标 | 结果 | 有效数量 |
|---|---:|---:|
| Context Recall（检索内容覆盖率） | 0.9455 | 200/200 |
| Context Precision（检索内容精度） | 0.7931 | 200/200 |
| Answer Correctness（答案正确性） | 0.8300 | 200/200 |
| Faithfulness（忠实度） | 0.9663 | 200/200 |
| P50端到端查询耗时 | 1.342秒 | 200 |
| P95端到端查询耗时 | 2.159秒 | 200 |
| 平均每题Token | 5499.69 | 200 |

P50和P95共同构成第五项速度指标。四项质量分、速度和Token不合成为总分。

## 完成与异常

| 状态 | 数量 |
|---|---:|
| success | 200 |
| abnormal_refusal | 0 |
| empty_answer | 0 |
| system_failure | 0 |
| unknown | 0 |

## 精确chunk命中

精确命中只比较 `ground_truth_chunk_ids` 与最终引用的 `chunk_uid`，不对RRF分数应用余弦阈值。

| 指标 | 结果 |
|---|---:|
| 可评题数 | 200 |
| 至少命中一个标准chunk | 173 / 200 |
| 全部标准chunk命中 | 157 / 200 |
| Macro Recall | 0.8250 |
| Micro Recall | 0.8222 |
| 首个命中平均排名 | 1.55 |

## 单跳/多跳分层

| 题型 | 数量 | Context Recall | Answer Correctness | P50 | P95 | 全chunk命中率 |
|---|---:|---:|---:|---:|---:|---:|
| multi_hop | 70 | 0.9246 | 0.8562 | 1.465秒 | 2.281秒 | 0.7000 |
| single_hop | 130 | 0.9568 | 0.8158 | 1.252秒 | 2.159秒 | 0.8308 |

## Token来源

- 系统Token总数：1099939
- 查询嵌入：8753（粗略估算）
- 回答模型：1091186（接口实际usage）
- 真实接口usage与估算次数：{"rough_estimate": 200, "api_actual": 200}
- 无法确认的输出次数：0
- Ragas裁判与评分嵌入Token：7366509（单独记录，不计入平均每题系统Token）

## 人工审核

本报告不设置自动及格线。请结合六项指标、异常数量和逐题结果决定是否进入下一阶段。
S2运行还会在同目录生成 paired_comparison.json 和 paired_comparison.md。
