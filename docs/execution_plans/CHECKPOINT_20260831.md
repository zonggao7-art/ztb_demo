# 执行检查点（2026-08-31 下班存档）

> 用途：保存当前执行节点，便于回家后继续。所有结论均有代码/测试/文档佐证。

---

## 一、已完成的交付（可放心，全部验证通过）

### 1. M0–M6 优化（已交付，全量测试 314 passed）

| 模块 | 内容 | 状态 |
| --- | --- | --- |
| M0 | 基线冻结（239 passed 起点） | ✅ |
| M1 | PDF 解析适配（pdf_structure.py 表格原子块/目录过滤/双栏打标 + 修复标题行正文丢失缺陷） | ✅ |
| M2 | 块级去重 + 幂等（chunk_uid 判重 + enable_dedup） | ✅（真实 Milvus V-3 已验证） |
| M3 | 法条时效性（build_effective_expr + 检索 expr 过滤 + effective_date/status） | ✅ |
| M4 | 清洗保护条款号（短行白名单 + 页眉豁免） | ✅ |
| M5 | 契约常量复用 + csv_loader/citations 拆分（re-export 保兼容） | ✅ |
| M6 | 依赖修复 + requirements.lock + load_existing() 公开化 + 注释治理 | ✅ |

**当前全量测试基线：`314 passed`**（`pytest test -q --ignore=test/test_cloud_sync.py`）

### 2. PDF 三档路由（最新执行基线 = `pdf_legal_tiered_routing_execution_plan_20260831.md`）

| 里程碑 | 状态 |
| --- | --- |
| L0 页面画像采样 | ✅（book1 93% text / book2 98% 双栏 / book3 79% text；有框表≈0） |
| L1 页面分类器 | ✅（pdf_page_profile.py + pdf_legal_page_classifier.py，11 单测） |
| L2 结构化快路径 | ✅（pdf_fast_text.py + pdf_two_column_reflow.py，9 单测） |
| T4 Markdown 装配层 | ✅（pdf_markdown_assembler.py，10 单测，穿插还原+表格标签+manifest） |
| T2B 表格抽取/校验骨架 | ⏭️ 未做（可选/后置，本批有框表≈0） |
| L4 MinerU 接入 | ⏸️ 等服务器支线 |
| L6 接线 + golden set + 端到端 | ⏸️ 待 L4 |

**重要进展**："无 MinerU 也能完整装配快路径产物"链路已打通（L1→L2→T4→manifest）。

---

## 二、服务器部署现状（阻塞项，回家后第一优先级）

### 已完成的勘察（只读，未做任何写操作）

服务器 B 信息：
- **阿里云 ECS**：地域上海 cn-shanghai / 可用区 cn-shanghai-m，实例 ID `i-uf6eu8yo3ve01iz8m5jn`，规格 `ecs.gn6e-c12g1.3xlarge`（GPU 型）
- 公网 IP `8.153.82.13`，SSH 22 端口**当前网络下可通**（已用 paramiko 验证连接成功）
- 用户 `admin`，在 docker 组（跑 Docker 无需 sudo），**无免密 sudo**
- GPU：Tesla V100-SXM2-32GB，驱动 580.126.09 / CUDA 13.0，Docker CDI 已配置（nvidia.com/gpu）
- 内存 89G（83G 可用）/ 12 核
- Docker 29.6.1 + Compose v5.3.1
- MinerU **未安装**；现有镜像全走 daocloud 镜像源，`daemon.json` 里 registry-mirrors 为空

### 3 个硬阻塞（回家后必须先解决）

| 阻塞 | 状态 | 解法 |
| --- | --- | --- |
| 🔴 **磁盘 100% 满**（295G 用 288G，0 字节可用） | 最严重，不解决无法拉镜像 | 运维给**同一账号**挂一块 100G ESSD 数据盘到 /data；或各组清 group_4/5/6/public（共 194G，不能自己删） |
| 🟠 GPU 与其他组共享 | V100 上已有 `AI_write_bid` 进程占 2.5G | 与相关组协调错峰 |
| 🟠 Docker Hub 直连拉取超时 | docker.io i/o timeout | 确认 daocloud 上 MinerU 镜像 tag，或配可用 registry mirror |

### 关键决策（已明确）

- **自己账号买 ESSD 挂不上**：ESSD 必须和 ECS 同一阿里云账号。→ 只能找运维/账号所有者买+挂载（100G 够，MinerU 约需 50G）。
- **OSS 不解决磁盘问题**：OSS 是对象存储，不能挂载当磁盘；占满的是别的组数据 + 系统盘。数据盘=ESSD 才正确。
- **腾讯云不行**：跨云厂商无法互挂。
- **网络/回家能否连**：取决于阿里云安全组 22 端口是否只放行公司 IP。验证：回家/手机热点直接 `ssh admin@8.153.82.13`，或看控制台安全组规则。

### 给运维的一句话

> 服务器 `i-uf6eu8yo3ve01iz8m5jn`（阿里云上海，8.153.82.13）系统盘 300G 已 100% 满，帮我挂一块 100G ESSD 数据盘并挂载到 /data。

### 待确认信息（回家后从用户处获取）

1. 磁盘挂好后的挂载路径（如 /data）
2. 可用端口（MinerU 服务默认 8001，需避开他人已用端口）
3. 镜像源（daocloud 是否有 MinerU 镜像 tag，还是配 mirror）
4. 回家后 SSH 是否连通（安全组验证）

---

## 三、已落地的部署骨架（仓库内，服务器盘好后复制过去即可）

```
deploy/mineru/
├── docker-compose.yml        # project 名 ztb-mineru，GPU/卷/端口/内存限制
├── .env.example              # token / 端口 / GPU 卡号 / 超时
└── service/
    ├── Dockerfile            # 基于官方 MinerU 镜像 + FastAPI
    ├── requirements.txt      # fastapi / uvicorn / python-multipart
    └── app.py                # POST /parse + GET /health（已语法检查通过）
```

服务器侧启动（盘好之后）：
```bash
cd group_7/mineru（或自行新建的组目录）
cp .env.example .env && vim .env
docker compose -p ztb-mineru up -d --build
```

---

## 四、回家后继续执行的顺序

1. **确认磁盘问题解决**（运维挂好 /data，或确认可用空间）
2. 确认 SSH 连通 + 改密码（**密码已明文出现在对话，务必改**）
3. 服务器部署 MinerU：确认镜像源 → 拉镜像 → 起 FastAPI 服务 → `curl /health`
4. 本地写 `MinerUApiParser`（对齐 POST /parse 协议）+ 接线三档路由
5. 补做 T2B 表格抽取/校验骨架（可选）
6. L4 MinerU 接入 + L6 接线 + golden set 验收 + 端到端

---

## 五、本会话新增文件清单（未提交，工作区状态）

**源码**
- `public_kb/ingestion/transforms/pdf_page_profile.py`
- `public_kb/ingestion/transforms/pdf_legal_page_classifier.py`
- `public_kb/ingestion/transforms/pdf_fast_text.py`
- `public_kb/ingestion/transforms/pdf_two_column_reflow.py`
- `public_kb/ingestion/transforms/pdf_markdown_assembler.py`
- `deploy/mineru/{docker-compose.yml, .env.example, service/{Dockerfile, requirements.txt, app.py}}`
- `scripts/pdf_l0_profile.py`

**测试**
- `test/test_pdf_legal_page_classifier.py`
- `test/test_pdf_fast_text.py`
- `test/test_pdf_markdown_assembler.py`

**文档**
- `docs/execution_plans/pdf_legal_tiered_routing_execution_plan_20260831.md`（最新基线 + 附录 A/B 执行状态）
- `docs/execution_plans/pdf_tiered_server_deploy_supplement_20260831.md`（部署补充 + 隔离方案）
- `docs/execution_plans/pdf_routing_pipeline_plan_20260831.md`（已被吸收，不再使用）
- `docs/execution_plans/README.md`（已更新计划表）

**数据产物（中间态，不进版本库）**
- `DATA/raw_data/_pdf_tiered_manifest/l0_profile.json`
- `DATA/raw_data/law_pdf/_pdf_*.txt/json`（早期 PDF 分析）

---

*本检查点保存了代码、测试、文档、服务器勘察结论与待办。回家后先读本文件即可无缝续接。*
