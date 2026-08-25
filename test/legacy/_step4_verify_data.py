"""步骤4验证: 数据完整性 + 注册资本样本检查"""
import os
import pymysql

conn = pymysql.connect(
    host="127.0.0.1", user="root", password=os.getenv("MYSQL_PASSWORD", ""),
    port=3306, database="ztb_clean", charset="utf8mb4"
)
cur = conn.cursor()
cur.execute("SELECT COUNT(*) FROM company_info")
print(f"company_info: {cur.fetchone()[0]} rows")

cur.execute("SELECT registered_capital, registered_capital_amount_cny FROM company_info WHERE registered_capital_amount_cny IS NOT NULL ORDER BY registered_capital_amount_cny DESC LIMIT 5")
print("\n注册资本 TOP 5 (按数值):")
for rc, amt in cur.fetchall():
    print(f"  {rc:<30s} → {amt:>20,.0f}")

cur.execute("SELECT registered_capital, registered_capital_amount_cny FROM company_info WHERE registered_capital_amount_cny IS NOT NULL ORDER BY registered_capital_amount_cny ASC LIMIT 5")
print("\n注册资本 BOTTOM 5 (按数值):")
for rc, amt in cur.fetchall():
    print(f"  {rc:<30s} → {amt:>20,.0f}")

cur.execute("SELECT COUNT(*) FROM company_info WHERE registered_capital_amount_cny IS NULL")
print(f"\nregistered_capital_amount_cny NULL rows: {cur.fetchone()[0]}")

# Sample some NULL rows
cur.execute("SELECT company_name, registered_capital FROM company_info WHERE registered_capital_amount_cny IS NULL LIMIT 5")
print("NULL samples:")
for name, rc in cur.fetchall():
    print(f"  {name} | [{rc}]")

cur.close()
conn.close()
