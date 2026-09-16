# 二轮 RAG 测试集

**共 200 题：130 道单跳（65%），70 道多跳（35%）。已核验新 chunk；S1、S2、S3、S4 均已完成正式测评。2026-09-12已使用DeepSeek官网API完成修正版S1：200题采集和800项评分全部成功。**

- 正式题集：[testset_200.jsonl](testset_200.jsonl)。
- 人工查看：[200题审核版.md](200题审核版.md)，多跳题分别列出两块依据支持的答案要点。
- 来源与检查：[构建记录.json](构建记录.json)。
- chunk 核验报告：[testset_200_chunk_verification.json](testset_200_chunk_verification.json)。
- S1运行前检查：[preflight_report.json](preflight_report.json)（已通过，未调用模型接口）。
- 准备完成说明：[第二轮第一阶段测评准备完成报告](../docs/eval_docs/第二轮第一阶段测评准备完成报告_20260910.md)。
- 历史S1（OpenRouter）：[原报告](runs/s1_20260910_formal_02/report.md)、[原汇总](runs/s1_20260910_formal_02/summary.json)、[问题总结](../docs/eval_docs/第二轮第一阶段S1测评问题总结_20260910.md)。
- 修正版S1（DeepSeek官网）：[重测报告](../docs/eval_docs/第二轮第一阶段S1官网API重测报告_20260912.md)、[正式报告](runs/s1_20260912_formal_03_official/report.md)、[完整汇总](runs/s1_20260912_formal_03_official/summary.json)、[对现有S2比较](runs/s1_20260912_formal_03_official/paired_comparison_official_s1_to_s2.md)、[对历史S1比较](runs/s1_20260912_formal_03_official/paired_comparison_openrouter_s1_to_official_s1.md)。
- 修正版S1冻结证据：[运行计划](s1_official_run_plan.json)、[源码快照](s1_official_source_snapshot.json)、[离线预检](s1_official_preflight_report.json)、[采集复核](runs/s1_20260912_formal_03_official/collection_verification.json)、[最终结果快照](s1_official_baseline_snapshot.json)。
- S2准备完成：[准备报告](../docs/eval_docs/第二轮第二阶段S2测评准备完成报告_20260910.md)、[运行计划](s2_run_plan.json)、[最终预检](s2_preflight_report.json)。
- S1冻结证据：[s1_baseline_snapshot.json](s1_baseline_snapshot.json)。
- S2执行源码冻结：[s2_source_snapshot.json](s2_source_snapshot.json)；正式采集和评分前都会验证，`.env`与密钥不进入快照。
- S2真实预演：[summary.json](smoke_runs/s2_preflight_20260910_01/summary.json)（4题，仅验证链路，不进入正式成绩）。
- S2正式结果：[人工审核报告](runs/s2_20260910_formal_01/report.md)、[完整汇总](runs/s2_20260910_formal_01/summary.json)、[历史S1/S2配对比较](runs/s2_20260910_formal_01/paired_comparison.md)。修正版S1/S2比较保存在修正版S1目录，未覆盖历史文件。
- S3完成报告：[第二轮第三阶段S3测评报告](../docs/eval_docs/第二轮第三阶段S3测评准备完成报告_20260911.md)、[运行计划](s3_run_plan.json)、[最终结果](runs/s3_20260911_formal_02/report.md)。
- S4离线准备：[准备报告](../docs/eval_docs/第二轮第四阶段S4测评准备完成报告_20260911.md)、[运行计划](s4_run_plan.json)、[最终预检](s4_preflight_report.json)。
- S4冻结与诊断：[S3基线快照](s3_baseline_snapshot.json)、[S4源码快照](s4_source_snapshot.json)、[S3分数影子分析](s4_shadow_analysis.json)。首次付费预演的功能检查为4/4成功但样本身份标签无效，详见[身份审计](smoke_runs/s4_preflight_20260911_01/identity_audit.json)；[修正版预演复核](smoke_runs/s4_preflight_20260911_02/verification.json)已通过最终准备门禁且未调用Ragas。
- S4正式结果：[最终报告](runs/s4_20260911_formal_01/report.md)、[完整汇总](runs/s4_20260911_formal_01/summary.json)、[S3/S4配对比较](runs/s4_20260911_formal_01/paired_comparison.md)、[采集独立复核](runs/s4_20260911_formal_01/collection_verification.json)。200/200题采集成功，800/800项评分成功。
- S4评分过程：[首次审计](runs/s4_20260911_formal_01/scoring_interruption_audit.json)、[第二次审计](runs/s4_20260911_formal_01/scoring_interruption_audit_02.json)、[第三次审计](runs/s4_20260911_formal_01/scoring_interruption_audit_03.json)、[第四次审计](runs/s4_20260911_formal_01/scoring_interruption_audit_04.json)、[初次评分完成审计](runs/s4_20260911_formal_01/scoring_primary_pass_audit.json)、[失败指标恢复审计](runs/s4_20260911_formal_01/scoring_recovery_attempts/recovery_20260911T165549Z/attempts.jsonl)。初次评分768项成功、32项历史429失败；定向恢复32/32成功，未重算原成功项。

## 这次改了什么

原题集为 198 道单跳、2 道多跳。本次将其中 68 题改为两个新 chunk 的直接查找、对照或汇总题，其余 132 题不变，已审核的前 3 题也不变。

| 题型 | 数量 | 比例 | 所需标准来源 |
|---|---:|---:|---|
| 单跳 `single_hop` | 130 | 65% | 1 个新 chunk |
| 多跳 `multi_hop` | 70 | 35% | 2 个互补的新 chunk |
| 合计 | 200 | 100% | 270 次来源关联 |

多跳按新切分文件核对，不沿用旧 chunk 数量或旧标签。问题以直接查找规则为主，不增加复杂案例、计算或 ReAct 推理要求。配比与一轮一致，不代表两套题的实际难度已经测得相同。

## 题目和依据从哪里来

45 道问题原样复用一轮题目，8 道改写一轮题目；另 147 道基于新 chunk 编写，其中 79 道单跳、68 道多跳。旧题的具体来源是 `first_round_eval_data/testset_200.jsonl`；本次新增多跳题从《1200问》的当前 chunk 问答编写，未新增复用一轮 2000 题中的问题。

全部标准来源来自 `DATA/repaired_knowledge/book1—book3/documents.jsonl`，共涉及 **164 个不同的新 chunk**。每题的 ID 是 `metadata.chunk_uid`，正文保留对应 chunk 的完整原文。

## 五个字段怎么用

| 字段 | 内容 |
|---|---|
| `question` | 提交给检索系统的问题 |
| `reference` | 参考答案；新增多跳题分两部分列出 |
| `ground_truth_chunk_ids` | 新 `chunk_uid` 列表 |
| `ground_truth_text` | 与 ID 一一对应的完整新 chunk 正文 |
| `query_type` | `single_hop` 或 `multi_hop` |

运行时只提交 `question`；参考答案和标准来源留在评分侧。检索结果另存，题集中没有旧 chunk ID 或旧成绩。旧脚本如强制将 ID 转成整数，需要适配新字符串 ID。

跨块题应检查两部分依据是否找齐，不能命中其中一块就算完整命中。标注未穷举所有等价来源：检索出其他新 chunk 时，也应按实际内容判断是否覆盖答案。

已核验总数、配比、五字段格式、问题去重、ID 存在性、270 次来源正文逐字一致，以及 3 道审核样例保留。答案文字重合率和问题相似度只是复核线索，不等于正确率，也不是检索成绩。

## 测评命令

以下命令均从项目根目录执行。正式采集固定按题集顺序逐题调用完整的 `knowledge_qa`，不启动 ReAct。

```powershell
# 不调用付费接口：检查题集、S1参数、模型变量、Ragas构造和知识库连接
.\.venv-eval\Scripts\python.exe scripts\run_rag_eval.py validate --stage s1 --check-kb

# 不生成文本：完整检查S2题集、基线、模型列表、BM25/RRF、索引和知识库
.\.venv-eval\Scripts\python.exe scripts\run_rag_eval.py validate --stage s2 --check-kb --check-provider

# 不生成文本：完整检查S4题集、S3基线、动态过滤、模型列表和知识库
.\.venv-eval\Scripts\python.exe scripts\run_rag_eval.py validate --stage s4 --check-kb --check-provider

# 正式采集：只有人工决定启动后才执行
.\.venv-eval\Scripts\python.exe scripts\run_rag_eval.py collect --stage s1 --run-id s1_YYYYMMDD --confirm-live-run

# Ragas评分：采集完成后单独执行
.\.venv-eval\Scripts\python.exe scripts\run_rag_eval.py score --run-dir second_round_eval_data\runs\s1_YYYYMMDD --confirm-paid-scoring

# 不调用付费接口：根据已有文件重建报告
.\.venv-eval\Scripts\python.exe scripts\run_rag_eval.py report --run-dir second_round_eval_data\runs\s1_YYYYMMDD

# S2正式采集与评分（当前预留运行编号）
.\.venv-eval\Scripts\python.exe scripts\run_rag_eval.py collect --stage s2 --run-id s2_20260910_formal_01 --confirm-live-run
.\.venv-eval\Scripts\python.exe scripts\run_rag_eval.py score --run-dir second_round_eval_data\runs\s2_20260910_formal_01 --confirm-paid-scoring

# S4四题修正版真实预演：已执行并通过，不运行Ragas
.\.venv-eval\Scripts\python.exe scripts\run_rag_eval.py smoke-s4 --run-id s4_preflight_20260911_02 --confirm-live-run

# S4正式采集：已执行，200/200成功
.\.venv-eval\Scripts\python.exe scripts\run_rag_eval.py collect --stage s4 --run-id s4_20260911_formal_01 --confirm-live-run

# S4初次正式评分：已完成200行；不要再次运行score
.\.venv-eval\Scripts\python.exe scripts\run_rag_eval.py score --run-dir second_round_eval_data\runs\s4_20260911_formal_01 --confirm-paid-scoring

# S4失败指标补偿：已完成32/32，未重算768个成功指标
.\.venv-eval\Scripts\python.exe scripts\run_rag_eval.py recover-scores --run-dir second_round_eval_data\runs\s4_20260911_formal_01 --confirm-paid-recovery
```

每次运行保存 `manifest.json`、`results.jsonl`、`scores.jsonl`、`summary.json`、`report.md` 和独立的评分Token记录。S2还会保存精确chunk诊断、单跳/多跳分层以及 `paired_comparison.json` / `paired_comparison.md`。结果目录不会覆盖已有运行；采集和评分命令都要求显式确认参数，避免误调用真实接口。
