"""为 price_inquiry 涉及的表创建 FULLTEXT 索引（中文 ngram 解析器）。

运行前请确认 MySQL 已配置 ngram 解析器：
    [mysqld]
    ngram_token_size=2
    ft_min_word_len=1
"""

from __future__ import annotations

import re
import sys

sys.path.insert(0, "..")

import os


DB_CONFIG = {
    "host": "192.168.10.120",
    "user": "iflytek",
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "port": 3306,
    "charset": "utf8mb4",
    "connect_timeout": 30,
    "read_timeout": 300,
}

_PRICE_DBS = [
    "xunfei_202605_01",
    "bidding_information_dai",
    "xunfei5",
    "xunfei_06",
    "tm",
]

_SEMANTIC_PATTERNS = [
    "title", "name", "tender_title", "project_name", "material_name",
    "content", "article_text", "rule_title", "procurement_title",
    "标题", "项目名称", "标的物", "采购内容", "公告内容", "项目内容",
    "产品标题", "产品内容描述", "公司名称", "company_name",
]


def _is_semantic(name: str) -> bool:
    return any(p.lower() in name.lower() for p in _SEMANTIC_PATTERNS)


from _diag_common import get_connection


def generate_ddl(database: str) -> list[tuple[str, str, list[str]]]:
    """生成 (table, index_name, columns) 列表。"""
    conn = get_connection(database, db_config=DB_CONFIG)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA=%s AND TABLE_ROWS > 0 ORDER BY TABLE_ROWS DESC",
                (database,),
            )
            tables = [row[0] for row in cur.fetchall()]
            ddls = []
            for table in tables:
                cur.execute(
                    "SELECT COLUMN_NAME, COLUMN_TYPE FROM information_schema.COLUMNS "
                    "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s ORDER BY ORDINAL_POSITION",
                    (database, table),
                )
                semantic_cols = []
                for col_name, col_type in cur.fetchall():
                    if _is_semantic(col_name) and col_type.lower().startswith(("varchar", "text", "longtext", "mediumtext", "tinytext")):
                        semantic_cols.append(col_name)
                if not semantic_cols:
                    continue
                # 限制索引列数，优先前 3 个语义列
                semantic_cols = semantic_cols[:3]
                index_name = f"ft_{table}_{'_'.join(semantic_cols)}"[:64]
                # 清理索引名中不适合的字符
                index_name = re.sub(r"[^a-zA-Z0-9_]", "_", index_name)
                ddls.append((table, index_name, semantic_cols))
            return ddls
    finally:
        conn.close()


def main(*, dry_run: bool = False) -> None:
    for db in _PRICE_DBS:
        print(f"-- Database: {db}")
        try:
            ddl_list = generate_ddl(db)
        except Exception as e:
            print(f"-- ERROR generating DDL for {db}: {e}")
            continue

        if not ddl_list:
            print("--   no semantic columns found; skip")
            continue

        for table, index_name, cols in ddl_list:
            col_sql = ", ".join(f"`{c}`" for c in cols)
            sql = (
                f"ALTER TABLE `{db}`.`{table}` "
                f"ADD FULLTEXT INDEX `{index_name}` ({col_sql}) WITH PARSER ngram;"
            )
            print(f"{sql}")
            if not dry_run:
                conn = get_connection(db, db_config=DB_CONFIG)
                try:
                    with conn.cursor() as cur:
                        cur.execute(sql)
                    conn.commit()
                    print("--   created OK")
                except Exception as e:
                    print(f"--   create failed: {e}")
                finally:
                    conn.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只打印 DDL 不执行")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
