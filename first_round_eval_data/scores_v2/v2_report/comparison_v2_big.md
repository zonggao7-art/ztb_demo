# v2 大模型评测结果对比

# 表 1：v2 三个大模型 三指标横向对比（N=1000）

- 数据源：`scores_v2/{model}_summary.json`
- 评分模型：deepseek-chat（temperature=0.0）。deepseek_v4_flash 与裁判同源，标注「(自评)」。

| 模型 | 样本量 | Faithfulness | Answer-Relevancy | Answer-Correctness |
|---|---|---|---|---|
| deepseek_v4_flash (自评) | 1000 | 0.7476 | 0.7511 | 0.7523 |
| hy3 | 1000 | 0.7307 | 0.7035 | 0.7196 |
| minimax_m3 | 1000 | 0.7017 | 0.7569 | 0.7275 |

# 表 2：v1 → v2 三指标前后对比（大模型）

- 数据源：`scores/{model}_summary.json`（v1）vs `scores_v2/{model}_summary.json`（v2）
- 「Δ」= v2 − v1；「变化幅度」= (v2 − v1) / v1 × 100%。正值 = 升，负值 = 降。
- 三个模型使用同一套 N=1000 测试集。

| 模型 | 指标 | v1 分数 | v2 分数 | Δ 绝对差 | 变化幅度 |
|---|---|---|---|---|---|
| deepseek_v4_flash (自评) | faithfulness | 0.6971 | 0.7476 | +0.0504 | +7.24% |
| deepseek_v4_flash (自评) | answer_relevancy | 0.7447 | 0.7511 | +0.0064 | +0.85% |
| deepseek_v4_flash (自评) | answer_correctness | 0.7436 | 0.7523 | +0.0087 | +1.17% |
| hy3 | faithfulness | 0.7027 | 0.7307 | +0.0281 | +4.00% |
| hy3 | answer_relevancy | 0.7014 | 0.7035 | +0.0021 | +0.30% |
| hy3 | answer_correctness | 0.6860 | 0.7196 | +0.0335 | +4.89% |
| minimax_m3 | faithfulness | 0.6040 | 0.7017 | +0.0977 | +16.17% |
| minimax_m3 | answer_relevancy | 0.7713 | 0.7569 | -0.0144 | -1.86% |
| minimax_m3 | answer_correctness | 0.6595 | 0.7275 | +0.0680 | +10.31% |
