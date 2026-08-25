"""MySQL EXPLAIN ANALYZE 检查工具

用法示例：
    python test/explain_sql.py --db bidding_information_dai --sql "SELECT * FROM companies WHERE company_name LIKE '%沙发%' LIMIT 5"

会自动标记全表扫描 (type=ALL) 与 Using filesort 等性能杀手。
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

import os

import pymysql

DB_CONFIG = {
    "host": "192.168.10.120",
    "user": "iflytek",
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "port": 3306,
    "charset": "utf8mb4",
    "connect_timeout": 30,
    "read_timeout": 300,
}


def _format_sql_for_explain(raw_sql: str, params: tuple | list) -> str:
    """为 EXPLAIN ANALYZE 生成可执行的 SQL（将占位符替换为转义后的字面量）。"""
    sql = raw_sql.strip()
    if not params:
        return sql
    # 简单替换 %s；实际使用请保证参数已转义
    parts = sql.split("%s")
    if len(parts) - 1 != len(params):
        return sql
    result = []
    for i, part in enumerate(parts[:-1]):
        val = params[i]
        if isinstance(val, str):
            escaped = val.replace("\\", "\\\\").replace("'", "''")
            literal = f"'{escaped}'"
        elif val is None:
            literal = "NULL"
        else:
            literal = str(val)
        result.append(part)
        result.append(literal)
    result.append(parts[-1])
    return "".join(result)


def _run_explain(conn: pymysql.Connection, sql: str) -> list[dict[str, Any]]:
    """执行 EXPLAIN ANALYZE 并返回字典列表。"""
    with conn.cursor() as cur:
        cur.execute(f"EXPLAIN ANALYZE {sql}")
        rows = cur.fetchall()
        columns = [desc[0] for desc in cur.description]
        return [dict(zip(columns, row)) for row in rows]


def _detect_problems(rows: list[dict[str, Any]]) -> list[str]:
    """识别性能问题。"""
    problems: list[str] = []
    for row in rows:
        text = " ".join(str(v) for v in row.values() if v is not None).lower()
        # 全表扫描 / 索引扫描无法覆盖时
        if "table scan" in text or re.search(r"type[\s:]*all", text):
            problems.append("全表扫描 (Table scan / type=ALL)")
        # 在找到行后仍需要过滤（LIKE '%x%' 导致）
        if "filter:" in text:
            problems.append("回表/过滤 (Filter)")
        if "filesort" in text:
            problems.append("Using filesort")
        if "using temporary" in text:
            problems.append("Using temporary")
    return problems


def explain_sql(database: str, sql: str, params: tuple = ()) -> dict[str, Any]:
    """对单条 SQL 执行 EXPLAIN ANALYZE 并返回结构化结果。"""
    executable = _format_sql_for_explain(sql, params)
    conn = pymysql.connect(**DB_CONFIG, database=database)
    try:
        rows = _run_explain(conn, executable)
        problems = _detect_problems(rows)
        return {
            "database": database,
            "original_sql": sql,
            "params": params,
            "explained_sql": executable,
            "explain_rows": rows,
            "problems": problems,
            "is_clean": not problems,
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="MySQL EXPLAIN ANALYZE 诊断工具")
    parser.add_argument("--db", required=True, help="目标数据库名")
    parser.add_argument("--sql", required=True, help="待分析的 SQL（可包含 %s 占位符）")
    parser.add_argument("--param", nargs="*", default=[], help="SQL 占位符参数")
    parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")
    args = parser.parse_args()

    params = tuple(args.param)
    result = explain_sql(args.db, args.sql, params)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        print("=" * 80)
        print(f"数据库: {result['database']}")
        print(f"原始 SQL: {result['original_sql']}")
        print(f"参数: {result['params']}")
        print("-" * 80)
        print("EXPLAIN ANALYZE 结果:")
        for row in result["explain_rows"]:
            print(json.dumps(row, ensure_ascii=False, default=str))
        print("-" * 80)
        if result["problems"]:
            print("发现性能问题:")
            for p in result["problems"]:
                print(f"  ⚠ {p}")
        else:
            print("未发现明显性能问题")


if __name__ == "__main__":
    main()
