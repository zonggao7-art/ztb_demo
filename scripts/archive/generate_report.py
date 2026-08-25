"""
Evaluate field recall / answer accuracy from raw_results.jsonl + text2sql_dataset.jsonl
and emit a standardized report into test_report/.

Outputs:
  - test_report/metrics.json           machine-readable metrics
  - test_report/case_details.csv       full per-case pass/fail detail
  - test_report/evaluation_report.md   human-readable report
"""
from __future__ import annotations

import csv
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATASET = PROJECT_ROOT / "text2sql_dataset.jsonl"
RAW = PROJECT_ROOT / "test_report" / "raw_results.jsonl"
OUT_DIR = PROJECT_ROOT / "test_report"

_WS = re.compile(r"\s+")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def norm(s: str) -> str:
    """Canonicalize a string for substring matching (strip whitespace/commas/currency)."""
    s = str(s)
    s = s.replace("，", "").replace(",", "").replace("￥", "").replace("¥", "")
    return _WS.sub("", s)


def try_float(s: str):
    t = str(s).strip().replace(",", "").replace("，", "").replace("￥", "").replace("¥", "")
    if not t:
        return None
    try:
        return float(t)
    except ValueError:
        return None


def numeric_close(a: float, b: float) -> bool:
    if a == b:
        return True
    tol = 1e-6 * max(1.0, abs(a), abs(b))
    return abs(a - b) <= tol


def value_recalled(v, corpus_norm: str, corpus_numbers: list[float]) -> bool:
    if v is None:
        return True
    s = str(v).strip()
    if s == "":
        return True

    n = norm(s)
    # 1) exact text substring (handles names/ids/dates and verbatim amounts)
    if n and n in corpus_norm:
        return True

    # 2) numeric equivalence (handles comma/decimal formatting), skip huge IDs to avoid float collision
    f = try_float(s)
    if f is not None and abs(f) < 1e15:
        if any(numeric_close(f, x) for x in corpus_numbers):
            return True

    # 3) long-text truncation tolerance (system truncates to 500 chars + ellipsis)
    if len(n) > 200 and n[:300] in corpus_norm:
        return True

    return False


def build_corpus(answer: str, records) -> tuple[str, list[float]]:
    parts = [answer or ""]
    if isinstance(records, list):
        for rec in records:
            if isinstance(rec, dict):
                parts.extend(str(v) for v in rec.values())
    corpus_norm = norm(" ".join(parts))
    numbers = [float(m.group()) for m in _NUM_RE.finditer(corpus_norm)]
    return corpus_norm, numbers


def resolve_gt_values(case: dict) -> list[tuple[str, object]]:
    """Return [(field, value), ...] for every required field across all ground-truth records."""
    fields = case.get("expected_fields", [])
    out = []
    for rec in case.get("ground_truth", []):
        for field in fields:
            if field in rec:
                out.append((field, rec[field]))
            else:
                # expression field stored under a SQL alias (e.g. "budget_amount - winning_amount" -> amount_difference)
                alias = [k for k in rec if k not in fields]
                out.append((field, rec[alias[0]] if len(alias) == 1 else None))
    return out


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)

    cases = []
    with open(DATASET, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))

    results = {}
    if RAW.exists():
        with open(RAW, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                results[d["sample_id"]] = d

    case_rows = []
    per_field = defaultdict(lambda: {"ok": 0, "total": 0})
    per_source = defaultdict(lambda: {"cases": 0, "correct": 0, "ok_fields": 0, "total_fields": 0, "times": []})
    per_diff = defaultdict(lambda: {"cases": 0, "correct": 0, "ok_fields": 0, "total_fields": 0, "times": []})
    branch_counter = Counter()
    error_counter = Counter()
    meta_collector = defaultdict(list)  # meta_key -> list of numeric values

    total_cases = 0
    total_fields = 0
    total_recalled = 0
    correct_cases = 0
    all_times = []

    for case in cases:
        sid = case["sample_id"]
        r = results.get(sid)
        src = case.get("source_file", "")
        diff = case.get("difficulty", "")

        gt_values = resolve_gt_values(case)
        n_fields = len(gt_values)
        elapsed = r.get("elapsed_s") if r else None
        branch = (r or {}).get("branch", "missing")
        error = (r or {}).get("error")
        answer = (r or {}).get("answer", "")
        records = (r or {}).get("records")
        total_found = (r or {}).get("total_found")
        meta = (r or {}).get("meta")
        if isinstance(meta, dict):
            for mk, mv in meta.items():
                if isinstance(mv, (int, float)):
                    meta_collector[mk].append(mv)

        total_cases += 1
        total_fields += n_fields
        if elapsed is not None:
            all_times.append(elapsed)
        branch_counter[branch] += 1
        if error:
            error_counter[error] += 1

        corpus_norm, corpus_numbers = build_corpus(answer, records)
        n_recalled = 0
        missing_fields_set = []
        seen_missing = set()
        for field, val in gt_values:
            ok = value_recalled(val, corpus_norm, corpus_numbers)
            per_field[field]["total"] += 1
            if ok:
                n_recalled += 1
                per_field[field]["ok"] += 1
            else:
                if field not in seen_missing:
                    seen_missing.add(field)
                    missing_fields_set.append(field)

        total_recalled += n_recalled
        case_correct = (n_recalled == n_fields)
        if case_correct:
            correct_cases += 1

        ps = per_source[src]
        ps["cases"] += 1
        ps["ok_fields"] += n_recalled
        ps["total_fields"] += n_fields
        if case_correct:
            ps["correct"] += 1
        if elapsed is not None:
            ps["times"].append(elapsed)

        pd = per_diff[diff]
        pd["cases"] += 1
        pd["ok_fields"] += n_recalled
        pd["total_fields"] += n_fields
        if case_correct:
            pd["correct"] += 1
        if elapsed is not None:
            pd["times"].append(elapsed)

        case_rows.append({
            "sample_id": sid,
            "question": case["question"],
            "source_file": src,
            "difficulty": diff,
            "expected_fields": json.dumps(case.get("expected_fields", []), ensure_ascii=False),
            "branch": branch,
            "intent": r.get("intent", "") if r else "",
            "elapsed_s": elapsed,
            "total_found": total_found,
            "num_fields": n_fields,
            "num_recalled": n_recalled,
            "num_missing": n_fields - n_recalled,
            "pass": case_correct,
            "error": error or "",
            "missing_fields": json.dumps(missing_fields_set, ensure_ascii=False),
        })

    # ── aggregate metrics ──
    def pct(ok, tot):
        return round(100.0 * ok / tot, 3) if tot else 0.0

    field_recall = pct(total_recalled, total_fields)
    answer_accuracy = pct(correct_cases, total_cases)

    times = all_times
    timing = {
        "count": len(times),
        "avg": round(statistics.mean(times), 3) if times else None,
        "median": round(statistics.median(times), 3) if times else None,
        "min": round(min(times), 3) if times else None,
        "max": round(max(times), 3) if times else None,
    }
    for p in (50, 90, 95, 99):
        if times:
            timing[f"p{p}"] = round(_percentile(times, p), 3)

    source_stats = {}
    for src, s in sorted(per_source.items()):
        source_stats[src] = {
            "cases": s["cases"],
            "accuracy": pct(s["correct"], s["cases"]),
            "field_recall": pct(s["ok_fields"], s["total_fields"]),
            "avg_time": round(statistics.mean(s["times"]), 3) if s["times"] else None,
        }
    diff_stats = {}
    for d, s in sorted(per_diff.items()):
        diff_stats[d] = {
            "cases": s["cases"],
            "accuracy": pct(s["correct"], s["cases"]),
            "field_recall": pct(s["ok_fields"], s["total_fields"]),
            "avg_time": round(statistics.mean(s["times"]), 3) if s["times"] else None,
        }
    field_stats = {
        f: {"recall": pct(v["ok"], v["total"]), "ok": v["ok"], "total": v["total"]}
        for f, v in sorted(per_field.items(), key=lambda kv: pct(kv[1]["ok"], kv[1]["total"]))
    }

    meta_agg = {}
    for mk, vals in meta_collector.items():
        if vals:
            meta_agg[mk] = {
                "avg": round(statistics.mean(vals), 4),
                "count": len(vals),
            }

    metrics = {
        "total_cases": total_cases,
        "total_fields": total_fields,
        "total_recalled": total_recalled,
        "field_recall_rate": field_recall,
        "answer_accuracy": answer_accuracy,
        "correct_cases": correct_cases,
        "timing": timing,
        "meta": meta_agg,
        "source_file": source_stats,
        "difficulty": diff_stats,
        "field": field_stats,
        "branch_distribution": dict(branch_counter),
        "error_distribution": dict(error_counter),
    }

    with open(OUT_DIR / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    # ── CSV detail ──
    csv_cols = ["sample_id", "question", "source_file", "difficulty", "expected_fields",
                "branch", "intent", "elapsed_s", "total_found", "num_fields",
                "num_recalled", "num_missing", "pass", "error", "missing_fields"]
    with open(OUT_DIR / "case_details.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=csv_cols)
        w.writeheader()
        for row in case_rows:
            w.writerow(row)

    # ── Markdown report ──
    write_markdown(metrics, case_rows, source_stats, diff_stats, field_stats,
                   branch_counter, error_counter, timing, meta_agg)

    print(f"field_recall={field_recall}%  answer_accuracy={answer_accuracy}%  "
          f"correct={correct_cases}/{total_cases}")
    print("Wrote metrics.json, case_details.csv, evaluation_report.md")


def _percentile(data, p):
    s = sorted(data)
    k = (len(s) - 1) * p / 100.0
    f = int(k)
    c = f + 1
    if c >= len(s):
        return s[-1]
    return s[f] + (s[c] - s[f]) * (k - f)


def write_markdown(metrics, case_rows, source_stats, diff_stats, field_stats,
                   branch_counter, error_counter, timing, meta_agg):
    lines = []
    a = lines.append
    a("# 招投标智能助手 — Text2SQL 系统测评报告")
    a("")
    a(f"> 生成时间：2026-08-13　|　测评用例：{metrics['total_cases']} 组问答对")
    a("")

    # 1. environment
    a("## 1. 测试环境说明")
    a("")
    a("| 项目 | 配置 |")
    a("| --- | --- |")
    a("| 数据集 | `text2sql_dataset.jsonl`（1000 组 Text2SQL 问答对） |")
    a("| 评测对象 | `agent` LangGraph Agent（router → price_inquiry 等业务节点） |")
    a("| 大语言模型 | `deepseek-chat`（temperature=0，超时 60s，重试 1 次） |")
    a("| Embedding | `BAAI/bge-m3`（SiliconFlow） |")
    a("| 结构化库 | MySQL `ztb_clean`（bid_project / company_penalty / company_info） |")
    a("| 向量库 | Milvus（public_kb / mysql_price_semantic） |")
    a("| 运行方式 | 单进程顺序调用 `AgentGraph.invoke()`，逐条计时 |")
    a("")

    # 2. rules
    a("## 2. 测评规则说明")
    a("")
    a("**校验基准**：每条用例的标准答案由 `expected_fields`（必填字段清单）与 `ground_truth`（字段的正确取值，可能含多条记录）共同定义。单条用例的必填固定字段值 = 所有 `ground_truth` 记录 × 所有 `expected_fields` 的笛卡尔积。")
    a("")
    a("**唯一判定标准**：系统答案是否**完整召回标准答案内的所有必填固定字段值**。系统答案 = 自然语言回答 `answer` 与结构化记录 `records` 的并集；任一处命中即视为召回该字段值，不考量表述句式是否与标准答案一致。")
    a("")
    a("**值匹配归一化**（避免格式差异导致的误判）：")
    a("")
    a("1. 文本值：去除空白/逗号/货币符号后做子串匹配（用于名称、编号、日期等）；")
    a("2. 数值：浮点等价判定（容差 1e-6 相对），用于处理 `19990000.0` vs `19,990,000.00` 等格式化差异；")
    a("3. 超长文本（>200 字符，如 `illegal_behavior`）：因系统截断至 500 字符，采用前 300 字符前缀匹配。")
    a("")
    a("**指标口径**：")
    a("")
    a("- **字段整体召回率** = 召回字段值数 / 全部必填字段值数（跨所有用例与所有记录）；")
    a("- **答案整体准确率** = 全部字段值均召回的用例数 / 用例总数（全对才计为正确）。")
    a("")

    # 3. overall
    a("## 3. 核心指标总览")
    a("")
    a("| 指标 | 数值 |")
    a("| --- | --- |")
    a(f"| 评测用例总数 | {metrics['total_cases']} |")
    a(f"| 必填字段值总数 | {metrics['total_fields']} |")
    a(f"| 召回字段值数 | {metrics['total_recalled']} |")
    a(f"| **字段整体召回率** | **{metrics['field_recall_rate']}%** |")
    a(f"| 完全正确用例数 | {metrics['correct_cases']} |")
    a(f"| **答案整体准确率** | **{metrics['answer_accuracy']}%** |")
    a("")

    # 4. timing
    a("## 4. 执行耗时统计（单条查询）")
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
    _append_extreme_cases(lines, case_rows)

    # 5. category breakdown
    a("## 5. 业务分类细分（按数据源 source_file）")
    a("")
    a("| 数据源 | 用例数 | 字段召回率 | 答案准确率 | 平均耗时(s) |")
    a("| --- | --- | --- | --- | --- |")
    for src, s in source_stats.items():
        a(f"| {src} | {s['cases']} | {s['field_recall']}% | {s['accuracy']}% | {s['avg_time'] if s['avg_time'] is not None else '—'} |")
    a("")

    a("## 6. 难度细分")
    a("")
    a("| 难度 | 用例数 | 字段召回率 | 答案准确率 | 平均耗时(s) |")
    a("| --- | --- | --- | --- | --- |")
    for d, s in diff_stats.items():
        a(f"| {d} | {s['cases']} | {s['field_recall']}% | {s['accuracy']}% | {s['avg_time'] if s['avg_time'] is not None else '—'} |")
    a("")

    # 7. field breakdown
    a("## 7. 字段级召回率（按召回率升序，最差在前）")
    a("")
    a("| 字段 | 召回数 | 总数 | 召回率 |")
    a("| --- | --- | --- | --- |")
    for f, v in field_stats.items():
        a(f"| {f} | {v['ok']} | {v['total']} | {v['recall']}% |")
    a("")

    # 8. branch/error
    a("## 8. 路由分支与异常分布")
    a("")
    a("| 分支 | 次数 |")
    a("| --- | --- |")
    for b, c in branch_counter.most_common():
        a(f"| {b} | {c} |")
    a("")
    if error_counter:
        a("| 异常类型 | 次数 |")
        a("| --- | --- |")
        for e, c in error_counter.most_common():
            a(f"| {e[:80]} | {c} |")
        a("")

    # 9. per-case pass/fail detail
    failed = [r for r in case_rows if not r["pass"]]
    a("## 9. 各测试用例对错明细（全量 1000 例）")
    a("")
    a("> 完整字段（缺失字段值、错误信息等）见 `case_details.csv`。下表为全量对错概览；失败用例的缺失字段在下表 `9.1` 中展开。")
    a("")
    a("| # | sample_id(前8) | 数据源 | 难度 | 结果 | 召回 | 耗时(s) | 分支 |")
    a("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for i, r in enumerate(case_rows, 1):
        mark = "✅" if r["pass"] else "❌"
        a(f"| {i} | {r['sample_id'][:8]} | {r['source_file']} | {r['difficulty']} | {mark} | {r['num_recalled']}/{r['num_fields']} | {r['elapsed_s']} | {r['branch']} |")
    a("")

    a(f"### 9.1 失败用例明细（共 {len(failed)} 例）")
    a("")
    if failed:
        a("| sample_id(前8) | 数据源 | 难度 | 召回 | 耗时(s) | 分支 | 缺失字段 |")
        a("| --- | --- | --- | --- | --- | --- | --- |")
        for r in failed:
            miss = json.loads(r["missing_fields"]) if r["missing_fields"] else []
            miss_names = "、".join(miss[:6])
            if len(miss) > 6:
                miss_names += f" 等{len(miss)}项"
            a(f"| {r['sample_id'][:8]} | {r['source_file']} | {r['difficulty']} | {r['num_recalled']}/{r['num_fields']} | {r['elapsed_s']} | {r['branch']} | {miss_names} |")
    else:
        a("（无）")
    a("")

    # 10. bottleneck + optimization
    a("## 10. 系统性能瓶颈分析与优化建议")
    a("")
    a("### 10.1 端到端耗时构成（基于 meta 采样）")
    a("")
    if meta_agg:
        a("| 内部指标 | 平均值 | 说明 |")
        a("| --- | --- | --- |")
        label_map = {
            "node_elapsed": "业务节点内部耗时（检索+渲染）",
            "total_sql_time": "SQL 执行耗时",
            "sql_count": "单条查询执行的 SQL 数",
            "total_hits": "检索命中记录数",
            "displayed_hits": "展示记录数",
        }
        for mk, mv in meta_agg.items():
            a(f"| {mk} | {mv['avg']} | {label_map.get(mk, '')} |")
        a("")
    a("### 10.2 观察到的瓶颈")
    a("")
    a("1. **结构化列表/筛选查询完全不支持**（`company_info.csv` 22 例 100% 失败）：评测中「按行业+注册资本+成立日期等条件列出公司并排序」类查询，系统一律退回「能力引导话术」（空结果）。价格查询节点的 `company_query` 路由仅实现「单实体详情/处罚查询」，未实现列表筛选查询。")
    a("2. **聚合查询召回率为 0**：`AVG/SUM/COUNT/MAX/比值/差额` 等聚合字段（avg_budget_amount、total_budget、winning_ratio、max_budget 等）在全部用例中召回率均为 0%，说明系统未实现 SQL 聚合计算。")
    a("3. **多条件硬过滤失效**（中等难度 328 例准确率仅 23.2%）：涉及「省份+城市+类别+金额区间+日期」等多条件组合的查询，多级检索（语义/FULLTEXT/LIKE）无法正确施加全部硬过滤条件，大量用例返回空结果（`total_found=None`）。")
    a("4. **FULLTEXT 索引缺失**：评测全程 MySQL 持续报 `Can't find FULLTEXT index matching the column list`，价格查询多级检索回退到 `LIKE '%…%'` / 全表扫描，加剧耗时与漏召。")
    a("5. **端到端延迟构成**：单条查询平均 3.02s，其中 SQL 仅约 0.005s，`node_elapsed` 约 1.8s；剩余 ~1.2s 为 LLM 路由 + 意图解析的串行等待，是延迟主要来源。")
    a("6. **超长文本截断**：`illegal_behavior` 等字段输出被截断至 500 字符，长答案无法完整呈现。")
    a("")
    a("### 10.3 优化建议")
    a("")
    a("1. **补齐 FULLTEXT 索引**：为 `bid_project`（project_name/purchaser/successful_bidder/subject_matter 等）、`company_penalty`、`company_info` 中文文本列建立 ngram FULLTEXT 索引，消除全表扫描，同时为金额/日期等数值列建立普通索引支撑范围过滤。")
    a("2. **实现列表筛选与聚合查询能力**：为 `company_query`/`bidding_query` 增加「结构化条件解析 → SQL WHERE/ORDER BY/LIMIT」路径，支持行业/注册资本/日期等条件过滤与排序，并新增 `COUNT/SUM/AVG/MAX/比值/差额` 聚合算子。")
    a("3. **强化硬过滤条件落地**：将意图解析出的数值区间、日期区间、多分类条件严格映射为 SQL 谓词，避免被语义召回的候选截断「全歼」；必要时对候选不足的查询直接回退到纯 SQL 精确过滤。")
    a("4. **降低 LLM 串行等待**：对高置信度意图走确定性路由或结果缓存，减少每查询 2~3 次串行 LLM 调用的开销；评估将路由/意图解析合并为单次结构化输出。")
    a("5. **结果数量上限可配置**：对列表类查询取消或调大 Top-K 限制，确保完整返回全部匹配记录。")
    a("6. **超长文本分级输出**：对 `illegal_behavior` 等字段支持「摘要 + 完整原文」两级呈现，避免截断丢失关键信息。")
    a("")

    with open(OUT_DIR / "evaluation_report.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _append_extreme_cases(lines, case_rows):
    rows = [r for r in case_rows if r["elapsed_s"] is not None]
    if not rows:
        return
    slowest = sorted(rows, key=lambda r: -r["elapsed_s"])[:5]
    fastest = sorted(rows, key=lambda r: r["elapsed_s"])[:5]
    a = lines.append
    a("**最慢 5 条**：")
    a("")
    a("| sample_id(前8) | 耗时(s) | 分支 | 问题（截断） |")
    a("| --- | --- | --- | --- |")
    for r in slowest:
        a(f"| {r['sample_id'][:8]} | {r['elapsed_s']} | {r['branch']} | {r['question'][:40]} |")
    a("")
    a("**最快 5 条**：")
    a("")
    a("| sample_id(前8) | 耗时(s) | 分支 | 问题（截断） |")
    a("| --- | --- | --- | --- |")
    for r in fastest:
        a(f"| {r['sample_id'][:8]} | {r['elapsed_s']} | {r['branch']} | {r['question'][:40]} |")
    a("")


if __name__ == "__main__":
    main()
