"""步骤5b+步骤6: 验证所有索引"""
import os
import pymysql

conn = pymysql.connect(
    host="127.0.0.1", user="root", password=os.getenv("MYSQL_PASSWORD", ""),
    port=3306, database="ztb_clean", charset="utf8mb4"
)
cur = conn.cursor()

# ── 步骤5b：确认 registered_capital_amount_cny 索引 ──
print("=== 步骤5b: 注册资本数值列索引 ===")
cur.execute("SHOW INDEX FROM company_info WHERE Key_name = 'idx_registered_capital_amount'")
rows = cur.fetchall()
if rows:
    for r in rows:
        print(f"  Index: {r[2]}, Column: {r[4]}, Type: {r[10]}")
else:
    print("  NOT FOUND — creating...")
    cur.execute("ALTER TABLE company_info ADD INDEX idx_registered_capital_amount (registered_capital_amount_cny)")
    conn.commit()
    print("  CREATED")

# ── 步骤6：EXPLAIN 验证 ──
print("\n=== 步骤6: EXPLAIN 索引验证 ===")

tests = [
    ("BTREE: province", "EXPLAIN SELECT * FROM company_info WHERE province = '广东'", "idx_province"),
    ("注册资本 range", "EXPLAIN SELECT * FROM company_info WHERE registered_capital_amount_cny BETWEEN 1000000 AND 100000000", "idx_registered_capital_amount"),
    ("FULLTEXT: company", "EXPLAIN SELECT * FROM company_info WHERE MATCH(company_name, business_scope, industry, address) AGAINST('科技' IN BOOLEAN MODE)", "ft_company_info"),
    ("FULLTEXT: product", "EXPLAIN SELECT * FROM product_info WHERE MATCH(product_name, supplier_name, product_parameters, category) AGAINST('防水涂料' IN BOOLEAN MODE)", "ft_product"),
    ("FULLTEXT: bidding", "EXPLAIN SELECT * FROM bid_project WHERE MATCH(project_name, purchaser, successful_bidder, subject_matter) AGAINST('福建师范大学' IN BOOLEAN MODE)", "ft_bid_project"),
    ("FULLTEXT: penalty", "EXPLAIN SELECT * FROM company_penalty WHERE MATCH(company_name, illegal_behavior, penalty_result) AGAINST('餐饮' IN BOOLEAN MODE)", "ft_penalty"),
    ("UNIQUE: credit_code", "EXPLAIN SELECT * FROM company_info WHERE credit_code = '91330100MA2TEST'", "uk_credit_code"),
    ("UNIQUE: project_number", "EXPLAIN SELECT * FROM bid_project WHERE project_number = 'TEST001'", "uk_project_number"),
]

for label, sql, expected_key in tests:
    cur.execute(sql)
    result = cur.fetchone()
    key = result[5] if len(result) > 5 else "N/A"
    rtype = result[1] if len(result) > 1 else "N/A"
    status = "OK" if key == expected_key else "MISS"
    print(f"  [{status}] {label}: key={key}, type={rtype} (expected: {expected_key})")

# ── 功能验证：执行真实查询 ──
print("\n=== 功能验证: 真实查询测试 ===")

cur.execute("SELECT company_name, registered_capital_amount_cny FROM company_info WHERE registered_capital_amount_cny BETWEEN 1000000 AND 100000000 ORDER BY registered_capital_amount_cny DESC LIMIT 3")
print("注册资本 100万~1亿 范围查询:")
for name, amt in cur.fetchall():
    print(f"  {name}: {amt:,.0f}")

cur.execute("SELECT product_name, price FROM product_info WHERE MATCH(product_name, supplier_name, product_parameters, category) AGAINST('防水涂料' IN BOOLEAN MODE) LIMIT 3")
print("\nFULLTEXT '防水涂料':")
for name, price in cur.fetchall():
    print(f"  {name}: {price}")

cur.close()
conn.close()
print("\n索引验证完成.")
