"""最终验证：数据库状态总览"""
import os
import pymysql

conn = pymysql.connect(
    host="127.0.0.1", user="root", password=os.getenv("MYSQL_PASSWORD", ""),
    port=3306, database="ztb_clean", charset="utf8mb4"
)
cur = conn.cursor()
print("===== ztb_clean 数据库最终状态 =====")
cur.execute(
    "SELECT TABLE_NAME, TABLE_ROWS, "
    "ROUND(DATA_LENGTH/1024/1024,2) AS data_mb, "
    "ROUND(INDEX_LENGTH/1024/1024,2) AS idx_mb "
    "FROM information_schema.TABLES WHERE TABLE_SCHEMA='ztb_clean' "
    "ORDER BY TABLE_NAME"
)
for t in cur.fetchall():
    print(f"  {t[0]:<20s} rows={t[1]:>6d}  data={t[2]:>8.2f}MB  idx={t[3]:>8.2f}MB")

# Index summary
cur.execute(
    "SELECT TABLE_NAME, COUNT(*) AS idx_count "
    "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA='ztb_clean' "
    "GROUP BY TABLE_NAME ORDER BY TABLE_NAME"
)
print("\nIndex count per table:")
for t in cur.fetchall():
    print(f"  {t[0]:<20s} {t[1]} indexes")

# FULLTEXT indexes
cur.execute(
    "SELECT TABLE_NAME, INDEX_NAME, INDEX_TYPE "
    "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA='ztb_clean' "
    "AND INDEX_TYPE='FULLTEXT'"
)
print("\nFULLTEXT indexes:")
for t in cur.fetchall():
    print(f"  {t[0]}.{t[1]} ({t[2]})")

# registered_capital_amount_cny stats
cur.execute(
    "SELECT COUNT(*) AS total, "
    "COUNT(registered_capital_amount_cny) AS parsed, "
    "ROUND(MIN(registered_capital_amount_cny),0) AS min_val, "
    "ROUND(MAX(registered_capital_amount_cny),0) AS max_val "
    "FROM company_info"
)
r = cur.fetchone()
print("\nregistered_capital_amount_cny stats:")
print(f"  Total: {r[0]}, Parsed: {r[1]}, NULL: {r[0]-r[1]}")
print(f"  Range: {r[2]:,.0f} ~ {r[3]:,.0f}")

cur.close()
conn.close()
print("\nFinal verification complete.")
