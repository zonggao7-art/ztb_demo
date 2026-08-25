r"""
扫描 MySQL 服务器上所有用户数据库中的非空表，
将每张表的原始数据直接导出为 CSV 文件。

输出目录: D:\DEMO\zhaotoubiao_demo\raw_tables
文件命名: {数据库名}_{表名}.csv
CSV 格式: UTF-8 编码, 逗号分隔, 首行为列名。
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import datetime
from typing import Any

import pymysql

# ── 数据库连接配置 ──────────────────────────────────────────
DB_CONFIG: dict[str, Any] = {
    "host": "192.168.10.120",
    "user": "iflytek",
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "port": 3306,
    "charset": "utf8mb4",
    "connect_timeout": 30,
    "read_timeout": 300,
}

OUTPUT_DIR = r"D:\DEMO\zhaotoubiao_demo\raw_tables"

# 需要跳过的系统库
SYSTEM_DBS = {"information_schema", "mysql", "performance_schema", "sys"}

# 每次 fetch 的行数，避免一次性加载大表撑爆内存
FETCH_BATCH = 5000


# ── 值序列化 ────────────────────────────────────────────────
def _cell_value(val: Any) -> str:
    """将数据库返回的任意值转为适合写入 CSV 的字符串。"""
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return str(val)


# ── 连接管理 ────────────────────────────────────────────────
def get_connection(database: str | None = None) -> pymysql.Connection:
    """创建 MySQL 连接。"""
    config = DB_CONFIG.copy()
    if database:
        config["database"] = database
    return pymysql.connect(**config)


# ── 数据库枚举 ──────────────────────────────────────────────
def list_user_databases() -> list[str]:
    """列出所有用户数据库（排除系统库）。"""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW DATABASES")
            dbs = [row[0] for row in cur.fetchall()]
        return sorted(db for db in dbs if db not in SYSTEM_DBS)
    finally:
        conn.close()


# ── 非空表概览 ──────────────────────────────────────────────
def get_non_empty_tables(database: str) -> list[dict[str, Any]]:
    """查询某个数据库中所有 TABLE_ROWS > 0 的表。"""
    conn = get_connection(database)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    TABLE_NAME,
                    TABLE_ROWS,
                    ROUND(DATA_LENGTH   / 1024 / 1024, 2) AS DATA_SIZE_MB,
                    TABLE_COMMENT
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_ROWS > 0
                ORDER BY TABLE_ROWS DESC
                """,
                (database,),
            )
            cols = [desc[0] for desc in cur.description]
            tables = []
            for row in cur.fetchall():
                rec = dict(zip(cols, row))
                rec["TABLE_ROWS"] = int(rec["TABLE_ROWS"] or 0)
                rec["DATA_SIZE_MB"] = float(rec["DATA_SIZE_MB"] or 0)
                rec["TABLE_COMMENT"] = str(rec["TABLE_COMMENT"] or "")
                tables.append(rec)
            return tables
    finally:
        conn.close()


# ── 单表导出为 CSV ──────────────────────────────────────────
def export_table_to_csv(
    database: str,
    table_name: str,
    output_path: str,
) -> int:
    """将一张表的所有数据导出为 CSV，返回实际写入行数。

    使用 SSCursor（流式游标）避免大表撑爆内存。
    """
    conn = get_connection(database)
    try:
        # SSCursor = 服务端游标，逐批取回，内存友好
        cur = conn.cursor(pymysql.cursors.SSCursor)

        # 先取列名
        cur.execute(f"SELECT * FROM `{table_name}` LIMIT 0")
        col_names = [desc[0] for desc in cur.description]
        if not col_names:
            return 0

        # 实际查询全部数据
        cur.execute(f"SELECT * FROM `{table_name}`")

        with open(output_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter=",", quoting=csv.QUOTE_MINIMAL)

            # 首行：列名
            writer.writerow(col_names)

            row_count = 0
            while True:
                batch = cur.fetchmany(FETCH_BATCH)
                if not batch:
                    break
                for row in batch:
                    writer.writerow([_cell_value(v) for v in row])
                row_count += len(batch)

        return row_count

    finally:
        conn.close()


# ── 主流程 ──────────────────────────────────────────────────
def main() -> None:
    # 1. 确保输出目录存在
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("=" * 70)
    print("  MySQL 非空表数据导出工具 (CSV)")
    print(f"  目标服务器: {DB_CONFIG['host']}:{DB_CONFIG['port']}")
    print(f"  输出目录:   {OUTPUT_DIR}")
    print("=" * 70)

    # 2. 获取用户数据库列表
    print("\n[步骤 1/3] 正在获取数据库列表 ...")
    try:
        databases = list_user_databases()
    except pymysql.MySQLError as e:
        print(f"  ✗ 连接服务器失败: {e}")
        sys.exit(1)

    if not databases:
        print("  ⚠ 未发现用户数据库，退出。")
        return

    print(f"  ✓ 发现 {len(databases)} 个用户数据库: {', '.join(databases)}")

    # 3. 逐库扫描并导出
    total_files = 0
    total_rows_exported = 0
    total_errors = 0
    db_count = len(databases)

    print("\n[步骤 2/3] 正在扫描各库非空表并导出 CSV ...")
    for db_idx, db_name in enumerate(databases, 1):
        print(f"\n  [{db_idx:2d}/{db_count}] 数据库: {db_name}")

        # 3a. 获取非空表列表
        try:
            tables = get_non_empty_tables(db_name)
        except pymysql.MySQLError as e:
            print(f"    ✗ 查询表列表失败: {e}")
            continue
        except Exception as e:
            print(f"    ✗ 未知错误: {e}")
            continue

        if not tables:
            print("    - 无非空表，跳过")
            continue

        print(f"    - 共 {len(tables)} 张非空表")

        # 3b. 逐表导出
        for tbl_idx, t in enumerate(tables, 1):
            table_name = t["TABLE_NAME"]
            est_rows = t["TABLE_ROWS"]
            size_mb = t["DATA_SIZE_MB"]

            # 文件名：{数据库名}_{表名}.csv
            safe_table = table_name.replace("/", "_").replace("\\", "_")
            file_name = f"{db_name}_{safe_table}.csv"
            file_path = os.path.join(OUTPUT_DIR, file_name)

            progress = f"    [{tbl_idx:3d}/{len(tables)}] {table_name}"
            print(f"{progress:<55s} 估算 {est_rows:>10,} 行  {size_mb:>8.2f} MB", end=" ", flush=True)

            try:
                actual_rows = export_table_to_csv(db_name, table_name, file_path)
                total_files += 1
                total_rows_exported += actual_rows
                print(f"→ 实际导出 {actual_rows:,} 行")
            except pymysql.MySQLError as e:
                total_errors += 1
                print(f"✗ 错误: {e}")
                # 删除可能写入一半的文件
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass
            except Exception as e:
                total_errors += 1
                print(f"✗ 未知错误: {e}")
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                    except OSError:
                        pass

    # 4. 打印汇总
    print("\n" + "=" * 70)
    print("  导出完成!")
    print("=" * 70)
    print(f"  数据库扫描:        {db_count} 个")
    print(f"  成功导出文件:      {total_files} 个")
    print(f"  导出总行数:        {total_rows_exported:,}")
    if total_errors:
        print(f"  失败/错误:         {total_errors} 个")
    print(f"  输出目录:          {OUTPUT_DIR}")
    print("=" * 70)


if __name__ == "__main__":
    main()
