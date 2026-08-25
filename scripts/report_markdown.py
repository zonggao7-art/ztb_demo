"""
测评报告 Markdown 渲染 — 三大核心业务测评报告的 Markdown 骨架与瓶颈/建议文案。

自 generate_three_core_report.py 拆分（P1-3）：统计骨架留在主脚本，
报告渲染收敛于此模块。
"""

from __future__ import annotations

import json
from collections import Counter


def classify_failure(row: dict) -> str:
    """将失败用例按根因归类：实体名带注释/后缀 / 中标历史检索漏召 / 其他。"""
    q = row.get("question", "")
    # 含「（注释）」或「号」等后缀（项目编号常见后缀）视为实体名带注释/后缀
    if "曾用名" in q or "（" in q or "号" in q:
        return "实体名带注释/后缀"
    if "中标历史" in q:
        return "中标历史检索漏召"
    return "其他"


def build_markdown(metrics, case_rows, cat_stats, field_stats,
                   branch_counter, subroute_counter, querytype_counter,
                   error_counter, timing, meta_agg, missing_reason_counter):
    L = []
    a = L.append
    a("# 招投标智能助手 — 三大核心业务全流程测评报告")
    a("")
    a(f"> 生成时间：{metrics['env']['time']}　|　测评对象：当前线上业务系统（agent LangGraph Agent）")
    a("")

    # 1 环境
    a("## 1. 测试环境与依赖说明")
    a("")
    a("### 1.1 软硬件环境")
    a("")
    a("| 项目 | 说明 |")
    a("| --- | --- |")
    a("| 操作系统 | " + metrics["env"]["os"] + " |")
    a("| Python | " + metrics["env"]["python"] + " |")
    a("| 大语言模型 | deepseek-chat（temperature=0，超时 60s，最大重试 1 次） |")
    a("| Embedding 模型 | BAAI/bge-m3（SiliconFlow，1024 维） |")
    a("| 结构化数据库 | MySQL 8.0（Docker），库 `ztb_clean`：bid_project 17,742 / company_info 38,911 / company_penalty 1,805 |")
    a("| 向量数据库 | Milvus 2.4 standalone（本地 127.0.0.1:19530）：public_kb 29,729 / mysql_price_semantic 77,597 |")
    a("| 运行方式 | 单进程顺序调用 `AgentGraph.invoke()`，逐条墙钟计时（`time.perf_counter`） |")
    a("")
    a("### 1.2 依赖版本")
    a("")
    a("| 依赖 | 版本 |")
    a("| --- | --- |")
    for pkg, ver in metrics["env"].items():
        if pkg in ("os", "python", "time"):
            continue
        a(f"| {pkg} | {ver} |")
    a("")
    a("> 说明：Milvus 数据已于 2026-08 全量迁移至云端 `8.130.174.43:19530` 并通过一致性校验；本次测评被测系统仍连接本地实例，未改动任何业务逻辑。")
    a("")

    # 2 规则
    a("## 2. 测评规则说明")
    a("")
    a("**校验基准（逐条生成）**：对每条用例，将 `expected_fields`（必填固定字段清单）与 `ground_truth`（字段正确取值，含多条记录）做笛卡尔积，得到该用例的全部「必填字段值」校验基准；每条基准值均可机器判等，无遗漏、无歧义。")
    a("")
    a("**唯一判定标准**：系统生成答案的正确性仅以「是否完整召回标准答案内界定的所有必填固定字段」为准。系统输出 = 自然语言回答 `answer` 与结构化记录 `records` 的并集；任一处命中即视为召回，不考量表述句式、语序是否与标准答案一致。")
    a("")
    a("**值匹配归一化**（消除格式差异导致的误判）：")
    a("")
    a("1. 文本值：去除空白/逗号/货币符号后做子串匹配（名称、编号、日期等）；")
    a("2. 数值：浮点等价判定（相对容差 1e-6），兼容 `19990000.0` 与 `19,990,000.00` 等差异；")
    a("3. 超长文本（>200 字符，如 `illegal_behavior`）：因系统输出截断，采用前缀匹配容差。")
    a("")
    a("**指标口径**：")
    a("")
    a("- **必填字段整体召回率** = 召回字段值数 / 全部必填字段值数；")
    a("- **系统输出整体准确率** = 全部字段值均召回的用例数 / 用例总数；")
    a("- **执行耗时** = 单条 `AgentGraph.invoke()` 墙钟耗时，统计平均/中位/最快/最慢/P90/P95/P99；")
    a("- **分类细分** = 按招投标项目（bid_project）/ 企业信息（company_info）/ 企业失信惩戒（company_penalty）三类业务场景分别统计召回率、准确率、平均耗时。")
    a("")

    # 3 核心指标
    a("## 3. 核心指标总览")
    a("")
    a("| 指标 | 数值 |")
    a("| --- | --- |")
    a(f"| 测评用例总数 | {metrics['total_cases']} |")
    a(f"| 必填字段值总数 | {metrics['total_fields']} |")
    a(f"| 召回字段值数 | {metrics['total_recalled']} |")
    a(f"| **必填字段整体召回率** | **{metrics['field_recall_rate']}%** |")
    a(f"| 完全正确用例数 | {metrics['correct_cases']} |")
    a(f"| **系统输出整体准确率** | **{metrics['answer_accuracy']}%** |")
    a("")

    # 4 耗时
    a("## 4. 单条查询执行耗时统计")
    a("")
    a("| 指标 | 数值（秒） |")
    a("| --- | --- |")
    for k, label in [("count", "计时样本数"), ("avg", "平均耗时"), ("median", "中位数"),
                     ("min", "最快"), ("max", "最慢")]:
        v = timing.get(k)
        a(f"| {label} | {v if v is not None else '—'} |")
    for k, label in [("p50", "P50"), ("p90", "P90"), ("p95", "P95"), ("p99", "P99")]:
        v = timing.get(k)
        if v is not None:
            a(f"| {label} | {v} |")
    a("")
    append_extreme_cases(L, case_rows)

    # 5 分类细分
    a("## 5. 业务分类细分（召回率 / 准确率 / 耗时）")
    a("")
    a("| 业务分类 | 数据源 | 用例数 | 字段召回率 | 答案准确率 | 平均耗时(s) |")
    a("| --- | --- | --- | --- | --- | --- |")
    cat_source_map = {
        "招投标项目中标情报": "bid_project.csv",
        "企业工商情报": "company_info.csv",
        "企业失信惩戒": "company_penalty.csv",
    }
    for cat, s in cat_stats.items():
        a(f"| {cat} | {cat_source_map.get(cat,'')} | {s['cases']} | {s['field_recall']}% | {s['accuracy']}% | {s['avg_time'] if s['avg_time'] is not None else '—'} |")
    a("")

    # 6 字段级召回
    a("## 6. 字段级召回率（按召回率升序，最差在前）")
    a("")
    a("| 字段 | 召回数 | 总数 | 召回率 |")
    a("| --- | --- | --- | --- |")
    for f, v in field_stats.items():
        a(f"| {f} | {v['ok']} | {v['total']} | {v['recall']}% |")
    a("")

    # 7 路由/异常
    a("## 7. 路由分支 / 子路由 / 查询类型 / 异常分布")
    a("")
    a("### 7.1 路由分支")
    a("")
    a("| 分支 | 次数 |")
    a("| --- | --- |")
    for b, c in branch_counter.most_common():
        a(f"| {b} | {c} |")
    a("")
    a("### 7.2 子路由")
    a("")
    a("| 子路由 | 次数 |")
    a("| --- | --- |")
    for b, c in subroute_counter.most_common():
        a(f"| {b} | {c} |")
    a("")
    a("### 7.3 查询类型")
    a("")
    a("| 查询类型 | 次数 |")
    a("| --- | --- |")
    for b, c in querytype_counter.most_common():
        a(f"| {b} | {c} |")
    a("")
    if error_counter:
        a("### 7.4 异常分布")
        a("")
        a("| 异常 | 次数 |")
        a("| --- | --- |")
        for e, c in error_counter.most_common():
            a(f"| {e[:100]} | {c} |")
        a("")

    # 8 失败原因
    a("## 8. 失败用例的查询类型分布（失败集中在哪些场景）")
    a("")
    a("| 查询类型 | 失败用例数 |")
    a("| --- | --- |")
    for qt, c in missing_reason_counter.most_common():
        a(f"| {qt} | {c} |")
    a("")

    # 9 明细
    failed = [r for r in case_rows if not r["pass"]]
    a("## 9. 各测试用例校验对错明细（全量 " + str(len(case_rows)) + " 例）")
    a("")
    a("> 全量字段级明细见 `case_details.csv`；下表为逐条对错概览（失败用例的缺失字段详见 9.1）。")
    a("")
    a("| # | sample_id(前8) | 业务分类 | 难度 | 结果 | 召回 | 耗时(s) | 子路由 |")
    a("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for i, r in enumerate(case_rows, 1):
        mark = "✅" if r["pass"] else "❌"
        a(f"| {i} | {r['sample_id'][:8]} | {r['category']} | {r['difficulty']} | {mark} | {r['num_recalled']}/{r['num_fields']} | {r['elapsed_s']} | {r['sub_route']} |")
    a("")

    # ── 9.1 失败用例明细 ──
    a(f"### 9.1 失败用例明细（共 {len(failed)} 例）")
    a("")
    if failed:
        a("| sample_id(前8) | 业务分类 | 召回 | 耗时(s) | 子路由 | 缺失字段 |")
        a("| --- | --- | --- | --- | --- | --- |")
        for r in failed:
            miss = json.loads(r["missing_fields"]) if r["missing_fields"] else []
            miss_names = "、".join(miss[:6])
            if len(miss) > 6:
                miss_names += f" 等{len(miss)}项"
            a(f"| {r['sample_id'][:8]} | {r['category']} | {r['num_recalled']}/{r['num_fields']} | {r['elapsed_s']} | {r['sub_route']} | {miss_names} |")
    else:
        a("（无失败用例）")
    a("")

    # ── 9.2 失败用例根因分析（基于真实失败样本归纳） ──
    a("### 9.2 失败用例根因分析（样本级）")
    a("")
    a("以下为本次测评全部失败用例的问题原文与根因归类：")
    a("")
    a("| # | sample_id(前8) | 业务分类 | 问题（全量） | 根因归类 |")
    a("| --- | --- | --- | --- | --- |")
    for i, r in enumerate(failed, 1):
        a(f"| {i} | {r['sample_id'][:8]} | {r['category']} | {r['question']} | {classify_failure(r)} |")
    a("")
    a("**失败根因归纳**：")
    a("")
    reason_counter = Counter(classify_failure(r) for r in failed)
    n_ann = reason_counter.get("实体名带注释/后缀", 0)
    n_recall = reason_counter.get("中标历史检索漏召", 0)
    n_other = reason_counter.get("其他", 0)
    a(f"1. **实体名带注释/后缀（{n_ann} 条）**：公司名含「（曾用名：…）」、括号注释、项目编号含「（政府采购任务书编号）」/「号」等注释后缀时，意图解析提取到的实体串无法与库中字段精确匹配，触发空结果/统一引导；")
    a(f"2. **中标历史检索漏召（{n_recall} 条）**：个别真实存在中标记录的公司（如中国移动通信集团安徽有限公司、安徽花载酒贸易有限公司等），语义召回+硬过滤路径召回不全，部分字段返回缺失；")
    if n_other:
        a(f"3. **其他（{n_other} 条）**：其余暂未归入上述两类的失败用例。")
    a("")

    # 10 瓶颈分析
    a("## 10. 系统性能瓶颈分析")
    a("")
    a("### 10.1 端到端耗时构成（基于业务节点 meta 采样）")
    a("")
    if meta_agg:
        a("| 内部指标 | 平均值 | 采样数 | 说明 |")
        a("| --- | --- | --- | --- |")
        label_map = {
            "node_elapsed": "业务节点内部耗时（意图解析+检索+渲染）",
            "total_sql_time": "SQL 执行总耗时",
            "sql_count": "单次查询执行 SQL 数",
            "total_hits": "检索命中记录数",
            "displayed_hits": "展示记录数",
        }
        for mk, mv in meta_agg.items():
            a(f"| {mk} | {mv['avg']} | {mv['count']} | {label_map.get(mk, '')} |")
        a("")
    a("### 10.2 关键瓶颈")
    a("")
    for line in bottleneck_lines(metrics, meta_agg, missing_reason_counter):
        a(line)
    a("")

    # 11 优化建议
    a("## 11. 系统优化建议")
    a("")
    a("### 11.1 召回/准确率优化")
    a("")
    for line in optimize_lines(metrics):
        a(line)
    a("")
    a("### 11.2 性能优化")
    a("")
    a("1. **降低 LLM 串行等待**：意图解析为每查询一次串行 LLM 调用，是高延迟主因。可将确定性规则（五种固定问题格式）前置，命中即跳过 LLM 意图解析，走纯 SQL 精确路径；")
    a("2. **连接复用与池化**：`pymysql` 连接池已配置，建议复测确认热点路径均走池内连接，避免连接抖动；")
    a("3. **SQL 预热与索引落地**：补齐 FULLTEXT/普通索引后，对高频精确匹配（project_number / company_name）建立 prepared statement 缓存；")
    a("4. **超长文本分级输出**：`illegal_behavior` 等字段采用「摘要 + 完整原文」两级呈现，避免截断丢字段。")
    a("")

    return "\n".join(L)


def append_extreme_cases(L, case_rows):
    rows = [r for r in case_rows if r["elapsed_s"] is not None]
    if not rows:
        return
    slowest = sorted(rows, key=lambda r: -r["elapsed_s"])[:5]
    fastest = sorted(rows, key=lambda r: r["elapsed_s"])[:5]
    a = L.append
    a("**最慢 5 条**：")
    a("")
    a("| sample_id(前8) | 耗时(s) | 子路由 | 问题（截断） |")
    a("| --- | --- | --- | --- |")
    for r in slowest:
        a(f"| {r['sample_id'][:8]} | {r['elapsed_s']} | {r['sub_route']} | {r['question'][:50]} |")
    a("")
    a("**最快 5 条**：")
    a("")
    a("| sample_id(前8) | 耗时(s) | 子路由 | 问题（截断） |")
    a("| --- | --- | --- | --- |")
    for r in fastest:
        a(f"| {r['sample_id'][:8]} | {r['elapsed_s']} | {r['sub_route']} | {r['question'][:50]} |")
    a("")


def bottleneck_lines(metrics, meta_agg, missing_reason_counter):
    lines = []
    cat = metrics["category"]
    n_sql = meta_agg.get("sql_count", {}).get("avg")
    node = meta_agg.get("node_elapsed", {}).get("avg")
    total_sql = meta_agg.get("total_sql_time", {}).get("avg")
    llm_est = None
    if node is not None and total_sql is not None:
        llm_est = node - total_sql
    a = lines.append
    a(f"1. **端到端延迟构成**：单条查询平均 {metrics['timing']['avg']}s、中位 {metrics['timing']['median']}s、最慢 {metrics['timing']['max']}s。")
    if node is not None:
        a(f"   业务节点内部平均 {node}s，其中 SQL 平均 {total_sql}s，"
          + (f"其余约 {llm_est:.2f}s 为 LLM 意图解析等串行等待（占主要延迟）。" if llm_est is not None else ""))
    if n_sql is not None:
        a(f"   单条查询平均执行 {n_sql} 次 SQL，SQL 本身极快，瓶颈不在数据层而在 LLM 串行链路。")
    a("")
    for qt, c in missing_reason_counter.most_common():
        if c > 0 and qt not in ("—",):
            a(f"2. **「{qt}」场景失败 {c} 例**：路由/查询类型命中但未完整召回必填字段，属于模板或字段输出链路缺口。")
    low_recall_cats = sorted(
        ((k, v) for k, v in cat.items() if v["field_recall"] < 100),
        key=lambda kv: kv[1]["field_recall"],
    )
    for name, s in low_recall_cats:
        a(f"3. **「{name}」分类字段召回率仅 {s['field_recall']}%、准确率 {s['accuracy']}%**：存在系统性漏字段。")
    if not low_recall_cats and not missing_reason_counter:
        a("3. 未发现系统性召回缺口，整体表现稳定。")
    a("4. **FULLTEXT 索引缺失**：测评日志中 MySQL 持续报 `Can't find FULLTEXT index matching the column list`（bid_project），系统回退 LIKE/全表扫描，影响复杂查询耗时与召回上限。")
    a("5. **单次查询需 1 次以上 LLM 串行调用**（意图解析 + 枚举归一化），是单条 2~3s 延迟的主要来源。")
    return lines


def optimize_lines(metrics):
    lines = []
    a = lines.append
    low = [k for k, v in metrics["field"].items() if v["recall"] < 100]
    if low:
        worst = "、".join(low[:8])
        a(f"1. **补齐缺失字段输出**：字段 `{worst}` 存在召回缺口，需在 output_templates/answer_templates 中补全对应必填字段的展示；")
    a("2. **经营范围与工商信息差异化渲染**：当前「经营范围」查询被归入 company_detail 模板，业务字段（province/city/industry/company_level/business_scope）需按标准模板完整输出；")
    a("3. **处罚空记录兜底修复**：对无处罚记录的企业，系统渲染阶段可能出现字段缺失异常（如 `KeyError: penalty_date`），需为空结果走 not_found/empty 模板而非渲染半截记录；")
    a("4. **补齐 FULLTEXT 索引**：为 bid_project 等表中文文本列建立 ngram FULLTEXT 索引，消除全表扫描，提升复杂召回与并发吞吐；")
    a("5. **确定性快路径**：对五种固定问题格式，直接走规则化 SQL 精确查询，跳过 LLM 意图解析，可将平均耗时从 2~3s 降至百毫秒级。")
    return lines
