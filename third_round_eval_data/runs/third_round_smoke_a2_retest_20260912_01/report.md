# 第三轮统一主 Agent 测评报告

> 运行编号：`third_round_smoke_a2_retest_20260912_01`  
> 运行类型：`smoke`  
> 题集：`third_round_unified_agent_20260912_reviewed_01`  
> 阈值集：`third_round_formal_thresholds_20260912_v1`

## 一、运行完整性

- 计划主集尝试：3
- 已记录主集结果：3
- 主集采集完整：True
- Ragas评分行：0
- 评分器与采集计划一致：True
- 主集判定：`incomplete`
- 可靠性判定：`not_run`

## 二、核心指标

| 轮次 | 指标 | 结果 | 门槛 | 判定 | 分子/分母 |
|---:|---|---:|---:|---:|---:|
| 1 | A1 | 0 | =0 | True | 0/1 |
| 1 | A2 | 1.0000 | ≥98.00% | True | 3.0000/3.0000 |
| 1 | A3 | 1.0000 | ≥98.00% | True | 1.0000/1.0000 |
| 1 | A4 | 0.0000 | ≤2.00% | True | 0.0000/2.0000 |
| 1 | A5 | 1.0000 | ≥99.00% | True | 2.0000/2.0000 |
| 1 | A6 | N/A | =100.00% | N/A | 0.0000/0.0000 |
| 1 | A7 | 1.0000 | ≥98.00% | True | 1.0000/1.0000 |
| 1 | A8 | 1.0000 | ≥95.00% | True | 1.0000/1.0000 |
| 1 | A9 | 1.0000 | =100.00% | True | 35.0000/35.0000 |
| 1 | A10 | N/A | ≥98.00% | N/A | 0.0000/0.0000 |
| 2 | A1 | 0 | =0 | True | 0/1 |
| 2 | A2 | 1.0000 | ≥98.00% | True | 3.0000/3.0000 |
| 2 | A3 | 1.0000 | ≥98.00% | True | 1.0000/1.0000 |
| 2 | A4 | 0.0000 | ≤2.00% | True | 0.0000/2.0000 |
| 2 | A5 | 1.0000 | ≥99.00% | True | 2.0000/2.0000 |
| 2 | A6 | N/A | =100.00% | N/A | 0.0000/0.0000 |
| 2 | A7 | 1.0000 | ≥98.00% | True | 1.0000/1.0000 |
| 2 | A8 | 1.0000 | ≥95.00% | True | 1.0000/1.0000 |
| 2 | A9 | 1.0000 | =100.00% | True | 35.0000/35.0000 |
| 2 | A10 | N/A | ≥98.00% | N/A | 0.0000/0.0000 |
| 3 | A1 | 0 | =0 | True | 0/1 |
| 3 | A2 | 1.0000 | ≥98.00% | True | 3.0000/3.0000 |
| 3 | A3 | 1.0000 | ≥98.00% | True | 1.0000/1.0000 |
| 3 | A4 | 0.0000 | ≤2.00% | True | 0.0000/2.0000 |
| 3 | A5 | 1.0000 | ≥99.00% | True | 2.0000/2.0000 |
| 3 | A6 | N/A | =100.00% | N/A | 0.0000/0.0000 |
| 3 | A7 | 1.0000 | ≥98.00% | True | 1.0000/1.0000 |
| 3 | A8 | 1.0000 | ≥95.00% | True | 1.0000/1.0000 |
| 3 | A9 | 1.0000 | =100.00% | True | 35.0000/35.0000 |
| 3 | A10 | N/A | ≥98.00% | N/A | 0.0000/0.0000 |

## 三、RAG与SQL子集

| 轮次 | 指标 | 结果 | 门槛 | 判定 |
|---:|---|---:|---:|---:|
| 1 | B1 | N/A | ≥95.00% | N/A |
| 1 | B2 | N/A | ≥85.00% | N/A |
| 1 | B3 | N/A | ≥80.00% | N/A |
| 1 | B4 | N/A | ≥95.00% | N/A |
| 1 | B5 | 1.0000 | ≥88.00% | True |
| 1 | B6 | 1.0000 | ≥87.00% | True |
| 1 | C1 | 1.0000 | ≥99.00% | True |
| 1 | C2 | 1.0000 | =100.00% | True |
| 1 | C3 | 1.0000 | ≥98.00% | True |
| 1 | C4 | 1.0000 | =100.00% | True |
| 1 | C5 | 1.0000 | =100.00% | True |
| 2 | B1 | N/A | ≥95.00% | N/A |
| 2 | B2 | N/A | ≥85.00% | N/A |
| 2 | B3 | N/A | ≥80.00% | N/A |
| 2 | B4 | N/A | ≥95.00% | N/A |
| 2 | B5 | 1.0000 | ≥88.00% | True |
| 2 | B6 | 1.0000 | ≥87.00% | True |
| 2 | C1 | 1.0000 | ≥99.00% | True |
| 2 | C2 | 1.0000 | =100.00% | True |
| 2 | C3 | 1.0000 | ≥98.00% | True |
| 2 | C4 | 1.0000 | =100.00% | True |
| 2 | C5 | 1.0000 | =100.00% | True |
| 3 | B1 | N/A | ≥95.00% | N/A |
| 3 | B2 | N/A | ≥85.00% | N/A |
| 3 | B3 | N/A | ≥80.00% | N/A |
| 3 | B4 | N/A | ≥95.00% | N/A |
| 3 | B5 | 1.0000 | ≥88.00% | True |
| 3 | B6 | 1.0000 | ≥87.00% | True |
| 3 | C1 | 1.0000 | ≥99.00% | True |
| 3 | C2 | 1.0000 | =100.00% | True |
| 3 | C3 | 1.0000 | ≥98.00% | True |
| 3 | C4 | 1.0000 | =100.00% | True |
| 3 | C5 | 1.0000 | =100.00% | True |

## 四、效率观察项

- 首次运行策略：`report_only_not_a_blocking_gate`
- 完整请求 P50：4.6250 秒
- 完整请求 P95：9.5327 秒
- 首次状态反馈 P50/P95：0.0000 / 0.0000 秒
- 首段可交付答案 P50/P95：4.6250 / 9.5327 秒
- 平均模型调用：4.0000
- 平均工具尝试：2.0000
- 平均工具执行：2.0000
- 平均被拒绝工具尝试：0.0000
- 平均模型纠错：0.0000
- D5在线成本：`not_available_from_current_unified_contract`

### D4 分阶段耗时

| 阶段 | 样本数 | P50（秒） | P95（秒） |
|---|---:|---:|---:|
| agent_loop | 3 | 4.6250 | 9.5327 |
| tool:knowledge_qa | 3 | 1.7030 | 6.8501 |
| tool:query_company_award_history | 3 | 0.0150 | 0.0717 |
| validation | 3 | 0.0000 | 0.0000 |

## 五、强制分组

以下为原始分子/分母；正式门槛作用于每次完整正式运行，不自动作用于每个子组。

| 维度 | 分组 | 题数 | 指标 | 结果 | 分子/分母或有效数 |
|---|---|---:|---|---:|---:|
| authorized_tool | knowledge_qa | 3 | A1 | 0 | 0/1 |
| authorized_tool | knowledge_qa | 3 | A2 | 1.0000 | 9.0000/9.0000 |
| authorized_tool | knowledge_qa | 3 | A3 | 1.0000 | 3.0000/3.0000 |
| authorized_tool | knowledge_qa | 3 | A4 | 0.0000 | 0.0000/6.0000 |
| authorized_tool | knowledge_qa | 3 | A5 | 1.0000 | 6.0000/6.0000 |
| authorized_tool | knowledge_qa | 3 | A7 | 1.0000 | 3.0000/3.0000 |
| authorized_tool | knowledge_qa | 3 | A8 | 1.0000 | 3.0000/3.0000 |
| authorized_tool | knowledge_qa | 3 | A9 | 1.0000 | 105.0000/105.0000 |
| authorized_tool | knowledge_qa | 3 | B1 | N/A | 0/3 |
| authorized_tool | knowledge_qa | 3 | B2 | N/A | 0/3 |
| authorized_tool | knowledge_qa | 3 | B3 | N/A | 0/3 |
| authorized_tool | knowledge_qa | 3 | B4 | N/A | 0/3 |
| authorized_tool | knowledge_qa | 3 | B5 | 1.0000 | 3.0000/3.0000 |
| authorized_tool | knowledge_qa | 3 | B6 | 1.0000 | 3.0000/3.0000 |
| authorized_tool | knowledge_qa | 3 | C1 | 1.0000 | 3.0000/3.0000 |
| authorized_tool | knowledge_qa | 3 | C2 | 1.0000 | 90.0000/90.0000 |
| authorized_tool | knowledge_qa | 3 | C3 | 1.0000 | 90.0000/90.0000 |
| authorized_tool | knowledge_qa | 3 | C4 | 1.0000 | 3.0000/3.0000 |
| authorized_tool | knowledge_qa | 3 | C5 | 1.0000 | 3.0000/3.0000 |
| authorized_tool | query_company_award_history | 3 | A1 | 0 | 0/1 |
| authorized_tool | query_company_award_history | 3 | A2 | 1.0000 | 9.0000/9.0000 |
| authorized_tool | query_company_award_history | 3 | A3 | 1.0000 | 3.0000/3.0000 |
| authorized_tool | query_company_award_history | 3 | A4 | 0.0000 | 0.0000/6.0000 |
| authorized_tool | query_company_award_history | 3 | A5 | 1.0000 | 6.0000/6.0000 |
| authorized_tool | query_company_award_history | 3 | A7 | 1.0000 | 3.0000/3.0000 |
| authorized_tool | query_company_award_history | 3 | A8 | 1.0000 | 3.0000/3.0000 |
| authorized_tool | query_company_award_history | 3 | A9 | 1.0000 | 105.0000/105.0000 |
| authorized_tool | query_company_award_history | 3 | B1 | N/A | 0/3 |
| authorized_tool | query_company_award_history | 3 | B2 | N/A | 0/3 |
| authorized_tool | query_company_award_history | 3 | B3 | N/A | 0/3 |
| authorized_tool | query_company_award_history | 3 | B4 | N/A | 0/3 |
| authorized_tool | query_company_award_history | 3 | B5 | 1.0000 | 3.0000/3.0000 |
| authorized_tool | query_company_award_history | 3 | B6 | 1.0000 | 3.0000/3.0000 |
| authorized_tool | query_company_award_history | 3 | C1 | 1.0000 | 3.0000/3.0000 |
| authorized_tool | query_company_award_history | 3 | C2 | 1.0000 | 90.0000/90.0000 |
| authorized_tool | query_company_award_history | 3 | C3 | 1.0000 | 90.0000/90.0000 |
| authorized_tool | query_company_award_history | 3 | C4 | 1.0000 | 3.0000/3.0000 |
| authorized_tool | query_company_award_history | 3 | C5 | 1.0000 | 3.0000/3.0000 |
| case_category | sql_rag_cross_capability | 3 | A1 | 0 | 0/1 |
| case_category | sql_rag_cross_capability | 3 | A2 | 1.0000 | 9.0000/9.0000 |
| case_category | sql_rag_cross_capability | 3 | A3 | 1.0000 | 3.0000/3.0000 |
| case_category | sql_rag_cross_capability | 3 | A4 | 0.0000 | 0.0000/6.0000 |
| case_category | sql_rag_cross_capability | 3 | A5 | 1.0000 | 6.0000/6.0000 |
| case_category | sql_rag_cross_capability | 3 | A7 | 1.0000 | 3.0000/3.0000 |
| case_category | sql_rag_cross_capability | 3 | A8 | 1.0000 | 3.0000/3.0000 |
| case_category | sql_rag_cross_capability | 3 | A9 | 1.0000 | 105.0000/105.0000 |
| case_category | sql_rag_cross_capability | 3 | B1 | N/A | 0/3 |
| case_category | sql_rag_cross_capability | 3 | B2 | N/A | 0/3 |
| case_category | sql_rag_cross_capability | 3 | B3 | N/A | 0/3 |
| case_category | sql_rag_cross_capability | 3 | B4 | N/A | 0/3 |
| case_category | sql_rag_cross_capability | 3 | B5 | 1.0000 | 3.0000/3.0000 |
| case_category | sql_rag_cross_capability | 3 | B6 | 1.0000 | 3.0000/3.0000 |
| case_category | sql_rag_cross_capability | 3 | C1 | 1.0000 | 3.0000/3.0000 |
| case_category | sql_rag_cross_capability | 3 | C2 | 1.0000 | 90.0000/90.0000 |
| case_category | sql_rag_cross_capability | 3 | C3 | 1.0000 | 90.0000/90.0000 |
| case_category | sql_rag_cross_capability | 3 | C4 | 1.0000 | 3.0000/3.0000 |
| case_category | sql_rag_cross_capability | 3 | C5 | 1.0000 | 3.0000/3.0000 |
| finish_status | complete | 3 | A1 | 0 | 0/1 |
| finish_status | complete | 3 | A2 | 1.0000 | 9.0000/9.0000 |
| finish_status | complete | 3 | A3 | 1.0000 | 3.0000/3.0000 |
| finish_status | complete | 3 | A4 | 0.0000 | 0.0000/6.0000 |
| finish_status | complete | 3 | A5 | 1.0000 | 6.0000/6.0000 |
| finish_status | complete | 3 | A7 | 1.0000 | 3.0000/3.0000 |
| finish_status | complete | 3 | A8 | 1.0000 | 3.0000/3.0000 |
| finish_status | complete | 3 | A9 | 1.0000 | 105.0000/105.0000 |
| finish_status | complete | 3 | B1 | N/A | 0/3 |
| finish_status | complete | 3 | B2 | N/A | 0/3 |
| finish_status | complete | 3 | B3 | N/A | 0/3 |
| finish_status | complete | 3 | B4 | N/A | 0/3 |
| finish_status | complete | 3 | B5 | 1.0000 | 3.0000/3.0000 |
| finish_status | complete | 3 | B6 | 1.0000 | 3.0000/3.0000 |
| finish_status | complete | 3 | C1 | 1.0000 | 3.0000/3.0000 |
| finish_status | complete | 3 | C2 | 1.0000 | 90.0000/90.0000 |
| finish_status | complete | 3 | C3 | 1.0000 | 90.0000/90.0000 |
| finish_status | complete | 3 | C4 | 1.0000 | 3.0000/3.0000 |
| finish_status | complete | 3 | C5 | 1.0000 | 3.0000/3.0000 |
| formal_run | repeat_1 | 1 | A1 | 0 | 0/1 |
| formal_run | repeat_1 | 1 | A2 | 1.0000 | 3.0000/3.0000 |
| formal_run | repeat_1 | 1 | A3 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_1 | 1 | A4 | 0.0000 | 0.0000/2.0000 |
| formal_run | repeat_1 | 1 | A5 | 1.0000 | 2.0000/2.0000 |
| formal_run | repeat_1 | 1 | A7 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_1 | 1 | A8 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_1 | 1 | A9 | 1.0000 | 35.0000/35.0000 |
| formal_run | repeat_1 | 1 | B1 | N/A | 0/1 |
| formal_run | repeat_1 | 1 | B2 | N/A | 0/1 |
| formal_run | repeat_1 | 1 | B3 | N/A | 0/1 |
| formal_run | repeat_1 | 1 | B4 | N/A | 0/1 |
| formal_run | repeat_1 | 1 | B5 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_1 | 1 | B6 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_1 | 1 | C1 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_1 | 1 | C2 | 1.0000 | 30.0000/30.0000 |
| formal_run | repeat_1 | 1 | C3 | 1.0000 | 30.0000/30.0000 |
| formal_run | repeat_1 | 1 | C4 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_1 | 1 | C5 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_2 | 1 | A1 | 0 | 0/1 |
| formal_run | repeat_2 | 1 | A2 | 1.0000 | 3.0000/3.0000 |
| formal_run | repeat_2 | 1 | A3 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_2 | 1 | A4 | 0.0000 | 0.0000/2.0000 |
| formal_run | repeat_2 | 1 | A5 | 1.0000 | 2.0000/2.0000 |
| formal_run | repeat_2 | 1 | A7 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_2 | 1 | A8 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_2 | 1 | A9 | 1.0000 | 35.0000/35.0000 |
| formal_run | repeat_2 | 1 | B1 | N/A | 0/1 |
| formal_run | repeat_2 | 1 | B2 | N/A | 0/1 |
| formal_run | repeat_2 | 1 | B3 | N/A | 0/1 |
| formal_run | repeat_2 | 1 | B4 | N/A | 0/1 |
| formal_run | repeat_2 | 1 | B5 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_2 | 1 | B6 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_2 | 1 | C1 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_2 | 1 | C2 | 1.0000 | 30.0000/30.0000 |
| formal_run | repeat_2 | 1 | C3 | 1.0000 | 30.0000/30.0000 |
| formal_run | repeat_2 | 1 | C4 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_2 | 1 | C5 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_3 | 1 | A1 | 0 | 0/1 |
| formal_run | repeat_3 | 1 | A2 | 1.0000 | 3.0000/3.0000 |
| formal_run | repeat_3 | 1 | A3 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_3 | 1 | A4 | 0.0000 | 0.0000/2.0000 |
| formal_run | repeat_3 | 1 | A5 | 1.0000 | 2.0000/2.0000 |
| formal_run | repeat_3 | 1 | A7 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_3 | 1 | A8 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_3 | 1 | A9 | 1.0000 | 35.0000/35.0000 |
| formal_run | repeat_3 | 1 | B1 | N/A | 0/1 |
| formal_run | repeat_3 | 1 | B2 | N/A | 0/1 |
| formal_run | repeat_3 | 1 | B3 | N/A | 0/1 |
| formal_run | repeat_3 | 1 | B4 | N/A | 0/1 |
| formal_run | repeat_3 | 1 | B5 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_3 | 1 | B6 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_3 | 1 | C1 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_3 | 1 | C2 | 1.0000 | 30.0000/30.0000 |
| formal_run | repeat_3 | 1 | C3 | 1.0000 | 30.0000/30.0000 |
| formal_run | repeat_3 | 1 | C4 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_3 | 1 | C5 | 1.0000 | 1.0000/1.0000 |
| normal_adversarial_or_fault | normal | 3 | A1 | 0 | 0/1 |
| normal_adversarial_or_fault | normal | 3 | A2 | 1.0000 | 9.0000/9.0000 |
| normal_adversarial_or_fault | normal | 3 | A3 | 1.0000 | 3.0000/3.0000 |
| normal_adversarial_or_fault | normal | 3 | A4 | 0.0000 | 0.0000/6.0000 |
| normal_adversarial_or_fault | normal | 3 | A5 | 1.0000 | 6.0000/6.0000 |
| normal_adversarial_or_fault | normal | 3 | A7 | 1.0000 | 3.0000/3.0000 |
| normal_adversarial_or_fault | normal | 3 | A8 | 1.0000 | 3.0000/3.0000 |
| normal_adversarial_or_fault | normal | 3 | A9 | 1.0000 | 105.0000/105.0000 |
| normal_adversarial_or_fault | normal | 3 | B1 | N/A | 0/3 |
| normal_adversarial_or_fault | normal | 3 | B2 | N/A | 0/3 |
| normal_adversarial_or_fault | normal | 3 | B3 | N/A | 0/3 |
| normal_adversarial_or_fault | normal | 3 | B4 | N/A | 0/3 |
| normal_adversarial_or_fault | normal | 3 | B5 | 1.0000 | 3.0000/3.0000 |
| normal_adversarial_or_fault | normal | 3 | B6 | 1.0000 | 3.0000/3.0000 |
| normal_adversarial_or_fault | normal | 3 | C1 | 1.0000 | 3.0000/3.0000 |
| normal_adversarial_or_fault | normal | 3 | C2 | 1.0000 | 90.0000/90.0000 |
| normal_adversarial_or_fault | normal | 3 | C3 | 1.0000 | 90.0000/90.0000 |
| normal_adversarial_or_fault | normal | 3 | C4 | 1.0000 | 3.0000/3.0000 |
| normal_adversarial_or_fault | normal | 3 | C5 | 1.0000 | 3.0000/3.0000 |
| normal_empty_or_failure | normal_result | 3 | A1 | 0 | 0/1 |
| normal_empty_or_failure | normal_result | 3 | A2 | 1.0000 | 9.0000/9.0000 |
| normal_empty_or_failure | normal_result | 3 | A3 | 1.0000 | 3.0000/3.0000 |
| normal_empty_or_failure | normal_result | 3 | A4 | 0.0000 | 0.0000/6.0000 |
| normal_empty_or_failure | normal_result | 3 | A5 | 1.0000 | 6.0000/6.0000 |
| normal_empty_or_failure | normal_result | 3 | A7 | 1.0000 | 3.0000/3.0000 |
| normal_empty_or_failure | normal_result | 3 | A8 | 1.0000 | 3.0000/3.0000 |
| normal_empty_or_failure | normal_result | 3 | A9 | 1.0000 | 105.0000/105.0000 |
| normal_empty_or_failure | normal_result | 3 | B1 | N/A | 0/3 |
| normal_empty_or_failure | normal_result | 3 | B2 | N/A | 0/3 |
| normal_empty_or_failure | normal_result | 3 | B3 | N/A | 0/3 |
| normal_empty_or_failure | normal_result | 3 | B4 | N/A | 0/3 |
| normal_empty_or_failure | normal_result | 3 | B5 | 1.0000 | 3.0000/3.0000 |
| normal_empty_or_failure | normal_result | 3 | B6 | 1.0000 | 3.0000/3.0000 |
| normal_empty_or_failure | normal_result | 3 | C1 | 1.0000 | 3.0000/3.0000 |
| normal_empty_or_failure | normal_result | 3 | C2 | 1.0000 | 90.0000/90.0000 |
| normal_empty_or_failure | normal_result | 3 | C3 | 1.0000 | 90.0000/90.0000 |
| normal_empty_or_failure | normal_result | 3 | C4 | 1.0000 | 3.0000/3.0000 |
| normal_empty_or_failure | normal_result | 3 | C5 | 1.0000 | 3.0000/3.0000 |
| rag_single_or_multi_hop | single_hop | 3 | A1 | 0 | 0/1 |
| rag_single_or_multi_hop | single_hop | 3 | A2 | 1.0000 | 9.0000/9.0000 |
| rag_single_or_multi_hop | single_hop | 3 | A3 | 1.0000 | 3.0000/3.0000 |
| rag_single_or_multi_hop | single_hop | 3 | A4 | 0.0000 | 0.0000/6.0000 |
| rag_single_or_multi_hop | single_hop | 3 | A5 | 1.0000 | 6.0000/6.0000 |
| rag_single_or_multi_hop | single_hop | 3 | A7 | 1.0000 | 3.0000/3.0000 |
| rag_single_or_multi_hop | single_hop | 3 | A8 | 1.0000 | 3.0000/3.0000 |
| rag_single_or_multi_hop | single_hop | 3 | A9 | 1.0000 | 105.0000/105.0000 |
| rag_single_or_multi_hop | single_hop | 3 | B1 | N/A | 0/3 |
| rag_single_or_multi_hop | single_hop | 3 | B2 | N/A | 0/3 |
| rag_single_or_multi_hop | single_hop | 3 | B3 | N/A | 0/3 |
| rag_single_or_multi_hop | single_hop | 3 | B4 | N/A | 0/3 |
| rag_single_or_multi_hop | single_hop | 3 | B5 | 1.0000 | 3.0000/3.0000 |
| rag_single_or_multi_hop | single_hop | 3 | B6 | 1.0000 | 3.0000/3.0000 |
| rag_single_or_multi_hop | single_hop | 3 | C1 | 1.0000 | 3.0000/3.0000 |
| rag_single_or_multi_hop | single_hop | 3 | C2 | 1.0000 | 90.0000/90.0000 |
| rag_single_or_multi_hop | single_hop | 3 | C3 | 1.0000 | 90.0000/90.0000 |
| rag_single_or_multi_hop | single_hop | 3 | C4 | 1.0000 | 3.0000/3.0000 |
| rag_single_or_multi_hop | single_hop | 3 | C5 | 1.0000 | 3.0000/3.0000 |
| single_or_multi_entity | single_entity | 3 | A1 | 0 | 0/1 |
| single_or_multi_entity | single_entity | 3 | A2 | 1.0000 | 9.0000/9.0000 |
| single_or_multi_entity | single_entity | 3 | A3 | 1.0000 | 3.0000/3.0000 |
| single_or_multi_entity | single_entity | 3 | A4 | 0.0000 | 0.0000/6.0000 |
| single_or_multi_entity | single_entity | 3 | A5 | 1.0000 | 6.0000/6.0000 |
| single_or_multi_entity | single_entity | 3 | A7 | 1.0000 | 3.0000/3.0000 |
| single_or_multi_entity | single_entity | 3 | A8 | 1.0000 | 3.0000/3.0000 |
| single_or_multi_entity | single_entity | 3 | A9 | 1.0000 | 105.0000/105.0000 |
| single_or_multi_entity | single_entity | 3 | B1 | N/A | 0/3 |
| single_or_multi_entity | single_entity | 3 | B2 | N/A | 0/3 |
| single_or_multi_entity | single_entity | 3 | B3 | N/A | 0/3 |
| single_or_multi_entity | single_entity | 3 | B4 | N/A | 0/3 |
| single_or_multi_entity | single_entity | 3 | B5 | 1.0000 | 3.0000/3.0000 |
| single_or_multi_entity | single_entity | 3 | B6 | 1.0000 | 3.0000/3.0000 |
| single_or_multi_entity | single_entity | 3 | C1 | 1.0000 | 3.0000/3.0000 |
| single_or_multi_entity | single_entity | 3 | C2 | 1.0000 | 90.0000/90.0000 |
| single_or_multi_entity | single_entity | 3 | C3 | 1.0000 | 90.0000/90.0000 |
| single_or_multi_entity | single_entity | 3 | C4 | 1.0000 | 3.0000/3.0000 |
| single_or_multi_entity | single_entity | 3 | C5 | 1.0000 | 3.0000/3.0000 |

## 七、解释边界

- B1～B4缺失或评分失败时，正式主集只能标记为不完整，不能只发布已有指标。
- A9自动部分验证事实与证据的可追溯关联；法规语义支持另由B4约束。
- D1～D6首次正式运行只报告，不作为否决项。
- D5在统一运行时暴露组件级usage与费用前保持不可用，不使用估算值冒充实付成本。
- 当前D3等于经过门禁的最终答案耗时；运行时提供更早的可交付答案事件后才可细分首段耗时。
- D4只统计当前stage事件能闭合配对的Agent、工具和输出校验区间。
- 可靠性批次与普通主集分开报告。

### 本次运行限制

- A9 is deterministic evidence association; legal semantic support is additionally gated by B4.
- C3 requires up to eight complete frozen-truth records per SQL action because the production renderer publishes at most eight records per action.
- D5 remains unavailable until the unified runtime exposes component usage and cost metadata.
- D3 equals validated final-answer latency because the current collector does not expose an earlier deliverable-answer event.
- Required group breakdowns report raw numerators and denominators; formal thresholds apply to each complete run, not every subgroup.
- Reliability E metrics are scored from a separate reliability batch.
