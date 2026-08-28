# Milvus 2.5 升级前混合检索预适配执行方案（重构版）

> 编制日期: 2026-08-27（取代同路径旧版"纯稠密补偿"方案）
> 核心目标: 当前代码完成 2.5 前置适配 → 升级当天零代码变更 → 直接重建入库即激活混合检索
> 约束: 本批次不改 docker-compose 镜像版本 / 不重建在线集合 / 不迁移数据

---

## 一、目标与非目标

### 目标

1. 离线入库链路改造为"版本探测驱动的双态 Schema":
   服务端 < 2.5 走现有纯稠密 Schema（行为零变化），
   服务端 >= 2.5 自动升级为含稀疏通道的完整 Schema。
2. 在线检索侧修正既有缺陷，使 hybrid 分支在 2.5 环境下首次触发即为正确逻辑。
3. 提供升级日 Runbook 与验收命令序列，混合检索通/不通均可一步定位。
4. 评测驱动调优: 用 106 题基线对比 hybrid vs dense-only，数据说话再调参。

### 非目标

- 不在本批次执行镜像升级与集合重建（属后续基础设施批次）。
- 不引入 rank_bm25 等本地稀疏库做客户端模拟。
- 不调整 chunk 切片参数与 chunk_uid 派生口径。

---

## 二、现状诊断结论

### 2.1 入库侧（回答第一问）: 稀疏索引完全缺失

| 应有组件 | 现状 | 影响 |
|----------|------|------|
| `sparse_vector` SPARSE_FLOAT_VECTOR 字段 | 缺失 | 检索侧探测恒 False |
| BM25 Function (text -> sparse_vector) | 缺失 | 无稀疏向量生成机制 |
| SPARSE_INVERTED_INDEX / BM25 索引 | 缺失 | 无法倒排召回 |

`milvus_store.initialize_collection()` 仅创建 id/text/vector 三字段与单一 IVF_FLAT(COSINE) 索引。
此为混合检索不通的根本原因（必要条件），另需服务端 >= 2.5 才能承接 Function（充分条件）。

### 2.2 在线检索侧（回答第二问）: 骨架已备，两处待修

已有能力（无需重写）:
- `_retrieve()` 已实现 sparse_vector 探测 → 双路 AnnSearchRequest → RRFRanker 融合 → Reranker 精排 → 自适应阈值全流程。

必须修正:
1. **metric_type 硬编码错误**: sparse 路声明 `"IP"`，而 BM25 Function 索引的度量是 BM25。
   IP 适用于客户端自行生成稀疏向量的场景；Function 自动生成场景下声明 IP 将在 2.5 触发不匹配异常或静默偏差。
   改法: 移除硬编码 metric_type，改传 `{"drop_ratio_search": 可配}`，由索引自身度量生效。
2. **schema 探测未缓存**: 每次 query 都发起 `describe_collection()` RPC 判断 sparse 字段是否存在。
   Schema 静态不变，应首查后缓存。

一并修复（与方向无关的正确性隐患）:
3. 降级路 langchain 包装器返回 score 的 distance/similarity 歧义，过滤方向可能反转。
4. 启动日志宣称"bge-m3 混合检索模式"，实际恒走降级路，需回归事实标识。

---

## 三、总体策略: 版本探测驱动的双态切换

```
initialize_collection(documents)
        │
        ▼
_probe_server_supports_sparse(client)      ← get_server_version() 解析
        │
   >= 2.5.0? ──否──► 旧分支: 三字段 + 单索引（现状路径, 行为不变）
        │是
        ▼
新分支: 五字段(text/vector/sparse_vector/id[+动态]) 
        + BM25 Function + 双索引(IVF_FLAT + SPARSE_INVERTED_INDEX)
        │
        ▼
create 后 describe_collection 校验 functions 存在
        │缺失 ──► fail-fast 抛异常（防 Function 静默丢弃类问题）
        │存在
        ▼
flush -> load -> 批量插入（现有 _batch_insert 不感知差异, 全部复用）
```

**为什么能保证"升级后零代码变更"**: 分支条件绑定的是运行时服务端真实版本而非配置文件。
当前 2.4 环境 → 自然走旧分支（与今天行为逐字节一致）; 升级后重启 → 探测为 2.5 → 新建集合自动带稀疏三件套。
代码、流程、人工操作均无需任何额外变更，重建即生效。

pymilvus 客户端侧说明: Function/FunctionType 构造 API 已在当前依赖可用（test/verify_sparse_root_cause.py 已验证可构造），
本改造不需要更新 requirements 版本约束。

---

## 四、Step 1 入库链路改造（ milvus_store.py , 约 1 天）

> 执行时点: **立即**。在 2.4 环境下合并即可，新分支处于休眠态，风险极低。

### 1.1 Schema/Index 构建抽离

将 initialize_collection 内联的 schema/index_params 构建抽成:

```python
def _build_schema(self) -> tuple:
    """根据 sparse 支持度返回差异化 schema。"""
def _build_index_params(self, *, enable_sparse: bool):
    """稠密索引恒定; enable_sparse 时追加稀疏倒排索引。"""
def _probe_server_supports_sparse(self) -> bool:
    """get_server_version 解析; 解析失败安全降级为 False 并 WARNING。"""
```

探针实现要点:
- 复用 test/verify_sparse_root_cause.py 中 T1 的解析逻辑但抽公共小函数;
- 结果缓存到实例属性，避免同一进程多次 init 重复 RPC;
- 版本字符串异常格式（如 dev 构建 v2.5.x-dev）取前三段数字比较。

### 1.2 稀疏分支三件套（仅 >= 2.5 时构建）

```python
schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)
schema.add_function(Function(
    name="bm25_text_fn",
    function_type=FunctionType.BM25,
    input_field_names=["text"],
    output_field_names=["sparse_vector"],
))
index_params.add_index(
    field_name="sparse_vector",
    index_type="SPARSE_INVERTED_INDEX",
    metric_type="BM25",
)
```

字段名 `sparse_vector`、function 输入源 `text` 必须与在线检索侧（qa_chain）现有引用严格一致，不得另行命名。

### 1.3 创建后 fail-fast 校验

`create_collection` 之后立即 `describe_collection` 断言:
fields 含 sparse_vector 且 functions 非空。任一缺失 → raise RuntimeError,
并在消息中指向 verify_sparse_root_cause.py 供排查（防止重复出现 Function 被静默丢弃的场景）。

### 1.4 单元测试设计

新增 `test/test_milvus_store_sparse_schema.py`（全 mock MilvusClient, 不连真库）:
- 用例 A: mock server_version=v2.4.0 → create_collection 参数不含 sparse 相关定义;
- 用例 B: mock v2.5.4 → schema 字段列表含 sparse_vector, functions 含 bm25_text_fn, index 含稀疏倒排;
- 用例 C: mock get_server_version 抛异常 → 走旧分支且输出 WARNING 日志断言;
- 用例 D: mock 创建成功但 describe 返回 functions=[] → RuntimeError fail-fast 断言。

### 1.5 回归保障

现网 2.4 下运行 `python -m pytest test/test_bug_repairs.py test/test_recall_optimization.py -q`
+ 手工 smoke 一条 dense-only query，确认探测缓存未引入行为变化。

---

## 五、Step 2 在线检索就绪性修正（ qa_chain.py + config.py , 约 0.5 天）

> 执行时点: **立即**。所有修改对当前 2.4 是惰性安全的（hybrid 分支未激活）。

### 2.1 修正 sparse 路 param（对应诊断 2.2 第 1 条）

```python
sparse_req = AnnSearchRequest(
    data=[question],
    anns_field="sparse_vector",
    param={"drop_ratio_search": settings.sparse_drop_ratio_search},   # 默认 0.0
    limit=settings.hybrid_sparse_limit,
)
```

config.Settings 增加 `sparse_drop_ratio_search: float = 0.0`（env: SPARSE_DROP_RATIO_SEARCH）。
理由: 交由索引自身 BM25 度量生效; drop_ratio 作为后续调优旋钮预留。

### 2.2 schema 探测闭包缓存（对应诊断 2.2 第 2 条）

`_retrieve` 内 has_sparse 探测改为非局部变量缓存; 仅当本次请求成功返回后写入缓存，
连接失败允许下次重试。预期收益: 高频问答每查询省一次 RPC（约 5-15ms）。

### 2.3 降级路 score 语义统一（正确性修复）

`_dense_only_retrieve` 方案 B 显式统一为 similarity 语义后再过滤，附注释说明依据来源;
同时新增 `test/test_dense_fallback_semantics.py` mock distance 形态验证不再误杀。

### 2.4 模式标识与结构化日志

- build_qa_chain 完成时打印一次性 `ACTIVE_RETRIEVE_MODE=HYBRID|RERANK_FALLBACK|DENSE_ONLY`
  （取决于 collection/embeddings 是否注入 + has_sparse 探测结果）;
- 每查询压缩为单行 INFO: mode/dense_limit/sparse_limit/fusion/final/threshold/elapsed_ms。

### 2.5 对照开关（评测需要）

新增 env 开关 `DISABLE_HYBRID_SEARCH`（默认 false）。true 时即使 schema 具备 sparse 也强制走降级路。
用途: Step 4 A/B 评测的对照组配置，避免"升级后想跑 dense-only 基线还要改代码"。

### 2.6 单测补充

- 2.1/2.2: 扩展现有 mock client 用例，断言 AnnSearchRequest param 内容与 RPC 调用次数;
- 2.5: env=true 时 hybrid 分支短路走降级;

---

## 六、Step 3 升级日 Runbook（零代码变更）

> 执行时点: **Milvus 升级完成后当天**。前置确认 Step1/2 已合入主干。

### 3.0 基础设施侧（运维动作, 不涉及代码仓库）
1. `milvus/docker-compose.yml` image 改 `milvusdb/milvus:v2.5.x`（建议 x 取最新补丁）;
   数据卷沿用（Milvus 元数据向后兼容升级路径按官方文档执行）;
2. 启动后先跑连通性: `python -c "from pymilvus import MilvusClient; import os; from dotenv import load_dotenv; load_dotenv(); c=MilvusClient(uri=f\"http://{os.getenv('MILVUS_HOST')}:{os.getenv('MILVUS_PORT','19530')}\"); print(c.get_server_version())"`
   预期输出 v2.5.x;

### 3.1 重建入库
```bash
python -m public_kb --init --pdf-dir raw_pdfs
```
入库日志期望出现新分支特征（如 "sparse enabled" 或同义 INFO; Step1 实现时定稿文案）。

### 3.2 混合检索通路自检（按序执行, 任一步骤失败进入 3.3 排查树）

```bash
# (a) schema 断言: sparse_vector 存在且 functions 非空
python - <<'PY'
from pymilvus import MilvusClient
import os
from dotenv import load_dotenv
load_dotenv()
client = MilvusClient(uri=f"http://{os.getenv('MILVUS_HOST')}:{os.getenv('MILVUS_PORT','19530')}")
info = client.describe_collection("public_kb")
fields = [f.get("name") for f in info.get("fields", [])]
print("fields:", fields)
print("functions:", info.get("functions"))
assert "sparse_vector" in fields and info.get("functions"), "sparse channel missing"
print("OK")
PY

# (b) E2E 冒烟: 关注日志应出现 "混合检索:" 而非 "无稀疏向量字段...降级"
python test/e2e_chain_verify.py

# (c) 引用溯源评测（106 题）
python scripts/run_knowledge_citation_eval.py
```

### 3.3 失败排查树

| 症状 | 最可能原因 | 动作 |
|------|-----------|------|
| describe functions 为空（若 Step1 未启用 fail-fast 则入库半途才暴露） | 服务端实为 < 2.5 或 protobuf 变体不一致 | 复核 get_server_version 输出; 运行 test/verify_sparse_root_cause.py T2 |
| e2e 日志仍显示降级提示 | 应用进程连接的 Milvus host 指向旧实例 | 核对 .env MILVUS_HOST 是否漂移 |
| hybrid_search 抛 metric mismatch | 疑似老 schema 残留集合未被重建 | 确认 --init 走了 drop+create 幂等路径 |
| Reranker 大量超时 | 候选池膨胀 | 先调小 hybrid_fusion_limit 定位 |

---

## 七、Step 4 评测驱动调优（升级后第 1-3 天）

### A/B 设计

利用 2.5 的 DISABLE_HYBRID_SEARCH=false/true 两组各跑一遍
`scripts/run_knowledge_citation_eval.py`, 记录:
- Recall@5 / top1 相关度均值（提升 = hybrid 增益直接证据）;
- citation_validation R1-R7 通过率（不得回退）;
- P50/P95 端到端延迟（接受增幅上限建议 +25%）。

### 调优旋钮优先级（由粗到细）

1. hybrid_dense_limit / hybrid_sparse_limit / hybrid_fusion_limit（候选池规模）
2. rrf_k（融合平滑度）
3. sparse_drop_ratio_search（稀疏路噪声剪枝）
4. _adaptive_threshold 三档位（精度-上下文丰富度平衡）

每轮只动一个旋钮并保留 eval JSONL 差异报告，确保归因清晰。

---

## 八、分步执行节奏总览

| 步骤 | 改动范围 | 执行时点 | 依赖升级? | 工作量 | 主要风险 |
|------|----------|----------|-----------|--------|----------|
| Step 1 | milvus_store.py + 新单测 | 立即 | 否（2.4 下休眠） | ~1 天 | 低 |
| Step 2 | qa_chain.py / config.py + 单测 | 紧随 Step1 合并 | 否 | ~0.5 天 | 低 |
| Step 3 | 纯操作手册, 无代码 | 升级日 | 是 | 0.5-1 天 | 中（环境） |
| Step 4 | 配置调参为主 | 升级次日 | 是 | 1-2 天 | 低 |

执行顺序不可颠倒: Step2 的 metric_type 修正必须在 Step1 的稀疏分支激活前合入，
否则升级当日 first-run 即暴露 param 错误。

## 九、风险登记册

| # | 风险 | 缓解 |
|---|------|------|
| R1 | 版本字符串变体解析误判（dev/rc 后缀） | 三段数字截断 + 异常回退 False（宁可少用勿错用） |
| R2 | 2.5 早期版本 dynamic_field 与 BM25 共存限制变化 | fail-fast 校验第一时间暴露; 必要时收紧 enable_dynamic_field=False 分支预案 |
| R3 | 升级日 data volume 兼容问题（运维域） | Runbook 单列连通性步骤先行隔离 |
| R4 | hybrid 生效后延迟上升影响交互体验 | Step4 设置延迟增幅红线; fusion_limit 第一顺位回调 |
