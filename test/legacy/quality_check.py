"""
数据质量分析脚本 - 检查导出样本的脏数据情况
检查项: 空值/NULL、重复行、异常值、编码问题、字段完整度、长文本等
"""
import json
import os
import re

SAMPLE_DIR = "sample_export"

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def check_null_empty(value):
    """检查是否为空/NULL"""
    if value is None:
        return True
    if isinstance(value, str) and value.strip().lower() in ("", "null", "none", "nan", "na", "n/a", "-", "--", "未提供", "无"):
        return True
    return False

def check_encoding_issues(value):
    """检查编码问题"""
    if not isinstance(value, str):
        return False
    # 常见乱码特征
    patterns = [
        r'[\ufffd]',          # Unicode replacement character
        r'â€', r'Â',        # UTF-8 decoded as Latin-1
        r'\x00',             # NULL bytes
        r'[锟斤拷烫烫屯]+',  # GBK乱码
    ]
    for p in patterns:
        if re.search(p, value):
            return True
    return False

def check_html_artifacts(value):
    """检查HTML残留"""
    if not isinstance(value, str):
        return False
    html_patterns = [
        r'<[^>]{3,}>',       # HTML tags
        r'&[a-z]+;',         # HTML entities like &amp; &nbsp;
        r'&#\d+;',           # Numeric entities
    ]
    for p in html_patterns:
        if re.search(p, value):
            return True
    return False

def analyze_table(json_data):
    """分析单张表的数据质量"""
    db = json_data["database"]
    table = json_data["table"]
    category = json_data["category"]
    rows = json_data["data"]
    columns = json_data["columns"]
    
    result = {
        "db": db, "table": table, "category": category,
        "total_rows": len(rows), "total_cols": len(columns),
        "issues": [],
        "null_stats": {},
        "field_completeness": {},
    }
    
    # 1. 空值/NULL统计
    null_counts = {col: 0 for col in columns}
    for row in rows:
        for col in columns:
            if check_null_empty(row.get(col)):
                null_counts[col] += 1
    
    for col, count in null_counts.items():
        ratio = count / len(rows) if rows else 0
        result["null_stats"][col] = {"count": count, "ratio": round(ratio, 3)}
        if ratio > 0.5:
            result["issues"].append(f"⚠ 字段 '{col}' 空值率过高: {count}/{len(rows)} ({ratio:.0%})")
        elif ratio > 0.2:
            result["issues"].append(f"  字段 '{col}' 空值率: {count}/{len(rows)} ({ratio:.0%})")
    
    # 2. 完全重复行检测
    row_strs = [json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows]
    duplicates = len(row_strs) - len(set(row_strs))
    if duplicates > 0:
        result["issues"].append(f"⚠ 发现 {duplicates} 行完全重复")
    
    # 3. 编码问题
    encoding_issues = 0
    for row in rows:
        for col in columns:
            val = row.get(col)
            if check_encoding_issues(val):
                encoding_issues += 1
    if encoding_issues > 0:
        result["issues"].append(f"⚠ 发现 {encoding_issues} 个字段值存在编码乱码")
    
    # 4. HTML残留
    html_issues = 0
    html_examples = []
    for row in rows:
        for col in columns:
            val = row.get(col)
            if check_html_artifacts(val):
                html_issues += 1
                if len(html_examples) < 3:
                    snippet = str(val)[:100]
                    html_examples.append(f"    示例 [{col}]: {snippet}...")
    if html_issues > 0:
        result["issues"].append(f"⚠ 发现 {html_issues} 个字段值包含HTML标签/实体")
        result["issues"].extend(html_examples)
    
    # 5. 字段长度分布
    len_stats = {}
    for col in columns:
        lengths = [len(str(row.get(col, ""))) for row in rows if row.get(col) is not None]
        if lengths:
            len_stats[col] = {
                "min": min(lengths), "max": max(lengths),
                "avg": round(sum(lengths)/len(lengths)),
                "empty_count": null_counts[col]
            }
    result["field_completeness"] = len_stats
    
    # 6. 空行检测（所有字段均为空）
    empty_rows = 0
    for i, row in enumerate(rows):
        all_empty = all(check_null_empty(row.get(col)) for col in columns)
        if all_empty:
            empty_rows += 1
    if empty_rows > 0:
        result["issues"].append(f"⚠ 发现 {empty_rows} 行全部字段为空")
    
    # 7. 关键字段检查（标题/正文类字段的完整度）
    key_text_fields = ["title", "content", "项目名称", "项目名称", "content",
                       "项目名称", "project_name", "doc_type", "law_name"]
    for field in key_text_fields:
        if field in columns:
            non_empty = sum(1 for r in rows if not check_null_empty(r.get(field)))
            ratio = non_empty / len(rows) if rows else 0
            if ratio < 0.8:
                result["issues"].append(f"⚠ 关键字段 '{field}' 仅 {ratio:.0%} 有值")
    
    # 8. 特殊字符/不可见字符
    special_char_count = 0
    for row in rows:
        for col in columns:
            val = row.get(col)
            if isinstance(val, str):
                # 检查连续空白、tab混入等
                if re.search(r'\t', val) or re.search(r'  {3,}', val):
                    special_char_count += 1
    if special_char_count > 0:
        result["issues"].append(f"  发现 {special_char_count} 个字段值含Tab或多余空白")
    
    return result


# ========== 主流程 ==========
print("=" * 70)
print("  数据质量分析报告")
print("=" * 70)

# 找到所有单表JSON文件
json_files = [f for f in os.listdir(SAMPLE_DIR) 
              if f.endswith(".json") and not f.startswith("_merged")]

all_results = []
total_issues = 0
total_warnings = 0

for jf in sorted(json_files):
    path = os.path.join(SAMPLE_DIR, jf)
    data = load_json(path)
    result = analyze_table(data)
    all_results.append(result)

print(f"\n{'─'*70}")
for r in all_results:
    issue_count = len([i for i in r["issues"] if i.startswith("⚠")])
    total_issues += issue_count
    total_warnings += len(r["issues"])
    
    print(f"\n  【{r['db']}.{r['table']}】 {r['total_rows']}行 × {r['total_cols']}列 [{r['category']}]")
    
    if r["issues"]:
        for issue in r["issues"]:
            print(f"    {issue}")
    else:
        print("    ✓ 未发现明显问题")
    
    # 空值概况
    high_null_fields = [(col, stats) for col, stats in r["null_stats"].items() 
                        if stats["count"] > 0]
    if high_null_fields:
        print("    空值字段: ", end="")
        parts = []
        for col, stats in sorted(high_null_fields, key=lambda x: -x[1]["count"]):
            parts.append(f"{col}({stats['count']})")
        print(", ".join(parts))

print(f"\n{'='*70}")
print("  汇总统计")
print(f"{'='*70}")
print(f"  检查表数: {len(all_results)}")
print(f"  严重问题(⚠): {total_issues}")
print(f"  总提示数: {total_warnings}")

# ========== 给出清洗建议 ==========
print(f"\n{'='*70}")
print("  清洗建议")
print(f"{'='*70}")

if total_issues == 0:
    print("\n  ✅ 整体数据质量良好，Demo阶段可直接使用！")
    print("     如需进一步提升质量，建议关注以下方面：")

suggestions = []

# 分析各表具体问题
for r in all_results:
    table_key = f"{r['db']}.{r['table']}"
    
    # 高空值率字段
    high_null = [(col, stats) for col, stats in r["null_stats"].items() 
                 if stats["ratio"] > 0.5]
    if high_null:
        cols = [c[0] for c in high_null]
        suggestions.append(f"  [{table_key}] 建议移除高空值字段: {', '.join(cols)}")
    
    # HTML问题
    has_html = any("HTML" in i for i in r["issues"])
    if has_html:
        suggestions.append(f"  [{table_key}] 建议清洗HTML标签和实体")

if suggestions:
    print("\n  具体建议:")
    for s in suggestions:
        print(f"    {s}")

print(f"\n{'='*70}")
