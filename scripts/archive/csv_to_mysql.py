#!/usr/bin/env python3
"""
CSV → MySQL 批量导入脚本
将 raw_tables/ 目录下的 4 个 CSV 文件导入 ztb_clean 数据库。

功能：
- 流式读取 CSV（5000 条/批）
- 字段清洗：空值→NULL、金额去千分位、字符串去空白
- 注册资本数值化：正则提取 + 中文单位换算 → registered_capital_amount_cny
- BOM 头自动处理
- INSERT ... ON DUPLICATE KEY UPDATE 去重
- 导入后输出行数统计和 WARNING 计数

用法：
    python scripts/csv_to_mysql.py [--csv-dir raw_tables] [--batch-size 5000]
"""

import csv
import os
import re
import sys
import logging
import argparse
from typing import Optional

import pymysql

# ── 日志配置 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("csv_to_mysql")

# ── MySQL 连接配置 ──
_MYSQL_CONFIG = {
    "host": "127.0.0.1",
    "user": "root",
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "port": 3306,
    "charset": "utf8mb4",
    "connect_timeout": 10,
    "read_timeout": 60,
    "write_timeout": 60,
}

_CLEAN_DB = "ztb_clean"

# ── CSV → 表名映射 ──
TABLE_MAP = {
    "company_info.csv":    "company_info",
    "company_penalty.csv": "company_penalty",
    "product_info.csv":    "product_info",
    "bid_project.csv":     "bid_project",
}

# ── 金额列（需去除千分位逗号后转数值）──
MONEY_COLUMNS = {"price", "budget_amount", "winning_amount", "min_order_qty"}

# ── 注册资本解析 ──
# 匹配模式："500万人民币"、"1000万元"、"1.5亿元"、"200万美元"、"500" 等
_RC_PATTERN = re.compile(
    r"(?P<amount>[\d,]+\.?\d*)\s*"
    r"(?P<unit>亿|万)?\s*"
    r"(?P<currency_suffix>元|人民币|美元|美金|港元|港币|欧元|英镑|日元|韩元)?"
)

# 单位 → 乘数
_UNIT_MULTIPLIER = {
    "亿": 100_000_000,
    "万": 10_000,
}

# 外币标记（无法确定汇率，设为 NULL 并记录 WARNING）
_FOREIGN_CURRENCY = {"美元", "美金", "港元", "港币", "欧元", "英镑", "日元", "韩元"}


def parse_registered_capital(raw: Optional[str]) -> Optional[float]:
    """解析注册资本字符串，统一换算为人民币"元"。

    规则：
    - "500万人民币"  → 5,000,000
    - "1000万元"     → 10,000,000
    - "1.5亿元"      → 150,000,000
    - "6000万美元"   → NULL（外币，记录 WARNING）
    - "500"          → 500（视为"元"）
    - 无法识别       → NULL
    """
    if not raw or not isinstance(raw, str):
        return None

    raw = raw.strip()
    if not raw:
        return None

    m = _RC_PATTERN.match(raw)
    if not m:
        logger.warning("registered_capital parse failed (no match): [%s]", raw)
        return None

    amount_str = m.group("amount")
    unit = m.group("unit")
    currency_suffix = m.group("currency_suffix")

    # 检查是否为外币
    if currency_suffix and currency_suffix in _FOREIGN_CURRENCY:
        logger.warning(
            "registered_capital is foreign currency, set NULL: [%s]", raw
        )
        return None

    # 移除千分位逗号
    amount_str = amount_str.replace(",", "")
    try:
        amount = float(amount_str)
    except ValueError:
        logger.warning(
            "registered_capital amount parse failed: [%s] from [%s]",
            amount_str, raw,
        )
        return None

    # 应用单位乘数
    if unit and unit in _UNIT_MULTIPLIER:
        # 处理"亿元"/"万元"：如果单位后有"元"，乘数不变（"亿元"的"亿"已作为单位）
        amount *= _UNIT_MULTIPLIER[unit]

    return amount


def clean_row(row: dict, table_name: str) -> dict:
    """清洗单行数据。

    - 空字符串 / "N/A" / "-" / "NULL" → None
    - 字符串列：.strip() 去除首尾空白
    - 金额列：移除千分位逗号后转 float
    - 注册资本：解析并写入 registered_capital_amount_cny
    """
    cleaned = {}
    for k, v in row.items():
        # 处理 BOM 头（CSV 第一列可能带 \ufeff）
        clean_k = k.lstrip("\ufeff")

        if v is None:
            cleaned[clean_k] = None
        elif isinstance(v, str) and v.strip() in ("", "N/A", "-", "NULL", "null"):
            cleaned[clean_k] = None
        elif clean_k in MONEY_COLUMNS:
            try:
                cleaned[clean_k] = float(str(v).replace(",", "")) if v else None
            except ValueError:
                cleaned[clean_k] = None
        else:
            cleaned[clean_k] = str(v).strip()

    # 注册资本数值化（仅 company_info 表）
    if table_name == "company_info":
        rc_raw = row.get("registered_capital", row.get("\ufeffregistered_capital", None))
        cleaned["registered_capital_amount_cny"] = parse_registered_capital(rc_raw)

    return cleaned


def build_insert_sql(table_name: str, columns: list[str]) -> str:
    """构造 INSERT ... ON DUPLICATE KEY UPDATE SQL。"""
    col_names = ", ".join(f"`{c}`" for c in columns)
    placeholders = ", ".join(["%s"] * len(columns))

    # ON DUPLICATE KEY UPDATE：更新所有非主键列
    update_parts = []
    for c in columns:
        if c == "id":
            continue
        update_parts.append(f"`{c}` = VALUES(`{c}`)")

    if update_parts:
        update_clause = "ON DUPLICATE KEY UPDATE " + ", ".join(update_parts)
    else:
        update_clause = ""

    sql = f"INSERT INTO `{table_name}` ({col_names}) VALUES ({placeholders}) {update_clause}"
    return sql


def import_csv_to_mysql(
    csv_path: str,
    table_name: str,
    conn: pymysql.Connection,
    batch_size: int = 5000,
) -> int:
    """流式读取 CSV，批量 INSERT 到 MySQL。返回导入行数。"""
    logger.info("Importing %s → %s.%s ...", os.path.basename(csv_path), _CLEAN_DB, table_name)

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        columns = [c.lstrip("\ufeff") for c in (reader.fieldnames or [])]

        # 为 company_info 追加衍生列
        if table_name == "company_info":
            columns.append("registered_capital_amount_cny")

        insert_sql = build_insert_sql(table_name, columns)
        total = 0
        batch = []

        with conn.cursor() as cur:
            for row in reader:
                cleaned = clean_row(row, table_name)
                values = [cleaned.get(c) for c in columns]
                batch.append(values)

                if len(batch) >= batch_size:
                    cur.executemany(insert_sql, batch)
                    conn.commit()
                    total += len(batch)
                    logger.info("  ... %d rows inserted", total)
                    batch = []

            # 最后一批
            if batch:
                cur.executemany(insert_sql, batch)
                conn.commit()
                total += len(batch)

    logger.info("  Done: %d rows imported into %s", total, table_name)
    return total


def get_table_count(conn: pymysql.Connection, table_name: str) -> int:
    """查询表中行数。"""
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM `{_CLEAN_DB}`.`{table_name}`")
        return cur.fetchone()[0]


def get_null_count(
    conn: pymysql.Connection, table_name: str, column: str
) -> int:
    """查询某列 NULL 行数。"""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT COUNT(*) FROM `{_CLEAN_DB}`.`{table_name}` WHERE `{column}` IS NULL"
        )
        return cur.fetchone()[0]


def main():
    parser = argparse.ArgumentParser(description="CSV → MySQL 批量导入")
    parser.add_argument(
        "--csv-dir", default="raw_tables", help="CSV 文件目录（默认 raw_tables/）"
    )
    parser.add_argument(
        "--batch-size", type=int, default=5000, help="批量 INSERT 行数（默认 5000）"
    )
    parser.add_argument(
        "--truncate", action="store_true", help="导入前先 TRUNCATE 目标表（谨慎使用）"
    )
    args = parser.parse_args()

    csv_dir = os.path.abspath(args.csv_dir)
    if not os.path.isdir(csv_dir):
        logger.error("CSV directory not found: %s", csv_dir)
        sys.exit(1)

    # 连接 MySQL
    conn = pymysql.connect(database=_CLEAN_DB, **_MYSQL_CONFIG)
    logger.info("Connected to MySQL, database: %s", _CLEAN_DB)

    import_order = [
        "company_info.csv",
        "company_penalty.csv",
        "product_info.csv",
        "bid_project.csv",
    ]

    totals = {}

    for csv_file in import_order:
        table_name = TABLE_MAP[csv_file]
        csv_path = os.path.join(csv_dir, csv_file)

        if not os.path.exists(csv_path):
            logger.warning("CSV file not found, skipping: %s", csv_path)
            continue

        # 可选：TRUNCATE
        if args.truncate:
            logger.warning("TRUNCATE table %s ...", table_name)
            with conn.cursor() as cur:
                cur.execute(f"TRUNCATE TABLE `{_CLEAN_DB}`.`{table_name}`")
            conn.commit()

        # 导入
        total = import_csv_to_mysql(csv_path, table_name, conn, args.batch_size)
        totals[table_name] = total

    # ── 汇总统计 ──
    print("\n" + "=" * 60)
    print("  导入完成 — 行数统计")
    print("=" * 60)
    for table_name, imported in totals.items():
        db_count = get_table_count(conn, table_name)
        print(f"  {table_name:<25s}: imported={imported:>6d},  DB rows={db_count:>6d}")

    # ── 注册资本数值化统计 ──
    if "company_info" in totals:
        total_ci = get_table_count(conn, "company_info")
        null_rc = get_null_count(conn, "company_info", "registered_capital_amount_cny")
        parsed_ok = total_ci - null_rc
        print("\n  注册资本数值化统计:")
        print(f"    company_info 总行数:           {total_ci:>6d}")
        print(f"    registered_capital_amount_cny 已解析: {parsed_ok:>6d} ({parsed_ok/total_ci*100:.1f}%)")
        print(f"    registered_capital_amount_cny NULL:   {null_rc:>6d} ({null_rc/total_ci*100:.1f}%)")
        if null_rc > 0:
            print("    (请检查上方 WARNING 日志了解 NULL 原因)")

    conn.close()
    logger.info("All done.")


if __name__ == "__main__":
    main()
