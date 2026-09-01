"""
测评报告 HTML 渲染 — 内联 SVG 图表与全量明细表（无外部依赖）。

自 generate_three_core_report.py 拆分（P1-3）。
"""

from __future__ import annotations


def svg_hbar(items, max_val=None, width=760, bar_h=26, gap=10, colors=None):
    """横向条形图 SVG。items: [(label, value)]。"""
    if not items:
        return ""
    max_val = max_val or max(v for _, v in items) * 1.08 or 1
    plot_left, plot_right, top = 210, width - 30, 20
    plot_w = plot_right - plot_left
    height = top + len(items) * (bar_h + gap) + 20
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">']
    parts.append(f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff" rx="8"/>')
    # 网格线
    for gi in range(0, 5):
        x = plot_left + plot_w * gi / 4
        parts.append(f'<line x1="{x}" y1="{top}" x2="{x}" y2="{top + len(items) * (bar_h + gap)}" stroke="#eef1f5" stroke-width="1"/>')
    for i, (label, v) in enumerate(items):
        y = top + i * (bar_h + gap)
        bw = max(0, plot_w * v / max_val)
        color = (colors[i] if colors else "#2563eb")
        parts.append(f'<text x="{plot_left - 12}" y="{y + bar_h / 2 + 4}" text-anchor="end" font-size="13" fill="#334155">{label}</text>')
        parts.append(f'<rect x="{plot_left}" y="{y}" width="{bw}" height="{bar_h}" rx="4" fill="{color}" opacity="0.92"/>')
        parts.append(f'<text x="{plot_left + bw + 6}" y="{y + bar_h / 2 + 4}" font-size="12" fill="#0f172a">{v}</text>')
    parts.append("</svg>")
    return "".join(parts)


def build_html(metrics, case_rows, cat_stats, field_stats, timing, meta_agg, missing_reason_counter):
    cat = metrics["category"]
    # 图表 1：核心指标
    chart_overall = svg_hbar(
        [("必填字段整体召回率(%)", metrics["field_recall_rate"]),
         ("系统输出整体准确率(%)", metrics["answer_accuracy"])],
        colors=["#2563eb", "#16a34a"],
    )
    # 图表 2：分类字段召回率 + 准确率
    cat_items = [(k, v["field_recall"]) for k, v in cat.items()]
    cat_acc_items = [(k, v["accuracy"]) for k, v in cat.items()]
    chart_cat_recall = svg_hbar(cat_items, colors=["#2563eb"] * len(cat_items))
    chart_cat_acc = svg_hbar(cat_acc_items, colors=["#16a34a"] * len(cat_items))
    # 图表 3：耗时
    time_items = [
        ("平均耗时(s)", timing["avg"] if timing["avg"] is not None else 0),
        ("中位数(s)", timing["median"] if timing["median"] is not None else 0),
        ("P90(s)", timing.get("p90") or 0),
        ("P95(s)", timing.get("p95") or 0),
        ("最慢(s)", timing["max"] if timing["max"] is not None else 0),
    ]
    chart_time = svg_hbar(time_items, colors=["#d97706"] * len(time_items))
    # 图表 4：字段召回率（最差 15 个）
    worst_fields = list(field_stats.items())[:15]
    chart_field = svg_hbar(
        [(f"{f} ({v['ok']}/{v['total']})", v["recall"]) for f, v in worst_fields],
        colors=["#dc2626"] * len(worst_fields),
    )
    # 图表 5：分类平均耗时
    cat_time_items = [(k, v["avg_time"] or 0) for k, v in cat.items()]
    chart_cat_time = svg_hbar(cat_time_items, colors=["#7c3aed"] * len(cat_time_items))
    # 图表 6：失败查询类型分布
    fail_items = missing_reason_counter.most_common(10)
    chart_fail = svg_hbar(fail_items, colors=["#f97316"] * len(fail_items)) if fail_items else "（无失败用例）"

    env = metrics["env"]
    env_rows = "".join(
        f"<tr><td>{k}</td><td>{v}</td></tr>"
        for k, v in env.items() if k not in ("os", "python", "time", "llm")
    )
    cat_rows = "".join(
        f"<tr><td>{k}</td><td>{v['cases']}</td><td>{v['field_recall']}%</td>"
        f"<td>{v['accuracy']}%</td><td>{v['avg_time']}</td></tr>"
        for k, v in cat.items()
    )
    field_rows = "".join(
        f"<tr><td>{f}</td><td>{v['ok']}</td><td>{v['total']}</td><td>{v['recall']}%</td></tr>"
        for f, v in field_stats.items()
    )

    ok_count = sum(1 for r in case_rows if r["pass"])
    fail_count = len(case_rows) - ok_count
    # 汇总卡片
    cards = f"""
    <div style="display:flex;gap:14px;flex-wrap:wrap;margin:16px 0;">
      <div style="flex:1;min-width:150px;background:#eff6ff;border:1px solid #bfdbfe;border-radius:10px;padding:14px;text-align:center;">
        <div style="font-size:26px;font-weight:700;color:#1d4ed8;">{metrics['field_recall_rate']}%</div>
        <div style="color:#475569;font-size:13px;">必填字段整体召回率</div>
      </div>
      <div style="flex:1;min-width:150px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;padding:14px;text-align:center;">
        <div style="font-size:26px;font-weight:700;color:#15803d;">{metrics['answer_accuracy']}%</div>
        <div style="color:#475569;font-size:13px;">系统输出整体准确率</div>
      </div>
      <div style="flex:1;min-width:150px;background:#fff7ed;border:1px solid #fed7aa;border-radius:10px;padding:14px;text-align:center;">
        <div style="font-size:26px;font-weight:700;color:#c2410c;">{timing['avg']}s</div>
        <div style="color:#475569;font-size:13px;">平均耗时 / 最快 {timing['min']}s / 最慢 {timing['max']}s</div>
      </div>
      <div style="flex:1;min-width:150px;background:#faf5ff;border:1px solid #e9d5ff;border-radius:10px;padding:14px;text-align:center;">
        <div style="font-size:26px;font-weight:700;color:#6d28d9;">{ok_count} / {len(case_rows)}</div>
        <div style="color:#475569;font-size:13px;">完全正确用例（失败 {fail_count}）</div>
      </div>
    </div>"""

    detail_rows = "".join(
        f"<tr class=\"{'pass' if r['pass'] else 'fail'}\">"
        f"<td>{i}</td><td>{r['sample_id'][:8]}</td><td>{r['category']}</td><td>{r['difficulty']}</td>"
        f"<td>{'✅' if r['pass'] else '❌'}</td><td>{r['num_recalled']}/{r['num_fields']}</td>"
        f"<td>{r['elapsed_s']}</td><td>{r['sub_route']}</td></tr>"
        for i, r in enumerate(case_rows, 1)
    )

    html = f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>三大核心业务全流程测评报告</title>
<style>
  body {{ font-family: "Microsoft YaHei", "PingFang SC", sans-serif; background:#f1f5f9; margin:0; color:#0f172a; }}
  .wrap {{ max-width:1080px; margin:0 auto; padding:24px; }}
  h1 {{ font-size:24px; }}
  h2 {{ font-size:19px; border-bottom:2px solid #cbd5e1; padding-bottom:6px; margin-top:36px; color:#1e293b; }}
  h3 {{ font-size:16px; color:#334155; }}
  table {{ border-collapse:collapse; width:100%; background:#fff; margin:10px 0 20px; font-size:13px; }}
  th,td {{ border:1px solid #e2e8f0; padding:6px 9px; text-align:left; }}
  th {{ background:#f8fafc; }}
  tr.pass td {{ background:#f0fdf4; }}
  tr.fail td {{ background:#fef2f2; }}
  .meta {{ color:#64748b; font-size:13px; }}
  .card-title {{ font-size:14px; font-weight:700; margin:14px 0 6px; }}
</style>
</head>
<body><div class="wrap">
<h1>招投标智能助手 — 三大核心业务全流程测评报告</h1>
<p class="meta">生成时间：{env['time']} ｜ 测评对象：当前线上业务系统（agent LangGraph Agent） ｜ 测评用例：{metrics['total_cases']} 条</p>
{cards}

<h2>1. 测试环境与依赖说明</h2>
<h3>1.1 软硬件环境</h3>
<table>
<tr><th>项目</th><th>说明</th></tr>
<tr><td>操作系统</td><td>{env['os']}</td></tr>
<tr><td>Python</td><td>{env['python']}</td></tr>
<tr><td>大语言模型</td><td>{env.get('llm', '未知')}（temperature=0，超时 60s，重试 1 次）</td></tr>
<tr><td>Embedding</td><td>BAAI/bge-m3（SiliconFlow，1024 维）</td></tr>
<tr><td>结构化数据库</td><td>MySQL 8.0（Docker）ztb_clean：bid_project 17,742 / company_info 38,911 / company_penalty 1,805</td></tr>
<tr><td>向量数据库</td><td>Milvus 2.4 standalone：public_kb 29,729 / mysql_price_semantic 77,597</td></tr>
<tr><td>运行方式</td><td>单进程顺序调用 AgentGraph.invoke()，逐条墙钟计时</td></tr>
</table>
<h3>1.2 依赖版本</h3>
<table>
<tr><th>依赖</th><th>版本</th></tr>{env_rows}
</table>

<h2>2. 测评规则说明</h2>
<p><b>校验基准：</b>每条用例的必填固定字段 = <code>expected_fields</code> × <code>ground_truth</code> 全部记录的笛卡尔积，逐值可机器判等。</p>
<p><b>唯一判定标准：</b>系统输出（<code>answer</code> 文本 ∪ <code>records</code> 结构化记录）是否完整召回全部必填字段值；不考量句式与语序。</p>
<p><b>值匹配归一化：</b>文本去空白/逗号/货币符号后子串匹配；数值浮点等价（容差 1e-6）；超长文本前缀容差。</p>
<p><b>指标口径：</b>字段整体召回率 = 召回值数/全部必填值数；答案整体准确率 = 全部字段均召回的用例占比；耗时 = 单条墙钟；分类按招投标项目/企业信息/企业失信惩戒拆分。</p>

<h2>3. 核心指标可视化</h2>
<div class="card-title">核心指标总览</div>
{chart_overall}
<div class="card-title">业务分类字段召回率（%）</div>
{chart_cat_recall}
<div class="card-title">业务分类答案准确率（%）</div>
{chart_cat_acc}

<h2>4. 执行耗时统计（单条查询）</h2>
<div class="card-title">耗时分布（秒）</div>
{chart_time}
<div class="card-title">业务分类平均耗时（秒）</div>
{chart_cat_time}
<table>
<tr><th>指标</th><th>数值(秒)</th></tr>
<tr><td>计时样本数</td><td>{timing['count']}</td></tr>
<tr><td>平均 / 中位数</td><td>{timing['avg']} / {timing['median']}</td></tr>
<tr><td>最快 / 最慢</td><td>{timing['min']} / {timing['max']}</td></tr>
<tr><td>P50 / P90 / P95 / P99</td><td>{timing.get('p50')} / {timing.get('p90')} / {timing.get('p95')} / {timing.get('p99')}</td></tr>
</table>

<h2>5. 业务分类细分指标</h2>
<table>
<tr><th>业务分类</th><th>用例数</th><th>字段召回率</th><th>答案准确率</th><th>平均耗时(s)</th></tr>{cat_rows}
</table>

<h2>6. 字段级召回率（最差在前）</h2>
<div class="card-title">字段级召回率 Top-15 最差</div>
{chart_field}
<table>
<tr><th>字段</th><th>召回数</th><th>总数</th><th>召回率</th></tr>{field_rows}
</table>

<h2>7. 失败场景分布</h2>
<div class="card-title">失败用例按查询类型分布</div>
{chart_fail}

<h2>8. 性能瓶颈分析</h2>
<ul>
<li>单条平均 {timing['avg']}s，其中 SQL 平均 {meta_agg.get('total_sql_time',{}).get('avg','—')}s、业务节点平均 {meta_agg.get('node_elapsed',{}).get('avg','—')}s，主要延迟来自 LLM 串行意图解析。</li>
<li>MySQL 全程缺失 FULLTEXT 索引（bid_project 等表），复杂查询回退 LIKE/全表扫描。</li>
<li>「{missing_reason_counter.most_common(1)[0][0] if missing_reason_counter else '无'}」等场景存在系统性漏字段/渲染异常。</li>
</ul>

<h2>9. 逐条校验对错明细（全量 {len(case_rows)} 例）</h2>
<p class="meta">完整字段级数据见 <code>case_details.csv</code>。</p>
<table>
<tr><th>#</th><th>sample_id</th><th>业务分类</th><th>难度</th><th>结果</th><th>召回</th><th>耗时(s)</th><th>子路由</th></tr>
{detail_rows}
</table>
</div></body></html>"""
    return html
