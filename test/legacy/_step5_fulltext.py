"""步骤5: 构建FULLTEXT索引"""
import os
import pymysql

conn = pymysql.connect(
    host="127.0.0.1", user="root", password=os.getenv("MYSQL_PASSWORD", ""),
    port=3306, database="ztb_clean", charset="utf8mb4"
)
cur = conn.cursor()

# Check ngram_token_size
cur.execute("SHOW VARIABLES LIKE 'ngram_token_size'")
result = cur.fetchone()
print(f"ngram_token_size = {result[1] if result else 'NOT SET'}")
if result and result[1] != '2':
    print("WARNING: ngram_token_size should be 2! Check my.cnf.")

fulltext_indexes = [
    ("company_info", "ft_company_info",
     "`company_name`, `business_scope`, `industry`, `address`"),
    ("company_penalty", "ft_penalty",
     "`company_name`, `illegal_behavior`, `penalty_result`"),
    ("product_info", "ft_product",
     "`product_name`, `supplier_name`, `product_parameters`, `category`"),
    ("bid_project", "ft_bid_project",
     "`project_name`, `purchaser`, `successful_bidder`, `subject_matter`"),
]

for table, idx_name, columns in fulltext_indexes:
    sql = f"ALTER TABLE `{table}` ADD FULLTEXT INDEX `{idx_name}` ({columns}) WITH PARSER ngram"
    print(f"Building FULLTEXT on {table}...")
    try:
        cur.execute(sql)
        print(f"  OK: {idx_name} created")
    except Exception as e:
        # Check if already exists
        if "Duplicate key name" in str(e):
            print(f"  SKIP: {idx_name} already exists")
        else:
            print(f"  ERROR: {e}")

conn.commit()
cur.close()
conn.close()
print("\nFULLTEXT index build complete.")
