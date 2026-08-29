# Execution Plans

本目录只存放仍在推进或已完成历史归档的执行计划，避免与审查报告、验证报告和长期设计文档混放。

## 当前计划

| 文件 | 说明 | 状态 |
| --- | --- | --- |
| [hybrid_retrieval_refactor_plan.md](hybrid_retrieval_refactor_plan.md) | 混合检索重构与 Milvus 升级前期准备的总计划 | A/B 与 POC C1-C3 已完成 |
| [milvus_upgrade_poc_execution_plan.md](milvus_upgrade_poc_execution_plan.md) | Milvus 2.5+/2.6 小批量数据写入与混合检索验证方案 | POC 8/8 已通过 |
| [code_optimization_execution_plan_hybrid_retrieval.md](code_optimization_execution_plan_hybrid_retrieval.md) | 混合检索代码优化执行方案 | 已被 POC 结果和后续工程化审查部分覆盖 |
| [pipeline_refactor_execution_plan.md](pipeline_refactor_execution_plan.md) | `public_kb` 在线检索、生成层、离线入库 Pipeline 的分阶段重构方案 | 已完成，待全量入库 |

## 文档放置规则

- 执行计划、阶段任务、迁移步骤：放在本目录。
- 代码审查报告、POC 验证报告、测试报告：继续放在 `docs/` 根目录。
- 长期架构设计：继续放在 `docs/` 根目录或其他既有专题目录。

当前最新代码结构审查入口是 [../code_review_two_pipelines.md](../code_review_two_pipelines.md)；当前 POC 验证证据入口是 [../hybrid_poc_verification_report.md](../hybrid_poc_verification_report.md)；当前最终验证报告入口是 [../pipeline_refactor_final_validation_20260829.md](../pipeline_refactor_final_validation_20260829.md)。
