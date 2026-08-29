# 招投标智能助手

> 政府招投标领域的智能问答助手：结合 MySQL 结构化查询 + Milvus 法律知识库 RAG + DeepSeek 推理，覆盖专业知识问答、价格/中标查询、通用对话和文档问答四大能力。

---

## 技术栈

- **Agent 框架**：LangGraph StateGraph（`agent/`）
- **LLM**：DeepSeek（推理 + 路由）
- **向量库**：Milvus（BGE-m3 1024 维 + 稀疏向量混合检索 + BGE-reranker-v2-m3 重排）
- **关系库**：MySQL `ztb_clean`（带 ngram FULLTEXT 索引）
- **RAG 引擎**：`public_kb/`（MinerU 解析 PDF → 语义切片 → Milvus 入库 → LCEL 混合检索链）
- **Embedding / Rerank**：SiliconFlow（BGE-m3 / BGE-reranker-v2-m3）
- **PDF 解析**：MinerU API

---

## 快速开始（首次接入）

### 1. 克隆仓库

```bash
git clone git@github.com:zonggao7-art/ztb_demo.git
cd ztb_demo
```

### 2. 安装依赖

> ⚠️ `setuptools` 必须 **< 70**（pymilvus 2.4.x 依赖 `pkg_resources`，已被新版移除）

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env，填入你的 API key、数据库密码等
```

需要的环境变量（见 [.env.example](.env.example)）：
- `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` — 推理 LLM
- `SILICONFLOW_API_KEY` / `SILICONFLOW_BASE_URL` — Embedding + Rerank
- `MINERU_API_TOKEN` — PDF 解析
- `MYSQL_*` — MySQL 连接
- `MILVUS_HOST` / `MILVUS_PORT` — Milvus 连接
- `TAVILY_API_KEY`（可选）— 联网检索

### 4. 获取数据资产

**⚠️ 本仓库不包含数据**（`.gitignore` 已排除）。需要单独向团队获取：

- `DATA/` — 业务数据库原始 SQL 导出
- `raw_pdfs/`、`new_pdfs/`、`raw_policy/`、`raw_tables/` — 法律 PDF 与原始政策表
- `cloud_sync/` — 云同步相关数据
- `*.jsonl`（`testset_*.jsonl`、`text2sql_dataset.jsonl` 等）— 评估测试集
- `milvus/volumes/` — Milvus 向量库快照（如有）

向项目负责人索要，**统一放置到仓库根目录**。

### 5. 启动基础设施（按需）

```bash
# Milvus（向量库）
docker compose -f milvus/docker-compose.yml up -d

# MySQL（业务数据库）
docker compose -f docker/mysql/docker-compose.yml up -d
```

### 6. 验证安装

```bash
# 跑一次单轮问答
python -m agent --question "招标方式有哪些？"

# 进入交互模式
python -m agent --interactive
```

---

## 开发流程

### 分支策略

- `main` 是受保护分支，**不允许直接 push**，必须通过 PR 合并
- 新功能/修复 → 从 `main` 拉新分支 → 在自己的分支上开发 → 提 PR → review → 合并

```bash
# 1. 拉最新 main
git checkout main && git pull

# 2. 新建分支（命名规范见下）
git checkout -b feat/xxx
# 或
git checkout -b fix/xxx
# 或
git checkout -b docs/xxx
# 或
git checkout -b refactor/xxx

# 3. 写代码、提交
git add .
git commit -m "feat: xxx"

# 4. 推到自己分支
git push origin feat/xxx
# 5. 在 GitHub 上发起 Pull Request
```

### Commit message 规范（建议）

```
feat: 新增 xxx 功能
fix: 修复 xxx bug
docs: 文档/注释更新
refactor: 重构（不改外部行为）
test: 测试用例
chore: 杂项（依赖、CI 等）
```

### 合并前自检

```bash
# 跑测试
python -m pytest test/ -v

# 跑诊断（如有 SQL 改动）
python test/explain_sql.py --db ztb_clean --sql "<SQL>"
```

---

## 项目结构

```
.
├── agent/                  # LangGraph Agent 主框架
│   ├── graph.py            # StateGraph 构建
│   ├── router.py           # LLM 意图路由
│   ├── state.py            # AgentState 定义
│   ├── checkpointer.py     # 会话记忆后端
│   └── nodes/              # 业务节点
│       ├── knowledge_qa.py # 专业知识问答
│       ├── price_inquiry/  # 价格/中标查询（package）
│       ├── general_chat.py # 通用对话
│       ├── doc_qa.py       # 文档问答
│       └── fallback.py     # 兜底
├── public_kb/              # RAG 引擎
│   ├── rag_engine.py       # PublicKnowledgeRAG 统一门面
│   ├── config.py           # 全局配置
│   ├── contracts.py        # 输入输出 / Milvus / 检索契约
│   ├── chunk_ids.py        # chunk_id 与 chunk_uid
│   ├── services/           # Embedding、LLM、Milvus、PDF 解析
│   ├── ingestion/          # Source / Transform / Sink / Pipeline / CLI
│   ├── retrieval/          # 混合检索、RRF、Reranker、降级
│   └── generation/         # Prompt、上下文、问答链、引用
├── scripts/                # 评估、报告、运维脚本
├── test/                   # 单元测试 + 诊断脚本
├── docker/
│   └── mysql/              # MySQL 容器配置（init/01-schema.sql 是建表脚本）
├── milvus/                 # Milvus docker-compose（volumes/ 数据不入仓）
├── docs/                   # 项目文档、审计报告
├── archive/                # 历史数据迁移工具（冻结保留）
├── requirements.txt
├── .env.example            # 环境变量模板
├── .gitignore
└── AGENTS.md / CLAUDE.md   # 给 AI 助手的项目说明
```

---

## 常见问题

**Q: `ModuleNotFoundError: No module named 'pkg_resources'`**
A: `pip install 'setuptools<70'`（pymilvus 2.4.x 需要）

**Q: 拉代码后跑不起来？**
A: 检查 `.env` 是否填好；MySQL/Milvus 是否启动；数据目录是否就位。

**Q: 怎么往知识库加新法规 PDF？**
A: 把 PDF 放到 `raw_pdfs/`，然后跑 `python -m public_kb --init --pdf-dir raw_pdfs`（首次或全量重建）。增量添加见 `public_kb/rag_engine.py` 的 `add_pdf(path)` 方法。

**Q: 我改了某个节点的逻辑，怎么本地验证？**
A: 单条问题：`python -m agent --question "..." --verbose`。交互模式：`python -m agent --interactive`。

**Q: 我不想把 `.env` push 上去，会不会不小心泄露？**
A: `.env` 已在 `.gitignore` 中。如果想确认：`git check-ignore -v .env` 应返回该路径。如发现 `.env` 被追踪了，立即联系项目负责人轮换所有 key。

---

## 详细文档

- [CLAUDE.md](CLAUDE.md) — 给 Claude Code / AI 助手的项目说明（架构、约束、命令）
- [docs/project_overview.md](docs/project_overview.md) — 综合参考文档（v2.0，~1100 行，覆盖架构、部署、故障排查、技术债）
- [AGENTS.md](AGENTS.md) — 多 Agent 协作说明

---

## 联系方式

- 项目负责人：zonggao7-art
- Issue 反馈：直接在 GitHub 上提 Issue
