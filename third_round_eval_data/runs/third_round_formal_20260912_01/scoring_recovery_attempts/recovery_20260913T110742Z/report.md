# 第三轮统一主 Agent 测评报告

> 运行编号：`third_round_formal_20260912_01`  
> 运行类型：`formal`  
> 题集：`third_round_unified_agent_20260912_reviewed_01`  
> 阈值集：`third_round_formal_thresholds_20260912_v1`

## 一、运行完整性

- 计划主集尝试：840
- 已记录主集结果：840
- 主集采集完整：True
- Ragas评分行：330
- 评分器与采集计划一致：True
- 主集判定：`incomplete`
- 可靠性判定：`not_run`

## 二、核心指标

| 轮次 | 指标 | 结果 | 门槛 | 判定 | 分子/分母 |
|---:|---|---:|---:|---:|---:|
| 1 | A1 | 12 | =0 | False | 12/1 |
| 1 | A2 | 0.9132 | ≥98.00% | False | 589.0000/645.0000 |
| 1 | A3 | 0.9909 | ≥98.00% | True | 218.0000/220.0000 |
| 1 | A4 | 0.0782 | ≤2.00% | False | 28.0000/358.0000 |
| 1 | A5 | 0.9910 | ≥99.00% | True | 330.0000/333.0000 |
| 1 | A6 | 0.9714 | =100.00% | False | 34.0000/35.0000 |
| 1 | A7 | 0.9250 | ≥98.00% | False | 259.0000/280.0000 |
| 1 | A8 | 0.9864 | ≥95.00% | True | 217.0000/220.0000 |
| 1 | A9 | 1.0000 | =100.00% | True | 2016.0000/2016.0000 |
| 1 | A10 | 0.7000 | ≥98.00% | False | 42.0000/60.0000 |
| 2 | A1 | 12 | =0 | False | 12/1 |
| 2 | A2 | 0.9189 | ≥98.00% | False | 589.0000/641.0000 |
| 2 | A3 | 0.9909 | ≥98.00% | True | 218.0000/220.0000 |
| 2 | A4 | 0.0730 | ≤2.00% | False | 26.0000/356.0000 |
| 2 | A5 | 0.9910 | ≥99.00% | True | 330.0000/333.0000 |
| 2 | A6 | 0.9714 | =100.00% | False | 34.0000/35.0000 |
| 2 | A7 | 0.9250 | ≥98.00% | False | 259.0000/280.0000 |
| 2 | A8 | 0.9864 | ≥95.00% | True | 217.0000/220.0000 |
| 2 | A9 | 1.0000 | =100.00% | True | 2016.0000/2016.0000 |
| 2 | A10 | 0.7000 | ≥98.00% | False | 42.0000/60.0000 |
| 3 | A1 | 12 | =0 | False | 12/1 |
| 3 | A2 | 0.9190 | ≥98.00% | False | 590.0000/642.0000 |
| 3 | A3 | 0.9909 | ≥98.00% | True | 218.0000/220.0000 |
| 3 | A4 | 0.0756 | ≤2.00% | False | 27.0000/357.0000 |
| 3 | A5 | 0.9910 | ≥99.00% | True | 330.0000/333.0000 |
| 3 | A6 | 0.9714 | =100.00% | False | 34.0000/35.0000 |
| 3 | A7 | 0.9286 | ≥98.00% | False | 260.0000/280.0000 |
| 3 | A8 | 0.9909 | ≥95.00% | True | 218.0000/220.0000 |
| 3 | A9 | 1.0000 | =100.00% | True | 2016.0000/2016.0000 |
| 3 | A10 | 0.7000 | ≥98.00% | False | 42.0000/60.0000 |

## 三、RAG与SQL子集

| 轮次 | 指标 | 结果 | 门槛 | 判定 |
|---:|---|---:|---:|---:|
| 1 | B1 | N/A | ≥95.00% | N/A |
| 1 | B2 | N/A | ≥85.00% | N/A |
| 1 | B3 | N/A | ≥80.00% | N/A |
| 1 | B4 | N/A | ≥95.00% | N/A |
| 1 | B5 | 0.8955 | ≥88.00% | True |
| 1 | B6 | 0.8848 | ≥87.00% | True |
| 1 | C1 | 0.9865 | ≥99.00% | False |
| 1 | C2 | 1.0000 | =100.00% | True |
| 1 | C3 | 0.9922 | ≥98.00% | True |
| 1 | C4 | 0.9865 | =100.00% | False |
| 1 | C5 | 0.9949 | =100.00% | False |
| 2 | B1 | 0.9793 | ≥95.00% | True |
| 2 | B2 | 0.8920 | ≥85.00% | True |
| 2 | B3 | N/A | ≥80.00% | N/A |
| 2 | B4 | 0.9669 | ≥95.00% | True |
| 2 | B5 | 0.8955 | ≥88.00% | True |
| 2 | B6 | 0.8848 | ≥87.00% | True |
| 2 | C1 | 0.9865 | ≥99.00% | False |
| 2 | C2 | 1.0000 | =100.00% | True |
| 2 | C3 | 0.9922 | ≥98.00% | True |
| 2 | C4 | 0.9865 | =100.00% | False |
| 2 | C5 | 0.9949 | =100.00% | False |
| 3 | B1 | 0.9814 | ≥95.00% | True |
| 3 | B2 | 0.8981 | ≥85.00% | True |
| 3 | B3 | 0.8277 | ≥80.00% | True |
| 3 | B4 | 0.9640 | ≥95.00% | True |
| 3 | B5 | 0.8955 | ≥88.00% | True |
| 3 | B6 | 0.8848 | ≥87.00% | True |
| 3 | C1 | 0.9865 | ≥99.00% | False |
| 3 | C2 | 1.0000 | =100.00% | True |
| 3 | C3 | 0.9922 | ≥98.00% | True |
| 3 | C4 | 0.9865 | =100.00% | False |
| 3 | C5 | 0.9949 | =100.00% | False |

## 四、效率观察项

- 首次运行策略：`report_only_not_a_blocking_gate`
- 完整请求 P50：2.7500 秒
- 完整请求 P95：5.1425 秒
- 首次状态反馈 P50/P95：0.0000 / 0.0000 秒
- 首段可交付答案 P50/P95：2.7500 / 5.1425 秒
- 平均模型调用：2.6917
- 平均工具尝试：1.2952
- 平均工具执行：1.2750
- 平均被拒绝工具尝试：0.0202
- 平均模型纠错：0.0000
- D5在线成本：`not_available_from_current_unified_contract`

### D4 分阶段耗时

| 阶段 | 样本数 | P50（秒） | P95（秒） |
|---|---:|---:|---:|
| agent_loop | 839 | 2.7500 | 5.1401 |
| tool:knowledge_qa | 338 | 2.2030 | 2.9371 |
| tool:query_company_award_history | 102 | 0.0150 | 0.0160 |
| tool:query_company_business_scope | 133 | 0.0160 | 0.0160 |
| tool:query_company_penalty | 144 | 0.0155 | 0.0160 |
| tool:query_company_registration | 192 | 0.0160 | 0.0310 |
| tool:query_project_award | 162 | 0.0150 | 0.0160 |
| validation | 840 | 0.0000 | 0.0000 |

## 五、强制分组

以下为原始分子/分母；正式门槛作用于每次完整正式运行，不自动作用于每个子组。

| 维度 | 分组 | 题数 | 指标 | 结果 | 分子/分母或有效数 |
|---|---|---:|---|---:|---:|
| authorized_tool | knowledge_qa | 330 | A1 | 0 | 0/1 |
| authorized_tool | knowledge_qa | 330 | A2 | 0.9773 | 774.0000/792.0000 |
| authorized_tool | knowledge_qa | 330 | A3 | 0.9909 | 327.0000/330.0000 |
| authorized_tool | knowledge_qa | 330 | A4 | 0.0261 | 12.0000/459.0000 |
| authorized_tool | knowledge_qa | 330 | A5 | 0.9933 | 447.0000/450.0000 |
| authorized_tool | knowledge_qa | 330 | A7 | 0.9909 | 327.0000/330.0000 |
| authorized_tool | knowledge_qa | 330 | A8 | 0.9909 | 327.0000/330.0000 |
| authorized_tool | knowledge_qa | 330 | A9 | 1.0000 | 2325.0000/2325.0000 |
| authorized_tool | knowledge_qa | 330 | B1 | N/A | 327/330 |
| authorized_tool | knowledge_qa | 330 | B2 | N/A | 327/330 |
| authorized_tool | knowledge_qa | 330 | B3 | N/A | 324/330 |
| authorized_tool | knowledge_qa | 330 | B4 | N/A | 327/330 |
| authorized_tool | knowledge_qa | 330 | B5 | 0.8955 | 295.5000/330.0000 |
| authorized_tool | knowledge_qa | 330 | B6 | 0.8848 | 438.0000/495.0000 |
| authorized_tool | knowledge_qa | 330 | C1 | 0.9750 | 117.0000/120.0000 |
| authorized_tool | knowledge_qa | 330 | C2 | 1.0000 | 834.0000/834.0000 |
| authorized_tool | knowledge_qa | 330 | C3 | 0.9789 | 834.0000/852.0000 |
| authorized_tool | knowledge_qa | 330 | C4 | 0.9750 | 117.0000/120.0000 |
| authorized_tool | knowledge_qa | 330 | C5 | 1.0000 | 120.0000/120.0000 |
| authorized_tool | no_business_tool | 180 | A1 | 36 | 36/1 |
| authorized_tool | no_business_tool | 180 | A10 | 0.7000 | 126.0000/180.0000 |
| authorized_tool | no_business_tool | 180 | A2 | 0.4846 | 126.0000/260.0000 |
| authorized_tool | no_business_tool | 180 | A4 | 1.0000 | 66.0000/66.0000 |
| authorized_tool | no_business_tool | 180 | A7 | 0.7000 | 126.0000/180.0000 |
| authorized_tool | query_company_award_history | 87 | A1 | 0 | 0/1 |
| authorized_tool | query_company_award_history | 87 | A2 | 0.9277 | 231.0000/249.0000 |
| authorized_tool | query_company_award_history | 87 | A3 | 0.9655 | 84.0000/87.0000 |
| authorized_tool | query_company_award_history | 87 | A4 | 0.0755 | 12.0000/159.0000 |
| authorized_tool | query_company_award_history | 87 | A5 | 0.9800 | 147.0000/150.0000 |
| authorized_tool | query_company_award_history | 87 | A6 | 1.0000 | 24.0000/24.0000 |
| authorized_tool | query_company_award_history | 87 | A7 | 0.9655 | 84.0000/87.0000 |
| authorized_tool | query_company_award_history | 87 | A8 | 0.9655 | 84.0000/87.0000 |
| authorized_tool | query_company_award_history | 87 | A9 | 1.0000 | 1896.0000/1896.0000 |
| authorized_tool | query_company_award_history | 87 | B1 | 1.0000 | 24/24 |
| authorized_tool | query_company_award_history | 87 | B2 | 0.8872 | 24/24 |
| authorized_tool | query_company_award_history | 87 | B3 | N/A | 23/24 |
| authorized_tool | query_company_award_history | 87 | B4 | 0.9821 | 24/24 |
| authorized_tool | query_company_award_history | 87 | B5 | 1.0000 | 24.0000/24.0000 |
| authorized_tool | query_company_award_history | 87 | B6 | 1.0000 | 36.0000/36.0000 |
| authorized_tool | query_company_award_history | 87 | C1 | 0.9762 | 123.0000/126.0000 |
| authorized_tool | query_company_award_history | 87 | C2 | 1.0000 | 1776.0000/1776.0000 |
| authorized_tool | query_company_award_history | 87 | C3 | 0.9900 | 1776.0000/1794.0000 |
| authorized_tool | query_company_award_history | 87 | C4 | 0.9762 | 123.0000/126.0000 |
| authorized_tool | query_company_award_history | 87 | C5 | 1.0000 | 120.0000/120.0000 |
| authorized_tool | query_company_business_scope | 123 | A1 | 0 | 0/1 |
| authorized_tool | query_company_business_scope | 123 | A2 | 0.9944 | 352.0000/354.0000 |
| authorized_tool | query_company_business_scope | 123 | A3 | 1.0000 | 123.0000/123.0000 |
| authorized_tool | query_company_business_scope | 123 | A4 | 0.0000 | 0.0000/231.0000 |
| authorized_tool | query_company_business_scope | 123 | A5 | 1.0000 | 231.0000/231.0000 |
| authorized_tool | query_company_business_scope | 123 | A6 | 1.0000 | 27.0000/27.0000 |
| authorized_tool | query_company_business_scope | 123 | A7 | 0.9837 | 121.0000/123.0000 |
| authorized_tool | query_company_business_scope | 123 | A8 | 0.9837 | 121.0000/123.0000 |
| authorized_tool | query_company_business_scope | 123 | A9 | 1.0000 | 1011.0000/1011.0000 |
| authorized_tool | query_company_business_scope | 123 | B1 | 1.0000 | 24/24 |
| authorized_tool | query_company_business_scope | 123 | B2 | 0.9644 | 24/24 |
| authorized_tool | query_company_business_scope | 123 | B3 | 0.8263 | 24/24 |
| authorized_tool | query_company_business_scope | 123 | B4 | 0.9531 | 24/24 |
| authorized_tool | query_company_business_scope | 123 | B5 | 1.0000 | 24.0000/24.0000 |
| authorized_tool | query_company_business_scope | 123 | B6 | 1.0000 | 33.0000/33.0000 |
| authorized_tool | query_company_business_scope | 123 | C1 | 1.0000 | 207.0000/207.0000 |
| authorized_tool | query_company_business_scope | 123 | C2 | 1.0000 | 891.0000/891.0000 |
| authorized_tool | query_company_business_scope | 123 | C3 | 1.0000 | 891.0000/891.0000 |
| authorized_tool | query_company_business_scope | 123 | C4 | 1.0000 | 207.0000/207.0000 |
| authorized_tool | query_company_business_scope | 123 | C5 | 1.0000 | 192.0000/192.0000 |
| authorized_tool | query_company_penalty | 114 | A1 | 0 | 0/1 |
| authorized_tool | query_company_penalty | 114 | A2 | 0.9815 | 318.0000/324.0000 |
| authorized_tool | query_company_penalty | 114 | A3 | 0.9737 | 111.0000/114.0000 |
| authorized_tool | query_company_penalty | 114 | A4 | 0.0143 | 3.0000/210.0000 |
| authorized_tool | query_company_penalty | 114 | A5 | 0.9718 | 207.0000/213.0000 |
| authorized_tool | query_company_penalty | 114 | A6 | 0.8889 | 24.0000/27.0000 |
| authorized_tool | query_company_penalty | 114 | A7 | 0.9737 | 111.0000/114.0000 |
| authorized_tool | query_company_penalty | 114 | A8 | 0.9737 | 111.0000/114.0000 |
| authorized_tool | query_company_penalty | 114 | A9 | 1.0000 | 843.0000/843.0000 |
| authorized_tool | query_company_penalty | 114 | B1 | 1.0000 | 24/24 |
| authorized_tool | query_company_penalty | 114 | B2 | 0.9296 | 24/24 |
| authorized_tool | query_company_penalty | 114 | B3 | N/A | 23/24 |
| authorized_tool | query_company_penalty | 114 | B4 | N/A | 23/24 |
| authorized_tool | query_company_penalty | 114 | B5 | 1.0000 | 24.0000/24.0000 |
| authorized_tool | query_company_penalty | 114 | B6 | 1.0000 | 39.0000/39.0000 |
| authorized_tool | query_company_penalty | 114 | C1 | 0.9683 | 183.0000/189.0000 |
| authorized_tool | query_company_penalty | 114 | C2 | 1.0000 | 723.0000/723.0000 |
| authorized_tool | query_company_penalty | 114 | C3 | 0.9757 | 723.0000/741.0000 |
| authorized_tool | query_company_penalty | 114 | C4 | 0.9683 | 183.0000/189.0000 |
| authorized_tool | query_company_penalty | 114 | C5 | 0.9767 | 126.0000/129.0000 |
| authorized_tool | query_company_registration | 150 | A1 | 0 | 0/1 |
| authorized_tool | query_company_registration | 150 | A2 | 1.0000 | 435.0000/435.0000 |
| authorized_tool | query_company_registration | 150 | A3 | 1.0000 | 150.0000/150.0000 |
| authorized_tool | query_company_registration | 150 | A4 | 0.0000 | 0.0000/285.0000 |
| authorized_tool | query_company_registration | 150 | A5 | 1.0000 | 285.0000/285.0000 |
| authorized_tool | query_company_registration | 150 | A6 | 1.0000 | 27.0000/27.0000 |
| authorized_tool | query_company_registration | 150 | A7 | 1.0000 | 150.0000/150.0000 |
| authorized_tool | query_company_registration | 150 | A8 | 1.0000 | 150.0000/150.0000 |
| authorized_tool | query_company_registration | 150 | A9 | 1.0000 | 1860.0000/1860.0000 |
| authorized_tool | query_company_registration | 150 | B1 | 1.0000 | 24/24 |
| authorized_tool | query_company_registration | 150 | B2 | 0.8491 | 24/24 |
| authorized_tool | query_company_registration | 150 | B3 | 0.8970 | 24/24 |
| authorized_tool | query_company_registration | 150 | B4 | 0.9741 | 24/24 |
| authorized_tool | query_company_registration | 150 | B5 | 1.0000 | 24.0000/24.0000 |
| authorized_tool | query_company_registration | 150 | B6 | 1.0000 | 39.0000/39.0000 |
| authorized_tool | query_company_registration | 150 | C1 | 1.0000 | 261.0000/261.0000 |
| authorized_tool | query_company_registration | 150 | C2 | 1.0000 | 1740.0000/1740.0000 |
| authorized_tool | query_company_registration | 150 | C3 | 1.0000 | 1740.0000/1740.0000 |
| authorized_tool | query_company_registration | 150 | C4 | 1.0000 | 261.0000/261.0000 |
| authorized_tool | query_company_registration | 150 | C5 | 1.0000 | 231.0000/231.0000 |
| authorized_tool | query_project_award | 162 | A1 | 0 | 0/1 |
| authorized_tool | query_project_award | 162 | A2 | 0.9869 | 453.0000/459.0000 |
| authorized_tool | query_project_award | 162 | A3 | 0.9815 | 159.0000/162.0000 |
| authorized_tool | query_project_award | 162 | A4 | 0.0101 | 3.0000/297.0000 |
| authorized_tool | query_project_award | 162 | A5 | 0.9800 | 294.0000/300.0000 |
| authorized_tool | query_project_award | 162 | A6 | 0.9714 | 102.0000/105.0000 |
| authorized_tool | query_project_award | 162 | A7 | 0.9815 | 159.0000/162.0000 |
| authorized_tool | query_project_award | 162 | A8 | 0.9815 | 159.0000/162.0000 |
| authorized_tool | query_project_award | 162 | A9 | 1.0000 | 2190.0000/2190.0000 |
| authorized_tool | query_project_award | 162 | B1 | N/A | 23/24 |
| authorized_tool | query_project_award | 162 | B2 | N/A | 23/24 |
| authorized_tool | query_project_award | 162 | B3 | N/A | 23/24 |
| authorized_tool | query_project_award | 162 | B4 | N/A | 23/24 |
| authorized_tool | query_project_award | 162 | B5 | 1.0000 | 24.0000/24.0000 |
| authorized_tool | query_project_award | 162 | B6 | 1.0000 | 33.0000/33.0000 |
| authorized_tool | query_project_award | 162 | C1 | 0.9783 | 270.0000/276.0000 |
| authorized_tool | query_project_award | 162 | C2 | 1.0000 | 2070.0000/2070.0000 |
| authorized_tool | query_project_award | 162 | C3 | 0.9914 | 2070.0000/2088.0000 |
| authorized_tool | query_project_award | 162 | C4 | 0.9783 | 270.0000/276.0000 |
| authorized_tool | query_project_award | 162 | C5 | 0.9877 | 240.0000/243.0000 |
| case_category | independent_multi_action | 105 | A1 | 0 | 0/1 |
| case_category | independent_multi_action | 105 | A2 | 1.0000 | 324.0000/324.0000 |
| case_category | independent_multi_action | 105 | A3 | 1.0000 | 105.0000/105.0000 |
| case_category | independent_multi_action | 105 | A4 | 0.0000 | 0.0000/219.0000 |
| case_category | independent_multi_action | 105 | A5 | 1.0000 | 219.0000/219.0000 |
| case_category | independent_multi_action | 105 | A7 | 1.0000 | 105.0000/105.0000 |
| case_category | independent_multi_action | 105 | A8 | 1.0000 | 105.0000/105.0000 |
| case_category | independent_multi_action | 105 | A9 | 1.0000 | 1335.0000/1335.0000 |
| case_category | independent_multi_action | 105 | C1 | 1.0000 | 219.0000/219.0000 |
| case_category | independent_multi_action | 105 | C2 | 1.0000 | 1335.0000/1335.0000 |
| case_category | independent_multi_action | 105 | C3 | 1.0000 | 1335.0000/1335.0000 |
| case_category | independent_multi_action | 105 | C4 | 1.0000 | 219.0000/219.0000 |
| case_category | independent_multi_action | 105 | C5 | 1.0000 | 195.0000/195.0000 |
| case_category | negative | 180 | A1 | 36 | 36/1 |
| case_category | negative | 180 | A10 | 0.7000 | 126.0000/180.0000 |
| case_category | negative | 180 | A2 | 0.4846 | 126.0000/260.0000 |
| case_category | negative | 180 | A4 | 1.0000 | 66.0000/66.0000 |
| case_category | negative | 180 | A7 | 0.7000 | 126.0000/180.0000 |
| case_category | rag_anchor | 210 | A1 | 0 | 0/1 |
| case_category | rag_anchor | 210 | A2 | 1.0000 | 420.0000/420.0000 |
| case_category | rag_anchor | 210 | A3 | 1.0000 | 210.0000/210.0000 |
| case_category | rag_anchor | 210 | A4 | 0.0000 | 0.0000/210.0000 |
| case_category | rag_anchor | 210 | A5 | 1.0000 | 210.0000/210.0000 |
| case_category | rag_anchor | 210 | A7 | 1.0000 | 210.0000/210.0000 |
| case_category | rag_anchor | 210 | A8 | 1.0000 | 210.0000/210.0000 |
| case_category | rag_anchor | 210 | A9 | 1.0000 | 891.0000/891.0000 |
| case_category | rag_anchor | 210 | B1 | N/A | 208/210 |
| case_category | rag_anchor | 210 | B2 | N/A | 208/210 |
| case_category | rag_anchor | 210 | B3 | N/A | 207/210 |
| case_category | rag_anchor | 210 | B4 | N/A | 209/210 |
| case_category | rag_anchor | 210 | B5 | 0.8357 | 175.5000/210.0000 |
| case_category | rag_anchor | 210 | B6 | 0.8190 | 258.0000/315.0000 |
| case_category | result_dependent | 105 | A1 | 0 | 0/1 |
| case_category | result_dependent | 105 | A2 | 0.9808 | 306.0000/312.0000 |
| case_category | result_dependent | 105 | A3 | 0.9714 | 102.0000/105.0000 |
| case_category | result_dependent | 105 | A4 | 0.0145 | 3.0000/207.0000 |
| case_category | result_dependent | 105 | A5 | 0.9714 | 204.0000/210.0000 |
| case_category | result_dependent | 105 | A6 | 0.9714 | 102.0000/105.0000 |
| case_category | result_dependent | 105 | A7 | 0.9714 | 102.0000/105.0000 |
| case_category | result_dependent | 105 | A8 | 0.9714 | 102.0000/105.0000 |
| case_category | result_dependent | 105 | A9 | 1.0000 | 1683.0000/1683.0000 |
| case_category | result_dependent | 105 | C1 | 0.9714 | 204.0000/210.0000 |
| case_category | result_dependent | 105 | C2 | 1.0000 | 1683.0000/1683.0000 |
| case_category | result_dependent | 105 | C3 | 0.9894 | 1683.0000/1701.0000 |
| case_category | result_dependent | 105 | C4 | 0.9714 | 204.0000/210.0000 |
| case_category | result_dependent | 105 | C5 | 0.9836 | 180.0000/183.0000 |
| case_category | single_sql | 120 | A1 | 0 | 0/1 |
| case_category | single_sql | 120 | A2 | 0.9917 | 238.0000/240.0000 |
| case_category | single_sql | 120 | A3 | 1.0000 | 120.0000/120.0000 |
| case_category | single_sql | 120 | A4 | 0.0000 | 0.0000/120.0000 |
| case_category | single_sql | 120 | A5 | 1.0000 | 120.0000/120.0000 |
| case_category | single_sql | 120 | A7 | 0.9833 | 118.0000/120.0000 |
| case_category | single_sql | 120 | A8 | 0.9833 | 118.0000/120.0000 |
| case_category | single_sql | 120 | A9 | 1.0000 | 705.0000/705.0000 |
| case_category | single_sql | 120 | C1 | 1.0000 | 120.0000/120.0000 |
| case_category | single_sql | 120 | C2 | 1.0000 | 705.0000/705.0000 |
| case_category | single_sql | 120 | C3 | 1.0000 | 705.0000/705.0000 |
| case_category | single_sql | 120 | C4 | 1.0000 | 120.0000/120.0000 |
| case_category | single_sql | 120 | C5 | 1.0000 | 87.0000/87.0000 |
| case_category | sql_rag_cross_capability | 120 | A1 | 0 | 0/1 |
| case_category | sql_rag_cross_capability | 120 | A2 | 0.9516 | 354.0000/372.0000 |
| case_category | sql_rag_cross_capability | 120 | A3 | 0.9750 | 117.0000/120.0000 |
| case_category | sql_rag_cross_capability | 120 | A4 | 0.0482 | 12.0000/249.0000 |
| case_category | sql_rag_cross_capability | 120 | A5 | 0.9875 | 237.0000/240.0000 |
| case_category | sql_rag_cross_capability | 120 | A7 | 0.9750 | 117.0000/120.0000 |
| case_category | sql_rag_cross_capability | 120 | A8 | 0.9750 | 117.0000/120.0000 |
| case_category | sql_rag_cross_capability | 120 | A9 | 1.0000 | 1434.0000/1434.0000 |
| case_category | sql_rag_cross_capability | 120 | B1 | N/A | 119/120 |
| case_category | sql_rag_cross_capability | 120 | B2 | N/A | 119/120 |
| case_category | sql_rag_cross_capability | 120 | B3 | N/A | 117/120 |
| case_category | sql_rag_cross_capability | 120 | B4 | N/A | 118/120 |
| case_category | sql_rag_cross_capability | 120 | B5 | 1.0000 | 120.0000/120.0000 |
| case_category | sql_rag_cross_capability | 120 | B6 | 1.0000 | 180.0000/180.0000 |
| case_category | sql_rag_cross_capability | 120 | C1 | 0.9750 | 117.0000/120.0000 |
| case_category | sql_rag_cross_capability | 120 | C2 | 1.0000 | 834.0000/834.0000 |
| case_category | sql_rag_cross_capability | 120 | C3 | 0.9789 | 834.0000/852.0000 |
| case_category | sql_rag_cross_capability | 120 | C4 | 0.9750 | 117.0000/120.0000 |
| case_category | sql_rag_cross_capability | 120 | C5 | 1.0000 | 120.0000/120.0000 |
| finish_status | clarify | 66 | A1 | 0 | 0/1 |
| finish_status | clarify | 66 | A10 | 0.7727 | 51.0000/66.0000 |
| finish_status | clarify | 66 | A2 | 0.7083 | 51.0000/72.0000 |
| finish_status | clarify | 66 | A7 | 0.7727 | 51.0000/66.0000 |
| finish_status | complete | 676 | A1 | 21 | 21/1 |
| finish_status | complete | 676 | A10 | 0.0000 | 0.0000/21.0000 |
| finish_status | complete | 676 | A2 | 0.9596 | 1640.0000/1709.0000 |
| finish_status | complete | 676 | A3 | 0.9954 | 652.0000/655.0000 |
| finish_status | complete | 676 | A4 | 0.0408 | 42.0000/1030.0000 |
| finish_status | complete | 676 | A5 | 0.9970 | 988.0000/991.0000 |
| finish_status | complete | 676 | A6 | 1.0000 | 102.0000/102.0000 |
| finish_status | complete | 676 | A7 | 0.9645 | 652.0000/676.0000 |
| finish_status | complete | 676 | A8 | 0.9954 | 652.0000/655.0000 |
| finish_status | complete | 676 | A9 | 1.0000 | 6044.0000/6044.0000 |
| finish_status | complete | 676 | B1 | N/A | 327/330 |
| finish_status | complete | 676 | B2 | N/A | 327/330 |
| finish_status | complete | 676 | B3 | N/A | 324/330 |
| finish_status | complete | 676 | B4 | N/A | 327/330 |
| finish_status | complete | 676 | B5 | 0.8955 | 295.5000/330.0000 |
| finish_status | complete | 676 | B6 | 0.8848 | 438.0000/495.0000 |
| finish_status | complete | 676 | C1 | 0.9955 | 658.0000/661.0000 |
| finish_status | complete | 676 | C2 | 1.0000 | 4553.0000/4553.0000 |
| finish_status | complete | 676 | C3 | 0.9961 | 4553.0000/4571.0000 |
| finish_status | complete | 676 | C4 | 0.9955 | 658.0000/661.0000 |
| finish_status | complete | 676 | C5 | 1.0000 | 580.0000/580.0000 |
| finish_status | partial | 20 | A1 | 15 | 15/1 |
| finish_status | partial | 20 | A10 | 0.0000 | 0.0000/15.0000 |
| finish_status | partial | 20 | A2 | 0.0290 | 2.0000/69.0000 |
| finish_status | partial | 20 | A3 | 0.4000 | 2.0000/5.0000 |
| finish_status | partial | 20 | A4 | 0.9512 | 39.0000/41.0000 |
| finish_status | partial | 20 | A5 | 0.2500 | 2.0000/8.0000 |
| finish_status | partial | 20 | A6 | 0.0000 | 0.0000/3.0000 |
| finish_status | partial | 20 | A7 | 0.0000 | 0.0000/20.0000 |
| finish_status | partial | 20 | A8 | 0.0000 | 0.0000/5.0000 |
| finish_status | partial | 20 | A9 | 1.0000 | 4.0000/4.0000 |
| finish_status | partial | 20 | C1 | 0.2500 | 2.0000/8.0000 |
| finish_status | partial | 20 | C2 | 1.0000 | 4.0000/4.0000 |
| finish_status | partial | 20 | C3 | 0.1818 | 4.0000/22.0000 |
| finish_status | partial | 20 | C4 | 0.2500 | 2.0000/8.0000 |
| finish_status | partial | 20 | C5 | 0.4000 | 2.0000/5.0000 |
| finish_status | unsupported | 78 | A1 | 0 | 0/1 |
| finish_status | unsupported | 78 | A10 | 0.9615 | 75.0000/78.0000 |
| finish_status | unsupported | 78 | A2 | 0.9615 | 75.0000/78.0000 |
| finish_status | unsupported | 78 | A7 | 0.9615 | 75.0000/78.0000 |
| formal_run | repeat_1 | 280 | A1 | 12 | 12/1 |
| formal_run | repeat_1 | 280 | A10 | 0.7000 | 42.0000/60.0000 |
| formal_run | repeat_1 | 280 | A2 | 0.9132 | 589.0000/645.0000 |
| formal_run | repeat_1 | 280 | A3 | 0.9909 | 218.0000/220.0000 |
| formal_run | repeat_1 | 280 | A4 | 0.0782 | 28.0000/358.0000 |
| formal_run | repeat_1 | 280 | A5 | 0.9910 | 330.0000/333.0000 |
| formal_run | repeat_1 | 280 | A6 | 0.9714 | 34.0000/35.0000 |
| formal_run | repeat_1 | 280 | A7 | 0.9250 | 259.0000/280.0000 |
| formal_run | repeat_1 | 280 | A8 | 0.9864 | 217.0000/220.0000 |
| formal_run | repeat_1 | 280 | A9 | 1.0000 | 2016.0000/2016.0000 |
| formal_run | repeat_1 | 280 | B1 | N/A | 107/110 |
| formal_run | repeat_1 | 280 | B2 | N/A | 107/110 |
| formal_run | repeat_1 | 280 | B3 | N/A | 105/110 |
| formal_run | repeat_1 | 280 | B4 | N/A | 107/110 |
| formal_run | repeat_1 | 280 | B5 | 0.8955 | 98.5000/110.0000 |
| formal_run | repeat_1 | 280 | B6 | 0.8848 | 146.0000/165.0000 |
| formal_run | repeat_1 | 280 | C1 | 0.9865 | 220.0000/223.0000 |
| formal_run | repeat_1 | 280 | C2 | 1.0000 | 1519.0000/1519.0000 |
| formal_run | repeat_1 | 280 | C3 | 0.9922 | 1519.0000/1531.0000 |
| formal_run | repeat_1 | 280 | C4 | 0.9865 | 220.0000/223.0000 |
| formal_run | repeat_1 | 280 | C5 | 0.9949 | 194.0000/195.0000 |
| formal_run | repeat_2 | 280 | A1 | 12 | 12/1 |
| formal_run | repeat_2 | 280 | A10 | 0.7000 | 42.0000/60.0000 |
| formal_run | repeat_2 | 280 | A2 | 0.9189 | 589.0000/641.0000 |
| formal_run | repeat_2 | 280 | A3 | 0.9909 | 218.0000/220.0000 |
| formal_run | repeat_2 | 280 | A4 | 0.0730 | 26.0000/356.0000 |
| formal_run | repeat_2 | 280 | A5 | 0.9910 | 330.0000/333.0000 |
| formal_run | repeat_2 | 280 | A6 | 0.9714 | 34.0000/35.0000 |
| formal_run | repeat_2 | 280 | A7 | 0.9250 | 259.0000/280.0000 |
| formal_run | repeat_2 | 280 | A8 | 0.9864 | 217.0000/220.0000 |
| formal_run | repeat_2 | 280 | A9 | 1.0000 | 2016.0000/2016.0000 |
| formal_run | repeat_2 | 280 | B1 | 0.9793 | 110/110 |
| formal_run | repeat_2 | 280 | B2 | 0.8920 | 110/110 |
| formal_run | repeat_2 | 280 | B3 | N/A | 109/110 |
| formal_run | repeat_2 | 280 | B4 | 0.9669 | 110/110 |
| formal_run | repeat_2 | 280 | B5 | 0.8955 | 98.5000/110.0000 |
| formal_run | repeat_2 | 280 | B6 | 0.8848 | 146.0000/165.0000 |
| formal_run | repeat_2 | 280 | C1 | 0.9865 | 220.0000/223.0000 |
| formal_run | repeat_2 | 280 | C2 | 1.0000 | 1519.0000/1519.0000 |
| formal_run | repeat_2 | 280 | C3 | 0.9922 | 1519.0000/1531.0000 |
| formal_run | repeat_2 | 280 | C4 | 0.9865 | 220.0000/223.0000 |
| formal_run | repeat_2 | 280 | C5 | 0.9949 | 194.0000/195.0000 |
| formal_run | repeat_3 | 280 | A1 | 12 | 12/1 |
| formal_run | repeat_3 | 280 | A10 | 0.7000 | 42.0000/60.0000 |
| formal_run | repeat_3 | 280 | A2 | 0.9190 | 590.0000/642.0000 |
| formal_run | repeat_3 | 280 | A3 | 0.9909 | 218.0000/220.0000 |
| formal_run | repeat_3 | 280 | A4 | 0.0756 | 27.0000/357.0000 |
| formal_run | repeat_3 | 280 | A5 | 0.9910 | 330.0000/333.0000 |
| formal_run | repeat_3 | 280 | A6 | 0.9714 | 34.0000/35.0000 |
| formal_run | repeat_3 | 280 | A7 | 0.9286 | 260.0000/280.0000 |
| formal_run | repeat_3 | 280 | A8 | 0.9909 | 218.0000/220.0000 |
| formal_run | repeat_3 | 280 | A9 | 1.0000 | 2016.0000/2016.0000 |
| formal_run | repeat_3 | 280 | B1 | 0.9814 | 110/110 |
| formal_run | repeat_3 | 280 | B2 | 0.8981 | 110/110 |
| formal_run | repeat_3 | 280 | B3 | 0.8277 | 110/110 |
| formal_run | repeat_3 | 280 | B4 | 0.9640 | 110/110 |
| formal_run | repeat_3 | 280 | B5 | 0.8955 | 98.5000/110.0000 |
| formal_run | repeat_3 | 280 | B6 | 0.8848 | 146.0000/165.0000 |
| formal_run | repeat_3 | 280 | C1 | 0.9865 | 220.0000/223.0000 |
| formal_run | repeat_3 | 280 | C2 | 1.0000 | 1519.0000/1519.0000 |
| formal_run | repeat_3 | 280 | C3 | 0.9922 | 1519.0000/1531.0000 |
| formal_run | repeat_3 | 280 | C4 | 0.9865 | 220.0000/223.0000 |
| formal_run | repeat_3 | 280 | C5 | 0.9949 | 194.0000/195.0000 |
| normal_adversarial_or_fault | adversarial | 54 | A1 | 9 | 9/1 |
| normal_adversarial_or_fault | adversarial | 54 | A10 | 0.7222 | 39.0000/54.0000 |
| normal_adversarial_or_fault | adversarial | 54 | A2 | 0.5909 | 39.0000/66.0000 |
| normal_adversarial_or_fault | adversarial | 54 | A4 | 1.0000 | 9.0000/9.0000 |
| normal_adversarial_or_fault | adversarial | 54 | A7 | 0.7222 | 39.0000/54.0000 |
| normal_adversarial_or_fault | normal | 786 | A1 | 27 | 27/1 |
| normal_adversarial_or_fault | normal | 786 | A10 | 0.6905 | 87.0000/126.0000 |
| normal_adversarial_or_fault | normal | 786 | A2 | 0.9286 | 1729.0000/1862.0000 |
| normal_adversarial_or_fault | normal | 786 | A3 | 0.9909 | 654.0000/660.0000 |
| normal_adversarial_or_fault | normal | 786 | A4 | 0.0678 | 72.0000/1062.0000 |
| normal_adversarial_or_fault | normal | 786 | A5 | 0.9910 | 990.0000/999.0000 |
| normal_adversarial_or_fault | normal | 786 | A6 | 0.9714 | 102.0000/105.0000 |
| normal_adversarial_or_fault | normal | 786 | A7 | 0.9402 | 739.0000/786.0000 |
| normal_adversarial_or_fault | normal | 786 | A8 | 0.9879 | 652.0000/660.0000 |
| normal_adversarial_or_fault | normal | 786 | A9 | 1.0000 | 6048.0000/6048.0000 |
| normal_adversarial_or_fault | normal | 786 | B1 | N/A | 327/330 |
| normal_adversarial_or_fault | normal | 786 | B2 | N/A | 327/330 |
| normal_adversarial_or_fault | normal | 786 | B3 | N/A | 324/330 |
| normal_adversarial_or_fault | normal | 786 | B4 | N/A | 327/330 |
| normal_adversarial_or_fault | normal | 786 | B5 | 0.8955 | 295.5000/330.0000 |
| normal_adversarial_or_fault | normal | 786 | B6 | 0.8848 | 438.0000/495.0000 |
| normal_adversarial_or_fault | normal | 786 | C1 | 0.9865 | 660.0000/669.0000 |
| normal_adversarial_or_fault | normal | 786 | C2 | 1.0000 | 4557.0000/4557.0000 |
| normal_adversarial_or_fault | normal | 786 | C3 | 0.9922 | 4557.0000/4593.0000 |
| normal_adversarial_or_fault | normal | 786 | C4 | 0.9865 | 660.0000/669.0000 |
| normal_adversarial_or_fault | normal | 786 | C5 | 0.9949 | 582.0000/585.0000 |
| normal_empty_or_failure | empty_field | 21 | A1 | 0 | 0/1 |
| normal_empty_or_failure | empty_field | 21 | A2 | 0.9608 | 49.0000/51.0000 |
| normal_empty_or_failure | empty_field | 21 | A3 | 1.0000 | 21.0000/21.0000 |
| normal_empty_or_failure | empty_field | 21 | A4 | 0.0000 | 0.0000/30.0000 |
| normal_empty_or_failure | empty_field | 21 | A5 | 1.0000 | 30.0000/30.0000 |
| normal_empty_or_failure | empty_field | 21 | A7 | 0.9048 | 19.0000/21.0000 |
| normal_empty_or_failure | empty_field | 21 | A8 | 0.9048 | 19.0000/21.0000 |
| normal_empty_or_failure | empty_field | 21 | A9 | 1.0000 | 225.0000/225.0000 |
| normal_empty_or_failure | empty_field | 21 | B1 | 1.0000 | 6/6 |
| normal_empty_or_failure | empty_field | 21 | B2 | 0.8630 | 6/6 |
| normal_empty_or_failure | empty_field | 21 | B3 | 0.8587 | 6/6 |
| normal_empty_or_failure | empty_field | 21 | B4 | 0.9833 | 6/6 |
| normal_empty_or_failure | empty_field | 21 | B5 | 1.0000 | 6.0000/6.0000 |
| normal_empty_or_failure | empty_field | 21 | B6 | 1.0000 | 12.0000/12.0000 |
| normal_empty_or_failure | empty_field | 21 | C1 | 1.0000 | 24.0000/24.0000 |
| normal_empty_or_failure | empty_field | 21 | C2 | 1.0000 | 195.0000/195.0000 |
| normal_empty_or_failure | empty_field | 21 | C3 | 1.0000 | 195.0000/195.0000 |
| normal_empty_or_failure | empty_field | 21 | C4 | 1.0000 | 24.0000/24.0000 |
| normal_empty_or_failure | empty_field | 21 | C5 | 1.0000 | 24.0000/24.0000 |
| normal_empty_or_failure | no_match | 84 | A1 | 0 | 0/1 |
| normal_empty_or_failure | no_match | 84 | A2 | 0.9733 | 219.0000/225.0000 |
| normal_empty_or_failure | no_match | 84 | A3 | 0.9643 | 81.0000/84.0000 |
| normal_empty_or_failure | no_match | 84 | A4 | 0.0213 | 3.0000/141.0000 |
| normal_empty_or_failure | no_match | 84 | A5 | 0.9583 | 138.0000/144.0000 |
| normal_empty_or_failure | no_match | 84 | A6 | 0.8889 | 24.0000/27.0000 |
| normal_empty_or_failure | no_match | 84 | A7 | 0.9643 | 81.0000/84.0000 |
| normal_empty_or_failure | no_match | 84 | A8 | 0.9643 | 81.0000/84.0000 |
| normal_empty_or_failure | no_match | 84 | A9 | 1.0000 | 378.0000/378.0000 |
| normal_empty_or_failure | no_match | 84 | C1 | 0.9583 | 138.0000/144.0000 |
| normal_empty_or_failure | no_match | 84 | C2 | 1.0000 | 378.0000/378.0000 |
| normal_empty_or_failure | no_match | 84 | C3 | 0.9545 | 378.0000/396.0000 |
| normal_empty_or_failure | no_match | 84 | C4 | 0.9583 | 138.0000/144.0000 |
| normal_empty_or_failure | no_match | 84 | C5 | 0.9500 | 57.0000/60.0000 |
| normal_empty_or_failure | normal_result | 555 | A1 | 0 | 0/1 |
| normal_empty_or_failure | normal_result | 555 | A2 | 0.9871 | 1374.0000/1392.0000 |
| normal_empty_or_failure | normal_result | 555 | A3 | 0.9946 | 552.0000/555.0000 |
| normal_empty_or_failure | normal_result | 555 | A4 | 0.0144 | 12.0000/834.0000 |
| normal_empty_or_failure | normal_result | 555 | A5 | 0.9964 | 822.0000/825.0000 |
| normal_empty_or_failure | normal_result | 555 | A6 | 1.0000 | 78.0000/78.0000 |
| normal_empty_or_failure | normal_result | 555 | A7 | 0.9946 | 552.0000/555.0000 |
| normal_empty_or_failure | normal_result | 555 | A8 | 0.9946 | 552.0000/555.0000 |
| normal_empty_or_failure | normal_result | 555 | A9 | 1.0000 | 5445.0000/5445.0000 |
| normal_empty_or_failure | normal_result | 555 | B1 | N/A | 321/324 |
| normal_empty_or_failure | normal_result | 555 | B2 | N/A | 321/324 |
| normal_empty_or_failure | normal_result | 555 | B3 | N/A | 318/324 |
| normal_empty_or_failure | normal_result | 555 | B4 | N/A | 321/324 |
| normal_empty_or_failure | normal_result | 555 | B5 | 0.8935 | 289.5000/324.0000 |
| normal_empty_or_failure | normal_result | 555 | B6 | 0.8820 | 426.0000/483.0000 |
| normal_empty_or_failure | normal_result | 555 | C1 | 0.9940 | 498.0000/501.0000 |
| normal_empty_or_failure | normal_result | 555 | C2 | 1.0000 | 3984.0000/3984.0000 |
| normal_empty_or_failure | normal_result | 555 | C3 | 0.9955 | 3984.0000/4002.0000 |
| normal_empty_or_failure | normal_result | 555 | C4 | 0.9940 | 498.0000/501.0000 |
| normal_empty_or_failure | normal_result | 555 | C5 | 1.0000 | 501.0000/501.0000 |
| normal_empty_or_failure | not_applicable | 180 | A1 | 36 | 36/1 |
| normal_empty_or_failure | not_applicable | 180 | A10 | 0.7000 | 126.0000/180.0000 |
| normal_empty_or_failure | not_applicable | 180 | A2 | 0.4846 | 126.0000/260.0000 |
| normal_empty_or_failure | not_applicable | 180 | A4 | 1.0000 | 66.0000/66.0000 |
| normal_empty_or_failure | not_applicable | 180 | A7 | 0.7000 | 126.0000/180.0000 |
| rag_single_or_multi_hop | multi_hop | 165 | A1 | 0 | 0/1 |
| rag_single_or_multi_hop | multi_hop | 165 | A2 | 0.9924 | 390.0000/393.0000 |
| rag_single_or_multi_hop | multi_hop | 165 | A3 | 1.0000 | 165.0000/165.0000 |
| rag_single_or_multi_hop | multi_hop | 165 | A4 | 0.0000 | 0.0000/225.0000 |
| rag_single_or_multi_hop | multi_hop | 165 | A5 | 1.0000 | 225.0000/225.0000 |
| rag_single_or_multi_hop | multi_hop | 165 | A7 | 1.0000 | 165.0000/165.0000 |
| rag_single_or_multi_hop | multi_hop | 165 | A8 | 1.0000 | 165.0000/165.0000 |
| rag_single_or_multi_hop | multi_hop | 165 | A9 | 1.0000 | 1233.0000/1233.0000 |
| rag_single_or_multi_hop | multi_hop | 165 | B1 | N/A | 163/165 |
| rag_single_or_multi_hop | multi_hop | 165 | B2 | N/A | 163/165 |
| rag_single_or_multi_hop | multi_hop | 165 | B3 | N/A | 160/165 |
| rag_single_or_multi_hop | multi_hop | 165 | B4 | N/A | 163/165 |
| rag_single_or_multi_hop | multi_hop | 165 | B5 | 0.8636 | 142.5000/165.0000 |
| rag_single_or_multi_hop | multi_hop | 165 | B6 | 0.8636 | 285.0000/330.0000 |
| rag_single_or_multi_hop | multi_hop | 165 | C1 | 1.0000 | 60.0000/60.0000 |
| rag_single_or_multi_hop | multi_hop | 165 | C2 | 1.0000 | 444.0000/444.0000 |
| rag_single_or_multi_hop | multi_hop | 165 | C3 | 1.0000 | 444.0000/444.0000 |
| rag_single_or_multi_hop | multi_hop | 165 | C4 | 1.0000 | 60.0000/60.0000 |
| rag_single_or_multi_hop | multi_hop | 165 | C5 | 1.0000 | 60.0000/60.0000 |
| rag_single_or_multi_hop | not_applicable | 510 | A1 | 36 | 36/1 |
| rag_single_or_multi_hop | not_applicable | 510 | A10 | 0.7000 | 126.0000/180.0000 |
| rag_single_or_multi_hop | not_applicable | 510 | A2 | 0.8750 | 994.0000/1136.0000 |
| rag_single_or_multi_hop | not_applicable | 510 | A3 | 0.9909 | 327.0000/330.0000 |
| rag_single_or_multi_hop | not_applicable | 510 | A4 | 0.1127 | 69.0000/612.0000 |
| rag_single_or_multi_hop | not_applicable | 510 | A5 | 0.9891 | 543.0000/549.0000 |
| rag_single_or_multi_hop | not_applicable | 510 | A6 | 0.9714 | 102.0000/105.0000 |
| rag_single_or_multi_hop | not_applicable | 510 | A7 | 0.8843 | 451.0000/510.0000 |
| rag_single_or_multi_hop | not_applicable | 510 | A8 | 0.9848 | 325.0000/330.0000 |
| rag_single_or_multi_hop | not_applicable | 510 | A9 | 1.0000 | 3723.0000/3723.0000 |
| rag_single_or_multi_hop | not_applicable | 510 | C1 | 0.9891 | 543.0000/549.0000 |
| rag_single_or_multi_hop | not_applicable | 510 | C2 | 1.0000 | 3723.0000/3723.0000 |
| rag_single_or_multi_hop | not_applicable | 510 | C3 | 0.9952 | 3723.0000/3741.0000 |
| rag_single_or_multi_hop | not_applicable | 510 | C4 | 0.9891 | 543.0000/549.0000 |
| rag_single_or_multi_hop | not_applicable | 510 | C5 | 0.9935 | 462.0000/465.0000 |
| rag_single_or_multi_hop | single_hop | 165 | A1 | 0 | 0/1 |
| rag_single_or_multi_hop | single_hop | 165 | A2 | 0.9624 | 384.0000/399.0000 |
| rag_single_or_multi_hop | single_hop | 165 | A3 | 0.9818 | 162.0000/165.0000 |
| rag_single_or_multi_hop | single_hop | 165 | A4 | 0.0513 | 12.0000/234.0000 |
| rag_single_or_multi_hop | single_hop | 165 | A5 | 0.9867 | 222.0000/225.0000 |
| rag_single_or_multi_hop | single_hop | 165 | A7 | 0.9818 | 162.0000/165.0000 |
| rag_single_or_multi_hop | single_hop | 165 | A8 | 0.9818 | 162.0000/165.0000 |
| rag_single_or_multi_hop | single_hop | 165 | A9 | 1.0000 | 1092.0000/1092.0000 |
| rag_single_or_multi_hop | single_hop | 165 | B1 | N/A | 164/165 |
| rag_single_or_multi_hop | single_hop | 165 | B2 | N/A | 164/165 |
| rag_single_or_multi_hop | single_hop | 165 | B3 | N/A | 164/165 |
| rag_single_or_multi_hop | single_hop | 165 | B4 | N/A | 164/165 |
| rag_single_or_multi_hop | single_hop | 165 | B5 | 0.9273 | 153.0000/165.0000 |
| rag_single_or_multi_hop | single_hop | 165 | B6 | 0.9273 | 153.0000/165.0000 |
| rag_single_or_multi_hop | single_hop | 165 | C1 | 0.9500 | 57.0000/60.0000 |
| rag_single_or_multi_hop | single_hop | 165 | C2 | 1.0000 | 390.0000/390.0000 |
| rag_single_or_multi_hop | single_hop | 165 | C3 | 0.9559 | 390.0000/408.0000 |
| rag_single_or_multi_hop | single_hop | 165 | C4 | 0.9500 | 57.0000/60.0000 |
| rag_single_or_multi_hop | single_hop | 165 | C5 | 1.0000 | 60.0000/60.0000 |
| single_or_multi_entity | multi_entity | 162 | A1 | 0 | 0/1 |
| single_or_multi_entity | multi_entity | 162 | A2 | 0.9876 | 477.0000/483.0000 |
| single_or_multi_entity | multi_entity | 162 | A3 | 0.9815 | 159.0000/162.0000 |
| single_or_multi_entity | multi_entity | 162 | A4 | 0.0093 | 3.0000/321.0000 |
| single_or_multi_entity | multi_entity | 162 | A5 | 0.9815 | 318.0000/324.0000 |
| single_or_multi_entity | multi_entity | 162 | A6 | 0.9714 | 102.0000/105.0000 |
| single_or_multi_entity | multi_entity | 162 | A7 | 0.9815 | 159.0000/162.0000 |
| single_or_multi_entity | multi_entity | 162 | A8 | 0.9815 | 159.0000/162.0000 |
| single_or_multi_entity | multi_entity | 162 | A9 | 1.0000 | 2520.0000/2520.0000 |
| single_or_multi_entity | multi_entity | 162 | C1 | 0.9815 | 318.0000/324.0000 |
| single_or_multi_entity | multi_entity | 162 | C2 | 1.0000 | 2520.0000/2520.0000 |
| single_or_multi_entity | multi_entity | 162 | C3 | 0.9929 | 2520.0000/2538.0000 |
| single_or_multi_entity | multi_entity | 162 | C4 | 0.9815 | 318.0000/324.0000 |
| single_or_multi_entity | multi_entity | 162 | C5 | 0.9899 | 294.0000/297.0000 |
| single_or_multi_entity | not_applicable | 390 | A1 | 36 | 36/1 |
| single_or_multi_entity | not_applicable | 390 | A10 | 0.7000 | 126.0000/180.0000 |
| single_or_multi_entity | not_applicable | 390 | A2 | 0.8029 | 546.0000/680.0000 |
| single_or_multi_entity | not_applicable | 390 | A3 | 1.0000 | 210.0000/210.0000 |
| single_or_multi_entity | not_applicable | 390 | A4 | 0.2391 | 66.0000/276.0000 |
| single_or_multi_entity | not_applicable | 390 | A5 | 1.0000 | 210.0000/210.0000 |
| single_or_multi_entity | not_applicable | 390 | A7 | 0.8615 | 336.0000/390.0000 |
| single_or_multi_entity | not_applicable | 390 | A8 | 1.0000 | 210.0000/210.0000 |
| single_or_multi_entity | not_applicable | 390 | A9 | 1.0000 | 891.0000/891.0000 |
| single_or_multi_entity | not_applicable | 390 | B1 | N/A | 208/210 |
| single_or_multi_entity | not_applicable | 390 | B2 | N/A | 208/210 |
| single_or_multi_entity | not_applicable | 390 | B3 | N/A | 207/210 |
| single_or_multi_entity | not_applicable | 390 | B4 | N/A | 209/210 |
| single_or_multi_entity | not_applicable | 390 | B5 | 0.8357 | 175.5000/210.0000 |
| single_or_multi_entity | not_applicable | 390 | B6 | 0.8190 | 258.0000/315.0000 |
| single_or_multi_entity | single_entity | 288 | A1 | 0 | 0/1 |
| single_or_multi_entity | single_entity | 288 | A2 | 0.9739 | 745.0000/765.0000 |
| single_or_multi_entity | single_entity | 288 | A3 | 0.9896 | 285.0000/288.0000 |
| single_or_multi_entity | single_entity | 288 | A4 | 0.0253 | 12.0000/474.0000 |
| single_or_multi_entity | single_entity | 288 | A5 | 0.9935 | 462.0000/465.0000 |
| single_or_multi_entity | single_entity | 288 | A7 | 0.9826 | 283.0000/288.0000 |
| single_or_multi_entity | single_entity | 288 | A8 | 0.9826 | 283.0000/288.0000 |
| single_or_multi_entity | single_entity | 288 | A9 | 1.0000 | 2637.0000/2637.0000 |
| single_or_multi_entity | single_entity | 288 | B1 | N/A | 119/120 |
| single_or_multi_entity | single_entity | 288 | B2 | N/A | 119/120 |
| single_or_multi_entity | single_entity | 288 | B3 | N/A | 117/120 |
| single_or_multi_entity | single_entity | 288 | B4 | N/A | 118/120 |
| single_or_multi_entity | single_entity | 288 | B5 | 1.0000 | 120.0000/120.0000 |
| single_or_multi_entity | single_entity | 288 | B6 | 1.0000 | 180.0000/180.0000 |
| single_or_multi_entity | single_entity | 288 | C1 | 0.9913 | 342.0000/345.0000 |
| single_or_multi_entity | single_entity | 288 | C2 | 1.0000 | 2037.0000/2037.0000 |
| single_or_multi_entity | single_entity | 288 | C3 | 0.9912 | 2037.0000/2055.0000 |
| single_or_multi_entity | single_entity | 288 | C4 | 0.9913 | 342.0000/345.0000 |
| single_or_multi_entity | single_entity | 288 | C5 | 1.0000 | 288.0000/288.0000 |

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
