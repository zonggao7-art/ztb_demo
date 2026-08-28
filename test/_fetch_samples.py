"""临时工具：从云端 ztb_clean 取公司名/项目名样本，写入 _samples.json。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import pymysql

conn = pymysql.connect(
    host=os.getenv("MYSQL_HOST"),
    port=int(os.getenv("MYSQL_PORT", "3306")),
    user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASSWORD"),
    database=os.getenv("MYSQL_CLEAN_DB", "ztb_clean"),
    charset="utf8mb4",
)
cur = conn.cursor()
cur.execute("SELECT company_name FROM company_info LIMIT 5")
companies = [r[0] for r in cur.fetchall()]
cur.execute("SELECT project_name FROM bid_project LIMIT 3")
projects = [r[0] for r in cur.fetchall()]
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_samples.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump({"companies": companies, "projects": projects}, f, ensure_ascii=False, indent=2)
print("written:", out)
