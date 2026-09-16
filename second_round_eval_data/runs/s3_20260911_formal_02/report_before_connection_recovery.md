# 第二轮RAG测评报告

- 运行编号：s3_20260911_formal_02
- 检索阶段：s3
- 采集完成：是
- 评分完成：是
- 正式完成：是
- 已完成尝试：200/200

## 六项核心指标

| 指标 | 结果 | 有效数量 |
|---|---:|---:|
| Context Recall（检索内容覆盖率） | 0.9886 | 188/200 |
| Context Precision（检索内容精度） | 0.8919 | 188/200 |
| Answer Correctness（答案正确性） | 0.8287 | 188/200 |
| Faithfulness（忠实度） | 0.9717 | 188/200 |
| P50端到端查询耗时 | 2.402秒 | 200 |
| P95端到端查询耗时 | 3.651秒 | 200 |
| 平均每题Token | 57186.03 | 200 |

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
| 至少命中一个标准chunk | 194 / 200 |
| 全部标准chunk命中 | 183 / 200 |
| Macro Recall | 0.9425 |
| Micro Recall | 0.9296 |
| 首个命中平均排名 | 1.44 |

## 单跳/多跳分层

| 题型 | 数量 | Context Recall | Answer Correctness | P50 | P95 | 全chunk命中率 |
|---|---:|---:|---:|---:|---:|---:|
| multi_hop | 70 | 0.9704 | 0.8515 | 2.448秒 | 3.257秒 | 0.8143 |
| single_hop | 130 | 0.9980 | 0.8170 | 2.360秒 | 3.711秒 | 0.9692 |

## 请求节流

- 最小题目启动间隔：10.0秒
- 实际累计等待：1483.327秒
- 实测最小启动间隔：9.996秒
- 节流等待不计入单题knowledge_qa耗时，逐题记录在throttle_wait_s。

## Token来源

- 系统Token总数：11437205
- 查询嵌入：8753（粗略估算）
- Reranker输入：10331645（接口无usage时为粗略估算）
- 回答模型：1096807（接口实际usage）
- 真实接口usage与估算次数：{"rough_estimate": 400, "api_actual": 200}
- 无法确认的输出次数：0
- Ragas裁判与评分嵌入Token：7222539（单独记录，不计入平均每题系统Token）

## 人工审核

本报告不设置自动及格线。请结合六项指标、异常数量和逐题结果决定是否进入下一阶段。
S2及后续阶段会在同目录生成 paired_comparison.json 和 paired_comparison.md。
## Reranker观测

- 有轨迹题数：200
- 结果状态：{"success": 200}
- Reranker P50：0.798秒
- Reranker P95：1.053秒
- 轨迹只含chunk_uid、重排前后名次和分数，不含候选正文。

