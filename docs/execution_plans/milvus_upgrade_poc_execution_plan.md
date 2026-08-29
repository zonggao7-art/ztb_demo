# 新版 Milvus 部署与小批量混合检索验证执行方案（C1-C3）

> 编制日期: 2026-08-28
> 前置状态: 离线阶段 A/B 已完成并提交（25d6dae，37+65 测试通过）
> 目标: 并行部署新版 Milvus → 小批量真实数据入库 → 分层验证混合检索链路通畅
> 铁律: 全程不动现有 2.4.0 栈与 public_kb 生产集合；POC 验证通过前不推送远端

---

## 0. 背景与代码就绪度

已就绪（离线验证通过）:
- `Settings.enable_bm25` 开关: true 时 Schema 自动携带 sparse_vector + BM25 Function + 稀疏倒排索引
- `Settings.milvus_uri` / `resolved_milvus_uri`: 支持显式指向新实例端口，与 2.4.0 共存
- `Settings.milvus_experiment_prefix` = `public_kb_hybrid_poc_`: recreate 安全护栏只放行该前缀
- `_validate_collection_contract`: 入库前 fail-fast 校验（fields/functions/indexes 三查）
- `strict_hybrid_validation`: true 时 hybrid 任何异常直接抛出，杜绝静默降级伪装成功
- `RetrievalDiagnostics`: retrieval_mode/dense_count/sparse_count/fusion_count/reranker_status 可观察

已知待真机确认项（离线无法证明）:
- pymilvus 3.0.1 与目标服务端的 Function/describe 结构兼容性
- `output_fields=["*"]` 对 BM25 内部字段的真实行为
- 中文切词质量: 当前仅 `enable_analyzer=True`，未配置 analyzer_params（默认 standard 对中文不友好，见第 4 节）
- 网络延迟/索引构建耗时

---

## 1. C1 并行部署新版 Milvus（Docker Desktop）

### 1.1 前置检查（约 10 分钟）

| 检查项 | 命令/方法 | 通过标准 |
|--------|-----------|----------|
| Docker Desktop 运行 | `docker version`（若命令不存在: 启动 Docker Desktop 后重开终端，确认其在 PATH） | Server 版本正常返回 |
| 资源配额 | Docker Desktop → Settings → Resources | 内存 ≥ 8GB（两套栈并行）；磁盘剩余 ≥ 15GB |
| 旧栈现状 | `docker ps` | milvus-standalone/etcd/minio 在跑且健康（本次部署全程不重启它们） |
| 端口空闲 | `netstat -ano | findstr "19531 9093 2380 9003 9004 3001"` | 全部无监听 |
| 镜像 tag 确认 | Docker Hub 或 `docker manifest inspect milvusdb/milvus:v2.6.x` | 选定受维护的固定 patch（示例 v2.6.2，执行前以 Hub 实际最新 2.6 patch 为准；禁止 latest） |

### 1.2 端口与资源规划（新旧完全错开）

| 组件 | 旧栈 2.4.0（不动） | 新栈 v2.6.x（本次） |
|------|--------------------|---------------------|
| standalone gRPC | 19530 | **19531** |
| standalone health | 9091 | **9093** |
| etcd client | 2379 | **2380** |
| minio API / console | 9000 / 9001 | **9003 / 9004** |
| Attu（可选） | 3000 | **3001** |
| 容器名 | milvus-standalone 等 | milvus-standalone-v26 等（-v26 后缀） |
| 数据卷 | ./volumes/* | ./volumes-v26/*（独立目录） |
| compose 项目名 | 默认(milvus) | `-p milvus-v26`（隔离网络与卷命名空间） |

### 1.3 新建 `milvus/docker-compose-v26.yml`

要点（在现有 compose 基础上复制修改四处）:
1. `container_name` 全部加 `-v26` 后缀;
2. 端口映射改为上表新栈列;
3. 卷路径 `./volumes` → `./volumes-v26`（etcd/minio/milvus 三个都改）;
4. etcd `--advertise-client-urls`/`--listen-client-urls` 端口同步 2380;
   standalone 的 `ETCD_ENDPOINTS: etcd-v26:2379`（容器内部端口不变，仅主机映射改）;
5. Attu 的 `MILVUS_URL: standalone-v26:19530`（容器内端口不变）。
   可选: 先注释 Attu 服务，首轮最小化排障。

> 注意: 容器互联走 compose 网络内部名与内部端口；改的只是"宿主机映射端口"。

### 1.4 部署步骤

```powershell
cd milvus
docker compose -p milvus-v26 -f docker-compose-v26.yml up -d
docker compose -p milvus-v26 -f docker-compose-v26.yml ps     # 等全部 healthy（standalone 首启约 60-90s）
```

### 1.5 部署后三项快检（全过才进 C2）

```powershell
# (a) 健康检查
curl http://localhost:9093/healthz          # 期望 OK

# (b) 版本确认（关键!）
& 'D:\agent_project\zhaotoubiao_demo 1\.venv\Scripts\python.exe' -c "from pymilvus import MilvusClient; c=MilvusClient(uri='http://localhost:19531'); print(c.get_server_version())"
# 期望输出 v2.6.x；若为空/报错 → 停在此排查，不进 C2

# (c) BM25 Function 服务端承接探测（复用 verify 思路指向新端口）
#     临时集合: create_schema → add sparse field + Function → create → describe 断言 functions 非空 → drop
#     成功标准: functions 在 describe 返回中真实保留
```

(c) 若 describe 中 functions 为空 → 服务端/客户端组合不兼容，
回退尝试 `milvusdb/milvus:v2.5.x` 最新 patch 重复 (b)(c)。

---

## 2. C2 小批量数据写入（30-100 chunks）

### 2.1 数据源与取样

- 源: `DATA/raw_data/*.md`（已清洗的法规 Markdown，跳过 MinerU/PDF 重管线）;
- 取样: 用 `SemanticChunker` 对 1-2 个代表性文档切片，取前 50 条
  （覆盖: 标题层级多样、长短块混合、含招标/投标/开评标等核心术语）;
- 命名: 集合 `public_kb_hybrid_poc_v1`（必须带实验前缀，recreate 护栏才允许覆盖迭代）。

### 2.2 POC 运行方式（不改 .env 文件）

两种手段，均不触碰生产配置文件:
1. **脚本内构造**: `Settings(milvus_uri="http://localhost:19531", collection_name="public_kb_hybrid_poc_v1", enable_bm25=True)`——MilvusStoreManager/build_qa_chain 均接受注入;
2. **会话级环境变量**（仅验证 qa_chain 全链路时）:
   `$env:MILVUS_URI='http://localhost:19531'; $env:MILVUS_COLLECTION='public_kb_hybrid_poc_v1'; $env:ENABLE_MILVUS_BM25='true'; $env:STRICT_HYBRID_VALIDATION='true'`
   （只影响当前终端进程）。

### 2.3 入库执行（新增 `scripts/poc_ingest_sample.py`）

```
流程: 读 raw_data md → SemanticChunker 切片 → 取 50 条
     → MilvusStoreManager(Settings(...)).initialize_collection(docs)
     （内部自动: 建稀疏三件套 → 契约 fail-fast 校验 → 向量化入库 → flush/load）
```

embedding 走真实 SiliconFlow bge-m3（50 条成本可忽略）。

### 2.4 入库后校验清单

| # | 校验 | 通过标准 |
|---|------|----------|
| 1 | `get_collection_stats` 行数 | == 50 |
| 2 | `describe_collection` fields | 含 text(带 analyzer)/vector/sparse_vector |
| 3 | describe functions | 含 `text_bm25_emb` 且 input=text/output=sparse_vector |
| 4 | `list_indexes` | 含 vector(IVF_FLAT/COSINE) 与 sparse_vector(SPARSE_INVERTED_INDEX/BM25, k1/b) |
| 5 | 持久性 | `docker restart` standalone-v26 后行数与 schema 不变 |

---

## 3. C3 分层混合检索验证（新增 `scripts/poc_verify_hybrid.py`）

### 3.1 分层用例（按序执行，全部记录到报告）

| # | 层级 | 方法 | 通过标准 |
|---|------|------|----------|
| 1 | dense 单路 | 原生 `client.search(anns_field='vector')` | 命中且 COSINE 分数 > 0.45 |
| 2 | BM25 单路 | 原生 `client.search(anns_field='sparse_vector', data=[中文问题])` | 有召回（记录 top1 分数与命中 doc_name） |
| 3 | hybrid+RRF | `AnnSearchRequest` 双路 + `RRFRanker(k=60)` | 融合命中数 ≥ 单路最大值；无异常 |
| 4 | 全链路-成功 | `build_qa_chain(...).invoke('招标方式有哪些？')` | `retrieval_diagnostics.retrieval_mode == 'hybrid_rerank'`（需真实 Reranker key）; sources/citations 非空 |
| 5 | 全链路-Reranker失败 | 注入 `http_client=假客户端(抛异常)` | `retrieval_mode == 'hybrid_rrf'`，`fallback_reason='reranker_failed'`，仍返回结果（RRF 序） |
| 6 | 拒答路径 | invoke 完全无关问题（如 '今天天气怎么样'） | mode ∈ {hybrid_*} 且 answer 为拒答话术，citations=[] |
| 7 | 引用契约 | 检查 case4 返回 | R1-R7 citation_validation.all_passed == True |
| 8 | 严格模式 | 全程 `STRICT_HYBRID_VALIDATION=true` | 上述任一 hybrid 异常直接抛出而非降级（证明无静默 fallback） |

### 3.2 链路"通"的最终判定口径

同时满足:
- 用例 1/2/3 全部无异常且有召回;
- 用例 4（或 Reranker key 不可用时的用例 5）: `retrieval_mode` ∈ {`hybrid_rerank`, `hybrid_rrf`}，即 diagnostics 证明真实走了稀疏通道;
- 用例 8 严格模式全程未触发 hybrid 异常。

### 3.3 失败排查树

| 症状 | 定位 | 处置 |
|------|------|------|
| (b) 版本查询失败 | 端口/防火墙/首启未就绪 | 重试+看 `docker logs milvus-standalone-v26` |
| (c) functions 为空 | 客户端-服务端不兼容 | 换 v2.5.x patch 重试; 仍失败→升级/降级 pymilvus 小版本 |
| 用例2 报 metric mismatch | 查询侧与索引度量不一致 | 核对 AnnSearchRequest param（应为 BM25） |
| 用例2 有召回但质量差/全 0 分 | 中文切词未生效 | 见第 4 节 analyzer 专项 |
| 用例4 mode=dense_native | schema 探测误判或集合名漂移 | 打印 `_has_sparse_cache` 初值; 核对 MILVUS_COLLECTION |
| 用例5 直接抛异常 | 严格模式工作正常的表现（预期行为） | 非故障 |

---

## 4. 中文 analyzer 专项（POC 预期代码修改点）

现状: `_build_schema` 仅 `enable_analyzer=True`，未指定分词器 → 默认 standard 按
空格/标点切分，中文长句几乎不切词 → BM25 单路召回可能极差。

POC 操作:
1. 先按默认跑完 C3 用例 2，**留存分数作为基线**;
2. 若 top 分数低或对"招标方式"这类词无召回 → 改代码: text 字段增加
   `analyzer_params={"type": "jieba"}`（或 `{"tokenizer": "jieba"}`，以 2.6 文档字段名为准），
   drop 实验集合重新入库（POC 数据 50 条，重建成本分钟级）;
3. 重跑用例 2 对比分数与命中 doc 差异 → 将结论写入报告。
   该修改是"验证后必须回补的代码改动"第一名候选。

---

## 5. 产出物与工作量

| 产出 | 说明 |
|------|------|
| `milvus/docker-compose-v26.yml` | 新栈编排（独立端口/卷/容器名） |
| `scripts/poc_ingest_sample.py` | 50 条取样入库脚本 |
| `scripts/poc_verify_hybrid.py` | 分层验证脚本（8 用例 + JSON 结果落盘） |
| `docs/hybrid_poc_verification_report.md` | 验证报告（含 analyzer A/B 结论） |

| 阶段 | 工作量 |
|------|--------|
| C1 部署+三项快检 | 1-2h（含镜像拉取） |
| C2 入库+校验 | 1h |
| C3 分层验证+analyzer 对比 | 0.5 天（含排查缓冲） |

## 6. 实验后清理

1. `docker compose -p milvus-v26 -f docker-compose-v26.yml down -v`（连卷一起删，或保留供复测）;
2. 关闭会话级环境变量（关闭终端即可）;
3. 生产 .env / docker-compose.yml / 2.4.0 栈全程未被触碰;
4. 验证报告归档后，按结论决定: 追加提交（analyzer 修正等）→ push → 再排生产切换窗口。
