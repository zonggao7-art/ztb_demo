# Execution Plans

本目录只存放仍在推进或已完成历史归档的执行计划，避免与审查报告、验证报告和长期设计文档混放。

## 当前计划

| 文件 | 说明 | 状态 |
| --- | --- | --- |
| [hybrid_retrieval_refactor_plan.md](hybrid_retrieval_refactor_plan.md) | 混合检索重构与 Milvus 升级前期准备的总计划 | A/B 与 POC C1-C3 已完成 |
| [milvus_upgrade_poc_execution_plan.md](milvus_upgrade_poc_execution_plan.md) | Milvus 2.5+/2.6 小批量数据写入与混合检索验证方案 | POC 8/8 已通过 |
| [code_optimization_execution_plan_hybrid_retrieval.md](code_optimization_execution_plan_hybrid_retrieval.md) | 混合检索代码优化执行方案 | 已被 POC 结果和后续工程化审查部分覆盖 |
| [pipeline_refactor_execution_plan.md](pipeline_refactor_execution_plan.md) | `public_kb` 在线检索、生成层、离线入库 Pipeline 的分阶段重构方案 | 已完成，待全量入库 |
| [public_kb_directory_consolidation_plan_20260829.md](public_kb_directory_consolidation_plan_20260829.md) | `public_kb` 目录结构收敛、兼容层治理与回归验证方案 | 已完成；仅保留 `qa_chain.py` 稳定入口 |
| [public_kb_optimization_execution_plan_20260830.md](public_kb_optimization_execution_plan_20260830.md) | 基于两份审查报告的总优化计划（M0–M6） | 已全部完成 |
| [public_kb_optimization_execution_detail_20260830.md](public_kb_optimization_execution_detail_20260830.md) | M0–M6 细化任务卡 + 附录 A–I 执行状态与验证记录 | 已全部完成，整体验证 V-1/V-2/V-3/V-5/V-6 通过；V-4 待 MinerU |
| [public_kb_m0_baseline_20260830.md](../public_kb_m0_baseline_20260830.md) | M0 基线冻结记录（239 passed 起点） | 已存档 |
| [pdf_routing_pipeline_plan_20260831.md](pdf_routing_pipeline_plan_20260831.md) | 原两层 PDF 路由方案 | **已被三档计划吸收，不再作为独立执行基线** |
| [pdf_legal_tiered_routing_execution_plan_20260831.md](pdf_legal_tiered_routing_execution_plan_20260831.md) | **PDF 三档路由（Tier A 快路径 / Tier B 表格 / Tier C MinerU）+ golden set 验收** — 最新执行基线 | 待执行（L0–L6） |
| [pdf_tiered_server_deploy_supplement_20260831.md](pdf_tiered_server_deploy_supplement_20260831.md) | 公司服务器 MinerU 部署拓扑 / 数据回流 / 远程解析协议 / 一键迁移 / 组员交付 | 待执行（依赖三档计划） |

## 文档放置规则

- 执行计划、阶段任务、迁移步骤：放在本目录。
- 代码审查报告、POC 验证报告、测试报告：继续放在 `docs/` 根目录。
- 长期架构设计：继续放在 `docs/` 根目录或其他既有专题目录。

当前最新代码结构审查入口是 [../code_review_two_pipelines.md](../code_review_two_pipelines.md)；当前 POC 验证证据入口是 [../hybrid_poc_verification_report.md](../hybrid_poc_verification_report.md)；当前最终验证报告入口是 [../pipeline_refactor_final_validation_20260829.md](../pipeline_refactor_final_validation_20260829.md)。
