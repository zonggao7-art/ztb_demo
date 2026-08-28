# 混合检索 POC 验证报告（新版 Milvus v2.6.23）

> 验证日期: 2026-08-28
> 环境: Docker Desktop v29.7.2 / Milvus v2.6.23 (POC 栈, gRPC 19531) / pymilvus 3.0.1 / Python 3.11.15
> 实验集合: public_kb_hybrid_poc_v1（50 chunks, BM25 Function + 中文 analyzer）
> 结论: **混合检索链路全面打通，8/8 用例通过**

---

## 一、执行过程

| 阶段 | 结果 | 备注 |
|------|------|------|
| C1 部署 | ✅ | 三容器 healthy；版本 2.6.23；BM25 Function 服务端承接探测 PASS |
| C2 入库 | ✅ | 50/50 行；fields/functions/indexes 四项全对；重启持久性通过 |
| C3 首轮 | 7/8 | case2 BM25 单路 0 命中 → 定位为中文分词缺失（方案第 4 节预判命中） |
| C3 复测 | **8/8** | 补 `analyzer_params={"type":"chinese"}` 重建集合后全过 |

## 二、首轮失败与修复（关键发现）

**现象**: BM25 单路查询"招标方式有哪些？"返回 0 命中；hybrid 融合的 10 条全部来自 dense 路。

**根因**: `milvus_store._build_schema` 仅设 `enable_analyzer=True` 未配分词器，
默认 standard 分析器对中文整句不切词 → 建索引与查询两侧 token 无法匹配。

**修复**（本轮已提交代码）:
- `public_kb/config.py`: 新增 `bm25_analyzer_type: str = "chinese"`（Milvus 内置 jieba 系中文分析器）;
- `public_kb/milvus_store.py`: enable_bm25 时为 text 字段追加 `analyzer_params={"type": <bm25_analyzer_type>}`。

## 三、修复前后对比（同一问题/同一集合/50 chunks）

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| case2 BM25 单路命中 | 0 | **5 条**, top_score=3.985（BM25 量纲）, 命中法规文档 |
| case3 稀疏路贡献 | 0 | **5 条**（融合 10 = dense 5 + sparse 5） |
| 全链路 retrieval_mode | hybrid_rerank（稀疏路空转） | **hybrid_rerank**（双通道真实参与） |

## 四、最终 8 用例明细（详见 test_report/hybrid_poc_c3_results.json）

| # | 用例 | 结果 | 关键观测 |
|---|------|------|----------|
| 1 | dense 单路 | PASS | 5 hits, top COSINE 0.673 |
| 2 | BM25 单路（原文直查） | PASS | 5 hits, top 3.985, 命中法规文档 |
| 3 | hybrid+RRF（原生双路） | PASS | 融合 10 = dense 5 + sparse 5 |
| 4 | 全链路（真实 Reranker） | PASS | mode=hybrid_rerank, reranker=success |
| 5 | Reranker 故障降级 | PASS | mode=hybrid_rrf, fallback=reranker_failed, 仍出 5 条（RRF 序） |
| 6 | 无关问题拒答 | PASS | 拒答话术, citations=0 |
| 7 | 引用溯源 R1-R7 | PASS | all_passed=true |
| 8 | 严格模式端到端 | PASS | 全程无 hybrid 异常 = 无静默降级 |

注: case4 的回答为"资料不足以完整回答"属真实拒答边界（50 条样本未覆盖该主题全文），
不影响链路判定; 全量数据迁移后由 citation eval（106 题）继续评估质量。

## 五、遗留事项

1. **代理抖动**: 首轮运行遭遇系统代理 TLS 中断（Embedding API），重试即恢复;
   网络不稳环境建议为 SiliconFlow 域名配置代理例外。
2. **case4 sources=1**: 动态阈值在 reranker 高置信时收紧所致，属预期调优旋钮
   （_adaptive_threshold），后续以 citation eval 数据驱动微调。
3. **生产切换前置清单**: 按主方案第七节跑 106 题 citation eval A/B（hybrid vs dense-only）
   → 数据达标后安排 2.4.0 → 2.6.x 正式切换窗口。
4. POC 栈回收: `docker compose -p milvus-v26 -f milvus/docker-compose-v26.yml down`（保留卷供复测）。
