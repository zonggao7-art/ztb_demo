"""
招投标RAG样本筛选工具 - 扫描所有数据库的表名/注释/列名，筛选相关表
"""
import pymysql
import json
import os
from datetime import datetime, date
from decimal import Decimal

DB_CONFIG = {
    "host": "192.168.10.120",
    "user": "iflytek",
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "port": 3306,
    "charset": "utf8mb4",
    "connect_timeout": 30,
    "read_timeout": 60,
}

OUTPUT_DIR = "db_explore_output"

def get_connection(database=None):
    config = DB_CONFIG.copy()
    if database:
        config["database"] = database
    return pymysql.connect(**config)

# ========== 第一步：扫描所有表 ==========
print("=" * 70)
print("  扫描所有数据库的表名和注释")
print("=" * 70)

conn = get_connection()
all_tables = []
with conn.cursor() as cursor:
    cursor.execute("""
        SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_ROWS, 
               ROUND(DATA_LENGTH/1024/1024, 2) AS SIZE_MB,
               TABLE_COMMENT
        FROM information_schema.TABLES
        WHERE TABLE_SCHEMA NOT IN ('information_schema','mysql','performance_schema','sys')
        ORDER BY TABLE_SCHEMA, TABLE_NAME
    """)
    for row in cursor.fetchall():
        all_tables.append({
            "db": str(row[0]),
            "table": str(row[1]),
            "rows": int(row[2] or 0),
            "size_mb": float(row[3] or 0),
            "comment": str(row[4] or "")
        })
conn.close()

print(f"  共发现 {len(all_tables)} 张表")

# ========== 第二步：关键词匹配 ==========
KEYWORDS = {
    "法律法规": ["法律", "法规", "法", "law", "legal", "rule", "regulation", "条例", "规章", "政策"],
    "采购招投标": ["采购", "招标", "投标", "中标", "公告", "bid", "tender", "procurement", 
                  "采购方式", "竞争性", "询价", "磋商", "开标", "评标", "废标", "资质",
                  "供应商", "报价", "合同", "项目", "预算", "金额"],
    "市场产品信息": ["市场", "产品", "商品", "价格", "commodity", "product", "market",
                   "信息", "资讯", "行业", "分类", "类别"],
    "RAG知识文档": ["文档", "document", "knowledge", "知识", "rag", "embedding", "向量",
                  "chunk", "片段", "段落", "content", "正文"],
}

results = {}
for category, keywords in KEYWORDS.items():
    results[category] = []

for t in all_tables:
    name_lower = t["table"].lower()
    comment = t["comment"]
    for category, keywords in KEYWORDS.items():
        matched = [kw for kw in keywords if kw.lower() in name_lower or kw in comment]
        if matched:
            results[category].append({**t, "matched_keywords": matched})

# ========== 第三步：输出匹配结果 ==========
print(f"\n{'=' * 70}")
print("  关键词匹配结果")
print("=" * 70)

for category, tables in results.items():
    print(f"\n--- {category} ({len(tables)} 张匹配) ---")
    if not tables:
        print("  (无匹配)")
        continue
    # 按行数降序，取前10
    sorted_tables = sorted(tables, key=lambda x: x["rows"], reverse=True)
    for t in sorted_tables[:10]:
        print(f"  {t['db']}.{t['table']:<40s} {t['rows']:>10,} 行  {t['size_mb']:>8.2f} MB  匹配: {t['matched_keywords']}  注释: {t['comment'][:50]}")

# ========== 第四步：列出所有表名（便于人工检查） ==========
print(f"\n{'=' * 70}")
print("  所有表名一览 (按数据库分组)")
print("=" * 70)

from collections import defaultdict
db_tables = defaultdict(list)
for t in all_tables:
    db_tables[t["db"]].append(t)

for db, tables in sorted(db_tables.items()):
    print(f"\n  [{db}] ({len(tables)} 张表)")
    for t in tables:
        comment_str = f"  ({t['comment']})" if t['comment'] else ""
        print(f"    {t['table']:<45s} {t['rows']:>10,} 行  {t['size_mb']:>8.2f} MB{comment_str}")

# ========== 第五步：保存完整扫描结果 ==========
def json_serializer(obj):
    if isinstance(obj, (datetime, date)):
        return obj.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(obj, Decimal):
        return float(obj)
    return str(obj)

scan_path = os.path.join(OUTPUT_DIR, "full_table_scan.json")
with open(scan_path, "w", encoding="utf-8") as f:
    json.dump({
        "all_tables": all_tables,
        "matched": results,
        "total_tables": len(all_tables),
        "scan_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }, f, ensure_ascii=False, indent=2, default=json_serializer)

print(f"\n\n完整扫描结果已保存: {scan_path}")
