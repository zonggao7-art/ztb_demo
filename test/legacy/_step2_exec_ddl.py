"""步骤2: 执行DDL — 创建ztb_clean数据库及4张表"""
import os
import pymysql

config = {
    "host": "127.0.0.1",
    "user": "root",
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "port": 3306,
    "charset": "utf8mb4",
    "connect_timeout": 10,
}

conn = pymysql.connect(**config)
print("MySQL connected OK")

with open(r'd:\DEMO\zhaotoubiao_demo\scripts\schema.sql', 'r', encoding='utf-8') as f:
    sql_content = f.read()

# Remove comment lines first, then split by semicolons
lines = sql_content.split('\n')
clean_lines = [l for l in lines if not l.strip().startswith('--')]
clean_sql = '\n'.join(clean_lines)

statements = []
for stmt in clean_sql.split(';'):
    stmt = stmt.strip()
    if not stmt:
        continue
    statements.append(stmt)

print(f"Found {len(statements)} SQL statements to execute")

cur = conn.cursor()
for i, stmt in enumerate(statements):
    try:
        cur.execute(stmt)
        # Print first 60 chars of statement
        preview = stmt.replace('\n', ' ')[:80]
        print(f"  [{i+1}/{len(statements)}] OK: {preview}...")
    except Exception as e:
        preview = stmt.replace('\n', ' ')[:80]
        print(f"  [{i+1}/{len(statements)}] ERROR: {preview}... => {e}")

conn.commit()

# Verify
cur.execute("SHOW TABLES IN ztb_clean")
tables = [t[0] for t in cur.fetchall()]
print(f"\nTables in ztb_clean: {tables}")

cur.execute("SHOW CREATE TABLE ztb_clean.company_info")
print("\ncompany_info DDL (first 300 chars):")
print(cur.fetchone()[1][:300])

cur.close()
conn.close()
print("\nDDL execution complete.")
