"""
导出RAG Demo样本数据 - 10张表共499行
输出: JSON (RAG友好) + CSV (便于查看)
"""
import pymysql
import json
import csv
import os
from datetime import datetime, date
from decimal import Decimal

DB_CONFIG = {
    "host": "192.168.10.120", "user": "iflytek", "password": os.getenv("MYSQL_PASSWORD", ""),
    "port": 3306, "charset": "utf8mb4", "connect_timeout": 30, "read_timeout": 120,
}

OUTPUT_DIR = "sample_export"
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 推荐样本清单: (数据库, 表名, 抽样行数, 类别)
SAMPLES = [
    # === 市场/产品/政策信息 ===
    ("xunfei888", "市场产品信息", 50, "市场产品信息"),
    ("xunfei_06", "product", 50, "市场产品信息"),
    ("xunfei888", "上海政策信息", 39, "市场产品信息"),  # 全抽
    # === 采购招投标 ===
    ("xunfei888", "政府招标信息", 50, "采购招投标"),
    ("lin_gang_6_ju_tou_1", "ods_tender", 80, "采购招投标"),
    ("xunfei_06", "tender", 50, "采购招投标"),
    # === 法律法规 ===
    ("xunfei888", "法律法规", 50, "法律法规"),
    ("xunfei_06", "laws", 30, "法律法规"),
    ("relissc_rag", "illegal_behavior_record", 50, "法律法规"),
    ("xunfei5", "ods_policy_regulation_files", 50, "法律法规"),
]


def json_serializer(obj):
    if isinstance(obj, (datetime, date)):
        return obj.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, bytes):
        return f"<BLOB:{len(obj)} bytes>"
    if obj is None:
        return None
    return str(obj)


def export_table(db_name, table_name, limit, category):
    """导出单张表的数据到JSON和CSV"""
    print(f"  导出 {db_name}.{table_name} (LIMIT {limit}) ...")

    conn = pymysql.connect(**DB_CONFIG, database=db_name)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM `{table_name}` LIMIT {limit}")
            col_names = [desc[0] for desc in cur.description]
            rows = cur.fetchall()

        print(f"    ✓ 获取 {len(rows)} 行, {len(col_names)} 列")

        # 转为字典列表
        records = []
        for row in rows:
            record = {}
            for col, val in zip(col_names, row):
                if isinstance(val, bytes):
                    val = f"<BLOB:{len(val)} bytes>"
                elif isinstance(val, (datetime, date)):
                    val = val.strftime("%Y-%m-%d %H:%M:%S")
                elif isinstance(val, Decimal):
                    val = float(val)
                record[col] = val
            records.append(record)

        safe_table_name = table_name.replace(".", "_")
        file_prefix = f"{db_name}__{safe_table_name}"

        # --- 保存 JSON ---
        json_path = os.path.join(OUTPUT_DIR, f"{file_prefix}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({
                "database": db_name,
                "table": table_name,
                "category": category,
                "row_count": len(records),
                "columns": col_names,
                "data": records
            }, f, ensure_ascii=False, indent=2, default=json_serializer)

        # --- 保存 CSV ---
        csv_path = os.path.join(OUTPUT_DIR, f"{file_prefix}.csv")
        with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=col_names)
            writer.writeheader()
            for record in records:
                # CSV中截断过长文本
                for k, v in record.items():
                    if isinstance(v, str) and len(v) > 500:
                        record[k] = v[:500] + "...[截断]"
                writer.writerow(record)

        return {
            "db": db_name, "table": table_name, "category": category,
            "rows": len(records), "columns": len(col_names),
            "json": json_path, "csv": csv_path
        }

    except Exception as e:
        print(f"    ✗ 导出失败: {e}")
        return {"db": db_name, "table": table_name, "rows": 0, "error": str(e)}
    finally:
        conn.close()


# ========== 主流程 ==========
print("=" * 60)
print("  RAG Demo 样本数据导出")
print(f"  计划导出 {len(SAMPLES)} 张表")
print(f"  输出目录: {os.path.abspath(OUTPUT_DIR)}/")
print("=" * 60)

results = []
total_rows = 0

for db, tbl, limit, cat in SAMPLES:
    result = export_table(db, tbl, limit, cat)
    results.append(result)
    total_rows += result.get("rows", 0)

# ========== 合并导出：按类别合并JSON ==========
print(f"\n{'─'*60}")
print("  合并按类别导出...")

merged_by_category = {}
for db, tbl, limit, cat in SAMPLES:
    safe_name = f"{db}__{tbl.replace('.', '_')}"
    json_path = os.path.join(OUTPUT_DIR, f"{safe_name}.json")
    if os.path.exists(json_path):
        with open(json_path, "r", encoding="utf-8") as f:
            table_data = json.load(f)
        if cat not in merged_by_category:
            merged_by_category[cat] = []
        merged_by_category[cat].append(table_data)

for cat, tables in merged_by_category.items():
    cat_path = os.path.join(OUTPUT_DIR, f"_merged_{cat}.json")
    total_cat_rows = sum(t["row_count"] for t in tables)
    with open(cat_path, "w", encoding="utf-8") as f:
        json.dump({
            "category": cat,
            "tables": tables,
            "total_rows": total_cat_rows,
            "table_count": len(tables),
        }, f, ensure_ascii=False, indent=2, default=json_serializer)
    print(f"    ✓ {cat}: {len(tables)} 张表, {total_cat_rows} 行 → {cat_path}")

# ========== 汇总报告 ==========
print(f"\n{'='*60}")
print("  导出完成！汇总：")
print(f"{'='*60}")

print(f"\n  {'数据库':<25s} {'表名':<30s} {'行数':>6s} {'类别':<12s}")
print(f"  {'─'*25} {'─'*30} {'─'*6} {'─'*12}")

for r in results:
    if "error" in r:
        print(f"  {r['db']:<25s} {r['table']:<30s} {'失败':>6s} {r['error'][:30]}")
    else:
        print(f"  {r['db']:<25s} {r['table']:<30s} {r['rows']:>6d} {r['category']:<12s}")

print(f"\n  {'─'*75}")
print(f"  {'总计':<55s} {total_rows:>6d} 行")
print("\n  输出文件列表:")
for r in results:
    if "json" in r:
        print(f"    JSON: {r['json']}")
        print(f"    CSV:  {r['csv']}")

print("\n  合并文件:")
for cat in merged_by_category:
    print(f"    _merged_{cat}.json")

print(f"\n{'='*60}")
print(f"  ✅ 全部导出完成！共 {total_rows} 行样本数据")
print(f"  目录: {os.path.abspath(OUTPUT_DIR)}/")
print(f"{'='*60}")
