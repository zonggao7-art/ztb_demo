# v1 小模型评测结果对比（生成阶段 · 三表拆分版）

# 表 A：三个质量指标对比（v1，小模型 N=196 抽样）

- 数据源：`scores/{qwen3.5_9b,glm_z1_9b}_scores.jsonl`
- 评分模型：deepseek-chat（temperature=0.0）。
- 小模型与大模型跑的是不同样本集（抽样 196 题），不可直接对比绝对分。

| 模型 | 样本量 | Faithfulness | Answer-Relevancy | Answer-Correctness |
|---|---|---|---|---|
| qwen3.5_9b | 194 | 0.7267 | 0.7474 | 0.7364 |
| glm_z1_9b | 196 | 0.3938 | 0.7999 | 0.6009 |

# 表 B：性能指标对比（精简版，v1，小模型 N=196 抽样）

- 数据源：`scores/{model}_summary.json`（由生成阶段 `model_results_small/` 聚合）
- 已剔除 token、成本、所有 P 分位、平均答案长度等列。

| 模型 | TTFT 均值(s) | 总时长 均值(s) | 吞吐(tok/s) | 失败率 | 拒答率 |
|---|---|---|---|---|---|
| qwen3.5_9b | 0.8650 | 71.6551 | 41.10 | 1.02% | 0.00% |
| glm_z1_9b | 0.3493 | 13.5323 | 60.90 | 0.00% | 0.00% |

# 表 C：单跳 vs 多跳 三个指标对比（v1，小模型 N=196 抽样）

- 数据源：`scores/{model}_scores.jsonl`，按 `query_type` groupby。
- 「衰减幅度」= (single − multi) / single × 100%，正值代表单跳更强。

| 模型 | Single Faith | Single Rel | Single Corr | Multi Faith | Multi Rel | Multi Corr | Faith 衰减 | Rel 衰减 | Corr 衰减 |
|---|---|---|---|---|---|---|---|---|---|
| qwen3.5_9b | 0.7894 | 0.8311 | 0.7850 | 0.6166 | 0.6025 | 0.6511 | +21.9% | +27.5% | +17.1% |
| glm_z1_9b | 0.4394 | 0.8126 | 0.5953 | 0.3060 | 0.7756 | 0.6117 | +30.4% | +4.5% | -2.8% |
