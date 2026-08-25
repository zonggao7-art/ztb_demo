"""步骤2: MySQL连接测试"""
import os
import pymysql

config = {
    "host": "192.168.10.120",
    "user": "iflytek",
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "port": 3306,
    "charset": "utf8mb4",
    "connect_timeout": 10,
}

try:
    conn = pymysql.connect(**config)
    print("MySQL connected OK")
    cur = conn.cursor()
    cur.execute("SELECT VERSION()")
    print("MySQL version:", cur.fetchone()[0])
    cur.execute("SHOW VARIABLES LIKE 'ngram_token_size'")
    result = cur.fetchone()
    if result:
        print(f"ngram_token_size: {result[1]}")
        if result[1] != '2':
            print("WARNING: ngram_token_size should be 2 for Chinese word segmentation!")
    else:
        print("WARNING: ngram_token_size variable not found (check MySQL version >= 5.7.6)")
    cur.close()
    conn.close()
except Exception as e:
    print(f"MySQL connection FAILED: {e}")
