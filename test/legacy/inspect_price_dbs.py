"""价格查询相关数据库的表结构/索引/样本速览"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import pymysql

DB_CONFIG = {
    "host": "192.168.10.120",
    "user": "iflytek",
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "port": 3306,
    "charset": "utf8mb4",
    "connect_timeout": 30,
    "read_timeout": 60,
}

_PRICE_DBS = [
    "xunfei_202605_01",
    "bidding_information_dai",
    "xunfei5",
    "xunfei_06",
    "tm",
]

OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "price_dbs_schema.json")


def get_connection(database: str | None = None) -> pymysql.Connection:
    config = DB_CONFIG.copy()
    if database:
        config["database"] = database
    return pymysql.connect(**config)


def serialize(obj: Any) -> Any:
    if isinstance(obj, (datetime, date)):
        return obj.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, bytes):
        return f"<BLOB:{len(obj)} bytes>"
    return str(obj)


def inspect_db(db_name: str) -> dict[str, Any]:
    print(f"Inspecting {db_name} ...")
    conn = get_connection(db_name)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT TABLE_NAME, TABLE_ROWS, TABLE_COMMENT "
                "FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=%s ORDER BY TABLE_ROWS DESC",
                (db_name,),
            )
            tables = []
            for row in cur.fetchall():
                table_name = row[0]
                tables.append({
                    "name": table_name,
                    "rows": row[1],
                    "comment": row[2],
                })

            for t in tables:
                table_name = t["name"]
                # columns
                cur.execute(
                    "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_KEY, COLUMN_COMMENT "
                    "FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s "
                    "ORDER BY ORDINAL_POSITION",
                    (db_name, table_name),
                )
                t["columns"] = [
                    {
                        "name": r[0],
                        "type": r[1],
                        "nullable": r[2],
                        "key": r[3],
                        "comment": r[4],
                    }
                    for r in cur.fetchall()
                ]
                # indexes
                cur.execute(
                    "SELECT INDEX_NAME, COLUMN_NAME, NON_UNIQUE, SEQ_IN_INDEX, INDEX_TYPE "
                    "FROM information_schema.STATISTICS "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s "
                    "ORDER BY INDEX_NAME, SEQ_IN_INDEX",
                    (db_name, table_name),
                )
                idx: dict[str, Any] = defaultdict(list)
                for r in cur.fetchall():
                    idx[r[0]].append({
                        "column": r[1],
                        "non_unique": bool(r[2]),
                        "seq": r[3],
                        "index_type": r[4],
                    })
                t["indexes"] = dict(idx)
                # sample
                try:
                    cur.execute(f"SELECT * FROM `{table_name}` LIMIT 1")
                    cols = [desc[0] for desc in cur.description]
                    sample = cur.fetchone()
                    t["sample"] = {
                        cols[i]: serialize(sample[i]) for i in range(len(cols))
                    } if sample else {}
                except Exception as e:
                    t["sample"] = {"_error": str(e)}
    finally:
        conn.close()

    return {"database": db_name, "tables": tables}


def main() -> None:
    output: list[dict[str, Any]] = []
    for db_name in _PRICE_DBS:
        try:
            output.append(inspect_db(db_name))
        except Exception as e:
            output.append({"database": db_name, "error": str(e)})

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2, default=serialize)
    print(f"Saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
