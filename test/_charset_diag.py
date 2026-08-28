"""临时诊断：判断云端 MySQL 中文数据是否已损坏（存成 '?'）。

方法：
  1. 取 company_name 的 HEX 原始字节 — 若为 3F3F3F... 则服务端数据已损坏。
  2. 查服务端字符集变量。
  3. 交叉验证 Milvus mysql_price_semantic 的 text 字段内容。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import pymysql
from pymilvus import MilvusClient

conn = pymysql.connect(
    host=os.getenv("MYSQL_HOST"),
    port=int(os.getenv("MYSQL_PORT", "3306")),
    user=os.getenv("MYSQL_USER"),
    password=os.getenv("MYSQL_PASSWORD"),
    database=os.getenv("MYSQL_CLEAN_DB", "ztb_clean"),
    charset="utf8mb4",
)
cur = conn.cursor()

print("== 服务端字符集变量 ==")
cur.execute(
    "SHOW VARIABLES WHERE Variable_name IN "
    "('character_set_server','character_set_database','character_set_client','character_set_connection','collation_server')"
)
for row in cur.fetchall():
    print("  ", row[0], "=", row[1])

print("\n== company_name HEX 采样 ==")
cur.execute("SELECT company_name, HEX(company_name) FROM company_info LIMIT 3")
for name, hexval in cur.fetchall():
    print(f"  name={name!r}  hex={str(hexval)[:60]}")

print("\n== 表/列字符集 ==")
cur.execute(
    "SELECT COLUMN_NAME, CHARACTER_SET_NAME, COLLATION_NAME "
    "FROM information_schema.COLUMNS "
    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME='company_info' AND COLUMN_NAME='company_name'",
    (os.getenv("MYSQL_CLEAN_DB", "ztb_clean"),),
)
for row in cur.fetchall():
    print("  ", row)

print("\n== Milvus mysql_price_semantic text 采样 ==")
client = MilvusClient(uri=f"http://{os.getenv('MILVUS_HOST')}:{os.getenv('MILVUS_PORT', '19530')}")
res = client.query(
    "mysql_price_semantic",
    filter='source_table == "company_info"',
    output_fields=["text", "source_id"],
    limit=3,
)
for r in res:
    print(f"  id={r.get('source_id')} text={r.get('text')[:80]!r}")

print("\n== Milvus public_kb text 采样 ==")
res = client.query(
    "public_kb",
    filter="",
    output_fields=["text", "doc_name"],
    limit=2,
)
for r in res:
    print(f"  doc={r.get('doc_name')!r} text={r.get('text')[:80]!r}")
