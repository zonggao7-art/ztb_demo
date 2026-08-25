r"""
MySQL Policy 表扫描与导出工具。

功能：
- 连接 MySQL 服务器，扫描所有可访问数据库
- 筛选表名匹配 "policy"（不区分大小写，以 policy 开头或包含 policy）的表
- 导出完整表结构（CREATE TABLE）与全量数据为 SQL 备份文件
- 导出全量数据为 UTF-8 编码 CSV 文件
- 全流程日志记录、异常重试（最多3次）、导出后校验

输出目录: D:\DEMO\zhaotoubiao_demo\raw_policy
日志文件: D:\DEMO\zhaotoubiao_demo\raw_policy\export_logs.log

用法：
    python scripts/export_policy_tables.py
"""

from __future__ import annotations

import csv
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pymysql

# ── 数据库连接配置 ──────────────────────────────────────────
DB_CONFIG: dict[str, Any] = {
    "host": "192.168.10.120",
    "user": "iflytek",
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "port": 3306,
    "charset": "utf8mb4",
    "connect_timeout": 10,
    "read_timeout": 300,
    "write_timeout": 300,
}

# ── 路径配置 ────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "raw_policy"
LOG_FILE = OUTPUT_DIR / "export_logs.log"

# ── 系统数据库（跳过）──────────────────────────────────────
SYSTEM_DBS = {"information_schema", "mysql", "performance_schema", "sys"}

# ── 每次 fetch 行数 ─────────────────────────────────────────
FETCH_BATCH = 5000

# ── 最大重连次数 ────────────────────────────────────────────
MAX_RETRIES = 3
RETRY_DELAY = 2  # 秒


# ═══════════════════════════════════════════════════════════════
# 日志配置（同时输出到文件和控制台）
# ═══════════════════════════════════════════════════════════════
def setup_logging(log_path: Path) -> logging.Logger:
    """配置双通道日志：文件（详细） + 控制台（INFO 级别）。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("policy_export")
    logger.setLevel(logging.DEBUG)

    # 避免重复添加 handler
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 文件 handler
    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    # 控制台 handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    return logger


# ═══════════════════════════════════════════════════════════════
# 连接管理（带重试）
# ═══════════════════════════════════════════════════════════════
def get_connection(
    logger: logging.Logger,
    database: str | None = None,
) -> pymysql.Connection:
    """创建 MySQL 连接，带重试机制。"""
    config = DB_CONFIG.copy()
    if database:
        config["database"] = database

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            conn = pymysql.connect(**config)
            logger.debug("数据库连接成功 [%s]", database or "(无指定)")
            return conn
        except pymysql.MySQLError as e:
            last_error = e
            logger.warning(
                "连接失败 (%s)，第 %d/%d 次重试: %s",
                database or "服务器", attempt, MAX_RETRIES, e,
            )
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)

    raise ConnectionError(
        f"无法连接到 MySQL ({config['host']}:{config['port']})，"
        f"已重试 {MAX_RETRIES} 次。最后错误: {last_error}"
    )


# ═══════════════════════════════════════════════════════════════
# 数据库枚举
# ═══════════════════════════════════════════════════════════════
def list_user_databases(logger: logging.Logger) -> list[str]:
    """列出当前用户有权限访问的所有数据库（排除系统库）。"""
    conn = get_connection(logger)
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW DATABASES")
            dbs = [row[0] for row in cur.fetchall()]
        user_dbs = sorted(db for db in dbs if db not in SYSTEM_DBS)
        logger.info("发现 %d 个用户数据库: %s", len(user_dbs), ", ".join(user_dbs))
        return user_dbs
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
# Policy 表筛选
# ═══════════════════════════════════════════════════════════════
def matches_policy(table_name: str) -> bool:
    r"""检查表名是否匹配 policy 规则。

    规则：表名以 policy 开头，或包含 policy（不区分大小写）。
    """
    lower = table_name.lower()
    # 正则：以 policy 开头 或 中间包含 _policy / policy_ / policy
    return bool(re.search(r"(^policy)|(policy)", lower))


def get_policy_tables(
    logger: logging.Logger, database: str
) -> list[dict[str, Any]]:
    """获取某个数据库中所有匹配 policy 规则的表及其元信息。"""
    conn = get_connection(logger, database)
    try:
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES")
            all_tables = [row[0] for row in cur.fetchall()]

        policy_tables: list[dict[str, Any]] = []
        for tbl in all_tables:
            if matches_policy(tbl):
                # 查询表统计信息
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT
                            TABLE_ROWS,
                            ROUND(DATA_LENGTH / 1024 / 1024, 2) AS DATA_SIZE_MB,
                            TABLE_COMMENT
                        FROM information_schema.TABLES
                        WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                        """,
                        (database, tbl),
                    )
                    row = cur.fetchone()
                    est_rows = int(row[0] or 0) if row else 0
                    size_mb = float(row[1] or 0) if row else 0.0
                    comment = str(row[2] or "") if row else ""
                    policy_tables.append({
                        "TABLE_NAME": tbl,
                        "TABLE_ROWS": est_rows,
                        "DATA_SIZE_MB": size_mb,
                        "TABLE_COMMENT": comment,
                    })

        return policy_tables
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
# 导出辅助函数
# ═══════════════════════════════════════════════════════════════
def _cell_value(val: Any) -> str:
    """将数据库返回值转为 CSV 安全字符串。"""
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return str(val)


def _escape_sql(val: Any) -> str:
    """将值转义为 SQL INSERT 安全字符串。"""
    if val is None:
        return "NULL"
    if isinstance(val, datetime):
        return f"'{val.strftime('%Y-%m-%d %H:%M:%S')}'"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, bytes):
        val = val.decode("utf-8", errors="replace")
    # 字符串转义
    escaped = str(val).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _build_safe_filename(database: str, table_name: str) -> str:
    """根据需要生成安全的文件名（添加时间戳避免冲突）。"""
    return f"{database}_{table_name}"


def _resolve_file_path(base_dir: Path, filename: str, ext: str) -> Path:
    """获取导出文件路径，若已存在则添加时间戳后缀。"""
    filepath = base_dir / f"{filename}{ext}"
    if not filepath.exists():
        return filepath
    # 添加时间戳后缀覆盖
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    alt_path = base_dir / f"{filename}_{ts}{ext}"
    return alt_path


# ═══════════════════════════════════════════════════════════════
# 表结构导出（CREATE TABLE 语句）
# ═══════════════════════════════════════════════════════════════
def get_create_table_sql(
    logger: logging.Logger, database: str, table_name: str
) -> str:
    """获取表的 CREATE TABLE 语句。"""
    conn = get_connection(logger, database)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SHOW CREATE TABLE `{table_name}`")
            row = cur.fetchone()
            if row and len(row) >= 2:
                return str(row[1])
            else:
                raise ValueError(f"无法获取 {database}.{table_name} 的建表语句")
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
# SQL 备份文件导出（CREATE TABLE + INSERT 数据）
# ═══════════════════════════════════════════════════════════════
def export_sql_backup(
    logger: logging.Logger,
    database: str,
    table_name: str,
    output_path: Path,
) -> int:
    """导出完整 SQL 备份（DDL + DML），返回写入行数。"""
    logger.info("  导出 SQL: %s", output_path.name)

    conn = get_connection(logger, database)
    try:
        # 使用 SSCursor 流式读取
        cur = conn.cursor(pymysql.cursors.SSCursor)

        # 获取列名
        cur.execute(f"SELECT * FROM `{table_name}` LIMIT 0")
        col_names = [desc[0] for desc in cur.description]
        if not col_names:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(f"-- 空表: {database}.{table_name}\n")
            return 0

        col_list = ", ".join(f"`{c}`" for c in col_names)

        with open(output_path, "w", encoding="utf-8") as f:
            # 写入文件头
            f.write(f"-- Database: {database}\n")
            f.write(f"-- Table: {table_name}\n")
            f.write(f"-- Export time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("-- Rows: (estimated from export)\n\n")

            # 写入 DDL
            ddl = get_create_table_sql(logger, database, table_name)
            f.write(f"{ddl};\n\n")

            # 写入数据
            f.write(f"-- Data for {database}.{table_name}\n")
            f.write(f"LOCK TABLES `{table_name}` WRITE;\n")
            f.write(f"/*!40000 ALTER TABLE `{table_name}` DISABLE KEYS */;\n\n")

            cur.execute(f"SELECT * FROM `{table_name}`")
            row_count = 0
            batch_values: list[str] = []

            while True:
                batch = cur.fetchmany(FETCH_BATCH)
                if not batch:
                    break

                for row in batch:
                    values = [_escape_sql(v) for v in row]
                    batch_values.append(f"({', '.join(values)})")

                    if len(batch_values) >= 500:
                        f.write(
                            f"INSERT INTO `{table_name}` ({col_list}) VALUES\n"
                            f"{',\n'.join(batch_values)};\n"
                        )
                        row_count += len(batch_values)
                        batch_values = []

            # 最后一批
            if batch_values:
                f.write(
                    f"INSERT INTO `{table_name}` ({col_list}) VALUES\n"
                    f"{',\n'.join(batch_values)};\n"
                )
                row_count += len(batch_values)

            f.write(f"\n/*!40000 ALTER TABLE `{table_name}` ENABLE KEYS */;\n")
            f.write("UNLOCK TABLES;\n")

        logger.info("  SQL 导出完成: %d 行", row_count)
        return row_count

    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
# CSV 数据导出
# ═══════════════════════════════════════════════════════════════
def export_csv_data(
    logger: logging.Logger,
    database: str,
    table_name: str,
    output_path: Path,
) -> int:
    """导出全量数据为 UTF-8 CSV 文件，返回写入行数。"""
    logger.info("  导出 CSV: %s", output_path.name)

    conn = get_connection(logger, database)
    try:
        cur = conn.cursor(pymysql.cursors.SSCursor)

        # 获取列名
        cur.execute(f"SELECT * FROM `{table_name}` LIMIT 0")
        col_names = [desc[0] for desc in cur.description]
        if not col_names:
            with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["(空表)"])
            return 0

        cur.execute(f"SELECT * FROM `{table_name}`")

        with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, delimiter=",", quoting=csv.QUOTE_MINIMAL)

            # BOM 已通过 utf-8-sig 处理
            writer.writerow(col_names)

            row_count = 0
            while True:
                batch = cur.fetchmany(FETCH_BATCH)
                if not batch:
                    break
                for row in batch:
                    writer.writerow([_cell_value(v) for v in row])
                row_count += len(batch)

        logger.info("  CSV 导出完成: %d 行", row_count)
        return row_count

    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
# 校验
# ═══════════════════════════════════════════════════════════════
def verify_export(
    logger: logging.Logger,
    database: str,
    table_name: str,
    sql_path: Path,
    csv_path: Path,
    expected_rows: int,
    sql_exported: int,
    csv_exported: int,
) -> bool:
    """校验导出文件。"""
    all_ok = True

    # 1. 检查文件是否存在且非空
    for label, path in [("SQL", sql_path), ("CSV", csv_path)]:
        if not path.exists():
            logger.error("  校验失败: %s 文件不存在: %s", label, path)
            all_ok = False
        elif path.stat().st_size == 0:
            logger.warning("  校验警告: %s 文件为空 (可能为空表): %s", label, path)
        else:
            logger.info("  校验通过: %s 文件存在，大小 %s", label, _format_size(path.stat().st_size))

    # 2. 核对 CSV 行数
    if csv_exported > 0 and csv_path.exists():
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            csv_lines = sum(1 for _ in reader) - 1  # 减去表头
        logger.info("  CSV 行数校验: 导出声明 %d 行, 文件实际 %d 行", csv_exported, csv_lines)
        if csv_lines != csv_exported:
            logger.warning("  CSV 行数不匹配! 导出 %d != 文件 %d", csv_exported, csv_lines)
            all_ok = False

    # 3. 核对 SQL 文件中 INSERT 行数（粗略检查）
    if sql_exported > 0 and sql_path.exists():
        with open(sql_path, "r", encoding="utf-8") as f:
            sql_content = f.read()
        # 统计 VALUES 中的行数（粗略：统计 ),( 分隔符数量）
        insert_rows = sql_content.count("),\n(") + (1 if "VALUES\n(" in sql_content else 0)
        logger.info("  SQL 行数校验: 导出声明 %d 行, INSERT 块估 %d 行", sql_exported, insert_rows)

    # 4. 与数据库实际行数对比
    if expected_rows >= 0:
        logger.info("  数据库估算行数: %d, 导出行数: %d", expected_rows, csv_exported)

    return all_ok


def _format_size(size_bytes: int) -> str:
    """格式化文件大小。"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════
def main() -> None:
    # ── 1. 前置路径准备 ──
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # 测试写入权限
    try:
        test_file = OUTPUT_DIR / ".write_test"
        test_file.touch()
        test_file.unlink()
    except (OSError, PermissionError) as e:
        print(f"✗ 目标目录无写入权限: {OUTPUT_DIR}")
        print(f"  错误: {e}")
        sys.exit(1)

    # ── 2. 初始化日志 ──
    logger = setup_logging(LOG_FILE)
    logger.info("=" * 60)
    logger.info("MySQL Policy 表导出工具启动")
    logger.info("目标服务器: %s:%d", DB_CONFIG["host"], DB_CONFIG["port"])
    logger.info("输出目录:   %s", OUTPUT_DIR)
    logger.info("=" * 60)

    # ── 3. 连接并扫描数据库 ──
    logger.info("[步骤 1/4] 连接 MySQL 并扫描数据库列表 ...")
    try:
        databases = list_user_databases(logger)
    except Exception as e:
        logger.error("连接服务器失败: %s", e)
        sys.exit(1)

    if not databases:
        logger.warning("未发现可访问的用户数据库，退出。")
        return

    # ── 4. 逐库扫描 policy 表并导出 ──
    logger.info("[步骤 2/4] 扫描各库中的 policy 表并导出 ...")
    total_sql_files = 0
    total_csv_files = 0
    total_rows_exported = 0
    total_errors = 0
    all_tables: list[dict[str, Any]] = []  # 记录所有匹配的表用于汇总

    for db_idx, db_name in enumerate(databases, 1):
        logger.info("  [%d/%d] 数据库: %s", db_idx, len(databases), db_name)

        try:
            policy_tables = get_policy_tables(logger, db_name)
        except Exception as e:
            logger.error("  查询 %s 的表列表失败: %s", db_name, e)
            total_errors += 1
            continue

        if not policy_tables:
            logger.info("  无匹配 policy 规则的表，跳过")
            continue

        logger.info("  发现 %d 张匹配 policy 规则的表", len(policy_tables))

        for tbl_idx, t in enumerate(policy_tables, 1):
            table_name = t["TABLE_NAME"]
            est_rows = t["TABLE_ROWS"]
            size_mb = t["DATA_SIZE_MB"]

            logger.info(
                "    [%d/%d] %s.%s  估算 %d 行  %.2f MB",
                tbl_idx, len(policy_tables), db_name, table_name, est_rows, size_mb,
            )

            safe_filename = _build_safe_filename(db_name, table_name)

            sql_path = _resolve_file_path(OUTPUT_DIR, safe_filename, "_backup.sql")
            csv_path = _resolve_file_path(OUTPUT_DIR, safe_filename, "_data.csv")

            sql_exported = 0
            csv_exported = 0
            success = True

            # 导出 SQL 备份
            try:
                sql_exported = export_sql_backup(logger, db_name, table_name, sql_path)
                total_sql_files += 1
            except Exception as e:
                logger.error("  ✗ SQL 导出失败 (%s.%s): %s", db_name, table_name, e)
                success = False
                total_errors += 1
                # 清理不完整文件
                if sql_path.exists():
                    try:
                        sql_path.unlink()
                    except OSError:
                        pass

            # 导出 CSV
            try:
                csv_exported = export_csv_data(logger, db_name, table_name, csv_path)
                total_csv_files += 1
                total_rows_exported += csv_exported
            except Exception as e:
                logger.error("  ✗ CSV 导出失败 (%s.%s): %s", db_name, table_name, e)
                success = False
                total_errors += 1
                if csv_path.exists():
                    try:
                        csv_path.unlink()
                    except OSError:
                        pass

            # ── 校验 ──
            if success:
                logger.info("  [校验] %s.%s", db_name, table_name)
                verify_export(
                    logger, db_name, table_name,
                    sql_path, csv_path,
                    est_rows, sql_exported, csv_exported,
                )

            # 记录到汇总列表
            all_tables.append({
                "database": db_name,
                "table_name": table_name,
                "estimated_rows": est_rows,
                "data_size_mb": size_mb,
                "sql_file": str(sql_path) if sql_path.exists() else "(失败)",
                "csv_file": str(csv_path) if csv_path.exists() else "(失败)",
                "csv_rows": csv_exported,
                "success": success,
            })

    # ── 5. 汇总输出 ──
    logger.info("")
    logger.info("=" * 60)
    logger.info("  导出完成 — 汇总报告")
    logger.info("=" * 60)
    logger.info("  扫描数据库:         %d 个", len(databases))
    logger.info("  匹配 policy 表:     %d 张", len(all_tables))
    logger.info("  成功 SQL 文件:      %d 个", total_sql_files)
    logger.info("  成功 CSV 文件:      %d 个", total_csv_files)
    logger.info("  导出总行数:         %d", total_rows_exported)
    if total_errors:
        logger.warning("  失败/错误:          %d 个", total_errors)
    logger.info("  输出目录:           %s", OUTPUT_DIR)
    logger.info("  日志文件:           %s", LOG_FILE)
    logger.info("=" * 60)

    # 逐表明细
    logger.info("")
    logger.info("  ── 各表导出明细 ──")
    for rec in all_tables:
        status = "✓" if rec["success"] else "✗"
        logger.info(
            "  %s %s.%s  行数: %d  |  SQL: %s  |  CSV: %s",
            status, rec["database"], rec["table_name"],
            rec["csv_rows"],
            Path(rec["sql_file"]).name,
            Path(rec["csv_file"]).name,
        )

    # 控制台打印最终摘要
    print("\n" + "=" * 60)
    print("  导出完成!")
    print("=" * 60)
    success_tables = sum(1 for r in all_tables if r["success"])
    print(f"  成功导出: {success_tables}/{len(all_tables)} 张表")
    print(f"  SQL 文件: {total_sql_files} 个")
    print(f"  CSV 文件: {total_csv_files} 个")
    print(f"  总行数:   {total_rows_exported:,}")
    if total_errors:
        print(f"  失败:     {total_errors} 个")
    print(f"  输出目录: {OUTPUT_DIR}")
    print(f"  日志文件: {LOG_FILE}")
    print("=" * 60)


if __name__ == "__main__":
    main()
