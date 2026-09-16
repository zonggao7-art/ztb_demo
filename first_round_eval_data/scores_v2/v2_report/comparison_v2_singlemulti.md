# v2 阶段 单跳 vs 多跳 答题情况

# 表 A：v2 三个大模型 单跳 vs 多跳 答题情况（N=1000）

- 数据源：`scores_v2/{model}_scores.jsonl`，按 `query_type` 分组后取均值。
- 「衰减」= (single − multi) / single × 100%，正值代表单跳更强。
- deepseek_v4_flash 与裁判同源（自评），绝对分仅供参考，差值相对稳健。

| 模型 | Single Faith | Single Rel | Single Corr | Multi Faith | Multi Rel | Multi Corr | Faith 衰减 | Rel 衰减 | Corr 衰减 | single_n | multi_n |
|---|---|---|---|---|---|---|---|---|---|---|---|
| deepseek_v4_flash (自评) | 0.8228 | 0.7950 | 0.7962 | 0.6079 | 0.6695 | 0.6705 | +26.1% | +15.8% | +15.8% | 650 | 350 |
| hy3 | 0.8113 | 0.7683 | 0.7820 | 0.5821 | 0.5839 | 0.6049 | +28.2% | +24.0% | +22.6% | 650 | 350 |
| minimax_m3 | 0.7744 | 0.8120 | 0.7622 | 0.5666 | 0.6548 | 0.6635 | +26.8% | +19.4% | +12.9% | 649 | 350 |

# 表 B：v2 两个小模型 单跳 vs 多跳 答题情况（N≈195-200 抽样）

- 数据源：`scores_v2/{model}_scores.jsonl`，按 `query_type` 分组后取均值。
- 「衰减」= (single − multi) / single × 100%，正值代表单跳更强。

| 模型 | Single Faith | Single Rel | Single Corr | Multi Faith | Multi Rel | Multi Corr | Faith 衰减 | Rel 衰减 | Corr 衰减 | single_n | multi_n |
|---|---|---|---|---|---|---|---|---|---|---|---|
| qwen3.5_9b | 0.8281 | 0.7675 | 0.7740 | 0.6830 | 0.6958 | 0.6425 | +17.5% | +9.3% | +17.0% | 127 | 66 |
| glm_z1_9b | 0.7056 | 0.8048 | 0.6959 | 0.4579 | 0.6864 | 0.6198 | +35.1% | +14.7% | +10.9% | 126 | 68 |
