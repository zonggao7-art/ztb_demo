# v2 小模型评测结果对比

# 表 1：v2 两个小模型 三指标横向对比（N≈195-200 抽样）

- 数据源：`scores_v2/{model}_summary.json`
- 评分模型：deepseek-chat（temperature=0.0）。
- 小模型与大模型跑的是不同样本集，不可直接对比绝对分。

| 模型 | 样本量 | Faithfulness | Answer-Relevancy | Answer-Correctness |
|---|---|---|---|---|
| qwen3.5_9b | 195 | 0.7785 | 0.7430 | 0.7290 |
| glm_z1_9b | 200 | 0.6187 | 0.7633 | 0.6695 |

# 表 2：v1 → v2 三指标前后对比（小模型）

- 数据源：`scores/{model}_summary.json`（v1）vs `scores_v2/{model}_summary.json`（v2）
- 「Δ」= v2 − v1；「变化幅度」= (v2 − v1) / v1 × 100%。正值 = 升，负值 = 降。
- v1 / v2 抽样量略有差异（qwen: 194→195, glm: 196→200），但同源题目集。

| 模型 | 指标 | v1 样本 | v2 样本 | v1 分数 | v2 分数 | Δ 绝对差 | 变化幅度 |
|---|---|---|---|---|---|---|---|
| qwen3.5_9b | faithfulness | 196 | 195 | 0.7267 | 0.7785 | +0.0518 | +7.13% |
| qwen3.5_9b | answer_relevancy | 196 | 195 | 0.7474 | 0.7430 | -0.0045 | -0.60% |
| qwen3.5_9b | answer_correctness | 196 | 195 | 0.7364 | 0.7290 | -0.0074 | -1.01% |
| glm_z1_9b | faithfulness | 196 | 200 | 0.3938 | 0.6187 | +0.2250 | +57.13% |
| glm_z1_9b | answer_relevancy | 196 | 200 | 0.7999 | 0.7633 | -0.0366 | -4.58% |
| glm_z1_9b | answer_correctness | 196 | 200 | 0.6009 | 0.6695 | +0.0686 | +11.41% |
