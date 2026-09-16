# 招投标智能助手 — 死代码 / 死模块只读排查报告

> 生成时间：2026-09-04
> 排查方式：全量 AST 静态分析 + 模块可达性图（BFS）+ 人工逐项复核
> 排查范围：`D:\DEMO\zhaotoubiao_demo` 下 152 个 Python 文件 / 30,807 行，以及 252 个非 Python 文件（.md/.json/.yml/.sql/.ps1/.env 等）
> 排除目录：`.conda`（虚拟环境）、`docker/`、`milvus/`、`__pycache__`、`.git`
> **本次排查未修改任何既有文件**（分析脚本与中间产物均写入系统临时目录）

---

## 0. 排查方法与判定口径

| 步骤 | 做法 |
|---|---|
| ① 模块可达性 | 以 `python -m agent` / `python -m public_kb` / `python -m cloud_sync` / `service.api` / `scripts/*.py` / pytest 收集 / 文档与脚本中的命令行引用为**强入口**，沿 import 图做 BFS。相对导入按 PEP 328 正确还原（`from .x` 在模块内解析为同级包，已修正解析器首版缺陷） |
| ② 引用判定 | 基于 AST 的 `Name(Load)` / `Attribute.attr` / `keyword.arg` / 装饰器 / 基类 / `__all__` 成员；另建字符串字面量索引以覆盖 `getattr`、`importlib.import_module`、配置与反射调用 |
| ③ 排除误报 | dunder 方法、pytest 自动收集项（`test_*` / `Test*` / `unittest.TestCase` 子类）、框架回调（LangChain、FPDF、FastAPI、argparse）、`__init__.py` 兼容层再导出 |
| ④ 人工复核 | 对 26 个高价值候选逐项读取源码确认 |

**置信度定义**
- **高**：无任何 AST 引用、无字符串引用、非框架回调、非测试收集项，可安全处理
- **中**：无引用但属于对外公共 API 或命令封装，删除可能影响外部使用者 / 完整性
- **低（不确定）**：存在反射、框架分发、接口实现或文档侧面证据，无法静态确认

---

## 1. 死模块（无任何入口可达）

**小计：24 个文件 / 3,430 行，占项目总代码量 11.1%**

### 1.1 真·孤儿文件（无人 import、无文档命令行引用、无配置引用）— 4 个 / 746 行

| # | 文件路径 | 行数 | 判断依据 | 置信度 |
|---|---|---|---|---|
| 1 | `_s3_tmp_node_async.py` | 374 | 无任何模块 import；仅被 `work_docs/嵌入模型与混合检索整改工作总结报告_20260901.md:158` 点名，且该文档原文标注「为本分支上更早的未跟踪工作文件，与本次整改无关，**建议另行清理**」 | 高 |
| 2 | `_s3_patch1.py` | 218 | 同上，无任何 import，仅同一句文档提及 | 高 |
| 3 | `_s3_patch1b.py` | 108 | 同上，无任何 import，仅同一句文档提及 | 高 |
| 4 | `_probe_stream_tmp.py` | 46 | 全项目（152 个 .py + 252 个非 .py 文件）零引用，连文档都未提及；文件名带 `_tmp_` 前缀，属调试残留 | 高 |

> 这四个文件位于项目**根目录**（不在任何 `archive/` 目录内），是最应该优先清理的部分。文档本身已建议清理 `_s3_*`。

### 1.2 已归档的一次性脚本（有意冻结保留）— 20 个 / 2,684 行

`AGENTS.md` 已明确记载：`archive/` — migrate_milvus_cloud.py、rebuild_and_verify.py；`scripts/archive/` — run_evaluation.py、generate_report.py、csv_to_mysql.py；`test/legacy/` — `_step*` 迁移步骤、scan_tables 等数据诊断工具，均为「历史数据准备/迁移工具，冻结保留」。

| 目录 | 文件数 | 行数 | 判断依据 | 置信度 |
|---|---|---|---|---|
| `archive/` | 2 | 151 | `migrate_milvus_cloud.py`(64) 仅被 `analysis_docs/project_overview.md` 以命令行形式提及；`rebuild_and_verify.py`(87) 零引用 | 高（死）/ 中（保留价值） |
| `scripts/archive/` | 3 | 996 | `generate_report.py`(523)、`csv_to_mysql.py`(324)、`run_evaluation.py`(149) 零 import、零命令行引用 | 高（死）/ 中（保留价值） |
| `test/legacy/` | 15 | 1,537 | 16 个文件全部零 import；目录名 `legacy` 且 `AGENTS.md` 已声明冻结保留 | 高（死）/ 中（保留价值） |

`test/legacy/` 明细：`_final_verify.py`(57)、`_step1_csv_check.py`(51)、`_step2_exec_ddl.py`(59)、`_step2_test_mysql.py`(32)、`_step4_verify_data.py`(34)、`_step56_verify_indexes.py`(62)、`_step5_fulltext.py`(46)、`diagnose_pdf.py`(111)、`export_samples.py`(189)、`inspect_price_dbs.py`(140)、`preview_candidates.py`(115)、`print_schema_summary.py`(13)、`quality_check.py`(244)、`scan_export_csv.py`(255)、`scan_tables.py`(129)

> **说明**：这一类是**有意归档**而非遗漏，置信度「高」指"确实是死代码"，「中」指"是否删除需权衡历史价值"。不建议直接删除，建议移出主工作区或明确标注。

### 1.3 排除的疑似死模块（实为可达）

| 文件 | 误判原因 |
|---|---|
| `agent/checkpointer.py` | 被 `agent/graph.py:32` 导入并调用（首版解析器相对导入解析错误导致误报，已修正） |
| `agent/streaming/context.py` | 被 `graph.py`、`router.py`、`nodes/*` 共 7 处导入 |
| `public_kb/mineru_parser.py` | 被 `public_kb/rag_engine.py:32` 导入 |
| `cloud_sync/cli.py` | 被 `cloud_sync/__main__.py:5` 导入 |
| `service/schemas.py` | 被 `service/api.py` 导入 |

---

## 2. 未使用导出（导出后无任何消费方）

**小计：4 项确认 + 1 项边缘**

| # | 文件路径 | 位置 | 导出名 | 判断依据 | 置信度 |
|---|---|---|---|---|---|
| 1 | `agent/runtime/__init__.py` | L34-40 `__all__` | `shutdown_executors` | 仅出现在 `__all__` 字符串与 L5 的 `from .async_bridge import ...` 中，全项目 AST 零调用 | 高 |
| 2 | `agent/runtime/__init__.py` | L34-40 `__all__` | `async_bridge` | 以子模块名导出，无任何 `from agent.runtime import async_bridge` 消费方 | 中 |
| 3 | `agent/runtime/__init__.py` | L34-40 `__all__` | `deadlines` | 同上，无消费方 | 中 |
| 4 | `agent/streaming/__init__.py` | L12-19 `__all__` | `adapt_langgraph_event` | 仅出现在 `__all__` 与 L4 import 中，全项目零调用 | 高 |
| 5 | `agent/runtime/__init__.py` | L34-40 `__all__` | `cancellation` | 无 AST 引用，但 `test/test_runtime_smoke.py` 中存在同名字符串（可能反射/装配使用） | 低（不确定） |

**重点说明 — `shutdown_executors` 存在实际资源风险**

`agent/runtime/async_bridge.py`：

```python
import atexit            # L76 —— 导入后从未使用

def shutdown_executors() -> None:     # L78
    global _IO_EXECUTOR, _CPU_EXECUTOR
    for ex in (_IO_EXECUTOR, _CPU_EXECUTOR):
        if ex is not None:
            ex.shutdown(wait=True)
    _IO_EXECUTOR = _CPU_EXECUTOR = None
```

`atexit` 被导入但从未调用 `atexit.register(shutdown_executors)`，且 `shutdown_executors` 全项目零调用 → **两个全局线程池永远不会主动关闭**，同时 `import atexit` 本身是一条死导入。这不只是死代码，而是"未接线的清理逻辑"，建议二选一：接上 `atexit.register`，或删除函数与 import。

---

## 3. 未调用的函数 / 方法

**小计：7 项确认死代码 + 4 项已排除误报**

### 3.1 确认无调用方（7 项）

| # | 文件路径 | 位置 | 名称 | 判断依据 | 置信度 |
|---|---|---|---|---|---|
| 1 | `agent/runtime/concurrency.py` | L80 | `list_registered()` | 全项目 AST 零引用（定义文件内外皆无）；docstring 自述「调试用」，但无调试入口调用 | 高 |
| 2 | `agent/runtime/async_bridge.py` | L78 | `shutdown_executors()` | 零调用，见 §2 资源风险说明 | 高 |
| 3 | `agent/streaming/protocol.py` | L156 | `adapt_langgraph_event()` | 零调用；docstring 称「阶段 1 不接入业务，留给阶段 5 改造时使用」，但 git 日志显示阶段 1~5 改造已完成（`78f2162 异步+记忆+流式改造整体快照（阶段1~5）`），预留期已过 | 高 |
| 4 | `test/test_rag_async.py` | L159 | `_make_docs_from_hits()` | 定义后全文件零调用，疑为异步改造重构后的遗留工具函数 | 高 |
| 5 | `test/test_rag_async.py` | L490 | `_fake_compile()` | 定义在 `test_graph_registers_async_knowledge_qa_node()` 内部，但测试实际改用 `patch.object(StateGraph, "compile")` 实现，该函数从未使用 | 高 |
| 6 | `cloud_sync/connection.py` | L383 | `ResilientRedisClient.dbsize()` | 零调用；同组 `ping`/`scan`/`type`/`dump`/`pttl` 均被使用，仅 `dbsize` 无消费方，属 Redis 命令封装的完整性保留 | 中 |
| 7 | `public_kb/rag_engine.py` | L375 | `PublicKnowledgeRAG.add_pdf()` | 零调用；为对外公共 API 且 `AGENTS.md` 有记载（`add_pdf(path)`），无仓库内消费方但可能有外部脚本使用者 | 中 |

### 3.2 已排除的误报（4 项，不建议处理）

| 文件路径 | 位置 | 名称 | 排除理由 |
|---|---|---|---|
| `public_kb/embedding_service.py` | L54 | `_SafeEmbeddings.aembed_documents()` | LangChain `Embeddings` 抽象基类的异步入口覆写，由框架在异步链路中分发调用；L69 有 `super().aembed_documents(...)` 转发。**框架回调，非死代码** |
| `scripts/build_report_pdf.py` | L37 | `Report.footer()` | FPDF `FPDF.footer()` 回调，由渲染引擎每页自动调用。**框架回调，非死代码** |
| `test/test_rag_async.py` | L100 | `_FakeVectorStore.similarity_search_with_score()` | mock 实现的 LangChain VectorStore 接口方法；虽当前测试全部以 `_FakeVectorStore([])` 空数据构造、路径未触发，但属接口实现。**不确定，建议保留** |
| `test/test_cloud_sync.py` | L247 | `MilvusIntegrationSmokeTest` | `unittest.TestCase` 子类，pytest 对 `unittest.TestCase` 派生类**无条件收集**（不受 `python_classes` 前缀规则限制），且文件 L313 有 `unittest.main()`。**会被执行，非死代码** |

---

## 4. 无用变量与常量

**小计：44 项（未使用导入 17 / 未使用模块级常量 1 / 未使用局部变量 26）**

### 4.1 未使用导入（17 项，置信度均为高）

已排除 `__init__.py` 的兼容层再导出（`agent/nodes/price_inquiry/__init__.py` 等刻意再导出 173 个符号，属设计意图，不计入）。

| # | 文件路径 | 行 | 未使用名 | 原始语句 |
|---|---|---|---|---|
| 1 | `agent/runtime/async_bridge.py` | 76 | `atexit` | `import atexit`（见 §2，属未接线逻辑） |
| 2 | `agent/nodes/price_inquiry/recall_async.py` | 27 | `_execute_recall_chain_for_table` | `from .recall import (...)` |
| 3 | `agent/nodes/price_inquiry/recall_async.py` | 27 | `_query_semantic_rows` | `from .recall import (...)` |
| 4 | `service/api.py` | 15 | `make_event` | `from agent.streaming import (...)` |
| 5 | `scripts/benchmark_async.py` | 56 | `Iterable` | `from typing import Callable, Iterable` |
| 6 | `_probe_stream_tmp.py` | 9 | `EventType` | `from agent.streaming import EventType`（文件本身即孤儿） |
| 7 | `test/test_agent_tool_loop.py` | 15 | `ToolMeta` | `from agent.tools.registry import ToolMeta, ToolRegistry` |
| 8 | `test/test_agent_tool_loop.py` | 15 | `ToolRegistry` | 同上 |
| 9 | `test/test_graph_ainvoke.py` | 11 | `build_graph` | `from agent.graph import AgentGraph, build_graph` |
| 10 | `test/test_graph_astream.py` | 5 | `pytest` | `import pytest` |
| 11 | `test/test_graph_astream.py` | 6 | `AIMessage` | `from langchain_core.messages import AIMessage` |
| 12 | `test/test_router_async.py` | 8 | `pytest` | `import pytest` |
| 13 | `test/test_router_async.py` | 9 | `AIMessage` | `from langchain_core.messages import AIMessage, HumanMessage` |
| 14 | `test/test_cli_stream.py` | 3 | `pytest` | `import pytest` |
| 15 | `test/test_knowledge_qa_stream.py` | 5 | `pytest` | `import pytest` |
| 16 | `test/test_price_inquiry_stream.py` | 5 | `pytest` | `import pytest` |
| 17 | `test/test_streaming_envelope.py` | 5 | `pytest` | `import pytest` |

> 第 7、8、9 项属于「符号在别处被使用，但在本测试文件导入后未使用」，删除不影响功能。

### 4.2 未使用模块级常量（1 项）

| # | 文件路径 | 行 | 名称 | 判断依据 | 置信度 |
|---|---|---|---|---|---|
| 1 | `public_kb/csv_loader.py` | 71 | `_CN_HEADING_FULLWIDTH_RE` | 定义后全项目零引用（同文件其他 `_CN_HEADING_*` 正则均在使用） | 高 |

### 4.3 未使用局部变量（26 项，置信度均为中）

集中在「元组解包后部分元素未使用」与「测试固件变量未消费」，属低风险但可清理。

| 文件路径 | 行 | 变量 | 说明 |
|---|---|---|---|
| `agent/nodes/price_inquiry/db_async.py` | 125 | `pool` | `pool = _get_pool()` 后未使用 |
| `public_kb/qa_chain.py` | 417 ×2 | `text` | `text, rrf_score, entity = candidates[idx]` |
| `public_kb/qa_chain_async.py` | 251 | `text` | 同上 |
| `service/api.py` | 58 | `chunk` | `async for chunk in heartbeat:` 未消费 |
| `test/db_explorer.py` | 257, 368 | `col_names` | 解包未用 |
| `test/db_explorer.py` | 470 | `keyword` | 解包未用 |
| `test/test_bug_repairs.py` | 97, 124 | `params` | `conditions, params = _build_constraint_conditions(...)` |
| `test/test_p0_12_project_number_detection.py` | 192, 210, 222 | `params` | 同上 |
| `test/test_sub_route.py` | 325 | `params` | 同上 |
| `test/test_cloud_sync.py` | 186 | `source` | 解包未用 |
| `test/test_db_async_pool.py` | 109, 110 | `mysql_acquire_timeout_s`, `mysql_max_pool_size` | 赋值未用 |
| `test/test_graph_ainvoke.py` | 53, 64 ×2, 72 | `mock_build`, `mock_compiled` | 解包未用 |
| `test/test_reranker_async.py` | 91 | `calls` | 赋值未用 |
| `test/test_sql_timeout.py` | 45, 67, 88 | `sql_stmt_timeout_s` | 赋值未用 |
| `test/test_tools_contract.py` | 283 | `rag` | 构造后未用 |

> 其中 `conditions, params = ...` 的 `params` 共 6 处，属同一模式重复出现，建议改用 `conditions, _ = ...`。

---

## 5. 不可达代码

**小计：1 项不可达语句 + 11 项空 except 分支**

### 5.1 真·不可达语句（1 项）

| # | 文件路径 | 位置 | 判断依据 | 置信度 |
|---|---|---|---|---|
| 1 | `agent/runtime/async_bridge.py` | L41-43 | `run_blocking()` 中 `return await loop.run_in_executor(...)`（L38-40）之后紧跟一段三引号字符串（L41-43），作为表达式语句**永不执行**。作者本意是写说明文字，但位置放错——若想作为函数文档应置于 `def` 之下、`return` 之前 | 高 |

```python
    return await loop.run_in_executor(executor or _IO_EXECUTOR,
                                      lambda: function(*args, **kwargs),
                                      )
    '''整段等价于：1、把function(args, **kwargs)交给线程池里的某个工作线程   # ← 不可达
    2、当前协程挂起，让出事件循环3、工作线程跑完，把结果塞回future4、事件循环调度回来，
    await拿到结果，继续往下走'''
```

### 5.2 空 except 分支（11 项，置信度中）

`except: pass` 会静默吞掉异常，虽可执行但无实际行为，属"活着的死代码"。

| 文件路径 | 行 |
|---|---|
| `agent/__main__.py` | 286 |
| `agent/nodes/price_inquiry/db.py` | 65, 100 |
| `agent/tools/base.py` | 97 |
| `cloud_sync/connection.py` | 242 |
| `scripts/export_policy_tables.py` | 548, 563 |
| `service/api.py` | 54 |
| `test/test_cloud_sync.py` | 291 |
| `test/test_milvus_ingest.py` | 245 |
| `test/test_model_identity_config.py` | 183 |

### 5.3 已排除的其他不可达模式（扫描后确认不存在）

补充扫描了以下模式，**均 0 命中**，说明分支逻辑整体健康：

- `sys.exit()` / `os._exit()` / `raise SystemExit` 之后的同级语句
- `if False` / `if 0` / `while False` 等常量假条件分支
- `if True` 之后不可达的 `else` 分支
- 恒真自比较（`if x == x`）
- `elif` 条件与前面 `if` 完全重复
- 空实现桩函数（仅 `pass` / `...` / `raise NotImplementedError` 的函数体）—— **0 个**，项目无占位桩

---

## 6. 汇总统计

| 分类 | 确认数量 | 不确定/边缘 | 涉及代码量 | 说明 |
|---|---:|---:|---:|---|
| **死模块** | 24 个文件 | — | 3,430 行（11.1%） | 孤儿 4 个 / 746 行，归档 20 个 / 2,684 行 |
| **未使用导出** | 4 项 | 1 项 | — | `__all__` 中无消费方的公共符号 |
| **未调用函数/方法** | 7 项 | — | — | 另有 4 项经复核为框架回调/接口实现，已排除 |
| **无用变量与常量** | 44 项 | — | — | 未使用导入 17 + 模块级常量 1 + 局部变量 26 |
| **不可达代码** | 1 项 | — | — | 另发现 11 处空 `except: pass` 分支 |
| **合计** | **80 项** | **1 项** | **3,430 行 + 52 处** | |

**误报排除统计**（分析过程中识别并剔除，避免误导）：

| 误报类型 | 数量 | 典型例子 |
|---|---:|---|
| 框架回调方法 | 2 | `aembed_documents`（LangChain）、`Report.footer`（FPDF） |
| 测试收集 / 接口实现 | 2 | `MilvusIntegrationSmokeTest`、`_FakeVectorStore.similarity_search_with_score` |
| 包兼容层再导出 | 173 | `agent/nodes/price_inquiry/__init__.py` 全体再导出 |
| 相对导入解析错误导致的误判 | 5 | `checkpointer.py`、`streaming/context.py`、`mineru_parser.py`、`cli.py`、`schemas.py` |
| 同文件内部调用 | 382 | 被误列为"仅模块内使用"，实为正常代码 |

---

## 7. 可进一步人工确认的建议

### 建议一（立即可处理，零风险）

1. **删除 3 个 `_s3_*` 根文件 + `_probe_stream_tmp.py`**（746 行）—— 零引用且文档自身已建议清理。
2. **补上 `atexit.register(shutdown_executors)` 或删除该函数与 `import atexit`** —— 当前是两个永不关闭的全局线程池，属真实资源隐患，不只是代码整洁问题。
3. **删除 17 条未使用导入** —— 建议先接入 `ruff`（`F401` 规则）自动检查，`__init__.py` 的兼容层再导出需加 `# noqa: F401` 白名单。
4. **修正 `async_bridge.py` L41-43 的不可达文档串** —— 移到 `def` 下方作为正式 docstring。

### 建议二（需产品/负责人确认）

5. **`adapt_langgraph_event`**：docstring 称"留给阶段 5 使用"，而阶段 5 已完成但未接入。需确认是**阶段 5 遗漏了接入**（则应补接线）还是**设计已废弃**（则应删除）。这是本次排查中唯一"可能是功能缺口"的项，建议优先确认。
6. **`PublicKnowledgeRAG.add_pdf`**：仓库内无消费方，但 `AGENTS.md` 将其列为公共 API。需确认是否有仓库外脚本在调用增量导入能力，否则应标注 `@deprecated` 或删除。
7. **`ResilientRedisClient.dbsize`**：确认是"为命令完整性预留"还是遗漏使用。若为前者建议加注释说明。
8. **`list_registered`**：调试用途但无调试入口。可保留并接入某个诊断命令，或删除。

### 建议三（流程层面）

9. **归档目录语义化**：`archive/`、`scripts/archive/`、`test/legacy/` 共 2,684 行已声明冻结保留。建议移出主工作区（如独立 `attic/` 仓库或 Git tag），否则每次静态扫描都会重复产出噪声。
10. **接入静态检查到 CI**：建议启用 `ruff` 的 `F401`（未使用导入）、`F841`（未使用局部变量）、`ARG`（未使用参数）与 `vulture`（死代码）并配置白名单，可防止本次发现的问题再次累积。
11. **为 `test/legacy/` 与 `archive/` 配置扫描排除**：在 `ruff`/`vulture` 配置中显式 exclude，避免掩盖真实问题。

### 无法静态确认、需运行时验证的项

12. **`cloud_sync` 的 `cmd_schema` / `verify` 等 CLI 子命令**：`argparse` 通过 `set_defaults(func=cmd_xxx)` 分发，属字符串/属性间接绑定。本次已通过 AST 追踪到 `build_parser()` 中的注册，判定为**活跃**，但若存在外部脚本按子命令名调用，建议用 `coverage` 跑一次真实调用链确认。
13. **`service/api.py` 的流式端点**（`chat_stream` 及内部 `event_bytes` / `heartbeat_bytes` 嵌套协程）：通过 FastAPI 装饰器与 SSE 响应体间接驱动，无仓库内调用方。属对外 HTTP 接口，**非死代码**，但建议补集成测试以固化行为。
14. **`public_kb/rag_engine.py` 的 `clear_kb`**：唯一内部消费者是已归档的 `archive/rebuild_and_verify.py`，活跃消费者只有 `public_kb/__main__.py:130` 的 CLI。若 CLI 入口后续下线，该方法将转为死代码，建议纳入观察。
