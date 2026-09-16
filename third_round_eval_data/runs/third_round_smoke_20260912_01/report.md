# 第三轮统一主 Agent 测评报告

> 运行编号：`third_round_smoke_20260912_01`  
> 运行类型：`smoke`  
> 题集：`third_round_unified_agent_20260912_reviewed_01`  
> 阈值集：`third_round_formal_thresholds_20260912_v1`

## 一、运行完整性

- 计划主集尝试：6
- 已记录主集结果：6
- 主集采集完整：True
- Ragas评分行：0
- 评分器与采集计划一致：False
- 主集判定：`incomplete`
- 可靠性判定：`not_run`

## 二、核心指标

| 轮次 | 指标 | 结果 | 门槛 | 判定 | 分子/分母 |
|---:|---|---:|---:|---:|---:|
| 1 | A1 | 0 | =0 | True | 0/1 |
| 1 | A2 | 0.8750 | ≥98.00% | False | 14.0000/16.0000 |
| 1 | A3 | 1.0000 | ≥98.00% | True | 5.0000/5.0000 |
| 1 | A4 | 0.0000 | ≤2.00% | True | 0.0000/8.0000 |
| 1 | A5 | 1.0000 | ≥99.00% | True | 8.0000/8.0000 |
| 1 | A6 | 1.0000 | =100.00% | True | 1.0000/1.0000 |
| 1 | A7 | 1.0000 | ≥98.00% | True | 6.0000/6.0000 |
| 1 | A8 | 1.0000 | ≥95.00% | True | 5.0000/5.0000 |
| 1 | A9 | 1.0000 | =100.00% | True | 57.0000/57.0000 |
| 1 | A10 | 1.0000 | ≥98.00% | True | 1.0000/1.0000 |

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

## 四、效率观察项

- 首次运行策略：`report_only_not_a_blocking_gate`
- 完整请求 P50：2.8830 秒
- 完整请求 P95：7.5000 秒
- 首次状态反馈 P50/P95：0.0000 / 0.0000 秒
- 首段可交付答案 P50/P95：2.8830 / 7.5000 秒
- 平均模型调用：2.8333
- 平均工具尝试：1.6667
- 平均工具执行：1.3333
- 平均被拒绝工具尝试：0.3333
- 平均模型纠错：0.1667
- D5在线成本：`not_available_from_current_unified_contract`

### D4 分阶段耗时

| 阶段 | 样本数 | P50（秒） | P95（秒） |
|---|---:|---:|---:|
| agent_loop | 6 | 2.8830 | 7.5000 |
| tool:knowledge_qa | 2 | 4.6490 | 6.7793 |
| tool:query_company_award_history | 1 | 0.0150 | 0.0150 |
| tool:query_company_business_scope | 1 | 0.0160 | 0.0160 |
| tool:query_company_penalty | 1 | 0.0150 | 0.0150 |
| tool:query_company_registration | 2 | 0.0855 | 0.1490 |
| tool:query_project_award | 1 | 0.0000 | 0.0000 |
| validation | 6 | 0.0000 | 0.0000 |

## 五、强制分组

以下为原始分子/分母；正式门槛作用于每次完整正式运行，不自动作用于每个子组。

| 维度 | 分组 | 题数 | 指标 | 结果 | 分子/分母或有效数 |
|---|---|---:|---|---:|---:|
| authorized_tool | knowledge_qa | 2 | A1 | 0 | 0/1 |
| authorized_tool | knowledge_qa | 2 | A2 | 0.7143 | 5.0000/7.0000 |
| authorized_tool | knowledge_qa | 2 | A3 | 1.0000 | 2.0000/2.0000 |
| authorized_tool | knowledge_qa | 2 | A4 | 0.0000 | 0.0000/3.0000 |
| authorized_tool | knowledge_qa | 2 | A5 | 1.0000 | 3.0000/3.0000 |
| authorized_tool | knowledge_qa | 2 | A7 | 1.0000 | 2.0000/2.0000 |
| authorized_tool | knowledge_qa | 2 | A8 | 1.0000 | 2.0000/2.0000 |
| authorized_tool | knowledge_qa | 2 | A9 | 1.0000 | 40.0000/40.0000 |
| authorized_tool | knowledge_qa | 2 | B1 | N/A | 0/2 |
| authorized_tool | knowledge_qa | 2 | B2 | N/A | 0/2 |
| authorized_tool | knowledge_qa | 2 | B3 | N/A | 0/2 |
| authorized_tool | knowledge_qa | 2 | B4 | N/A | 0/2 |
| authorized_tool | knowledge_qa | 2 | B5 | 1.0000 | 2.0000/2.0000 |
| authorized_tool | knowledge_qa | 2 | B6 | 1.0000 | 2.0000/2.0000 |
| authorized_tool | knowledge_qa | 2 | C1 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | knowledge_qa | 2 | C2 | 1.0000 | 30.0000/30.0000 |
| authorized_tool | knowledge_qa | 2 | C3 | 1.0000 | 30.0000/30.0000 |
| authorized_tool | knowledge_qa | 2 | C4 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | knowledge_qa | 2 | C5 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | no_business_tool | 1 | A1 | 0 | 0/1 |
| authorized_tool | no_business_tool | 1 | A10 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | no_business_tool | 1 | A2 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | no_business_tool | 1 | A7 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | query_company_award_history | 1 | A1 | 0 | 0/1 |
| authorized_tool | query_company_award_history | 1 | A2 | 0.6000 | 3.0000/5.0000 |
| authorized_tool | query_company_award_history | 1 | A3 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | query_company_award_history | 1 | A4 | 0.0000 | 0.0000/2.0000 |
| authorized_tool | query_company_award_history | 1 | A5 | 1.0000 | 2.0000/2.0000 |
| authorized_tool | query_company_award_history | 1 | A7 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | query_company_award_history | 1 | A8 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | query_company_award_history | 1 | A9 | 1.0000 | 35.0000/35.0000 |
| authorized_tool | query_company_award_history | 1 | B1 | N/A | 0/1 |
| authorized_tool | query_company_award_history | 1 | B2 | N/A | 0/1 |
| authorized_tool | query_company_award_history | 1 | B3 | N/A | 0/1 |
| authorized_tool | query_company_award_history | 1 | B4 | N/A | 0/1 |
| authorized_tool | query_company_award_history | 1 | B5 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | query_company_award_history | 1 | B6 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | query_company_award_history | 1 | C1 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | query_company_award_history | 1 | C2 | 1.0000 | 30.0000/30.0000 |
| authorized_tool | query_company_award_history | 1 | C3 | 1.0000 | 30.0000/30.0000 |
| authorized_tool | query_company_award_history | 1 | C4 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | query_company_award_history | 1 | C5 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | query_company_business_scope | 1 | A1 | 0 | 0/1 |
| authorized_tool | query_company_business_scope | 1 | A2 | 1.0000 | 3.0000/3.0000 |
| authorized_tool | query_company_business_scope | 1 | A3 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | query_company_business_scope | 1 | A4 | 0.0000 | 0.0000/2.0000 |
| authorized_tool | query_company_business_scope | 1 | A5 | 1.0000 | 2.0000/2.0000 |
| authorized_tool | query_company_business_scope | 1 | A7 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | query_company_business_scope | 1 | A8 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | query_company_business_scope | 1 | A9 | 1.0000 | 11.0000/11.0000 |
| authorized_tool | query_company_business_scope | 1 | C1 | 1.0000 | 2.0000/2.0000 |
| authorized_tool | query_company_business_scope | 1 | C2 | 1.0000 | 11.0000/11.0000 |
| authorized_tool | query_company_business_scope | 1 | C3 | 1.0000 | 11.0000/11.0000 |
| authorized_tool | query_company_business_scope | 1 | C4 | 1.0000 | 2.0000/2.0000 |
| authorized_tool | query_company_business_scope | 1 | C5 | 1.0000 | 2.0000/2.0000 |
| authorized_tool | query_company_penalty | 1 | A1 | 0 | 0/1 |
| authorized_tool | query_company_penalty | 1 | A2 | 1.0000 | 3.0000/3.0000 |
| authorized_tool | query_company_penalty | 1 | A3 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | query_company_penalty | 1 | A4 | 0.0000 | 0.0000/2.0000 |
| authorized_tool | query_company_penalty | 1 | A5 | 1.0000 | 2.0000/2.0000 |
| authorized_tool | query_company_penalty | 1 | A6 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | query_company_penalty | 1 | A7 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | query_company_penalty | 1 | A8 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | query_company_penalty | 1 | A9 | 1.0000 | 6.0000/6.0000 |
| authorized_tool | query_company_penalty | 1 | C1 | 1.0000 | 2.0000/2.0000 |
| authorized_tool | query_company_penalty | 1 | C2 | 1.0000 | 6.0000/6.0000 |
| authorized_tool | query_company_penalty | 1 | C3 | 1.0000 | 6.0000/6.0000 |
| authorized_tool | query_company_penalty | 1 | C4 | 1.0000 | 2.0000/2.0000 |
| authorized_tool | query_company_penalty | 1 | C5 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | query_company_registration | 2 | A1 | 0 | 0/1 |
| authorized_tool | query_company_registration | 2 | A2 | 1.0000 | 5.0000/5.0000 |
| authorized_tool | query_company_registration | 2 | A3 | 1.0000 | 2.0000/2.0000 |
| authorized_tool | query_company_registration | 2 | A4 | 0.0000 | 0.0000/3.0000 |
| authorized_tool | query_company_registration | 2 | A5 | 1.0000 | 3.0000/3.0000 |
| authorized_tool | query_company_registration | 2 | A7 | 1.0000 | 2.0000/2.0000 |
| authorized_tool | query_company_registration | 2 | A8 | 1.0000 | 2.0000/2.0000 |
| authorized_tool | query_company_registration | 2 | A9 | 1.0000 | 11.0000/11.0000 |
| authorized_tool | query_company_registration | 2 | C1 | 1.0000 | 3.0000/3.0000 |
| authorized_tool | query_company_registration | 2 | C2 | 1.0000 | 11.0000/11.0000 |
| authorized_tool | query_company_registration | 2 | C3 | 1.0000 | 11.0000/11.0000 |
| authorized_tool | query_company_registration | 2 | C4 | 1.0000 | 3.0000/3.0000 |
| authorized_tool | query_company_registration | 2 | C5 | 1.0000 | 2.0000/2.0000 |
| authorized_tool | query_project_award | 1 | A1 | 0 | 0/1 |
| authorized_tool | query_project_award | 1 | A2 | 1.0000 | 3.0000/3.0000 |
| authorized_tool | query_project_award | 1 | A3 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | query_project_award | 1 | A4 | 0.0000 | 0.0000/2.0000 |
| authorized_tool | query_project_award | 1 | A5 | 1.0000 | 2.0000/2.0000 |
| authorized_tool | query_project_award | 1 | A6 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | query_project_award | 1 | A7 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | query_project_award | 1 | A8 | 1.0000 | 1.0000/1.0000 |
| authorized_tool | query_project_award | 1 | A9 | 1.0000 | 6.0000/6.0000 |
| authorized_tool | query_project_award | 1 | C1 | 1.0000 | 2.0000/2.0000 |
| authorized_tool | query_project_award | 1 | C2 | 1.0000 | 6.0000/6.0000 |
| authorized_tool | query_project_award | 1 | C3 | 1.0000 | 6.0000/6.0000 |
| authorized_tool | query_project_award | 1 | C4 | 1.0000 | 2.0000/2.0000 |
| authorized_tool | query_project_award | 1 | C5 | 1.0000 | 1.0000/1.0000 |
| case_category | independent_multi_action | 1 | A1 | 0 | 0/1 |
| case_category | independent_multi_action | 1 | A2 | 1.0000 | 3.0000/3.0000 |
| case_category | independent_multi_action | 1 | A3 | 1.0000 | 1.0000/1.0000 |
| case_category | independent_multi_action | 1 | A4 | 0.0000 | 0.0000/2.0000 |
| case_category | independent_multi_action | 1 | A5 | 1.0000 | 2.0000/2.0000 |
| case_category | independent_multi_action | 1 | A7 | 1.0000 | 1.0000/1.0000 |
| case_category | independent_multi_action | 1 | A8 | 1.0000 | 1.0000/1.0000 |
| case_category | independent_multi_action | 1 | A9 | 1.0000 | 11.0000/11.0000 |
| case_category | independent_multi_action | 1 | C1 | 1.0000 | 2.0000/2.0000 |
| case_category | independent_multi_action | 1 | C2 | 1.0000 | 11.0000/11.0000 |
| case_category | independent_multi_action | 1 | C3 | 1.0000 | 11.0000/11.0000 |
| case_category | independent_multi_action | 1 | C4 | 1.0000 | 2.0000/2.0000 |
| case_category | independent_multi_action | 1 | C5 | 1.0000 | 2.0000/2.0000 |
| case_category | negative | 1 | A1 | 0 | 0/1 |
| case_category | negative | 1 | A10 | 1.0000 | 1.0000/1.0000 |
| case_category | negative | 1 | A2 | 1.0000 | 1.0000/1.0000 |
| case_category | negative | 1 | A7 | 1.0000 | 1.0000/1.0000 |
| case_category | rag_anchor | 1 | A1 | 0 | 0/1 |
| case_category | rag_anchor | 1 | A2 | 1.0000 | 2.0000/2.0000 |
| case_category | rag_anchor | 1 | A3 | 1.0000 | 1.0000/1.0000 |
| case_category | rag_anchor | 1 | A4 | 0.0000 | 0.0000/1.0000 |
| case_category | rag_anchor | 1 | A5 | 1.0000 | 1.0000/1.0000 |
| case_category | rag_anchor | 1 | A7 | 1.0000 | 1.0000/1.0000 |
| case_category | rag_anchor | 1 | A8 | 1.0000 | 1.0000/1.0000 |
| case_category | rag_anchor | 1 | A9 | 1.0000 | 5.0000/5.0000 |
| case_category | rag_anchor | 1 | B1 | N/A | 0/1 |
| case_category | rag_anchor | 1 | B2 | N/A | 0/1 |
| case_category | rag_anchor | 1 | B3 | N/A | 0/1 |
| case_category | rag_anchor | 1 | B4 | N/A | 0/1 |
| case_category | rag_anchor | 1 | B5 | 1.0000 | 1.0000/1.0000 |
| case_category | rag_anchor | 1 | B6 | 1.0000 | 1.0000/1.0000 |
| case_category | result_dependent | 1 | A1 | 0 | 0/1 |
| case_category | result_dependent | 1 | A2 | 1.0000 | 3.0000/3.0000 |
| case_category | result_dependent | 1 | A3 | 1.0000 | 1.0000/1.0000 |
| case_category | result_dependent | 1 | A4 | 0.0000 | 0.0000/2.0000 |
| case_category | result_dependent | 1 | A5 | 1.0000 | 2.0000/2.0000 |
| case_category | result_dependent | 1 | A6 | 1.0000 | 1.0000/1.0000 |
| case_category | result_dependent | 1 | A7 | 1.0000 | 1.0000/1.0000 |
| case_category | result_dependent | 1 | A8 | 1.0000 | 1.0000/1.0000 |
| case_category | result_dependent | 1 | A9 | 1.0000 | 6.0000/6.0000 |
| case_category | result_dependent | 1 | C1 | 1.0000 | 2.0000/2.0000 |
| case_category | result_dependent | 1 | C2 | 1.0000 | 6.0000/6.0000 |
| case_category | result_dependent | 1 | C3 | 1.0000 | 6.0000/6.0000 |
| case_category | result_dependent | 1 | C4 | 1.0000 | 2.0000/2.0000 |
| case_category | result_dependent | 1 | C5 | 1.0000 | 1.0000/1.0000 |
| case_category | single_sql | 1 | A1 | 0 | 0/1 |
| case_category | single_sql | 1 | A2 | 1.0000 | 2.0000/2.0000 |
| case_category | single_sql | 1 | A3 | 1.0000 | 1.0000/1.0000 |
| case_category | single_sql | 1 | A4 | 0.0000 | 0.0000/1.0000 |
| case_category | single_sql | 1 | A5 | 1.0000 | 1.0000/1.0000 |
| case_category | single_sql | 1 | A7 | 1.0000 | 1.0000/1.0000 |
| case_category | single_sql | 1 | A8 | 1.0000 | 1.0000/1.0000 |
| case_category | single_sql | 1 | C1 | 1.0000 | 1.0000/1.0000 |
| case_category | single_sql | 1 | C4 | 1.0000 | 1.0000/1.0000 |
| case_category | sql_rag_cross_capability | 1 | A1 | 0 | 0/1 |
| case_category | sql_rag_cross_capability | 1 | A2 | 0.6000 | 3.0000/5.0000 |
| case_category | sql_rag_cross_capability | 1 | A3 | 1.0000 | 1.0000/1.0000 |
| case_category | sql_rag_cross_capability | 1 | A4 | 0.0000 | 0.0000/2.0000 |
| case_category | sql_rag_cross_capability | 1 | A5 | 1.0000 | 2.0000/2.0000 |
| case_category | sql_rag_cross_capability | 1 | A7 | 1.0000 | 1.0000/1.0000 |
| case_category | sql_rag_cross_capability | 1 | A8 | 1.0000 | 1.0000/1.0000 |
| case_category | sql_rag_cross_capability | 1 | A9 | 1.0000 | 35.0000/35.0000 |
| case_category | sql_rag_cross_capability | 1 | B1 | N/A | 0/1 |
| case_category | sql_rag_cross_capability | 1 | B2 | N/A | 0/1 |
| case_category | sql_rag_cross_capability | 1 | B3 | N/A | 0/1 |
| case_category | sql_rag_cross_capability | 1 | B4 | N/A | 0/1 |
| case_category | sql_rag_cross_capability | 1 | B5 | 1.0000 | 1.0000/1.0000 |
| case_category | sql_rag_cross_capability | 1 | B6 | 1.0000 | 1.0000/1.0000 |
| case_category | sql_rag_cross_capability | 1 | C1 | 1.0000 | 1.0000/1.0000 |
| case_category | sql_rag_cross_capability | 1 | C2 | 1.0000 | 30.0000/30.0000 |
| case_category | sql_rag_cross_capability | 1 | C3 | 1.0000 | 30.0000/30.0000 |
| case_category | sql_rag_cross_capability | 1 | C4 | 1.0000 | 1.0000/1.0000 |
| case_category | sql_rag_cross_capability | 1 | C5 | 1.0000 | 1.0000/1.0000 |
| finish_status | clarify | 1 | A1 | 0 | 0/1 |
| finish_status | clarify | 1 | A10 | 1.0000 | 1.0000/1.0000 |
| finish_status | clarify | 1 | A2 | 1.0000 | 1.0000/1.0000 |
| finish_status | clarify | 1 | A7 | 1.0000 | 1.0000/1.0000 |
| finish_status | complete | 5 | A1 | 0 | 0/1 |
| finish_status | complete | 5 | A2 | 0.8667 | 13.0000/15.0000 |
| finish_status | complete | 5 | A3 | 1.0000 | 5.0000/5.0000 |
| finish_status | complete | 5 | A4 | 0.0000 | 0.0000/8.0000 |
| finish_status | complete | 5 | A5 | 1.0000 | 8.0000/8.0000 |
| finish_status | complete | 5 | A6 | 1.0000 | 1.0000/1.0000 |
| finish_status | complete | 5 | A7 | 1.0000 | 5.0000/5.0000 |
| finish_status | complete | 5 | A8 | 1.0000 | 5.0000/5.0000 |
| finish_status | complete | 5 | A9 | 1.0000 | 57.0000/57.0000 |
| finish_status | complete | 5 | B1 | N/A | 0/2 |
| finish_status | complete | 5 | B2 | N/A | 0/2 |
| finish_status | complete | 5 | B3 | N/A | 0/2 |
| finish_status | complete | 5 | B4 | N/A | 0/2 |
| finish_status | complete | 5 | B5 | 1.0000 | 2.0000/2.0000 |
| finish_status | complete | 5 | B6 | 1.0000 | 2.0000/2.0000 |
| finish_status | complete | 5 | C1 | 1.0000 | 6.0000/6.0000 |
| finish_status | complete | 5 | C2 | 1.0000 | 47.0000/47.0000 |
| finish_status | complete | 5 | C3 | 1.0000 | 47.0000/47.0000 |
| finish_status | complete | 5 | C4 | 1.0000 | 6.0000/6.0000 |
| finish_status | complete | 5 | C5 | 1.0000 | 4.0000/4.0000 |
| formal_run | repeat_1 | 6 | A1 | 0 | 0/1 |
| formal_run | repeat_1 | 6 | A10 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_1 | 6 | A2 | 0.8750 | 14.0000/16.0000 |
| formal_run | repeat_1 | 6 | A3 | 1.0000 | 5.0000/5.0000 |
| formal_run | repeat_1 | 6 | A4 | 0.0000 | 0.0000/8.0000 |
| formal_run | repeat_1 | 6 | A5 | 1.0000 | 8.0000/8.0000 |
| formal_run | repeat_1 | 6 | A6 | 1.0000 | 1.0000/1.0000 |
| formal_run | repeat_1 | 6 | A7 | 1.0000 | 6.0000/6.0000 |
| formal_run | repeat_1 | 6 | A8 | 1.0000 | 5.0000/5.0000 |
| formal_run | repeat_1 | 6 | A9 | 1.0000 | 57.0000/57.0000 |
| formal_run | repeat_1 | 6 | B1 | N/A | 0/2 |
| formal_run | repeat_1 | 6 | B2 | N/A | 0/2 |
| formal_run | repeat_1 | 6 | B3 | N/A | 0/2 |
| formal_run | repeat_1 | 6 | B4 | N/A | 0/2 |
| formal_run | repeat_1 | 6 | B5 | 1.0000 | 2.0000/2.0000 |
| formal_run | repeat_1 | 6 | B6 | 1.0000 | 2.0000/2.0000 |
| formal_run | repeat_1 | 6 | C1 | 1.0000 | 6.0000/6.0000 |
| formal_run | repeat_1 | 6 | C2 | 1.0000 | 47.0000/47.0000 |
| formal_run | repeat_1 | 6 | C3 | 1.0000 | 47.0000/47.0000 |
| formal_run | repeat_1 | 6 | C4 | 1.0000 | 6.0000/6.0000 |
| formal_run | repeat_1 | 6 | C5 | 1.0000 | 4.0000/4.0000 |
| normal_adversarial_or_fault | normal | 6 | A1 | 0 | 0/1 |
| normal_adversarial_or_fault | normal | 6 | A10 | 1.0000 | 1.0000/1.0000 |
| normal_adversarial_or_fault | normal | 6 | A2 | 0.8750 | 14.0000/16.0000 |
| normal_adversarial_or_fault | normal | 6 | A3 | 1.0000 | 5.0000/5.0000 |
| normal_adversarial_or_fault | normal | 6 | A4 | 0.0000 | 0.0000/8.0000 |
| normal_adversarial_or_fault | normal | 6 | A5 | 1.0000 | 8.0000/8.0000 |
| normal_adversarial_or_fault | normal | 6 | A6 | 1.0000 | 1.0000/1.0000 |
| normal_adversarial_or_fault | normal | 6 | A7 | 1.0000 | 6.0000/6.0000 |
| normal_adversarial_or_fault | normal | 6 | A8 | 1.0000 | 5.0000/5.0000 |
| normal_adversarial_or_fault | normal | 6 | A9 | 1.0000 | 57.0000/57.0000 |
| normal_adversarial_or_fault | normal | 6 | B1 | N/A | 0/2 |
| normal_adversarial_or_fault | normal | 6 | B2 | N/A | 0/2 |
| normal_adversarial_or_fault | normal | 6 | B3 | N/A | 0/2 |
| normal_adversarial_or_fault | normal | 6 | B4 | N/A | 0/2 |
| normal_adversarial_or_fault | normal | 6 | B5 | 1.0000 | 2.0000/2.0000 |
| normal_adversarial_or_fault | normal | 6 | B6 | 1.0000 | 2.0000/2.0000 |
| normal_adversarial_or_fault | normal | 6 | C1 | 1.0000 | 6.0000/6.0000 |
| normal_adversarial_or_fault | normal | 6 | C2 | 1.0000 | 47.0000/47.0000 |
| normal_adversarial_or_fault | normal | 6 | C3 | 1.0000 | 47.0000/47.0000 |
| normal_adversarial_or_fault | normal | 6 | C4 | 1.0000 | 6.0000/6.0000 |
| normal_adversarial_or_fault | normal | 6 | C5 | 1.0000 | 4.0000/4.0000 |
| normal_empty_or_failure | no_match | 2 | A1 | 0 | 0/1 |
| normal_empty_or_failure | no_match | 2 | A2 | 1.0000 | 5.0000/5.0000 |
| normal_empty_or_failure | no_match | 2 | A3 | 1.0000 | 2.0000/2.0000 |
| normal_empty_or_failure | no_match | 2 | A4 | 0.0000 | 0.0000/3.0000 |
| normal_empty_or_failure | no_match | 2 | A5 | 1.0000 | 3.0000/3.0000 |
| normal_empty_or_failure | no_match | 2 | A6 | 1.0000 | 1.0000/1.0000 |
| normal_empty_or_failure | no_match | 2 | A7 | 1.0000 | 2.0000/2.0000 |
| normal_empty_or_failure | no_match | 2 | A8 | 1.0000 | 2.0000/2.0000 |
| normal_empty_or_failure | no_match | 2 | A9 | 1.0000 | 6.0000/6.0000 |
| normal_empty_or_failure | no_match | 2 | C1 | 1.0000 | 3.0000/3.0000 |
| normal_empty_or_failure | no_match | 2 | C2 | 1.0000 | 6.0000/6.0000 |
| normal_empty_or_failure | no_match | 2 | C3 | 1.0000 | 6.0000/6.0000 |
| normal_empty_or_failure | no_match | 2 | C4 | 1.0000 | 3.0000/3.0000 |
| normal_empty_or_failure | no_match | 2 | C5 | 1.0000 | 1.0000/1.0000 |
| normal_empty_or_failure | normal_result | 3 | A1 | 0 | 0/1 |
| normal_empty_or_failure | normal_result | 3 | A2 | 0.8000 | 8.0000/10.0000 |
| normal_empty_or_failure | normal_result | 3 | A3 | 1.0000 | 3.0000/3.0000 |
| normal_empty_or_failure | normal_result | 3 | A4 | 0.0000 | 0.0000/5.0000 |
| normal_empty_or_failure | normal_result | 3 | A5 | 1.0000 | 5.0000/5.0000 |
| normal_empty_or_failure | normal_result | 3 | A7 | 1.0000 | 3.0000/3.0000 |
| normal_empty_or_failure | normal_result | 3 | A8 | 1.0000 | 3.0000/3.0000 |
| normal_empty_or_failure | normal_result | 3 | A9 | 1.0000 | 51.0000/51.0000 |
| normal_empty_or_failure | normal_result | 3 | B1 | N/A | 0/2 |
| normal_empty_or_failure | normal_result | 3 | B2 | N/A | 0/2 |
| normal_empty_or_failure | normal_result | 3 | B3 | N/A | 0/2 |
| normal_empty_or_failure | normal_result | 3 | B4 | N/A | 0/2 |
| normal_empty_or_failure | normal_result | 3 | B5 | 1.0000 | 2.0000/2.0000 |
| normal_empty_or_failure | normal_result | 3 | B6 | 1.0000 | 2.0000/2.0000 |
| normal_empty_or_failure | normal_result | 3 | C1 | 1.0000 | 3.0000/3.0000 |
| normal_empty_or_failure | normal_result | 3 | C2 | 1.0000 | 41.0000/41.0000 |
| normal_empty_or_failure | normal_result | 3 | C3 | 1.0000 | 41.0000/41.0000 |
| normal_empty_or_failure | normal_result | 3 | C4 | 1.0000 | 3.0000/3.0000 |
| normal_empty_or_failure | normal_result | 3 | C5 | 1.0000 | 3.0000/3.0000 |
| normal_empty_or_failure | not_applicable | 1 | A1 | 0 | 0/1 |
| normal_empty_or_failure | not_applicable | 1 | A10 | 1.0000 | 1.0000/1.0000 |
| normal_empty_or_failure | not_applicable | 1 | A2 | 1.0000 | 1.0000/1.0000 |
| normal_empty_or_failure | not_applicable | 1 | A7 | 1.0000 | 1.0000/1.0000 |
| rag_single_or_multi_hop | not_applicable | 4 | A1 | 0 | 0/1 |
| rag_single_or_multi_hop | not_applicable | 4 | A10 | 1.0000 | 1.0000/1.0000 |
| rag_single_or_multi_hop | not_applicable | 4 | A2 | 1.0000 | 9.0000/9.0000 |
| rag_single_or_multi_hop | not_applicable | 4 | A3 | 1.0000 | 3.0000/3.0000 |
| rag_single_or_multi_hop | not_applicable | 4 | A4 | 0.0000 | 0.0000/5.0000 |
| rag_single_or_multi_hop | not_applicable | 4 | A5 | 1.0000 | 5.0000/5.0000 |
| rag_single_or_multi_hop | not_applicable | 4 | A6 | 1.0000 | 1.0000/1.0000 |
| rag_single_or_multi_hop | not_applicable | 4 | A7 | 1.0000 | 4.0000/4.0000 |
| rag_single_or_multi_hop | not_applicable | 4 | A8 | 1.0000 | 3.0000/3.0000 |
| rag_single_or_multi_hop | not_applicable | 4 | A9 | 1.0000 | 17.0000/17.0000 |
| rag_single_or_multi_hop | not_applicable | 4 | C1 | 1.0000 | 5.0000/5.0000 |
| rag_single_or_multi_hop | not_applicable | 4 | C2 | 1.0000 | 17.0000/17.0000 |
| rag_single_or_multi_hop | not_applicable | 4 | C3 | 1.0000 | 17.0000/17.0000 |
| rag_single_or_multi_hop | not_applicable | 4 | C4 | 1.0000 | 5.0000/5.0000 |
| rag_single_or_multi_hop | not_applicable | 4 | C5 | 1.0000 | 3.0000/3.0000 |
| rag_single_or_multi_hop | single_hop | 2 | A1 | 0 | 0/1 |
| rag_single_or_multi_hop | single_hop | 2 | A2 | 0.7143 | 5.0000/7.0000 |
| rag_single_or_multi_hop | single_hop | 2 | A3 | 1.0000 | 2.0000/2.0000 |
| rag_single_or_multi_hop | single_hop | 2 | A4 | 0.0000 | 0.0000/3.0000 |
| rag_single_or_multi_hop | single_hop | 2 | A5 | 1.0000 | 3.0000/3.0000 |
| rag_single_or_multi_hop | single_hop | 2 | A7 | 1.0000 | 2.0000/2.0000 |
| rag_single_or_multi_hop | single_hop | 2 | A8 | 1.0000 | 2.0000/2.0000 |
| rag_single_or_multi_hop | single_hop | 2 | A9 | 1.0000 | 40.0000/40.0000 |
| rag_single_or_multi_hop | single_hop | 2 | B1 | N/A | 0/2 |
| rag_single_or_multi_hop | single_hop | 2 | B2 | N/A | 0/2 |
| rag_single_or_multi_hop | single_hop | 2 | B3 | N/A | 0/2 |
| rag_single_or_multi_hop | single_hop | 2 | B4 | N/A | 0/2 |
| rag_single_or_multi_hop | single_hop | 2 | B5 | 1.0000 | 2.0000/2.0000 |
| rag_single_or_multi_hop | single_hop | 2 | B6 | 1.0000 | 2.0000/2.0000 |
| rag_single_or_multi_hop | single_hop | 2 | C1 | 1.0000 | 1.0000/1.0000 |
| rag_single_or_multi_hop | single_hop | 2 | C2 | 1.0000 | 30.0000/30.0000 |
| rag_single_or_multi_hop | single_hop | 2 | C3 | 1.0000 | 30.0000/30.0000 |
| rag_single_or_multi_hop | single_hop | 2 | C4 | 1.0000 | 1.0000/1.0000 |
| rag_single_or_multi_hop | single_hop | 2 | C5 | 1.0000 | 1.0000/1.0000 |
| single_or_multi_entity | multi_entity | 1 | A1 | 0 | 0/1 |
| single_or_multi_entity | multi_entity | 1 | A2 | 1.0000 | 3.0000/3.0000 |
| single_or_multi_entity | multi_entity | 1 | A3 | 1.0000 | 1.0000/1.0000 |
| single_or_multi_entity | multi_entity | 1 | A4 | 0.0000 | 0.0000/2.0000 |
| single_or_multi_entity | multi_entity | 1 | A5 | 1.0000 | 2.0000/2.0000 |
| single_or_multi_entity | multi_entity | 1 | A6 | 1.0000 | 1.0000/1.0000 |
| single_or_multi_entity | multi_entity | 1 | A7 | 1.0000 | 1.0000/1.0000 |
| single_or_multi_entity | multi_entity | 1 | A8 | 1.0000 | 1.0000/1.0000 |
| single_or_multi_entity | multi_entity | 1 | A9 | 1.0000 | 6.0000/6.0000 |
| single_or_multi_entity | multi_entity | 1 | C1 | 1.0000 | 2.0000/2.0000 |
| single_or_multi_entity | multi_entity | 1 | C2 | 1.0000 | 6.0000/6.0000 |
| single_or_multi_entity | multi_entity | 1 | C3 | 1.0000 | 6.0000/6.0000 |
| single_or_multi_entity | multi_entity | 1 | C4 | 1.0000 | 2.0000/2.0000 |
| single_or_multi_entity | multi_entity | 1 | C5 | 1.0000 | 1.0000/1.0000 |
| single_or_multi_entity | not_applicable | 2 | A1 | 0 | 0/1 |
| single_or_multi_entity | not_applicable | 2 | A10 | 1.0000 | 1.0000/1.0000 |
| single_or_multi_entity | not_applicable | 2 | A2 | 1.0000 | 3.0000/3.0000 |
| single_or_multi_entity | not_applicable | 2 | A3 | 1.0000 | 1.0000/1.0000 |
| single_or_multi_entity | not_applicable | 2 | A4 | 0.0000 | 0.0000/1.0000 |
| single_or_multi_entity | not_applicable | 2 | A5 | 1.0000 | 1.0000/1.0000 |
| single_or_multi_entity | not_applicable | 2 | A7 | 1.0000 | 2.0000/2.0000 |
| single_or_multi_entity | not_applicable | 2 | A8 | 1.0000 | 1.0000/1.0000 |
| single_or_multi_entity | not_applicable | 2 | A9 | 1.0000 | 5.0000/5.0000 |
| single_or_multi_entity | not_applicable | 2 | B1 | N/A | 0/1 |
| single_or_multi_entity | not_applicable | 2 | B2 | N/A | 0/1 |
| single_or_multi_entity | not_applicable | 2 | B3 | N/A | 0/1 |
| single_or_multi_entity | not_applicable | 2 | B4 | N/A | 0/1 |
| single_or_multi_entity | not_applicable | 2 | B5 | 1.0000 | 1.0000/1.0000 |
| single_or_multi_entity | not_applicable | 2 | B6 | 1.0000 | 1.0000/1.0000 |
| single_or_multi_entity | single_entity | 3 | A1 | 0 | 0/1 |
| single_or_multi_entity | single_entity | 3 | A2 | 0.8000 | 8.0000/10.0000 |
| single_or_multi_entity | single_entity | 3 | A3 | 1.0000 | 3.0000/3.0000 |
| single_or_multi_entity | single_entity | 3 | A4 | 0.0000 | 0.0000/5.0000 |
| single_or_multi_entity | single_entity | 3 | A5 | 1.0000 | 5.0000/5.0000 |
| single_or_multi_entity | single_entity | 3 | A7 | 1.0000 | 3.0000/3.0000 |
| single_or_multi_entity | single_entity | 3 | A8 | 1.0000 | 3.0000/3.0000 |
| single_or_multi_entity | single_entity | 3 | A9 | 1.0000 | 46.0000/46.0000 |
| single_or_multi_entity | single_entity | 3 | B1 | N/A | 0/1 |
| single_or_multi_entity | single_entity | 3 | B2 | N/A | 0/1 |
| single_or_multi_entity | single_entity | 3 | B3 | N/A | 0/1 |
| single_or_multi_entity | single_entity | 3 | B4 | N/A | 0/1 |
| single_or_multi_entity | single_entity | 3 | B5 | 1.0000 | 1.0000/1.0000 |
| single_or_multi_entity | single_entity | 3 | B6 | 1.0000 | 1.0000/1.0000 |
| single_or_multi_entity | single_entity | 3 | C1 | 1.0000 | 4.0000/4.0000 |
| single_or_multi_entity | single_entity | 3 | C2 | 1.0000 | 41.0000/41.0000 |
| single_or_multi_entity | single_entity | 3 | C3 | 1.0000 | 41.0000/41.0000 |
| single_or_multi_entity | single_entity | 3 | C4 | 1.0000 | 4.0000/4.0000 |
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
- The scorer changed after collection; this smoke was rescored with the current recorded scorer hash. Formal runs forbid this mismatch.
- Reliability E metrics are scored from a separate reliability batch.
