"""
三大核心测试集测评报告生成脚本。

读取三份测试集 + test_report/raw_results.jsonl（run_three_core_evaluation.py 产物），
按「完整召回标准答案内所有必填固定字段」唯一判定标准逐条校验，输出：

  - test_report/metrics.json            机器可读指标
  - test_report/case_details.csv        全量用例对错明细
  - test_report/evaluation_report.md    标准测评报告（Markdown，含规则/环境/指标/瓶颈/建议）
  - test_report/evaluation_report.html  可视化报告（内联 SVG 图表，无外部依赖）

渲染逻辑拆分：Markdown → report_markdown.py；HTML/SVG → report_html.py；
归一化/召回判定 → eval_report_common.py。

用法：
    python scripts/generate_three_core_report.py
"""
from __future__ import annotations

import csv
import json
import platform
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from eval_report_common import (  # noqa: E402
    _percentile,
    build_corpus,
    pct,
    resolve_gt_values,
    value_recalled,
)
from report_html import build_html  # noqa: E402
from report_markdown import build_markdown  # noqa: E402

OUT_DIR = PROJECT_ROOT / "test_report"
RAW = OUT_DIR / "raw_results.jsonl"

TESTSET_FILES = [
    (PROJECT_ROOT / "testset_bid_project.jsonl", "招投标项目中标情报"),
    (PROJECT_ROOT / "testset_company_info.jsonl", "企业工商情报"),
    (PROJECT_ROOT / "testset_company_penalty.jsonl", "企业失信惩戒"),
]


def load_cases():
    """加载三份测试集，并打上业务分类标签。"""
    cases = []
    for path, category in TESTSET_FILES:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                d["_category"] = category
                cases.append(d)
    return cases


def load_results():
    results = {}
    if RAW.exists():
        with open(RAW, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                results[d["sample_id"]] = d
    return results


def main():
    OUT_DIR.mkdir(exist_ok=True)
    cases = load_cases()
    results = load_results()

    if len(results) < len(cases):
        print(f"[WARN] 已完成 {len(results)}/{len(cases)}，结果不全，报告将基于已完成部分。")
    else:
        print(f"[OK] 全部 {len(cases)} 条已测评完成。")

    # ── 逐条校验 ──
    case_rows = []
    per_field = defaultdict(lambda: {"ok": 0, "total": 0})
    per_cat = defaultdict(lambda: {
        "cases": 0, "correct": 0, "ok_fields": 0, "total_fields": 0, "times": [],
    })
    per_source = defaultdict(lambda: {"cases": 0, "correct": 0, "ok_fields": 0, "total_fields": 0, "times": []})
    branch_counter = Counter()
    subroute_counter = Counter()
    querytype_counter = Counter()
    error_counter = Counter()
    meta_collector = defaultdict(list)

    total_cases = 0
    total_fields = 0
    total_recalled = 0
    correct_cases = 0
    all_times = []
    missing_reason_counter = Counter()  # query_type 维度统计失败原因

    for case in cases:
        sid = case["sample_id"]
        r = results.get(sid)
        if r is None:
            continue  # 该用例尚未完成测评，不计入本次统计
        cat = case.get("_category", "")
        src = case.get("source_file", "")
        diff = case.get("difficulty", "")

        gt_values = resolve_gt_values(case)
        n_fields = len(gt_values)
        elapsed = r.get("elapsed_s") if r else None
        branch = (r or {}).get("branch", "missing")
        sub_route = (r or {}).get("sub_route") or "—"
        query_type = (r or {}).get("query_type") or "—"
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
        subroute_counter[sub_route] += 1
        querytype_counter[query_type] += 1
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
        case_correct = n_recalled == n_fields
        if case_correct:
            correct_cases += 1
        else:
            missing_reason_counter[query_type] += 1

        pc = per_cat[cat]
        pc["cases"] += 1
        pc["ok_fields"] += n_recalled
        pc["total_fields"] += n_fields
        if case_correct:
            pc["correct"] += 1
        if elapsed is not None:
            pc["times"].append(elapsed)

        ps = per_source[src]
        ps["cases"] += 1
        ps["ok_fields"] += n_recalled
        ps["total_fields"] += n_fields
        if case_correct:
            ps["correct"] += 1
        if elapsed is not None:
            ps["times"].append(elapsed)

        case_rows.append({
            "sample_id": sid,
            "question": case["question"],
            "category": cat,
            "source_file": src,
            "difficulty": diff,
            "expected_fields": json.dumps(case.get("expected_fields", []), ensure_ascii=False),
            "branch": branch,
            "sub_route": sub_route,
            "query_type": query_type,
            "elapsed_s": elapsed,
            "total_found": total_found,
            "num_fields": n_fields,
            "num_recalled": n_recalled,
            "num_missing": n_fields - n_recalled,
            "pass": case_correct,
            "error": error or "",
            "missing_fields": json.dumps(missing_fields_set, ensure_ascii=False),
        })

    # ── 聚合指标 ──
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

    cat_stats = {}
    for cat, s in sorted(per_cat.items()):
        cat_stats[cat] = {
            "cases": s["cases"],
            "accuracy": pct(s["correct"], s["cases"]),
            "field_recall": pct(s["ok_fields"], s["total_fields"]),
            "avg_time": round(statistics.mean(s["times"]), 3) if s["times"] else None,
        }

    source_stats = {}
    for src, s in sorted(per_source.items()):
        source_stats[src] = {
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
                "min": round(min(vals), 4),
                "max": round(max(vals), 4),
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
        "category": cat_stats,
        "source_file": source_stats,
        "field": field_stats,
        "branch_distribution": dict(branch_counter),
        "sub_route_distribution": dict(subroute_counter),
        "query_type_distribution": dict(querytype_counter),
        "missing_reason_by_query_type": dict(missing_reason_counter),
        "error_distribution": dict(error_counter),
        "env": collect_env(),
    }

    with open(OUT_DIR / "metrics.json", "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    # ── CSV 明细 ──
    csv_cols = ["sample_id", "question", "category", "source_file", "difficulty",
                "expected_fields", "branch", "sub_route", "query_type", "elapsed_s",
                "total_found", "num_fields", "num_recalled", "num_missing",
                "pass", "error", "missing_fields"]
    with open(OUT_DIR / "case_details.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=csv_cols)
        w.writeheader()
        for row in case_rows:
            w.writerow(row)

    # ── Markdown 报告 ──
    md = build_markdown(metrics, case_rows, cat_stats, field_stats,
                        branch_counter, subroute_counter, querytype_counter,
                        error_counter, timing, meta_agg, missing_reason_counter)
    with open(OUT_DIR / "evaluation_report.md", "w", encoding="utf-8") as f:
        f.write(md)

    # ── HTML 可视化报告 ──
    html = build_html(metrics, case_rows, cat_stats, field_stats,
                      timing, meta_agg, missing_reason_counter)
    with open(OUT_DIR / "evaluation_report.html", "w", encoding="utf-8") as f:
        f.write(html)

    print(f"field_recall={field_recall}%  answer_accuracy={answer_accuracy}%  correct={correct_cases}/{total_cases}")
    print(f"timing: avg={timing['avg']}s median={timing['median']}s min={timing['min']}s max={timing['max']}s")
    print("Wrote metrics.json / case_details.csv / evaluation_report.md / evaluation_report.html")


# ═════════════════════════════════════════════════════════
# 环境信息采集
# ═════════════════════════════════════════════════════════
def collect_env():
    env = {
        "os": platform.platform(),
        "python": platform.python_version(),
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    try:
        import pymysql
        env["pymysql"] = getattr(pymysql, "__version__", "?")
    except Exception:
        env["pymysql"] = "?"
    try:
        import pymilvus
        env["pymilvus"] = getattr(pymilvus, "__version__", "?")
    except Exception:
        env["pymilvus"] = "?"
    for pkg in ("langgraph", "langchain_openai", "langchain_core", "openai", "pandas", "matplotlib"):
        try:
            m = __import__(pkg)
            env[pkg] = getattr(m, "__version__", "?")
        except Exception:
            env[pkg] = "未安装"
    return env


if __name__ == "__main__":
    main()
