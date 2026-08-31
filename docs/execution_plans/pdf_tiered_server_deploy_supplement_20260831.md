# 公司服务器 MinerU 部署 + 数据回流 + 远程解析接口方案（对三档路由计划的补充）

> 计划日期：2026-08-31
> 上游执行基线：`docs/execution_plans/pdf_legal_tiered_routing_execution_plan_20260831.md`（三档路由，作为最新基线）
> 本文件性质：补充章节，只覆盖该计划未展开的"部署拓扑 / 数据回流 / 远程接口 / 一键迁移"，不修改三档路由计划的模块与验收定义。
> 原两层方案：`docs/execution_plans/pdf_routing_pipeline_plan_20260831.md` 已被三档计划吸收，后续不再作为独立执行基线。

---

## 1. 部署拓扑与职责切分

**双机分层**：解析层在服务器（有 GPU），业务与向量库在本地。服务器上**新建自己的 group 文件夹**（如 `group_7`，不碰 `group_4/5/6`），本地归拢到 `deploy/`。

```
┌──────────────────────────┐        ┌──────────────────────────────────────┐
│ 本地 ztb_demo（业务侧）   │        │ 公司服务器 group_7（解析侧，GPU 32G）│
│                          │        │                                      │
│ deploy/                  │        │ group_7/                             │
│ ├─ milvus/compose.yml    │        │ └─ mineru/                           │
│ │    (etcd+minio+milvus) │        │    ├─ docker-compose.yml             │
│ └─ app/（可选，业务容器） │        │    ├─ .env（token/端口/GPU卡号）     │
│                          │        │    └─ service/（FastAPI 封装 magic-pdf）│
│  PdfRouter（T1-T4 编排） │        │                                      │
│   ├─ Tier A 快路径       │  HTTP  │  MinerU 服务容器                      │
│   ├─ Tier B 表格(本地)   │ ─────▶ │    POST /parse（子PDF→Markdown）      │
│   ├─ Tier C 复杂页 ──────┼────────▶│  GET  /health                       │
│   └─ Markdown 装配(本地) │        │  卷：models/workspace/cache（300G盘） │
│  TextCleaner→PdfStructure│        └──────────────────────┬───────────────┘
│   →SemanticChunker       │                  ┌───────────▼──────────┐
│   →向量化→入本地Milvus    │◀──内网一次拉取───│ workspace/artifacts   │
│  └──────────┬────────────┘                  └──────────────────────┘
│             └─ chunks.jsonl + metadata ──▶ 组员 → 本地向量库
└──────────────────────────┘
```

- **服务器（group_7/mineru）**：MinerU（Tier C）强制在此；Tier A/B 因服务器 12 核 CPU 也更适合放此（多线程），服务器统一产出 Markdown 中间态。
- **本地（ztb_demo）**：`TextCleaner → PdfStructure → SemanticChunker → 向量化 → Milvus` 与在线问答全部留在本地（M1-M6 已交付不动）。
- **通信**：本地 ↔ 服务器走内网 HTTP；向量库与业务都在本地，互不跨机。
- **唯一网络依赖**：Tier A/B/C 解析结果与 PDF 的往返传输（内网）。

## 2. 服务器配置可行性评估

| 配置项 | 服务器 | MinerU 需求 | 结论 |
| --- | --- | --- | --- |
| GPU 32G | 超标准模型 5 倍 | 4-6G | ✅ 可跑单实例高精度，或预留未来多实例 |
| 内存 92G | 远高于建议 16G | — | ✅ 充足 |
| CPU 12 核 vCPU | 快路径/表格多线程友好 | — | ✅ 充足 |
| 云盘 300G | 模型+Docker+中间态 | — | ✅ 充足 |
| 带宽 5M（0.6MB/s） | **唯一瓶颈** | — | ⚠️ 见 §3 数据回流 |

**前置确认**：若本地与服务器在同一内网/VPN，PDF 上传与结果回传走内网（远超 5M），带宽不再是瓶颈。务必先确认网络拓扑。

## 3. 数据回流方案（推荐：统一存云盘 + 一次性批量拉取）

### 3.1 结论

**服务器全量解析 → 结果统一存云盘 → 本地一次性批量拉取（压缩包/目录同步）**，不做边解析边回传。

### 3.2 为什么

- 解析是批量、耗时的（3 本 PDF 约 1900 页），没必要边解析边传；
- 云盘 300G 足够缓存全部 Markdown 中间态；
- 传输从"N 次小文件"降为"1 次打包"，规避 5M 带宽瓶颈；
- 职责清晰：**服务器只产 Markdown 中间态，本地只消费它**，M1-M6 在本地无改动。

### 3.3 中间态目录约定（服务器 group_7/mineru 挂载卷）

```text
group_7/mineru/volumes/workspace/        # 服务器侧挂载卷（云盘 300G）
├── uploads/            # 上传的源 PDF / 子 PDF（按内容哈希+页范围缓存）
├── output/             # 解析结果统一 Markdown
│   └── <doc_hash>/
│       ├── full.md          # 装配后的完整 Markdown（T4 产物）
│       ├── manifest.json    # 每页 route/parser/置信度/耗时/版本
│       └── ranges/          # MinerU 范围的子 Markdown
├── cache/              # MinerU 结果缓存（内容哈希+页范围+parser版本）
└── artifacts/          # 一次性交付物（tar.gz/jsonl 等）
```

### 3.4 本地拉取

- 方式：`scp/sftp/rsync` 拉取 `artifacts/<doc_hash>.tar.gz`（或整目录）。
- 频率：3 本 PDF 全部解析完成后拉一次；后续增量本再按需拉。
- 数据量估算：Parsed Markdown 约 4-8MB/本（1900 页 × ~800 字），3 本约 12-24MB，5M 带宽下单次拉取约 1-2 分钟，可接受。

## 4. 远端解析服务接口设计（为"一键迁移"预留）

### 4.1 解析服务 HTTP 协议

```http
POST /parse
Body: multipart/file (子 PDF) + {page_filter?, options?}
Header: Authorization: Bearer <token>
Response 200:
{
  "markdown": "...",            # 该范围的 Markdown
  "page_range": [12, 18],       # 原始 PDF 页范围
  "parser": "mineru",           # 解析器标识
  "parser_version": "2.0.x",    # 模型/版本（写缓存 key 用）
  "warnings": []
}

GET /health → {"status": "ok", "gpu": "32G", "queue_depth": 0}
```

### 4.2 本地侧适配（对齐 P-1 的 `MinerUApiParser`）

- `MinerUApiParser(base_url, token)` 只依赖上述协议，不感知 MinerU 装在哪；
- `PdfRouter` 的 Tier C 回调 = `MinerUApiParser.parse(sub_pdf_path) -> markdown`；
- **换部署位置只改 base_url 配置，代码零改动**。

### 4.3 缓存 key（本地/服务器双侧一致）

`md5(source_pdf_bytes) | page_range | parser | parser_version | 解析参数`

避免同一子 PDF 重复解析、版本升级后误用旧结果。

## 5. 部署目录规范（deploy 目录 + 多个 Compose）

**明确不做"整个项目迁服务器"**：向量库与在线问答在本地，迁过去也连不回本地 Milvus。采用**一个服务一个 Compose 项目**的分层结构：

```
服务器（公司 GPU 机，不碰 group_4/5/6）
└── group_7/                        ← 自己新建的组文件夹
    └── mineru/
        ├── docker-compose.yml      ← 项目名 ztb-mineru，GPU/卷/端口/token
        ├── .env.example            ← token、端口、CUDA_VISIBLE_DEVICES
        ├── service/                ← FastAPI 封装 magic-pdf + Dockerfile
        └── volumes/                ← models / workspace / cache（300G 盘）

本地（ztb_demo）
└── deploy/
    ├── milvus/                     ← 现有 milvus/docker-compose.yml 归拢至此
    │   └── docker-compose.yml      # etcd + minio + milvus-standalone
    └── app/                        ←（可选）业务容器化，非必需
```

**启动方式（两端各自一条命令）**：
```bash
# 服务器：启动解析服务
cd group_7/mineru && docker compose -p ztb-mineru up -d

# 本地：启动向量库（已有）
cd deploy/milvus && docker compose up -d
```

**可插拔**：本地 `.env` 加 `PDF_PARSE_BASE_URL=http://<服务器内网IP>:8002` 一个配置即切换解析服务；换机器/上云只改这个地址，代码零改动。这就是"一键迁移"的工程化形态——不是整个项目搬服务器，而是**唯一重依赖解析服务可插拔**。

## 6. 分块交付给组员

本地把拉回的 Markdown 中间态经 `TextCleaner → PdfStructure → SemanticChunker` 分块后，导出：

```text
chunks.jsonl    # 每行 {"text": ..., "metadata": {doc_name, chapter, chunk_index,
                #   chunk_uid, effective_date, status, source_file, ...}}
README.md       # 字段契约说明（对齐 M1-M6 的 Document.metadata + Milvus schema）
```

组员端按 M2 幂等规则导入本地向量库（`chunk_uid` 判重），无需重跑解析/分块。

## 7. 与三档路由计划的关系

| 项 | 归属 |
| --- | --- |
| 模块 T1-T4 / golden set / 混淆矩阵 / 质量指标 / 里程碑 L0-L6 | `pdf_legal_tiered_routing_execution_plan_20260831.md`（本文件不重复） |
| 部署拓扑 / 数据回流 / 远程解析协议 / 一键迁移 / 组员交付 | **本文件** |
| 原两层方案 `pdf_routing_pipeline_plan_20260831.md` | 被三档计划吸收，标注不再使用 |

## 8. 执行顺序建议

```
1. 本地：确认内网连通性与带宽（决定传输方式）
2. 服务器：Docker + Nvidia 容器工具 → 拉 MinerU 镜像 → 起 FastAPI 服务
3. 本地：写 MinerUApiParser（对齐 §4 协议）+ 三档路由接线（T1-T4）
4. 服务器：跑 3 本 PDF 全量解析 → 产物统一存云盘
5. 本地：一次拉取中间态 → M1-M6 分块 → 向量化入库 → 导出给组员
6. 验收：golden set / 混淆矩阵 / 端到端（对齐三档计划 §8）
```

---

*本文件为部署与接口补充方案，未改动任何代码。执行时以上游三档路由计划为质量基线，本文件为部署拓扑约束。*
---

## 附录：多组共用云服务器的隔离方案（2026-08-31 补充）

**前提确认**：本地与公司服务器同处一个内网 → 传输走内网，5M 公网带宽不再构成瓶颈；PDF 上传与结果回传均走内网。

**结论**：需要在服务器上**单独建一个隔离空间**部署，避免与其他组冲突。采用 Docker 项目级隔离（不新建虚拟机），要点：

| 隔离维度 | 做法 |
| --- | --- |
| 目录 | 专用组目录 `group_7/mineru/`（独立用户 `ztb-mineru` 所有，chmod 700 级，不碰 `group_4/5/6`） |
| 容器编排 | `docker compose -p ztb-mineru`（独立 project 名，网络/卷名前缀自动隔离） |
| 卷 | 独立卷：`ztb_mineru_models` / `ztb_mineru_workspace` / `ztb_mineru_cache`（不挂载 `/root` 共享路径） |
| 端口 | 独立端口映射（如 `8002:8000`，避开他人已用端口） |
| GPU | 单卡 32G 共享：容器内 `CUDA_VISIBLE_DEVICES` 按需指定；如将来多卡可再划分 |
| 资源限制 | docker compose 里 `deploy.resources.limits.memory` 限制内存，避免影响同机其他服务 |
| 访问 | 服务接口绑定内网 IP + `Authorization: Bearer <token>`；健康检查只对内网开放 |

**与数据回流方案的衔接**：隔离空间即 §3.3 的 `group_7/mineru/volumes/workspace` 目录，解析结果统一写在这里，本地经内网一次拉取 `artifacts/<doc_hash>.tar.gz`。

**交付物**：`group_7/mineru/docker-compose.yml`（含 project 名、卷、端口、内存限制、GPU、token 环境变量）入库后，服务器侧执行：
```bash
sudo useradd -m ztb-mineru
sudo mkdir -p group_7/mineru && sudo chown ztb-mineru: group_7/mineru
sudo -u ztb-mineru docker compose -p ztb-mineru -f group_7/mineru/docker-compose.yml up -d
```
