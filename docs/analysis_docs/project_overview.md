# 招投标智能助手 — 项目全览说明书

> **适用场景**：技术交接、新成员上手、架构评审  
> **维护责任人**：项目组  
> **文档版本**：v5.0（2026-09-04 对齐现状）

> ReAct 升级补记：本文主体描述保留的 legacy 路径；受约束 hybrid 路径、只读工具适配及验收状态见 [ReAct实施记录](../work_docs/ReAct实施记录_20260904.md)。2026-09-05 已按要求改为默认 hybrid、按需 ReAct；生产验收仍未完成，部署须显式控制准入。

---

## 版本更新记录

| 版本 | 日期 | 更新人 | 核心修改内容 |
|------|------|--------|-------------|
| v5.0 | 2026-09-04 | 项目组 | 对齐 2026-09 系列整改与死代码清理：`mysql_price_semantic` 语义召回全面退役（集合/代码/配置/重建脚本删除，召回链 = FULLTEXT → LIKE → 全表兜底）；`cloud_sync/` 云迁移线退役（§3.4/§7.5 改为退役记录）；CSV 政策语料管线删除；产品线表不建表；模型身份改为 .env 单一事实源（LLM_MODEL/EMBEDDING_MODEL/RERANKER_MODEL 必填，无默认值，原文 DeepSeek/SiliconFlow 写死表述全部修正）；依赖表对齐 requirements.txt（pymilvus ≥3.0 + Milvus 2.6.x、setuptools<70 约束解除、新增 httpx/orjson/DBUtils/fastapi 等）；目录树补齐 agent/runtime、streaming、tools 与 service/、public_kb 新模块；TD 台账与 AD 记录补注状态（TD-05/06/07 已解决、TD-10 已终止、AD-11/12 已废弃） |
| v4.0 | 2026-08-15 | 项目组 | 同步代码结构全面审计与重构（详见 [../analysis_docs/code_structure_audit_2026-08-15.md](../analysis_docs/code_structure_audit_2026-08-15.md)）：`price_inquiry.py`（3,152 行单体）拆分为 11 模块的包 `agent/nodes/price_inquiry/`；P0 死代码清理（净删约 400 行、修复 15 处硬编码数据库口令、pyflakes 告警归零、FULLTEXT_AND 无效阶段移除）；新增 `public_kb/llm_factory.py` 统一三处 LLM 构造；引用溯源体系（`chunk_ids.py` / `citations.py`，内联【来源N】标记 + R1-R7 校验）；评测脚本族去重（`eval_common` / `eval_report_common` / `report_html` / `report_markdown`）；产品线下线（`product_query` 子路由移除，产品专用字段清理）；历史脚本归档（`archive/`、`scripts/archive/`、`test/legacy/`）；更新目录结构、检索降级链、连接池、配置项、测试清单、技术债务台账（新增 TD-11）与决策记录（AD-13/AD-14） |
| v3.0 | 2026-08-14 | 项目组 | 同步项目演进：新增 `cloud_sync/` 云端同步包（阿里云迁移工具链）与部署章节 7.5，记录第一阶段云端写入验证结果（public_kb 29,729 条 + mysql_price_semantic 77,597 条）；补充 `public_kb` CSV 政策语料入库流水线（csv_loader / process_csv）；记录 P0-11 模糊匹配防范体系与 P0-12 项目编号检测修复；更新依赖版本至 requirements.txt 实际值（langchain-core <0.5.0、langchain-openai >=1.0.0、langchain-milvus >=0.4.0、tiktoken <0.9.0）；新增配置项（SQL 查询超时 15s、节点总超时 45s、语义自举开关）；更新目录结构、测试清单、技术债务台账（TD-05/TD-06、新增 TD-10）与决策记录（AD-11/AD-12） |
| v2.0 | 2026-08-11 | 项目组 | 重构文档框架：新增"部署与发布流程""常见问题排查指南""技术债务台账""核心技术选型决策记录""关键接口依赖说明""权限配置清单"六大专项章节；更新价格询价模块至 ztb_clean 统一数据源架构；更新 Milvus 混合检索参数；补充版本更新记录；逐条校验所有命令、链接与配置项至当前生产环境值 |
| v1.0 | 2026-08-05 | 项目组 | 初版，涵盖项目概述、技术栈清单、目录结构、核心工作流、数据源与检索策略、环境搭建、维护扩展建议 |

---

## 目录

1. [项目核心定位与背景](#1-项目核心定位与背景)
   - 1.1 [项目定位](#11-项目定位)
   - 1.2 [业务背景与价值](#12-业务背景与价值)
   - 1.3 [系统架构概览](#13-系统架构概览)
   - 1.4 [核心设计原则](#14-核心设计原则)
2. [技术栈全景说明](#2-技术栈全景说明)
   - 2.1 [核心依赖与版本（生产环境）](#21-核心依赖与版本生产环境)
   - 2.2 [外部服务与端点](#22-外部服务与端点)
   - 2.3 [关键模型参数](#23-关键模型参数)
   - 2.4 [端口清单](#24-端口清单)
3. [项目目录结构](#3-项目目录结构)
   - 3.1 [根目录总览](#31-根目录总览)
   - 3.2 [agent/ — LangGraph Agent 骨架](#32-agent--langgraph-agent-骨架)
   - 3.3 [public_kb/ — 公共知识库 RAG 引擎](#33-public_kb--公共知识库-rag-引擎)
   - 3.4 [cloud_sync/ — 云端数据同步包（已退役）](#34-cloud_sync--云端数据同步包已退役)
   - 3.5 [test/ — 工具脚本集合](#35-test--工具脚本集合)
   - 3.6 [数据目录](#36-数据目录)
   - 3.7 [配置文件](#37-配置文件)
4. [核心业务模块逻辑梳理](#4-核心业务模块逻辑梳理)
   - 4.1 [整体请求流程](#41-整体请求流程)
   - 4.2 [意图路由模块 (Router)](#42-意图路由模块-router)
   - 4.3 [专业知识问答模块 (knowledge_qa)](#43-专业知识问答模块-knowledge_qa)
   - 4.4 [智能询价模块 (price_inquiry)](#44-智能询价模块-price_inquiry)
   - 4.5 [通用对话模块 (general_chat)](#45-通用对话模块-general_chat)
   - 4.6 [文档问答模块 (doc_qa)](#46-文档问答模块-doc_qa)
   - 4.7 [兜底引导模块 (fallback)](#47-兜底引导模块-fallback)
   - 4.8 [异常兜底机制](#48-异常兜底机制)
   - 4.9 [回答模板与输出配置](#49-回答模板与输出配置)
5. [数据源与检索策略](#5-数据源与检索策略)
   - 5.1 [MySQL 结构化检索](#51-mysql-结构化检索)
   - 5.2 [Milvus 向量检索（Public Knowledge Base）](#52-milvus-向量检索public-knowledge-base)
   - 5.3 [MySQL 与 Milvus 协同策略](#53-mysql-与-milvus-协同策略)
6. [本地开发环境搭建](#6-本地开发环境搭建)
   - 6.1 [前置条件](#61-前置条件)
   - 6.2 [分步安装指引](#62-分步安装指引)
   - 6.3 [启动服务](#63-启动服务)
   - 6.4 [验证安装](#64-验证安装)
7. [部署与发布流程](#7-部署与发布流程)
   - 7.1 [环境拓扑](#71-环境拓扑)
   - 7.2 [生产环境配置核查清单](#72-生产环境配置核查清单)
   - 7.3 [发布流程](#73-发布流程)
   - 7.4 [回滚方案](#74-回滚方案)
8. [常见问题排查指南](#8-常见问题排查指南)
   - 8.1 [环境与依赖类](#81-环境与依赖类)
   - 8.2 [运行时错误类](#82-运行时错误类)
   - 8.3 [数据与检索类](#83-数据与检索类)
   - 8.4 [性能类](#84-性能类)
   - 8.5 [诊断工具速查](#85-诊断工具速查)
9. [技术债务台账](#9-技术债务台账)
10. [核心技术选型决策记录](#10-核心技术选型决策记录)
11. [关键接口依赖说明](#11-关键接口依赖说明)
    - 11.1 [内部接口契约](#111-内部接口契约)
    - 11.2 [外部 API 依赖](#112-外部-api-依赖)
12. [权限配置清单](#12-权限配置清单)
13. [附录：关键代码路径速查](#13-附录关键代码路径速查)

---

## 1. 项目核心定位与背景

### 1.1 项目定位

**招投标智能助手**是一个面向招投标领域的 AI Agent 系统，整合**结构化数据库查询**（MySQL）、**非结构化知识检索**（Milvus 混合 RAG）和**大语言模型推理**（.env 配置的 OpenAI 兼容 LLM），为用户提供四类核心能力：

| 能力 | 说明 | 典型示例 |
|------|------|---------|
| 专业知识问答 | 基于权威法规 PDF 的混合 RAG 检索 + 重排序 | "公开招标和邀请招标有什么区别？" |
| 智能询价 | 统一数据源 ztb_clean 多级路由 + FULLTEXT 多级降级召回 + 混合重排序 | "查一下XX公司有哪些行政处罚记录" |
| 通用对话 | LLM 原生对话 + 功能引导 | "你能做什么？" |
| 文档问答 | 上传招标文件进行分析和解读（Demo 阶段为占位，待上线） | — |

### 1.2 业务背景与价值

- **领域**：政府采购、工程招标、投标与评标全流程
- **知识库来源**：3 本权威招投标法规 PDF（约 2000+ 页），涵盖《中华人民共和国招标投标法律法规全书》《招标投标法律解读与风险防范实务》《政府采购、工程招标、投标与评标 1200 问》
- **结构化数据来源**：ztb_clean 统一数据库，包含企业信息、处罚记录、招标项目等清洗后的结构化数据（产品线已下线，product_info 不再建表）
- **核心价值**：将零散的招投标法规知识和历史中标数据整合为可对话查询的智能助手，降低信息检索门槛

### 1.3 系统架构概览

<!-- [VISUAL] 建议补充：系统整体架构图，展示 接入层 → Agent骨架 → 数据层 三层关系 -->
**截图占位：`analysis_docs/images/architecture_overview.png`**

```
┌─────────────────────────────────────────────────────────┐
│                   接入层 (Entry)                         │
│      CLI (python -m agent)  /  FastAPI SSE (service/)   │
└──────────────────────┬──────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────┐
│             LangGraph Agent 骨架 (Core)                  │
│                                                         │
│   START → router (LLM 意图分类)                          │
│                │                                        │
│       ┌────────┼────────┬────────┬────────┐             │
│       ▼        ▼        ▼        ▼        ▼             │
│  knowledge  price   general   doc    fallback           │
│    _qa    _inquiry  _chat    _qa                        │
│       │        │        │        │        │             │
│       └────────┴────────┴────────┴────────┘             │
│                        │                                │
│                       END                               │
└──────────────────────┬──────────────────────────────────┘
                       │
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
┌────────────┐ ┌──────────┐ ┌──────────────┐
│  Milvus    │ │  MySQL   │ │  LLM API     │
│ 向量数据库  │ │ ztb_clean│ │ (.env 配置)  │
│ (混合检索)  │ │ (结构化)  │ │              │
└────────────┘ └──────────┘ └──────────────┘
```

<!-- [VISUAL] 建议补充：LangGraph StateGraph 节点与条件边详细流程图 -->
**截图占位：`analysis_docs/images/graph_flow.png`**

### 1.4 核心设计原则

- **可插拔骨架**：新增业务节点无需修改 State 定义和 Graph 结构，只需在 `agent/nodes/` 下添加文件并在 `graph.py` 中注册
- **全局异常兜底**：所有业务节点统一包裹 `_with_fallback` 装饰器，单节点崩溃不中断整体流程
- **只读权威库**：`public_kb`（Milvus 向量库）设计为只读，仅支持批量初始化入库，不提供日常写入接口
- **配置驱动**：输出字段筛选（output_templates）与回答渲染（answer_templates）均为声明式配置，新增业务类型无需修改核心引擎
- **确定性优先**：MySQL 查询优先使用可索引的精确匹配（`=`、`>=`、`<=`），全文检索使用 `MATCH...AGAINST`，避免 `LIKE '%...%'` 全表扫描

---

## 2. 技术栈全景说明

### 2.1 核心依赖与版本（生产环境）

| 技术 | 版本 | 用途 |
|------|------|------|
| **Python** | 3.12 | 运行环境（Anaconda 管理） |
| **LangChain** | 1.2.12 | 官方 `create_agent` 循环；要求 LangGraph <1.2 |
| **LangChain Core** | 1.5.1 | LCEL 链式调用、Prompt 模板、输出解析 |
| **LangChain OpenAI** | 1.4.1 | OpenAI 兼容 API 封装（LLM / Embedding） |
| **LangChain Milvus** | 0.4.0 | Milvus 向量存储官方集成（含 BM25 混合检索） |
| **LangGraph** | 1.1.10 | StateGraph Agent 骨架、条件路由、Checkpointer |
| **langgraph-checkpoint** | 4.1.1 | Checkpointer 持久化支持 |
| **langgraph-checkpoint-sqlite / -postgres** | ≥2.0.0 | Checkpointer 可选后端子包（默认 memory 不需要） |
| **pymilvus** | ≥3.0, <4.0 | Milvus SDK（需 Milvus 服务端 2.6.x，BM25 Function 需 2.5+） |
| **pymysql** | ≥1.1.0 | MySQL 数据库连接驱动 |
| **openai** | 2.53.0 | OpenAI 兼容客户端 |
| **httpx** | ≥0.27, <0.29 | Reranker / 外部 HTTP 异步客户端 |
| **orjson** | ≥3.10 | SSE / JSON envelope 高效序列化 |
| **DBUtils** | ≥3.1, <4.0 | MySQL 有界连接池（异步轨 PooledDB） |
| **pydantic** | ≥2.6, <3.0 | envelope / schema 强类型 |
| **fastapi + uvicorn** | fastapi ≥0.111 | 阶段 5 SSE 服务（service/api.py） |
| **tenacity** | ≥8.2 | 退避重试统一封装 |
| **python-dotenv** | ≥1.0.0, <2.0.0 | .env 环境变量加载 |

> 注：`setuptools<70` 约束已于 2026-09 整改解除（pymilvus 3.x 不再依赖 `pkg_resources`）。旧依赖 `sentence-transformers` / `markdown` / `aiohttp` / `asyncpg` / `prometheus-client` / `uvloop` 已于 2026-09-04 死代码清理时移除（零引用）。

> 上述核心版本为 ReAct 本地独立环境已验证的兼容基线，并非本轮已完成生产部署。完整传递依赖见 `requirements.lock.txt`；真实服务兼容性仍属于放量前验收。

### 2.2 外部服务与端点

模型身份配置一律由 `.env` 声明（`LLM_MODEL` / `EMBEDDING_MODEL` / `RERANKER_MODEL` 及配套 `*_API_KEY` / `*_BASE_URL`），代码无默认值、无别名兜底，缺失即抛错（D4/D6）。下表为**当前 `.env` 示例值**，换服务/换模型只改 `.env`：

| 服务 | 端点（.env 配置） | 用途 |
|------|-------------|------|
| **LLM API（OpenAI 兼容）** | `.env LLM_BASE_URL`（当前示例 `https://openrouter.ai/api/v1`） | LLM 推理（模型: `.env LLM_MODEL`，当前示例 `z-ai/glm-5.3-flash`） |
| **SiliconFlow API** | `https://api.siliconflow.cn/v1` | Embedding（`BAAI/bge-m3`）+ Reranker（`BAAI/bge-reranker-v2-m3`；凭据/端点复用 EMBEDDING_*，有意设计） |
| **MySQL** | `.env MYSQL_HOST/PORT`（本地 Docker `127.0.0.1:3306` 或内网生产库） | 招投标结构化数据（数据库: `ztb_clean`） |
| **Milvus** | `.env MILVUS_HOST/PORT`（Docker `localhost:19530`） | 向量数据库（1024 维，dense+BM25 混合检索；需 2.6.x） |
| **MinerU（本地）** | 本地 `magic-pdf` CLI（subprocess 调用） | PDF 解析为 Markdown（仅初始化入库时使用，不走网络 API） |

### 2.3 关键模型参数

| 模型 | 用途 | 关键参数 |
|------|------|---------|
| `.env LLM_MODEL`（当前示例 `z-ai/glm-5.3-flash`） | LLM 对话与意图分类 | temperature=0.0, timeout=60s, max_retries=1 |
| `BAAI/bge-m3` | 中文文本向量化 | 1024 维, 8192 token 上限, timeout=30s |
| `BAAI/bge-reranker-v2-m3` | Cross-Encoder 重排序（HTTP API，非本地模型） | 对混合检索结果精排 |
| magic-pdf（本地 CLI） | PDF → Markdown 解析 | timeout=3600s |

### 2.4 端口清单

| 端口 | 服务 | 说明 |
|------|------|------|
| `19530` | Milvus gRPC | 向量数据库主端口 |
| `9091` | Milvus metrics | 健康检查与指标 |
| `3000` | Attu | Milvus 可视化管理面板 |
| `9000` | MinIO S3 API | Milvus 对象存储 |
| `9001` | MinIO Console | MinIO 管理控制台 |
| `2379` | etcd | Milvus 元数据存储 |
| `3306` | MySQL | 结构化数据库 |

---

## 3. 项目目录结构

### 3.1 根目录总览

```
zhaotoubiao_demo/
├── agent/                  # LangGraph Agent 骨架（核心）
├── public_kb/              # 公共知识库 RAG 引擎
├── service/                # FastAPI SSE 服务（阶段 5，uvicorn service.api:app）
├── test/                   # 工具脚本与测试集合（历史诊断脚本见 test/legacy/）
├── scripts/                # 评估与运维脚本（含 scripts/archive/ 归档区）
├── archive/                # 归档区：rebuild_and_verify.py
├── milvus/                 # Milvus Docker 部署（需 2.6.x）
├── docker/mysql/           # 本地 MySQL Docker 部署（可选）
├── DATA/                   # 原始数据与中间产物（raw_data / repaired_knowledge 等）
├── raw_pdfs/               # 3 本法规 PDF 源文件
├── raw_tables/             # MySQL 表 CSV 导出（product_info.csv 为产品线回滚资产）
├── test_report/            # 三大核心评测 / 引用溯源评测输出
├── design_docs/            # 设计/方案类文档（含未实施规划，均带状态标注）
├── work_docs/              # 工作总结类文档
├── analysis_docs/          # 分析/审计/评测类文档（含本文件）
├── .env / .env.example     # 环境变量配置（含真实密钥，绝不入仓）
├── requirements.txt        # Python 依赖清单
└── testset_knowledge.jsonl # 引用溯源评测集（run_knowledge_citation_eval.py 输入）
```

> **说明（2026-09-04 清理后）**：`cloud_sync/` 包、CSV 政策语料管线（`csv_loader.py`/`process_csv.py`）及 `raw_policy/`、`new_pdfs/`、`cleaned_v1_markdown/` 数据目录已删除；历史一次性脚本归档于 `archive/`、`scripts/archive/`、`test/legacy/`，不参与当前工作流。文档目录自 2026-09 起由单数 `docs/` 拆分为 `design_docs/`（方案）、`work_docs/`（总结）、`analysis_docs/`（分析/审计/评测）。

### 3.2 `agent/` — LangGraph Agent 骨架

```
agent/
├── __init__.py             # 包入口，导出 AgentGraph
├── __main__.py             # CLI 入口（python -m agent；--stream / --list-tools / --agent-mode）
├── state.py                # AgentState 类型定义
├── router.py               # 意图路由节点（LLM 分类，同步/异步双实现）
├── graph.py                # StateGraph 构建与编译（async_nodes 开关切双轨）
├── agent_loop.py           # Agent 自助调用模式（tool-calling 循环，AGENT_TOOLS_ENABLED）
├── checkpointer.py         # Checkpointer 工厂（memory/sqlite/postgres/redis）
├── runtime/                # 异步基础设施（线程池桥接、并发信号量、deadline）
├── streaming/              # 阶段 5 统一流式协议（StreamEvent envelope / SSE / 心跳）
├── tools/                  # 工具库（registry / price_db / knowledge / schemas，见 tools/README.md）
└── nodes/                  # 业务节点包
    ├── __init__.py
    ├── knowledge_qa.py     # 专业知识问答（同步轨，对接 Milvus RAG）
    ├── knowledge_qa_async.py # 专业知识问答（异步轨，token 级流式）
    ├── price_inquiry/      # 智能询价（2026-08-15 由单文件拆包，见下方说明）
    │   ├── __init__.py     # 兼容层，重导出全部历史符号（外部导入零改动）
    │   ├── node.py         # 入口 + 三层查询守卫 + 引导话术 + 能力边界回答
    │   ├── queries.py      # 三张表的专用查询函数
    │   ├── recall.py       # 多级降级检索链 + 超时 + 混合重排序 + 回表补列 + 召回漏斗
    │   ├── sql_builders.py # SQL 构建器 + 硬过滤条件 + 偏好放宽
    │   ├── intent.py       # 统一意图解析 + 关键词提取 + 实体校验 + 项目编号提取
    │   ├── enum_norm.py    # 枚举值归一化
    │   ├── db.py           # MySQL 连接池（无固定上限，复用 + 惰性新建）
    │   ├── schema.py       # 表分类 / 硬编码 schema
    │   └── models.py       # HardFilters / SearchIntent 数据模型
    │   └── node_async.py / recall_async.py / db_async.py  # 异步轨平行实现（ASYNC_BACKEND_ENABLED）
    ├── general_chat.py     # 通用对话（纯 LLM）
    ├── doc_qa.py           # 文档问答（占位节点）
    ├── fallback.py         # 兜底引导
    ├── answer_templates.py # 自然语言回答模板引擎
    └── output_templates.py # 输出字段筛选配置框架
```

> 注：`price_inquiry/semantic.py`（Milvus 结构化语义召回）已于 2026-09-04 全面退役删除，召回链为纯 SQL 多级降级。同步/异步为双轨并存：`ASYNC_BACKEND_ENABLED`（默认 false）决定 `graph.py` 注册哪套节点。

**关键文件说明：**

| 文件 | 核心职责 | 关键函数/类 |
|------|---------|------------|
| [state.py](file:///d:/DEMO/zhaotoubiao_demo/agent/state.py) | 定义通用 `AgentState`，所有分支共享 | `AgentState(TypedDict)`: `messages`, `router_intent`, `business_result` |
| [router.py](file:///d:/DEMO/zhaotoubiao_demo/agent/router.py) | LLM 意图分类，将用户问题路由到对应业务节点 | `RouterDecision`, `_route_via_structured_output()`, `_route_via_tool_calling()`, `build_router_node()` |
| [graph.py](file:///d:/DEMO/zhaotoubiao_demo/agent/graph.py) | 构建 StateGraph，注册节点与条件边 | `build_graph()`, `_with_fallback()`, `AgentGraph` |
| [checkpointer.py](file:///d:/DEMO/zhaotoubiao_demo/agent/checkpointer.py) | 对话记忆持久化抽象层 | `create_checkpointer(backend)`: 支持 memory/sqlite/postgres/redis |
| [price_inquiry/](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/price_inquiry/) | **智能询价包**（2026-08-15 由 3,152 行单文件拆为 11 模块）：意图解析（含项目编号检测）→ 子路由（company/bidding/all）→ 多级降级检索（OR FULLTEXT + LIKE 回退 + 逐关键词拆分 + 全表兜底）→ 混合重排序 → 二次回表补列 → 模板渲染；三层查询守卫 + 后置回溯校验防模糊匹配（P0-11/P0-12，见 §4.4） | `node_price_inquiry()`（[node.py](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/price_inquiry/node.py)）、`_extract_project_number_candidate()` / `_has_valid_query_entity()`（[intent.py](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/price_inquiry/intent.py)）、`_execute_recall_chain_core()` / `_rank_records()`（[recall.py](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/price_inquiry/recall.py)） |
| [knowledge_qa.py](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/knowledge_qa.py) | 对接 `public_kb` RAG 引擎（惰性单例 + `ensure_loaded()` 显式加载） | `_get_rag()`, `node_knowledge_qa()` |
| [answer_templates.py](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/answer_templates.py) | 自然语言回答模板引擎，分 query_type 渲染回答 | `AnswerTemplate`, `render_answer()` |
| [output_templates.py](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/output_templates.py) | 字段筛选声明式配置 | `FieldDescriptor`, `OutputTemplate` |

**路由意图枚举**（`RouterIntent`）：
- `knowledge_qa` → 专业知识问答（法律法规、流程、规则）
- `price_inquiry` → 历史中标价格/企业情报/处罚记录查询
- `general_chat` → 问候、自我介绍、功能咨询
- `doc_qa` → 文档问答（Demo 阶段为占位）
- `fallback` → 意图不明时的兜底引导

### 3.3 `public_kb/` — 公共知识库 RAG 引擎

```
public_kb/
├── __init__.py             # 包入口，懒加载 PublicKnowledgeRAG
├── __main__.py             # CLI 入口（python -m public_kb；--init / --ingest-handoff / --rebuild）
├── config.py               # 统一配置中心（Settings dataclass + CitationRuleConfig）
├── rag_engine.py           # RAG 引擎对外统一入口（query / init / ensure_loaded）
├── qa_chain.py             # LCEL 问答链（检索 + 拒答 + 引用溯源 + LLM 调用）
├── qa_chain_async.py       # 异步问答链（token 级流式，AsyncRAGPipeline）
├── llm_factory.py          # 统一 ChatOpenAI 工厂（graph / rag_engine / price_inquiry 三处共用）
├── milvus_store.py         # Milvus 向量存储管理器（dense + sparse BM25 混合 schema）
├── embedding_service.py    # Embedding 服务封装
├── mineru_parser.py        # PDF 解析器（subprocess 调用本地 magic-pdf CLI）
├── reranker.py             # SiliconFlow HTTP 精排客户端（httpx，非本地模型）
├── chunk_ids.py            # chunk_uid 稳定标识工具（内容派生，跨集合重建稳定）
├── citations.py            # 引用溯源模型 + 校验规则集（R1-R7，fail-soft）
├── chunker.py              # 语义切片器（Markdown 标题层级）
├── text_cleaner.py         # 文本清洗器（去噪、去重）
├── book_pipeline.py / normalize.py / handoff_validate.py  # 2026-09-03 重建管线（规范化层 / 质量门禁 / 交接包校验）
└── process_csv.py / csv_loader.py  # 已于 2026-09-04 删除（CSV 政策语料管线退役）
```

**RAG 处理流水线**：

```
PDF 文件
  → MinerUParser.parse()       (MinerU API 解析为 Markdown)
  → TextCleaner.clean()        (去噪清洗)
  → SemanticChunker.chunk()    (按标题层级切片, 2000 字/块, 100 字重叠)
  → create_embeddings()        (BGE-m3 向量化, 1024 维)
  → MilvusStoreManager         (存入 Milvus public_kb 集合)
  → build_qa_chain()           (构建 LCEL 混合检索问答链)
```

**知识库重建入库流水线（2026-09-03 起，现行唯一入库路径）**：

```
DATA/raw_data/ 中三本书的 MinerU content_list.json（修复起点）
  → normalize.py             (纯函数规范化层：结构块 + 页码)
  → chunker.py 增强          (父章节打包短单元，问答/法条完整)
  → handoff_validate.py      (质量门禁：uid 摘要 + P3 gates + 跨书唯一)
  → DATA/repaired_knowledge/ (交接包，uid 摘要锁定)
  → python -m public_kb --ingest-handoff DATA/repaired_knowledge
                             (幂等 upsert，主键 = chunk_uid VARCHAR)
```

> 历史路径：CSV 政策语料入库管线（`process_csv` / `csv_loader`，`python -m public_kb.process_csv --csv-dir raw_policy`）已于 2026-09-04 随其数据线一并删除，`raw_policy/` 目录不再存在。

**引用溯源体系（2026-08-14 引入，`chunk_ids.py` / `citations.py`）**：

- **chunk_uid 稳定标识**：入库时对每个 chunk 按 `(doc_name, chapter, chunk_index, 内容哈希)` 确定性派生 `chunk_uid`，跨集合重建不变、同内容重复行共享（用于测评去重检测），不同于无业务含义的 auto_id 主键。
- **内联引用标记**：LLM 回答时在相关结论句末标注【来源N】标记（`enable_inline_citations=True`）。
- **结构化校验（R1-R7）**：`CitationValidator` 对每条回答输出 `citation_validation` 结构化报告，覆盖引用完整性（chunk_id / chunk_uid / 数据源位置 / 原文完整）、上下文无遗漏（R5）、无幻觉引用（R6）、严格模式上下文全标记（R7，默认关）；所有规则 **fail-soft**，只产出报告不阻断回答。规则启停见 `config.CitationRuleConfig`。
- 引用溯源评测入口：`python scripts/run_knowledge_citation_eval.py`（详见 §3.5）；schema 规范见 [../design_docs/citation_schema.md](../design_docs/citation_schema.md)。

**检索配置（当前生产环境值）**：

| 参数 | 值 | 说明 |
|------|-----|------|
| `retrieval_top_k` | 5 | 稠密检索返回 Top-K |
| `similarity_threshold` | 0.45 | 降级路径阈值 |
| `hybrid_dense_limit` | 30 | 稠密向量检索候选数 |
| `hybrid_sparse_limit` | 30 | 稀疏向量检索候选数 |
| `hybrid_fusion_limit` | 30 | RRF 融合后取 Top-N |
| `nprobe` | 32 | IVF 检索探测单元数 |
| `rrf_k` | 60 | RRF 融合参数 k |
| `reranker_model` | `BAAI/bge-reranker-v2-m3` | Cross-Encoder 重排序模型 |
| `chunk_max_chars` | 2000 | 单块最大字符数 |
| `chunk_overlap_chars` | 100 | 句子切分时的重叠字符数 |

### 3.4 `cloud_sync/` — 云端数据同步包（已退役）

> **2026-09-04 退役**：云端（阿里云）数据迁移线已放弃，`cloud_sync/` 包、`archive/migrate_milvus_cloud.py`、`test/test_cloud_sync.py` 及其水印文件已全部删除，`.gitignore` 中对应条目已移除。当前数据底座为本地 Docker（Milvus 2.6.x + MySQL）。若未来重启云迁移，视为全新决策另行立项。

### 3.5 `test/` — 工具脚本集合

**活跃诊断工具**（2026-08-15 结构审计后精简为 4 个 + 共享公共层）：

| 工具脚本 | 命令示例 | 用途 |
|---------|---------|------|
| `_diag_common.py` | 被各诊断脚本引用 | 共享 MySQL 连接工厂 `get_connection()` |
| `db_explorer.py` | `python test/db_explorer.py --overview` | 快速浏览所有数据库的表数量/行数/大小 |
| `explain_sql.py` | `python test/explain_sql.py --db <DB> --sql "<SQL>"` | 执行 EXPLAIN ANALYZE，检测全表扫描 |
| `create_fulltext_indexes.py` | `python test/create_fulltext_indexes.py --dry-run` | 自动创建 FULLTEXT 索引 |
| `profile_node_price.py` | `python test/profile_node_price.py` | 对 price_inquiry 节点执行一次端到端 profile |

**自动化测试**（mock 内部函数，无需真实 DB/LLM，两种运行方式均可）：

| 测试文件 | 覆盖内容 |
|---------|---------|
| `test_recall_optimization.py` | 召回阶段逻辑测试 |
| `test_sub_route.py` | 子路由分类测试 |
| `test_bug_repairs.py` | 已修复 bug 的回归测试（2026-08-15 起直接从生产模块导入，删除本地冻结副本，生产回归时真实失败） |
| `test_p0_11_guard.py` / `test_p0_11_full_recall_fix.py` | P0-11 模糊匹配/盲目召回防范体系（三层前置拦截、检索词白名单、后置回溯校验） |
| `test_p0_12_project_number_detection.py` | P0-12 项目编号意图识别与 bid_project 字段合规性 |
| `test_citation_tracing.py` | 引用溯源测试（chunk_uid 生成、【来源N】标记解析、R1-R7 校验规则） |
| `test_model_identity_config.py` | 模型身份配置守卫（.env 必填、无默认、无别名兜底） |
| `test_tool_registry.py` / `test_tools_contract.py` / `test_agent_tool_loop.py` | 工具库与 agent-mode 契约测试 |
| `test_rag_async.py` / `test_sql_timeout.py` 等 `test_*_async.py` | 异步轨（RAG / 询价 / 路由 / 流式 / 连接池）测试 |

**评测脚本**（位于 `scripts/`，见 [run_three_core_evaluation.py](file:///d:/DEMO/zhaotoubiao_demo/scripts/run_three_core_evaluation.py)、[run_knowledge_citation_eval.py](file:///d:/DEMO/zhaotoubiao_demo/scripts/run_knowledge_citation_eval.py)）：三大核心模块评测与引用溯源评测（需真实 Milvus + LLM），输出写入 `test_report/`。共享骨架已抽取至 `eval_common.py` / `eval_report_common.py`；旧 text2sql 评测管线（`run_evaluation.py` / `generate_report.py`）已归档至 `scripts/archive/`。

**历史遗留脚本**：数据准备阶段的诊断/迁移脚本（`scan_tables.py`、`scan_export_csv.py`、`profile_current_price.py`、`_step*` 迁移步骤、`export_samples.py` 等）已归档至 `test/legacy/`，不参与当前工作流。

```powershell
python -m pytest test/ -v                  # pytest 方式
python -m unittest discover -s test -v     # 标准库方式
```

### 3.6 数据目录

| 目录 | 内容 | 来源 |
|------|------|------|
| `DATA/raw_data/` | MinerU（magic-pdf）解析产出的 Markdown + 中间文件 | 本地解析自动生成 |
| `DATA/repaired_knowledge/` | 2026-09-03 重建的交接包（uid 摘要 + 质量门禁产物） | book_pipeline 产出，`--ingest-handoff` 输入 |
| `DATA/sample_data/` | 各数据库导出的 JSON/CSV 样本 | 早期 `export_samples.py` 导出（脚本已归档） |
| `raw_pdfs/` | 3 本招投标法规 PDF 源文件（约 2000+ 页） | 手动放置 |
| `raw_tables/` | 非空表 CSV 数据导出（含 `product_info.csv` 产品线回滚资产） | 早期导出产物 |
| `test_report/` | 三大核心评测 / 引用溯源评测输出（`metrics.json`、`evaluation_report.*`、`knowledge_citation_results.jsonl` 等） | `run_three_core_evaluation.py` / `run_knowledge_citation_eval.py` 自动生成 |

> `raw_policy/`（政策 CSV 语料）与 `new_pdfs/` 已随 CSV 管线删除（2026-09-04）。

### 3.7 配置文件

| 文件 | 作用 |
|------|------|
| [.env](file:///d:/DEMO/zhaotoubiao_demo/.env) | API 密钥与端点配置（模型身份必填：`LLM_MODEL` / `EMBEDDING_MODEL` / `RERANKER_MODEL` 及凭据端点；缺失即抛错，见 [.env.example](file:///d:/DEMO/zhaotoubiao_demo/.env.example)） |
| [requirements.txt](file:///d:/DEMO/zhaotoubiao_demo/requirements.txt) | Python 依赖清单，含版本上下界约束 |
| [public_kb/config.py](file:///d:/DEMO/zhaotoubiao_demo/public_kb/config.py) | 全局配置中心（Settings dataclass + CitationRuleConfig；含 SQL 查询超时 15s、节点总超时 45s、内联引用溯源开关、引用校验规则 R1-R7 开关等） |
| [milvus/docker-compose.yml](file:///d:/DEMO/zhaotoubiao_demo/milvus/docker-compose.yml) | Milvus Standalone(2.6.x) + etcd + MinIO + Attu 四容器编排 |
| [milvus/启动Milvus.bat](file:///d:/DEMO/zhaotoubiao_demo/milvus/启动Milvus.bat) | Windows 一键启动脚本 |
| [docker/mysql/docker-compose.yml](file:///d:/DEMO/zhaotoubiao_demo/docker/mysql/docker-compose.yml) | 本地 MySQL 8.0 可选部署（`ztb_mysql` 容器，数据落盘 `docker/mysql/mysql_data`） |

---

## 4. 核心业务模块逻辑梳理

### 4.1 整体请求流程

以用户查询 **"帮我查一下皮艺沙发的中标记录"** 为例：

```
CLI: python -m agent --question "帮我查一下皮艺沙发的中标记录"
  → AgentGraph.invoke(question)
  → graph.invoke({"messages": [HumanMessage(content=question)]})
  → START → router → price_inquiry → END
```

### 4.2 意图路由模块 (Router)

**文件**：[agent/router.py](file:///d:/DEMO/zhaotoubiao_demo/agent/router.py)

**流程**：
1. 提取最近 3 轮对话历史 + 当前用户输入
2. LLM（`.env LLM_MODEL`，temperature=0）进行意图分类
3. 优先尝试 `with_structured_output(RouterDecision)`，API 不支持时回退到 Tool Calling
4. 输出意图写入 `state["router_intent"]`

**RouterDecision 模型**：
```python
class RouterDecision(BaseModel):
    intent: RouterIntent     # knowledge_qa | price_inquiry | general_chat | doc_qa | fallback
    reason: str              # 分类理由，4~8 字
```

> 注：`sub_intent` 预留字段已于 2026-08-15 结构审计清理（从未被读取）。

### 4.3 专业知识问答模块 (knowledge_qa)

**文件**：[agent/nodes/knowledge_qa.py](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/knowledge_qa.py)

**流程**：
1. 惰性单例初始化 `PublicKnowledgeRAG`（`ensure_loaded()` 显式加载向量库与问答链）
2. 调用 `rag.query(question)` 执行混合检索 + LLM 生成
3. 返回回答文本 + 引用来源（含【来源N】内联标记、标准化 citations 与 R1-R7 校验报告）

### 4.4 智能询价模块 (price_inquiry) — 重点

**包**：[agent/nodes/price_inquiry/](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/price_inquiry/)（2026-08-15 由 3,152 行单文件按职责拆为 11 个模块，`__init__.py` 重导出全部历史符号，外部导入零改动）

```
node.py（入口 + 三层查询守卫 + 引导话术）
  → intent.py（统一意图解析 + 项目编号提取 + 实体校验）
  → queries.py（表专用查询函数）
  → recall.py（多级降级检索链 + 超时 + 混合重排序 + 回表补列）
  → sql_builders.py / enum_norm.py / schema.py / db.py / models.py
  （异步轨平行实现：node_async.py / recall_async.py / db_async.py）
```

采用**意图解析 + 子路由 + 多级降级召回 + 混合重排序**架构：

#### 阶段 1：意图结构化解析 (`_parse_unified_intent()`，[intent.py](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/price_inquiry/intent.py))

```
用户问题: "帮我查一下皮艺沙发的中标记录"
  → LLM 解析 → SearchIntent:
     - sub_route: "bidding_query"
     - query_type: "bid_search"
     - hard_filters: {status: "中标"}
     - semantic_keywords: ["皮艺沙发"]
```

#### 阶段 2：子路由分发

`price_inquiry` 内部按 `sub_route` 进行二级路由（**产品线已下线，`product_query` 子路由已移除**，见 [product_line_deprecation.sql](file:///d:/DEMO/zhaotoubiao_demo/scripts/product_line_deprecation.sql)）：

| 子路由 | query_type | 描述 |
|--------|-----------|------|
| `company_query` | `company_detail` / `penalty_check` | 企业详情查询、企业处罚记录 |
| `bidding_query` | `bid_project` / `bid_search` | 招标项目详情、招标项目搜索 |
| `all` | 混合 | 跨三表（company_info / company_penalty / bid_project）通用查询 |

#### 阶段 3：多级降级召回（[recall.py](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/price_inquiry/recall.py)，P0 清理后 FULLTEXT_AND 无效阶段已删除）

```
优先级从高到低：
  Stage 1 (权重 1.00): OR FULLTEXT (MATCH...AGAINST IN BOOLEAN MODE，OR 语义)
  Stage 3 (权重 0.82): LIKE 回退（关键词逐词 LIKE '%...%'）
  Stage 4 (权重 0.72): 逐关键词拆分检索（FULLTEXT / LIKE 重试，去重合并）
  Stage 5 (权重 0.55): 全表扫描兜底（LIMIT 限制）
```

> 注：`_RECALL_STAGE_WEIGHTS` 保留键位 0/2 作为占位（键位 0 原为 Milvus 结构化语义召回——`mysql_price_semantic` 集合及 `semantic.py` 已于 2026-09-04 全面退役删除；键位 2 为 AND FULLTEXT 占位）。全硬过滤链零行时自动**放宽偏好性过滤重试**（P0-2），并输出**召回漏斗日志**量化硬过滤淘汰规模（P0-3）。

#### 阶段 4：混合重排序 + 回表补列

所有召回结果汇总后进行 Python 侧重排序，综合 MySQL 得分、语义相似度得分和阶段权重计算最终排序（`_rank_records`）。命中行确定后按主键**二次回表（SELECT \*）补齐输出模板声明的全部字段**（`_enrich_rows_full_columns`），避免"字段缺失"源于 SQL 缺列。

#### 阶段 5：模板渲染

将排序后的记录通过 `output_templates` 筛选字段，再通过 `answer_templates` 渲染为自然语言回答。

#### 风险防控：模糊匹配防范与项目编号检测（P0-11 / P0-12 修复）

- **P0-12 项目编号检测**：`_extract_project_number_candidate()`（[intent.py](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/price_inquiry/intent.py)）在意图解析阶段识别项目编号（纯编号如 `AH2024-001`、方括号格式如 `[350001]FJGGZY[GK]2024013`、口语化带编号），命中则强制走 `project_detail` 子路由，且 `bid_project` 表仅使用 `project_number` 字段召回，避免编号被当作公司名/关键词误匹配。
- **P0-11 防模糊匹配/盲目召回**（三层前置拦截 + 中台湾控 + 后置回溯，集中在 [node.py](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/price_inquiry/node.py)）：
  1. 工商主体名称格式校验 `_is_valid_company_name()`（拦截裸实体名、非公司名输入）；
  2. 有效查询实体检测 `_has_valid_query_entity()`（all 路由纳入前置实体校验）；
  3. `project_detail` 缺项目编号统一引导话术 `_build_unified_guidance()`；
  4. 中台湾控 — `bid_project` 检索词白名单过滤（`_build_search_term`）；
  5. 后置回溯 — 召回结果与查询实体匹配校验；`company_penalty` 查询由 LIKE 模糊匹配改为精确匹配。

回归测试：`test/test_p0_11_guard.py`、`test/test_p0_11_full_recall_fix.py`、`test/test_p0_12_project_number_detection.py`。

### 4.5 通用对话模块 (general_chat)

**文件**：[agent/nodes/general_chat.py](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/general_chat.py)

纯 LLM 闲聊模式，使用 System Prompt 将 LLM 限定为招投标领域助手角色，不涉及知识库查询、不连接数据库。

### 4.6 文档问答模块 (doc_qa)

**文件**：[agent/nodes/doc_qa.py](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/doc_qa.py)

Demo 阶段为占位节点，返回功能待上线提示。文件中已写明完整接口契约和上线改动清单（见 [技术债务台账](#9-技术债务台账)）。

### 4.7 兜底引导模块 (fallback)

**文件**：[agent/nodes/fallback.py](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/fallback.py)

处理两类情况：
1. 来自 `_with_fallback` 的异常降级（保留原始 answer）
2. 意图不明时列出可用功能清单引导用户

### 4.8 异常兜底机制

所有业务节点在注册时被 `_with_fallback` 包装（[graph.py](file:///d:/DEMO/zhaotoubiao_demo/agent/graph.py) L148-L151）：

```python
graph.add_node("knowledge_qa", _with_fallback(node_knowledge_qa))
graph.add_node("price_inquiry", _with_fallback(node_price_inquiry))
graph.add_node("general_chat", _with_fallback(node_general_chat))
graph.add_node("doc_qa", _with_fallback(node_doc_qa))
```

任何未捕获异常 → 自动降级返回友好提示，不会导致整个 Agent 崩溃。

### 4.9 回答模板与输出配置

<!-- [VISUAL] 建议补充：answer_templates 与 output_templates 协作流程图 -->
**截图占位：`analysis_docs/images/template_engine_flow.png`**

**output_templates**（字段筛选）与 **answer_templates**（自然语言渲染）分工明确：
- `output_templates.py`：声明式配置字段描述符（FieldDescriptor），管理三级优先级（required > conditional > optional）、空值行为（hide / show_placeholder / keep_null）、文本截断规则
- `answer_templates.py`：按 query_type 绑定回答模板，支持单条/多条/空结果/未找到四种场景，必须包含数据来源行（`ztb_clean.{table_name}`）

---

## 5. 数据源与检索策略

### 5.1 MySQL 结构化检索

#### 数据源：ztb_clean 统一数据库

当前生产环境使用单一清洗数据库 `ztb_clean`（由上游"上册"数据流水线产出），替代了早期分散的 5 个原始数据库架构。

- **连接信息**：通过环境变量 `MYSQL_HOST` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_PORT` 配置（本地 Docker 为 `127.0.0.1:3306`；内网/生产库按 `.env` 实际值），源码不含硬编码口令
- **清洁数据库**：`ztb_clean`（默认，可通过 `MYSQL_CLEAN_DB` 环境变量修改）
- **核心表**：
  - `company_info` — 企业基本信息
  - `company_penalty` — 企业处罚记录
  - `bid_project` — 招标项目信息
  - ~~`product_info`~~ — 产品线已于 2026-08-10 下线，**不再建表**（数据以 `raw_tables/product_info.csv` 留档作回滚资产，见 [product_line_deprecation.sql](file:///d:/DEMO/zhaotoubiao_demo/scripts/product_line_deprecation.sql)）
- **连接池**：手动连接池（[db.py](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/price_inquiry/db.py)），无固定上限，优先复用空闲连接（ping 校验存活），无空闲时惰性新建；会话级 `net_read/write/wait_timeout=28800` 保障流式游标长连接；SQL 读写超时读取 `settings.sql_query_timeout`（默认 15s）

#### FULLTEXT 索引配置

**MySQL 配置要求**（`my.cnf`）：
```ini
[mysqld]
ngram_token_size=2       # 中文双字分词
ft_min_word_len=1        # 最小词长
```

**索引 DDL**（参考 [test/recommended_indexes.sql](file:///d:/DEMO/zhaotoubiao_demo/test/recommended_indexes.sql)）：
```sql
-- bid_project 的 FULLTEXT 必须恰好是 (purchaser, successful_bidder)
-- （P0-11：MATCH 列集必须与索引定义完全一致）
ALTER TABLE `ztb_clean`.`bid_project`
    ADD FULLTEXT INDEX `ft_bid_project_purchaser_bidder`
    (`purchaser`, `successful_bidder`) WITH PARSER ngram;
```

### 5.2 Milvus 向量检索（Public Knowledge Base）

#### 集合配置（2026-09-03 重建后）

| 参数 | 生产环境值 |
|------|-----------|
| 集合名称 | `public_kb`（唯一活跃集合；`mysql_price_semantic` 已于 2026-09-04 全面退役） |
| 数据规模 | 3 本法规书 / 920 chunks（重建自 `DATA/repaired_knowledge`） |
| 主键 | `chunk_uid` VARCHAR（内容派生，`auto_id=False`，幂等 upsert） |
| 字段 | `chunk_uid` / `text` / `vector` / `sparse_vector` + `text_bm25` Function（jieba 分词） |
| 稠密索引 | `IVF_FLAT` / COSINE / nlist=256 |
| 稀疏索引 | `SPARSE_INVERTED_INDEX` / BM25 |
| nprobe | 32 |
| 相似度阈值 | 自适应阈值（主路径，地板 0.45） |
| 检索 Top-K | 5（最终）、30/30/30（稠密/稀疏/融合候选池） |

> **零降级（D3）**：检索/精排任何环节失败直接抛错（由 Agent 层兜底为友好降级话术），无 dense-only 降级、无伪造精排分；集合 schema 缺 `sparse_vector` + BM25 Function 会在链构建时 fail-fast 报错。

**数据规模说明**：`mysql_price_semantic`（结构化数据语义召回，曾 77,597 条）已于 2026-09-04 全面退役——集合、`semantic.py`、配置键与重建脚本一并删除，**勿建议重建**。

#### 混合检索流程

```
用户问题 "什么是公开招标？"
  → BGE-m3 稠密向量化 (1024 维) → dense_limit=30
  → BGE-m3 稀疏向量化              → sparse_limit=30
  → RRF 融合 (k=60)                → fusion_limit=30
  → BGE-reranker-v2-m3 重排序      → Top-K=5
  → 相似度过滤 (自适应阈值)
  → 低于阈值 → 拒答
  → 高于阈值 → 拼接上下文（按 chunk 编号） → LLM 生成回答（内联【来源N】标记）+ 引用溯源校验（R1-R7，见 §3.3）
```

### 5.3 MySQL 与 Milvus 协同策略

| 维度 | MySQL (ztb_clean) | Milvus (public_kb) |
|------|-------------------|---------------------|
| 数据类型 | 结构化字段（金额、日期、公司名） | 非结构化文本（法规全文、文档片段） |
| 查询方式 | SQL + FULLTEXT 多级降级召回 | 稠密+稀疏混合向量检索 + RRF + 重排序 |
| 优势 | 精确过滤、聚合统计、排序 | 语义匹配、跨段落理解 |
| 路由触发 | `price_inquiry` 意图 | `knowledge_qa` 意图 |

---

## 6. 本地开发环境搭建

### 6.1 前置条件

| 依赖 | 版本要求 | 说明 |
|------|---------|------|
| Python | 3.12 | 使用 Anaconda/miniconda 管理 |
| Docker Desktop | 最新版 | 运行 Milvus 向量数据库 |
| Git | 任意 | 版本管理 |
| MySQL 客户端 | — | 需能访问 `MYSQL_HOST` 指向的数据库（或配置本地 MySQL） |

### 6.2 分步安装指引

#### Step 1：克隆项目

```powershell
git clone <repo-url> zhaotoubiao_demo
cd D:\DEMO\zhaotoubiao_demo
```

#### Step 2：创建 Python 虚拟环境

```powershell
# 使用 conda
conda create -n zhaotoubiao python=3.12 -y
conda activate zhaotoubiao

# 或使用 venv
python -m venv venv
.\venv\Scripts\activate
```

#### Step 3：安装依赖

```powershell
pip install -r requirements.txt
```

> **注意**：旧版曾要求 `setuptools<70`（pymilvus 2.4.x 依赖 `pkg_resources`），该约束已于 2026-09 整改解除——pymilvus 3.x 不再依赖 `pkg_resources`，无需任何 setuptools 降级。

#### Step 4：配置环境变量

复制 `.env.example` 为 `.env` 并填入有效配置（模型身份必填，缺失即抛错）：

```powershell
# 编辑 .env 文件，至少配置以下项目（当前示例值见 .env.example）：
#   LLM_API_KEY=sk-xxx                   (LLM 服务方获取)
#   LLM_BASE_URL=https://openrouter.ai/api/v1
#   LLM_MODEL=z-ai/glm-5.3-flash         (.env 换什么模型，项目就调用什么模型)
#   EMBEDDING_API_KEY=sk-xxx             (SiliconFlow 平台获取)
#   EMBEDDING_BASE_URL=https://api.siliconflow.cn/v1
#   EMBEDDING_MODEL=BAAI/bge-m3          (1024 维，8192 token 上限)
#   RERANKER_MODEL=BAAI/bge-reranker-v2-m3  (凭据/端点复用 EMBEDDING_*)
#
# MySQL 配置（本地 Docker 或生产库）：
#   MYSQL_HOST=127.0.0.1
#   MYSQL_PORT=3306
#   MYSQL_USER=root
#   MYSQL_PASSWORD=<密码>
#   MYSQL_CLEAN_DB=ztb_clean
```

> PDF 解析使用本地 `magic-pdf` CLI（GPU 版 MinerU），无需配置网络 API token。

<!-- [SCREENSHOT] 建议补充：.env 文件配置完成后的截图 -->
**截图占位：`analysis_docs/images/env_config_example.png`**

#### Step 5：启动 Milvus 向量数据库

```powershell
cd milvus
.\启动Milvus.bat
```

或手动执行：

```powershell
cd milvus
docker compose up -d
```

验证 Milvus 是否正常启动：

```powershell
docker ps --filter "name=milvus"
# 预期看到 4 个容器：milvus-standalone, milvus-etcd, milvus-minio, milvus-attu
```

<!-- [SCREENSHOT] 建议补充：docker ps 显示 4 个 Milvus 容器正常运行的截图 -->
**截图占位：`analysis_docs/images/milvus_docker_status.png`**

#### Step 6：初始化公共知识库（首次运行）

```powershell
cd D:\DEMO\zhaotoubiao_demo
python -m public_kb --init --pdf-dir raw_pdfs
```

该命令将：
1. 调用本地 `magic-pdf` CLI 解析 `raw_pdfs/` 下的 PDF 文件为 Markdown
2. 文本清洗（去页眉页脚、去页码、去过短行、压缩空行）
3. 按 Markdown 标题层级语义切片（2000 字/块，100 字重叠）
4. BGE-m3 向量化后存入 Milvus `public_kb` 集合

> 预计耗时：3 本 PDF（约 2000+ 页）约 10~20 分钟（取决于本地 GPU 解析速度）。
> **现行推荐**：已有修复后的交接包时优先用 `python -m public_kb --ingest-handoff DATA/repaired_knowledge`（幂等 upsert，质量门禁已过），无需重跑 PDF 解析。

<!-- [SCREENSHOT] 建议补充：知识库初始化完成后的终端输出截图 -->
**截图占位：`analysis_docs/images/kb_init_complete.png`**

### 6.3 启动服务

#### 方式一：CLI 单次问答

```powershell
python -m agent --question "招标方式有哪些？"
```

#### 方式二：CLI 交互模式

```powershell
python -m agent --interactive
```

交互模式支持命令：
- 输入问题 → 获取回答
- `clear` → 清空当前会话历史
- `quit` / `exit` → 退出

#### 方式三：调试模式（详细日志）

```powershell
python -m agent --question "查一下智慧交通项目的中标价格" --verbose
```

#### 方式四：知识库独立测试

```powershell
python -m public_kb --interactive
```

### 6.4 验证安装

运行以下命令验证各组件是否就绪：

```powershell
# 1. 验证 MySQL 连接
python -c "import os; from dotenv import load_dotenv; load_dotenv(); import pymysql; conn=pymysql.connect(host=os.getenv('MYSQL_HOST','127.0.0.1'),user=os.getenv('MYSQL_USER','root'),password=os.getenv('MYSQL_PASSWORD',''),database=os.getenv('MYSQL_CLEAN_DB','ztb_clean')); print('MySQL OK:', conn.open); conn.close()"

# 2. 验证 Milvus 连接
python -c "from pymilvus import connections; connections.connect(host='localhost', port='19530'); print('Milvus OK')"

# 3. 验证 RAG 问答
python -m public_kb --question "招标方式有哪些？"

# 4. 验证 Agent 全流程
python -m agent --question "你能做什么？" --verbose

# 5. 运行自动化测试（无需真实 DB/LLM）
python -m pytest test/ -v
```

---

## 7. 部署与发布流程

### 7.1 环境拓扑

| 环境 | 用途 | 配置差异 |
|------|------|---------|
| **本地开发** | 日常开发与调试 | Docker Milvus（本地）、MySQL（`.env MYSQL_HOST` 指向本地 Docker 或内网库） |
| **生产环境** | 面向用户的服务 | Milvus（独立服务器或云服务）、MySQL（生产实例） |

### 7.2 生产环境配置核查清单

发布前请逐项确认：

- [ ] `.env` 中所有 API Key 均为生产环境密钥（非测试 Key）
- [ ] `LLM_API_KEY` 有效，余额充足（模型身份三项 `LLM_MODEL` / `LLM_BASE_URL` 已填写）
- [ ] `EMBEDDING_API_KEY` 有效，余额充足（`EMBEDDING_MODEL` / `EMBEDDING_BASE_URL` / `RERANKER_MODEL` 已填写）
- [ ] MySQL 连接信息为生产环境地址/端口/用户/密码
- [ ] `MYSQL_CLEAN_DB` 指向生产清洁数据库
- [ ] Milvus 服务可访问（2.6.x），`public_kb` 集合已初始化并包含最新向量数据
- [ ] MySQL `ztb_clean` 数据库数据为最新批次
- [ ] Python 依赖版本与 [requirements.txt](file:///d:/DEMO/zhaotoubiao_demo/requirements.txt) 锁定版本一致

### 7.3 发布流程

```powershell
# 1. 拉取最新代码
git pull origin main

# 2. 切换至发布分支（如有）
git checkout release/vX.Y.Z

# 3. 同步依赖
pip install -r requirements.txt

# 4. 验证数据库连接
python -c "from pymilvus import connections; connections.connect(host='localhost', port='19530'); print('Milvus OK')"
python -c "import pymysql; conn=pymysql.connect(host='<PROD_HOST>',user='<USER>',password='<PWD>',database='ztb_clean'); print('MySQL OK'); conn.close()"

# 5. 启动 Agent 验证
python -m agent --question "招标方式有哪些？" --verbose

# 6. 验证询价模块
python -m agent --question "帮我查一下智慧交通项目的中标价格" --verbose
```

### 7.4 回滚方案

如遇生产故障需紧急回滚：

```powershell
# 1. 切换至上一个稳定版本的 tag/commit
git checkout <previous-stable-tag>

# 2. 重新安装依赖
pip install -r requirements.txt

# 3. 重启服务
# （如使用进程管理器，执行对应的 restart 命令）
```

### 7.5 云端迁移（已退役）

> **2026-09-04 决策**：云端（阿里云）数据迁移线整体放弃，`cloud_sync/` 工具链已删除，不再维护迁移排期与操作指引。当前数据底座为本地 Docker（Milvus 2.6.x + MySQL 8.0）。历史阶段性结论（2026-08-13 可行性论证 + Milvus 云端写入验证通过）仅为存档记录，相关报告文档已删除。若未来重启云迁移，视为全新决策另行立项。

---

## 8. 常见问题排查指南

### 8.1 环境与依赖类

#### Q1：`ModuleNotFoundError: No module named 'pkg_resources'`

**说明**：该问题源于旧版 pymilvus 2.4.x。项目已于 2026-09 整改升级至 pymilvus ≥3.0（不再依赖 `pkg_resources`），正常安装 requirements.txt 即可，无需 setuptools 降级。若在旧环境中遇到，升级依赖：`pip install -r requirements.txt`。

#### Q2：Docker 启动 Milvus 失败（端口冲突）

**原因**：19530 / 9091 / 9000 / 9001 / 2379 / 3000 端口被占用。

**排查**：
```powershell
netstat -ano | findstr "19530"
```

**解决方案**：停止占用端口的进程，或修改 [docker-compose.yml](file:///d:/DEMO/zhaotoubiao_demo/milvus/docker-compose.yml) 中的端口映射。

#### Q3：Milvus 容器反复重启

**排查**：
```powershell
docker logs milvus-standalone --tail 50
docker logs milvus-etcd --tail 50
```

**常见原因**：磁盘空间不足（MinIO 数据卷占用过高）。

**解决方案**：
```powershell
# 清理 Milvus 数据卷（会丢失所有向量数据，谨慎操作）
docker compose down -v
docker compose up -d
# 重新初始化知识库
python -m public_kb --init --pdf-dir raw_pdfs
```

### 8.2 运行时错误类

#### Q4：Agent 启动报 API key 缺失（Settings() 抛"模型身份配置缺失"）

**原因**：模型身份配置（`LLM_MODEL` / `EMBEDDING_MODEL` / `RERANKER_MODEL` 及凭据端点）未配置或为空——项目口径是 .env 必填、无默认值、无别名兜底，缺失即拒绝启动。

**排查**：
```powershell
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print('LLM_MODEL:', os.getenv('LLM_MODEL','NOT SET'))"
```

**解决方案**：检查 `.env` 是否存在于项目根目录并按 `.env.example` 补齐全部必填模型身份项。

#### Q5：`LangGraph` 或 `LangChain` 版本冲突

**原因**：pip 依赖解析未遵循版本上下界约束。

**解决方案**：
```powershell
pip install -r requirements.txt --force-reinstall
```

#### Q6：price_inquiry 返回空结果

**排查**：
```powershell
# 1. 检查 MySQL 连接
python -c "import os; from dotenv import load_dotenv; load_dotenv(); import pymysql; conn=pymysql.connect(host=os.getenv('MYSQL_HOST','127.0.0.1'),user=os.getenv('MYSQL_USER','root'),password=os.getenv('MYSQL_PASSWORD',''),database=os.getenv('MYSQL_CLEAN_DB','ztb_clean')); cur=conn.cursor(); cur.execute('SELECT COUNT(*) FROM bid_project'); print('bid_project rows:', cur.fetchone()[0]); conn.close()"

# 2. 检查 FULLTEXT 索引
python test/explain_sql.py --db ztb_clean --sql "SELECT * FROM bid_project WHERE MATCH(purchaser, successful_bidder) AGAINST('智慧交通' IN BOOLEAN MODE) LIMIT 5"
```

#### Q7：RAG 检索召回过低或经常拒答

**排查**：
- 检查 `similarity_threshold` 是否过高（当前生产值 `0.45`）
- 检查知识库是否已初始化：Milvus Attu 面板（`http://localhost:3000`）查看 `public_kb` 集合行数

<!-- [SCREENSHOT] 建议补充：Attu 面板中 public_kb 集合的实体数量截图 -->
**截图占位：`analysis_docs/images/attu_public_kb_status.png`**

**调整方案**：在 [config.py](file:///d:/DEMO/zhaotoubiao_demo/public_kb/config.py) 中降低 `similarity_threshold` 或增大 `retrieval_top_k`。

### 8.3 数据与检索类

#### Q8：MinerU PDF 解析超时

**原因**：大 PDF 文件（如 2000+ 页的法规全书）OCR 解析耗时较长。

**解决方案**：检查 [config.py](file:///d:/DEMO/zhaotoubiao_demo/public_kb/config.py) 中 `mineru_timeout` 是否为 `3600`（秒），必要时增大。

#### Q9：MySQL FULLTEXT 索引不生效

**排查**：
```sql
-- 检查索引是否存在
SHOW INDEX FROM ztb_clean.bid_project WHERE Index_type = 'FULLTEXT';

-- 检查 ngram 配置
SHOW VARIABLES LIKE 'ngram_token_size';
SHOW VARIABLES LIKE 'ft_min_word_len';
```

**解决方案**：
```powershell
python test/create_fulltext_indexes.py
```

### 8.4 性能类

#### Q10：price_inquiry 查询慢（> 10 秒）

**排查**：
```powershell
python test/profile_node_price.py
```

观察 `[SQL_PROFILE]` 日志中的各阶段耗时，定位瓶颈：
- FULLTEXT 检索慢 → 检查索引是否存在、表数据量
- LIKE 回退触发 → 说明 FULLTEXT 索引不完整
- 全表扫描兜底触发 → 紧急优化信号，需立即补充索引

#### Q11：LLM API 调用频繁超时

**排查**：
```powershell
# 用 .env 中的 LLM_BASE_URL / LLM_API_KEY 测试连通性（OpenAI 兼容端点）
python -c "from openai import OpenAI; from dotenv import load_dotenv; import os; load_dotenv(); c=OpenAI(api_key=os.getenv('LLM_API_KEY'),base_url=os.getenv('LLM_BASE_URL')); print('OK')"
```

**解决方案**：在 [config.py](file:///d:/DEMO/zhaotoubiao_demo/public_kb/config.py) 中增大 `llm_timeout`（当前默认 60s）。

### 8.5 诊断工具速查

| 场景 | 工具/命令 |
|------|----------|
| SQL 性能分析 | `python test/explain_sql.py --db ztb_clean --sql "<SQL>"` |
| 全链路性能剖析 | `python test/profile_node_price.py` |
| 数据库概览 | `python test/db_explorer.py --overview` |
| FULLTEXT 索引检查 | `python test/create_fulltext_indexes.py --dry-run` |
| Milvus 集合状态 | Attu 面板: `http://localhost:3000` |
| Docker 日志 | `docker logs milvus-standalone --tail 100` |
| Agent 调试日志 | `python -m agent --question "..." --verbose` |
| 全量自动化测试 | `python -m pytest test/ -v` |

---

## 9. 技术债务台账

| ID | 类别 | 描述 | 优先级 | 影响范围 | 建议方案 | 预估工作量 |
|----|------|------|--------|---------|---------|-----------|
| TD-01 | 功能缺失 | `doc_qa` 文档问答节点为占位实现，未接入真实的文档向量存储/解析/Embedder | 高 | `agent/nodes/doc_qa.py`、新增依赖 | 1) 初始化 doc_vector_store 2) 实现 doc_parser 3) 实现 doc_embedder 4) 替换节点函数体 5) 调优 prompt（不改 State/Graph/其他节点） | 3~5 天 |
| TD-02 | 架构演进 | ✅ 已解决（2026-09-04）：旧架构残留示例（如 `bidding_information_dai.notifications` 索引 DDL）已从本文件清除，统一为 `ztb_clean` 现行示例 | — | — | — | — |
| TD-03 | 性能优化 | 全表扫描兜底（Stage 5）虽有权重惩罚（0.55）但在极端情况下仍可能触发并拉高延迟 | 中 | `agent/nodes/price_inquiry/recall.py` | 逐步扩展 FULLTEXT 索引覆盖范围，降低 Stage 5 触发概率；或对 Stage 5 增加超时熔断 | 1~2 天 |
| TD-04 | 可靠性 | Checkpointer 默认 MemorySaver（进程内存），服务重启丢失全部对话历史 | 中 | `agent/checkpointer.py` | 切换至 SQLite（单机）或 PostgreSQL（集群）后端（子包已入 requirements）；`create_checkpointer("sqlite")` 一行改动即可 | 0.5 天 |
| TD-05 | 运维 | ✅ 已解决：`.gitignore` 已完整覆盖（.env/DATA/raw_*/volumes/test_report 等） | — | — | — | — |
| TD-06 | 文档 | ✅ 已解决（2026-09 整改）：模型身份配置改为 .env 必填、无代码默认值（`__post_init__` 缺失即抛错），守卫测试 `test_model_identity_config.py` 固化 | — | — | — | — |
| TD-07 | 接入层 | ✅ 已解决（阶段 5）：`service/api.py` 提供 FastAPI SSE 流式端点（`POST /chat/stream`，含心跳），`uvicorn service.api:app` 启动 | — | — | — | — |
| TD-08 | 数据覆盖 | ztb_clean 数据库仅覆盖部分数据源，高价值原始库尚未纳入清洗流程 | 低 | 数据流水线（上册） | 扩展上游 ETL 流水线，将更多原始库纳入 `ztb_clean` | 5~10 天 |
| TD-09 | 监控 | 缺少应用级监控与告警（API 调用量、错误率、延迟分布） | 低 | 新增 `monitoring/` | 集成 Prometheus metrics + Grafana dashboard（依赖已于 2026-09-04 移除，启用时需重新引入） | 2~3 天 |
| TD-10 | 云迁移 | ✅ 已终止（2026-09-04）：云迁移线放弃，`cloud_sync/` 包与相关工具/文档删除。当前为本地 Docker 数据底座 | — | — | — | — |
| TD-11 | 架构演进 | ✅ 已解决：产品线下线后 `product_info` 不再建表（`docker/mysql/init/01-schema.sql` 已移除），数据以 `raw_tables/product_info.csv` 留档 | — | — | — | — |

---

## 10. 核心技术选型决策记录

| ID | 决策事项 | 选择方案 | 备选方案 | 决策理由 | 决策日期 |
|----|---------|---------|---------|---------|---------|
| AD-01 | Agent 框架 | **LangGraph (StateGraph)** | LlamaIndex Agent, AutoGen, 自研 | 1) 原生支持 LangChain 生态 2) StateGraph 条件路由 + Checkpointer 满足多轮对话需求 3) 可插拔节点设计天然支持业务扩展 4) 社区活跃、文档完善 | 2026 Q2 |
| AD-02 | LLM 服务 | **.env 声明的 OpenAI 兼容模型**（初期 deepseek-chat；2026-09 整改后模型身份只认 `LLM_MODEL`，当前示例 `z-ai/glm-5.3-flash` via OpenRouter） | GPT-4o, Qwen, 本地部署 | 1) OpenAI 兼容 API 零迁移成本 2) temperature=0 确定性输出适合意图分类 3) **模型身份 .env 单一事实源（D4/D6）：换模型只改 .env，代码不写死模型名** | 2026 Q2 / 2026-09 修订 |
| AD-03 | Embedding 模型 | **BAAI/bge-m3 (via SiliconFlow)** | text2vec-large-chinese, bge-large-zh-v1.5, OpenAI text-embedding-3 | 1) 1024 维向量 + 8192 token 上下文窗口，远超 bge-large-zh-v1.5（512 token） 2) 原生支持稠密+稀疏混合检索 3) 中文 MTEB 榜单领先 4) SiliconFlow 托管免运维 | 2026 Q2 |
| AD-04 | 向量数据库 | **Milvus Standalone（Docker；2026-09-01 起锁定 2.6.x）** | Pinecone, ChromaDB, Qdrant, Weaviate | 1) 社区活跃，与 LangChain Milvus 包深度集成 2) 支持 IVF_FLAT + 混合检索（2.6 支持 BM25 Function 服务端稀疏） 3) Docker 本地部署零成本 4) Attu 可视化管理面板。**注**：早期为 2.4，2026-09-01 D1 整改升级 2.6.x（旧 2.4 会静默丢弃 Function） | 2026 Q2 / 2026-09 修订 |
| AD-05 | 关系型数据库 | **MySQL 8.0+ (InnoDB)** | PostgreSQL, Elasticsearch | 1) 数据源本身为 MySQL 2) InnoDB FULLTEXT + ngram 分词原生支持中文全文检索 3) 无需引入额外存储组件 | 2026 Q2 |
| AD-06 | PDF 解析 | **本地 magic-pdf CLI（GPU 版 MinerU）** | PyPDF2, Unstructured, LlamaParse, MinerU 云 API | 1) 中文 PDF 解析质量最好（含 OCR） 2) 自动输出结构化 Markdown 3) 本地解析无需网络 API 与 token | 2026 Q2 / 2026-09 修订 |
| AD-07 | 重排序 | **BAAI/bge-reranker-v2-m3（SiliconFlow HTTP API）** | Cohere Rerank, 无重排序 | 1) 与 embedding 模型同系列，协同效果好 2) HTTP API 调用（httpx），**非本地 Cross-Encoder**（sentence-transformers 依赖已移除） 3) 显著提升 RRF 融合后的检索精度 | 2026 Q3 (P1 优化) |
| AD-08 | 数据源统一 | **ztb_clean 清洁数据库** | 维持 5 个分散原始库 | 1) 统一 Schema 降低查询复杂度 2) 数据清洗前置，减少运行时处理 3) 支持更高效的索引策略 | 2026 Q2 (上册) |
| AD-09 | 对话记忆 | **MemorySaver (默认) → SQLite/PostgreSQL（可选）** | Redis | 1) Demo 阶段零配置 2) `create_checkpointer` 工厂支持平滑升级，代码零改动 3) sqlite/postgres 后端子包已入 requirements | 2026 Q2 / 2026-09 修订 |
| AD-10 | setuptools 版本锁定 | ~~< 70~~ **已解除（2026-09 整改）** | — | pymilvus 升级至 3.x 后不再依赖 `pkg_resources`，setuptools 无需任何版本约束 | 2026 Q2 / 2026-09-04 终止 |
| AD-11 | 数据库云化选型 | ~~阿里云全托管~~ **已终止（2026-09-04）** | — | 云迁移线整体放弃（详见 §7.5）；决策记录保留为历史存档 | 2026-08-13 / 2026-09-04 终止 |
| AD-12 | 云端同步工具 | ~~自研 `cloud_sync/` 包~~ **已退役（2026-09-04）** | — | 云迁移放弃后包与测试、文档一并删除；决策记录保留为历史存档 | 2026-08-13 / 2026-09-04 退役 |
| AD-13 | price_inquiry 结构 | **单文件拆分为 11 模块包**（node / queries / recall / sql_builders / intent / semantic / enum_norm / db / schema / models + `__init__` 兼容层） | 维持 3,152 行单体 | 1) 唯一超 800 有效行的文件（2,513 行 / 10 项职责）三项判定标准全部超标 2) `__init__.py` 重导出全部历史符号，11 个外部导入方零改动 3) AST 精确切片迁移、函数体逐字节保留 4) 最大单文件降至 441 行，依赖方向单向无环 | 2026-08-15 |
| AD-14 | LLM 构造 | **`public_kb/llm_factory.py::create_llm()` 统一入口** | 三处各自构造 ChatOpenAI | graph.build_graph / rag_engine / price_inquiry 三处 kwargs 口径（model/api_key/temperature/timeout/max_retries/base_url）此前存在漂移风险；统一后由 Settings 单源驱动 | 2026-08-15 |

---

## 11. 关键接口依赖说明

### 11.1 内部接口契约

#### agent ↔ public_kb

| 接口 | 调用方 | 被调用方 | 说明 |
|------|--------|---------|------|
| `PublicKnowledgeRAG.query(question)` → `{"answer", "sources", "citations", "citation_validation"}` | `agent/nodes/knowledge_qa.py` | `public_kb/rag_engine.py` | RAG 问答接口，返回回答文本 + 引用来源列表 + 标准化 citations（chunk_id / chunk_uid / 数据源位置 / 原文）+ R1-R7 校验报告 |
| `PublicKnowledgeRAG.init_knowledge_base(pdf_dir)` | CLI 手动调用 | `public_kb/rag_engine.py` | 批量初始化知识库，仅管理员操作 |
| `PublicKnowledgeRAG.ensure_loaded()` | `agent/nodes/knowledge_qa.py` | `public_kb/rag_engine.py` | 显式加载向量库与问答链（消除跨包私有成员穿透） |
| `create_llm(settings)` | `agent/graph.py`、`agent/nodes/price_inquiry/` | `public_kb/llm_factory.py` | 统一 ChatOpenAI 构造入口（model / api_key / temperature / timeout / max_retries / base_url 口径一致） |
| `Settings()` | `agent/graph.py`, `agent/nodes/*` | `public_kb/config.py` | 全局配置单例，所有模块共享 |

#### agent 内部节点契约

所有业务节点遵循统一接口：

```python
def node_xxx(state: AgentState) -> dict:
    """
    Args:
        state: AgentState, 包含 messages, router_intent, business_result

    Returns:
        {"business_result": {"branch": "xxx", "answer": "...", "data": {...}},
         "messages": [AIMessage(content="...")]}
    """
```

| 节点 | branch 值 | data 字段 |
|------|----------|----------|
| `node_knowledge_qa` | `knowledge_qa` | `sources: list` |
| `node_price_inquiry` | `price_inquiry` | `sub_route, query_type, records, tables, intent, meta` |
| `node_general_chat` | `general_chat` | — |
| `node_doc_qa` | `doc_qa` | `status: "placeholder"`, `available_since: null` |
| `node_fallback` | `fallback` | `failed_branch: str`（来自异常降级时） |

#### AgentState 定义

文件：[agent/state.py](file:///d:/DEMO/zhaotoubiao_demo/agent/state.py)

```python
class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]  # 对话历史（ID 去重）
    router_intent: str                                     # 路由意图
    business_result: dict                                  # 业务负载（泛型 dict）
```

### 11.2 外部 API 依赖

#### LLM API（OpenAI 兼容，.env 声明）

| 项目 | 值 |
|------|-----|
| 端点 | `.env LLM_BASE_URL`（当前示例 `https://openrouter.ai/api/v1`） |
| 模型 | `.env LLM_MODEL`（当前示例 `z-ai/glm-5.3-flash`） |
| 认证 | `Authorization: Bearer {LLM_API_KEY}` |
| 超时 | 60s (可配置 `LLM_TIMEOUT`) |
| 重试 | 1 次 (可配置 `LLM_MAX_RETRIES`) |
| 降级方案 | 无（单点依赖，需关注 API 可用性） |

#### SiliconFlow API (Embedding + Reranker)

| 项目 | 值 |
|------|-----|
| 端点 | `https://api.siliconflow.cn/v1/embeddings`（Reranker 为同级 rerank 端点） |
| 模型 (Embedding) | `BAAI/bge-m3` |
| 模型 (Reranker) | `BAAI/bge-reranker-v2-m3`（凭据/端点复用 EMBEDDING_*，有意设计） |
| 认证 | `Authorization: Bearer {EMBEDDING_API_KEY}` |
| 超时 | 30s Embedding / 5s Rerank (可配置) |
| 重试 | 1 次 (可配置) |
| 降级方案 | 无（单点依赖；D3 零降级——精排失败直接抛错） |

#### MinerU（本地 magic-pdf CLI）

| 项目 | 值 |
|------|-----|
| 调用方式 | subprocess 调用本地 `magic-pdf` 命令行（GPU 版，无网络依赖） |
| 超时 | 3600s (可配置) |
| 用途 | 仅在知识库初始化（`--init --pdf-dir`）时调用，日常运行不依赖 |

#### Milvus gRPC

| 项目 | 生产环境值 |
|------|-----------|
| 地址 | `.env MILVUS_HOST/PORT`（Docker `localhost:19530`） |
| 服务端版本 | **2.6.x**（BM25 Function 需 2.5+；旧 2.4 会静默丢弃 Function） |
| 集合 | `public_kb`（唯一活跃集合；`mysql_price_semantic` 已退役删除） |

---

## 12. 权限配置清单

### 12.1 API 密钥权限

| 密钥 | 平台 | 获取方式 | 所需权限 | 续期周期 |
|------|------|---------|---------|---------|
| `LLM_API_KEY` | LLM 服务方（见 `LLM_BASE_URL`，当前示例 OpenRouter） | 对应平台 → API Keys | Chat Completions | 按量付费 |
| `EMBEDDING_API_KEY` | 硅基流动 | [siliconflow.cn](https://siliconflow.cn) → API 密钥 | Embedding + Reranker 模型调用（Reranker 复用此凭据） | 按量付费 |

### 12.2 数据库访问权限

| 资源 | 地址 | 用户 | 所需权限 | 说明 |
|------|------|------|---------|------|
| MySQL | `.env MYSQL_HOST:PORT`（本地 Docker 或内网生产库） | 见 `MYSQL_USER` | SELECT（只读） | 对 `ztb_clean` 数据库的所有表 |
| Milvus | `.env MILVUS_HOST:PORT` | 无认证 | 读写 | Docker 本地部署，网络层隔离 |

### 12.3 开发环境权限

| 资源 | 所需权限 | 说明 |
|------|---------|------|
| Docker Desktop | 管理员权限（Windows） | 运行 Milvus 容器 |
| Python 环境 | conda/venv 创建权限 | 隔离项目依赖 |
| Git 仓库 | Read + Write（开发分支） | 代码提交 |

### 12.4 端口与网络访问

| 源 | 目标 | 端口 | 协议 | 用途 |
|----|------|------|------|------|
| 开发机 | LLM 服务方（`LLM_BASE_URL`） | 443 | HTTPS | LLM 推理 |
| 开发机 | `api.siliconflow.cn` | 443 | HTTPS | Embedding + Reranker |
| 开发机 | MySQL（`MYSQL_HOST`） | 3306 | TCP | 结构化查询 |
| 开发机 | `localhost` | 19530 | gRPC | Milvus 向量检索 |
| 开发机 | `localhost` | 3000 | HTTP | Attu 管理面板 |

---

## 13. 附录：关键代码路径速查

| 想了解... | 看这里 |
|----------|--------|
| Agent 怎么启动的 | [agent/__main__.py](file:///d:/DEMO/zhaotoubiao_demo/agent/__main__.py) |
| 意图怎么分类的 | [agent/router.py](file:///d:/DEMO/zhaotoubiao_demo/agent/router.py) L162-L212 |
| State 有哪些字段 | [agent/state.py](file:///d:/DEMO/zhaotoubiao_demo/agent/state.py) L17-L35 |
| 图怎么构建的 | [agent/graph.py](file:///d:/DEMO/zhaotoubiao_demo/agent/graph.py) L108-L181 |
| 询价二级路由逻辑 | [agent/nodes/price_inquiry/node.py](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/price_inquiry/node.py)（`_SUB_ROUTE_MAP`） |
| 多阶段召回权重 | [agent/nodes/price_inquiry/recall.py](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/price_inquiry/recall.py) L30-L37（`_RECALL_STAGE_WEIGHTS`） |
| 回答模板怎么定义的 | [agent/nodes/answer_templates.py](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/answer_templates.py) |
| 输出字段怎么筛选的 | [agent/nodes/output_templates.py](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/output_templates.py) |
| RAG 怎么检索的 | [public_kb/qa_chain.py](file:///d:/DEMO/zhaotoubiao_demo/public_kb/qa_chain.py) |
| 引用溯源怎么做的 | [public_kb/citations.py](file:///d:/DEMO/zhaotoubiao_demo/public_kb/citations.py)、[public_kb/chunk_ids.py](file:///d:/DEMO/zhaotoubiao_demo/public_kb/chunk_ids.py)、[../design_docs/citation_schema.md](../design_docs/citation_schema.md) |
| LLM 怎么统一构造的 | [public_kb/llm_factory.py](file:///d:/DEMO/zhaotoubiao_demo/public_kb/llm_factory.py) |
| 混合检索参数 | [public_kb/config.py](file:///d:/DEMO/zhaotoubiao_demo/public_kb/config.py) |
| PDF 怎么解析的 | [public_kb/mineru_parser.py](file:///d:/DEMO/zhaotoubiao_demo/public_kb/mineru_parser.py) |
| 文本怎么切片存储的 | [public_kb/chunker.py](file:///d:/DEMO/zhaotoubiao_demo/public_kb/chunker.py) |
| 配置与超时参数 | [public_kb/config.py](file:///d:/DEMO/zhaotoubiao_demo/public_kb/config.py)（Settings dataclass + CitationRuleConfig） |
| Milvus 怎么连接的 | [public_kb/milvus_store.py](file:///d:/DEMO/zhaotoubiao_demo/public_kb/milvus_store.py) |
| 有哪些推荐索引 | [test/recommended_indexes.sql](file:///d:/DEMO/zhaotoubiao_demo/test/recommended_indexes.sql) |
| 数据库概览 | `python test/db_explorer.py --overview` |
| Docker Compose 配置 | [milvus/docker-compose.yml](file:///d:/DEMO/zhaotoubiao_demo/milvus/docker-compose.yml) |
| 工具库（tool registry）怎么用的 | [agent/tools/README.md](file:///d:/DEMO/zhaotoubiao_demo/agent/tools/README.md)、`python -m agent --list-tools`、`--agent-mode`（agent_loop.py） |
| 流式/异步基础设施在哪 | [agent/streaming/](file:///d:/DEMO/zhaotoubiao_demo/agent/streaming/)（envelope/协议）、[agent/runtime/](file:///d:/DEMO/zhaotoubiao_demo/agent/runtime/)（线程池/信号量）、[service/api.py](file:///d:/DEMO/zhaotoubiao_demo/service/api.py)（SSE 端点） |
| P0 防范体系怎么防的 | [agent/nodes/price_inquiry/intent.py](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/price_inquiry/intent.py)（`_extract_project_number_candidate` / `_has_valid_query_entity`）、[agent/nodes/price_inquiry/node.py](file:///d:/DEMO/zhaotoubiao_demo/agent/nodes/price_inquiry/node.py)（`_build_unified_guidance` 查询守卫） |
| 三大核心评测怎么跑 | [scripts/run_three_core_evaluation.py](file:///d:/DEMO/zhaotoubiao_demo/scripts/run_three_core_evaluation.py) + [scripts/generate_three_core_report.py](file:///d:/DEMO/zhaotoubiao_demo/scripts/generate_three_core_report.py)（共享骨架 `eval_common.py` / `eval_report_common.py`，报告渲染 `report_html.py` / `report_markdown.py`） |
| 引用溯源评测怎么跑 | [scripts/run_knowledge_citation_eval.py](file:///d:/DEMO/zhaotoubiao_demo/scripts/run_knowledge_citation_eval.py) |
| 代码结构审计与重构记录 | [../analysis_docs/code_structure_audit_2026-08-15.md](../analysis_docs/code_structure_audit_2026-08-15.md) |
| 产品线下线标记 | [scripts/product_line_deprecation.sql](file:///d:/DEMO/zhaotoubiao_demo/scripts/product_line_deprecation.sql) |
| 新测试入口 | [test/test_p0_11_guard.py](file:///d:/DEMO/zhaotoubiao_demo/test/test_p0_11_guard.py)、[test/test_p0_12_project_number_detection.py](file:///d:/DEMO/zhaotoubiao_demo/test/test_p0_12_project_number_detection.py)、[test/test_citation_tracing.py](file:///d:/DEMO/zhaotoubiao_demo/test/test_citation_tracing.py)、[test/test_model_identity_config.py](file:///d:/DEMO/zhaotoubiao_demo/test/test_model_identity_config.py) |
