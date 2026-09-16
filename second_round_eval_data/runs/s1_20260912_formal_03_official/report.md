# 第二轮RAG测评报告

- 运行编号：s1_20260912_formal_03_official
- 检索阶段：s1
- 采集完成：是
- 评分完成：是
- 正式完成：是
- 已完成尝试：200/200

## 六项核心指标

| 指标 | 结果 | 有效数量 |
|---|---:|---:|
| Context Recall（检索内容覆盖率） | 0.8858 | 200/200 |
| Context Precision（检索内容精度） | 0.7568 | 200/200 |
| Answer Correctness（答案正确性） | 0.7508 | 200/200 |
| Faithfulness（忠实度） | 0.9516 | 200/200 |
| P50端到端查询耗时 | 1.177秒 | 200 |
| P95端到端查询耗时 | 2.047秒 | 200 |
| 平均每题回答模型Token（API实际usage） | 5407.97 | 200 |

P50和P95共同构成第五项速度指标。四项质量分、速度和资源消耗不合成为总分。资源消耗按组件分别报告，不把Reranker估算Token与回答模型实际Token作为同一核心指标累加。

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
| 至少命中一个标准chunk | 149 / 200 |
| 全部标准chunk命中 | 123 / 200 |
| Macro Recall | 0.6800 |
| Micro Recall | 0.6630 |
| 首个命中平均排名 | 1.76 |

## 单跳/多跳分层

| 题型 | 数量 | Context Recall | Answer Correctness | P50 | P95 | 全chunk命中率 |
|---|---:|---:|---:|---:|---:|---:|
| multi_hop | 70 | 0.8059 | 0.7485 | 1.365秒 | 2.121秒 | 0.4286 |
| single_hop | 130 | 0.9288 | 0.7520 | 1.082秒 | 1.961秒 | 0.7154 |

## Token与处理量来源

| 组件 | 总量 | 平均每题 | 统计口径 |
|---|---:|---:|---|
| 回答模型 | 1081594 | 5407.97 | API实际usage |
| Reranker输入 | 0 | 0.00 | 接口无usage时为粗略估算 |
| 查询嵌入 | 8753 | 43.77 | 粗略估算 |
| Ragas裁判与评分嵌入 | 7588892 | 不并入系统问答 | 独立评分消耗 |

- 混合系统处理量总计：1090347；平均每题：5451.73。
- 上述混合处理量包含API实际usage与粗略估算，只用于内部容量分析，不作为模型成本或Token效率的核心结论。
- 如需跨组件汇总，优先按各供应商真实计费规则换算为每题实际费用。
- 真实接口usage与估算次数：{"rough_estimate": 200, "api_actual": 200}。
- 无法确认的输出次数：0。

## 人工审核

本报告不设置自动及格线。请结合六项指标、异常数量和逐题结果决定是否进入下一阶段。
S2及后续阶段会在同目录生成 paired_comparison.json 和 paired_comparison.md。

## 修正版运行说明

本次使用当前S4冻结代码、S1检索配置和DeepSeek官网`deepseek-flash`重新测评。完整说明见[官网API重测报告](../../../docs/eval_docs/第二轮第一阶段S1官网API重测报告_20260912.md)。

- [修正版S1对现有S2](paired_comparison_official_s1_to_s2.md)
- [旧S1对修正版S1](paired_comparison_openrouter_s1_to_official_s1.md)
