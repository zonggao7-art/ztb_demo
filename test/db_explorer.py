"""
数据库探查工具 - 快速扫描MySQL数据库，生成数据概览报告
适用于包含大量表（数万张）的数据库，帮助快速了解数据结构
"""
import json
import os
import argparse
from collections import defaultdict
from datetime import datetime, date
from decimal import Decimal

# ========== 数据库配置 ==========
DB_CONFIG = {
    "host": "192.168.10.120",
    "user": "iflytek",
    "password": os.getenv("MYSQL_PASSWORD", ""),
    "port": 3306,
    "charset": "utf8mb4",
    "connect_timeout": 30,
    "read_timeout": 60,
}

OUTPUT_DIR = "db_explore_output"
os.makedirs(OUTPUT_DIR, exist_ok=True)


from _diag_common import get_connection


def list_all_databases():
    """列出所有数据库"""
    conn = get_connection(db_config=DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            cursor.execute("SHOW DATABASES")
            databases = [row[0] for row in cursor.fetchall()]
            # 排除系统数据库
            sys_dbs = {"information_schema", "mysql", "performance_schema", "sys"}
            user_dbs = [db for db in databases if db not in sys_dbs]
            return user_dbs
    finally:
        conn.close()


def get_table_summary(database):
    """获取某个数据库中所有表的概览信息（表名、行数、大小、注释）"""
    conn = get_connection(database, db_config=DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            sql = """
                SELECT 
                    TABLE_NAME,
                    TABLE_ROWS,
                    ROUND(DATA_LENGTH / 1024 / 1024, 2) AS DATA_SIZE_MB,
                    ROUND(INDEX_LENGTH / 1024 / 1024, 2) AS INDEX_SIZE_MB,
                    TABLE_COMMENT,
                    CREATE_TIME,
                    UPDATE_TIME
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = %s
                ORDER BY TABLE_ROWS DESC
            """
            cursor.execute(sql, (database,))
            columns = [desc[0] for desc in cursor.description]
            tables = []
            for row in cursor.fetchall():
                tables.append(dict(zip(columns, row)))
            return tables
    finally:
        conn.close()


def get_table_columns(database, table_name):
    """获取表的列结构"""
    conn = get_connection(database, db_config=DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            sql = """
                SELECT 
                    COLUMN_NAME,
                    COLUMN_TYPE,
                    IS_NULLABLE,
                    COLUMN_KEY,
                    COLUMN_DEFAULT,
                    EXTRA,
                    COLUMN_COMMENT
                FROM information_schema.COLUMNS
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                ORDER BY ORDINAL_POSITION
            """
            cursor.execute(sql, (database, table_name))
            columns = [desc[0] for desc in cursor.description]
            cols = []
            for row in cursor.fetchall():
                cols.append(dict(zip(columns, row)))
            return cols
    finally:
        conn.close()


def get_sample_data(database, table_name, limit=3):
    """获取表的样本数据"""
    conn = get_connection(database, db_config=DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            # 使用反引号包裹表名，防止关键字冲突
            sql = f"SELECT * FROM `{table_name}` LIMIT {limit}"
            cursor.execute(sql)
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            samples = []
            for row in rows:
                sample = {}
                for i, col in enumerate(columns):
                    val = row[i]
                    # 处理特殊类型，确保JSON可序列化
                    if isinstance(val, bytes):
                        val = f"<BLOB:{len(val)} bytes>"
                    elif isinstance(val, datetime):
                        val = val.strftime("%Y-%m-%d %H:%M:%S")
                    elif val is None:
                        val = None
                    else:
                        # 截断长文本
                        val_str = str(val)
                        if len(val_str) > 200:
                            val_str = val_str[:200] + "..."
                        val = val_str
                    sample[col] = val
                samples.append(sample)
            return columns, samples
    except Exception as e:
        return [], [{"_error": str(e)}]
    finally:
        conn.close()


def json_serializer(obj):
    """处理JSON无法序列化的类型"""
    if isinstance(obj, (datetime, date)):
        return obj.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, bytes):
        return f"<BLOB:{len(obj)} bytes>"
    return str(obj)


def search_tables_by_keyword(database, keyword):
    """根据关键字搜索表名"""
    conn = get_connection(database, db_config=DB_CONFIG)
    try:
        with conn.cursor() as cursor:
            sql = """
                SELECT TABLE_NAME, TABLE_ROWS, TABLE_COMMENT
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = %s 
                  AND (TABLE_NAME LIKE %s OR TABLE_COMMENT LIKE %s)
                ORDER BY TABLE_NAME
                LIMIT 100
            """
            like_pattern = f"%{keyword}%"
            cursor.execute(sql, (database, like_pattern, like_pattern))
            columns = [desc[0] for desc in cursor.description]
            return [dict(zip(columns, row)) for row in cursor.fetchall()]
    finally:
        conn.close()


def explore_single_database(database, output_subdir):
    """深度探查单个数据库"""
    print(f"\n{'='*60}")
    print(f"  正在探查数据库: {database}")
    print(f"{'='*60}")

    # 1. 获取表概览
    print("  [1/3] 获取表概览...")
    tables = get_table_summary(database)
    total_tables = len(tables)
    total_rows = sum(t.get("TABLE_ROWS", 0) or 0 for t in tables)
    total_data_mb = sum(t.get("DATA_SIZE_MB", 0) or 0 for t in tables)

    print(f"  ├─ 表数量: {total_tables}")
    print(f"  ├─ 总记录数(估算): {total_rows:,}")
    print(f"  └─ 总数据大小: {total_data_mb:.2f} MB")

    # 保存表概览
    summary_path = os.path.join(output_subdir, "table_summary.json")
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump({
            "database": database,
            "total_tables": total_tables,
            "total_rows_estimate": total_rows,
            "total_data_mb": round(total_data_mb, 2),
            "tables": tables
        }, f, ensure_ascii=False, indent=2, default=json_serializer)
    print(f"  ✓ 表概览已保存: {summary_path}")

    # 2. 按行数分组统计
    size_groups = defaultdict(list)
    for t in tables:
        rows = t.get("TABLE_ROWS") or 0
        if rows == 0:
            size_groups["空表(0行)"].append(t["TABLE_NAME"])
        elif rows < 100:
            size_groups["小表(1-99行)"].append(t["TABLE_NAME"])
        elif rows < 10000:
            size_groups["中表(100-9999行)"].append(t["TABLE_NAME"])
        elif rows < 100000:
            size_groups["大表(1万-10万行)"].append(t["TABLE_NAME"])
        elif rows < 1000000:
            size_groups["超大表(10万-100万行)"].append(t["TABLE_NAME"])
        else:
            size_groups["巨型表(>100万行)"].append(t["TABLE_NAME"])

    print("\n  [2/3] 表规模分布:")
    for group, tbls in sorted(size_groups.items()):
        print(f"  ├─ {group}: {len(tbls)} 张")

    # 保存分组信息
    groups_path = os.path.join(output_subdir, "table_groups.json")
    with open(groups_path, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in size_groups.items()}, f, ensure_ascii=False, indent=2)

    # 3. 采样关键表的列结构和数据（默认采样前20张大表 + 前10张小表）
    print("\n  [3/3] 采样表结构...")

    # 选最大的20张表（按行数）
    large_tables = sorted(
        [t for t in tables if (t.get("TABLE_ROWS") or 0) > 0],
        key=lambda x: x.get("TABLE_ROWS") or 0, reverse=True
    )[:20]

    # 选有注释的表（这些更有语义价值）
    commented_tables = [t for t in tables if t.get("TABLE_COMMENT")]
    commented_sample = sorted(
        commented_tables,
        key=lambda x: x.get("TABLE_ROWS") or 0, reverse=True
    )[:10]

    # 合并去重
    sample_set = set()
    sample_tables = []
    for t in large_tables + commented_sample:
        if t["TABLE_NAME"] not in sample_set:
            sample_set.add(t["TABLE_NAME"])
            sample_tables.append(t)

    schema_data = {}
    for i, t in enumerate(sample_tables):
        tbl_name = t["TABLE_NAME"]
        rows_count = t.get("TABLE_ROWS") or 0
        comment = t.get("TABLE_COMMENT", "")
        print(f"  ├─ [{i+1}/{len(sample_tables)}] 采样: {tbl_name} ({rows_count} 行) {comment}")

        columns = get_table_columns(database, tbl_name)
        col_names, samples = get_sample_data(database, tbl_name, limit=2)

        schema_data[tbl_name] = {
            "row_count": rows_count,
            "comment": comment,
            "columns": columns,
            "sample_data": samples
        }

    schema_path = os.path.join(output_subdir, "table_schema_samples.json")
    with open(schema_path, "w", encoding="utf-8") as f:
        json.dump(schema_data, f, ensure_ascii=False, indent=2, default=json_serializer)
    print(f"  ✓ 表结构采样已保存: {schema_path}")

    return {
        "database": database,
        "total_tables": total_tables,
        "total_rows": total_rows,
        "total_data_mb": total_data_mb,
        "size_groups": {k: len(v) for k, v in size_groups.items()}
    }


def generate_readable_report(all_results):
    """生成易读的文本报告"""
    report_path = os.path.join(OUTPUT_DIR, "EXPLORE_REPORT.txt")
    lines = []
    lines.append("=" * 70)
    lines.append("  数据库探查报告")
    lines.append(f"  生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 70)

    total_tables_all = sum(r["total_tables"] for r in all_results)
    total_rows_all = sum(r["total_rows"] for r in all_results)
    total_mb_all = sum(r["total_data_mb"] for r in all_results)

    lines.append(f"\n服务器: {DB_CONFIG['host']}:{DB_CONFIG['port']}")
    lines.append(f"数据库总数: {len(all_results)}")
    lines.append(f"表总数: {total_tables_all}")
    lines.append(f"总记录数(估算): {total_rows_all:,}")
    lines.append(f"总数据量: {total_mb_all:.2f} MB")

    for r in all_results:
        lines.append(f"\n{'─' * 60}")
        lines.append(f"  数据库: {r['database']}")
        lines.append(f"  表数量: {r['total_tables']}")
        lines.append(f"  记录数: {r['total_rows']:,}")
        lines.append(f"  数据量: {r['total_data_mb']:.2f} MB")
        lines.append("  规模分布:")
        for group, count in r["size_groups"].items():
            lines.append(f"    - {group}: {count} 张")

    lines.append(f"\n{'=' * 70}")
    lines.append("  详细数据文件:")
    lines.append(f"  - {OUTPUT_DIR}/<数据库名>/table_summary.json     → 所有表的完整清单")
    lines.append(f"  - {OUTPUT_DIR}/<数据库名>/table_groups.json      → 按规模分组")
    lines.append(f"  - {OUTPUT_DIR}/<数据库名>/table_schema_samples.json → 关键表结构和样本数据")
    lines.append(f"  - {OUTPUT_DIR}/EXPLORE_REPORT.txt               → 本报告")
    lines.append("=" * 70)

    report_text = "\n".join(lines)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)
    print(f"\n{'='*60}")
    print(report_text)

    return report_path


def interactive_search(database):
    """交互式搜索表"""
    print(f"\n{'='*60}")
    print(f"  交互式表搜索 (数据库: {database})")
    print("  输入关键字搜索表名/注释，输入 'quit' 退出")
    print(f"{'='*60}")

    while True:
        keyword = input("\n  搜索关键字 > ").strip()
        if keyword.lower() in ("quit", "exit", "q"):
            break
        if not keyword:
            continue

        results = search_tables_by_keyword(database, keyword)
        if not results:
            print(f"  ⚠ 未找到匹配 '{keyword}' 的表")
            continue

        print(f"  找到 {len(results)} 张匹配的表:")
        for i, t in enumerate(results[:30], 1):
            rows = t.get("TABLE_ROWS") or 0
            comment = t.get("TABLE_COMMENT", "")
            print(f"  {i:3d}. {t['TABLE_NAME']:<50s} {rows:>10,} 行  {comment}")

        if len(results) > 30:
            print(f"  ... 还有 {len(results) - 30} 张表，请缩小搜索范围")

        # 询问是否查看某张表的结构
        detail = input("\n  输入表名查看结构 (回车跳过) > ").strip()
        if detail:
            print(f"  正在获取 {detail} 的结构...")
            columns = get_table_columns(database, detail)
            if columns:
                print(f"\n  {'列名':<30s} {'类型':<25s} {'键':<8s} {'注释'}")
                print(f"  {'─'*30} {'─'*25} {'─'*8} {'─'*30}")
                for col in columns:
                    key = col.get("COLUMN_KEY", "")
                    comment = col.get("COLUMN_COMMENT", "") or ""
                    print(f"  {col['COLUMN_NAME']:<30s} {col['COLUMN_TYPE']:<25s} {key:<8s} {comment}")

                # 样本数据
                col_names, samples = get_sample_data(database, detail, limit=3)
                if samples:
                    print("\n  样本数据 (前3行):")
                    for s in samples:
                        print(f"  {s}")


def quick_overview(databases):
    """快速浏览所有数据库的表数量（不深入探查）"""
    print("\n[快速概览] 正在统计各数据库的表数量...")
    overview = []
    for i, db in enumerate(databases, 1):
        try:
            tables = get_table_summary(db)
            total_rows = sum(t.get("TABLE_ROWS", 0) or 0 for t in tables)
            total_mb = sum(t.get("DATA_SIZE_MB", 0) or 0 for t in tables)
            overview.append({
                "database": db,
                "tables": len(tables),
                "rows": total_rows,
                "size_mb": round(total_mb, 2)
            })
            print(f"  [{i:2d}/{len(databases)}] {db:<35s} {len(tables):>6d} 张表  {total_rows:>12,} 行  {total_mb:>10.2f} MB")
        except Exception as e:
            print(f"  [{i:2d}/{len(databases)}] {db:<35s} ✗ 错误: {e}")

    # 保存概览
    overview_path = os.path.join(OUTPUT_DIR, "quick_overview.json")
    with open(overview_path, "w", encoding="utf-8") as f:
        json.dump(overview, f, ensure_ascii=False, indent=2, default=json_serializer)

    total_tables = sum(o["tables"] for o in overview)
    total_rows = sum(o["rows"] for o in overview)
    total_mb = sum(o["size_mb"] for o in overview)
    print(f"\n  {'─'*60}")
    print(f"  总计: {len(overview)} 个数据库, {total_tables} 张表, {total_rows:,} 行, {total_mb:.2f} MB")
    print(f"  概览已保存: {overview_path}")
    return overview


def parse_args():
    parser = argparse.ArgumentParser(
        description="数据库探查工具 - 快速扫描MySQL数据库",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python db_explorer.py --list                 仅列出所有数据库
  python db_explorer.py --overview             快速概览所有数据库的表数量
  python db_explorer.py --db bidding_data      深度探查指定数据库
  python db_explorer.py --db bidding_data,xunfei4  探查多个数据库(逗号分隔)
  python db_explorer.py --all                  探查所有数据库
  python db_explorer.py --search bidding_data 招标  在指定数据库中搜索表名
  python db_explorer.py --interactive          进入交互模式
        """
    )
    parser.add_argument("--list", action="store_true", help="仅列出所有数据库名称")
    parser.add_argument("--overview", action="store_true", help="快速概览所有数据库的表数量和大小")
    parser.add_argument("--db", type=str, help="指定要深度探查的数据库（多个用逗号分隔）")
    parser.add_argument("--all", action="store_true", help="深度探查所有数据库")
    parser.add_argument("--search", nargs=2, metavar=("DB", "KEYWORD"), help="在指定数据库中搜索包含关键字的表")
    parser.add_argument("--interactive", action="store_true", help="进入交互模式")
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 60)
    print("  数据库探查工具 v1.0")
    print(f"  目标服务器: {DB_CONFIG['host']}:{DB_CONFIG['port']}")
    print("=" * 60)

    # 第一步：列出所有数据库
    print("\n[步骤1] 正在获取数据库列表...")
    try:
        databases = list_all_databases()
    except Exception as e:
        print(f"  ✗ 连接失败: {e}")
        print("\n  请检查:")
        print("  1. 网络是否能连通 192.168.10.120")
        print("  2. 用户名密码是否正确")
        print("  3. MySQL服务是否在运行")
        print("  4. 是否已安装 pymysql: pip install pymysql")
        return

    if not databases:
        print("  ⚠ 未发现用户数据库")
        return

    print(f"  发现 {len(databases)} 个用户数据库:")
    for i, db in enumerate(databases, 1):
        print(f"    {i:2d}. {db}")

    # 处理命令行参数
    if args.list:
        return  # 已经列出了

    if args.overview:
        quick_overview(databases)
        return

    if args.search:
        db_name, keyword = args.search
        if db_name in databases:
            interactive_search(db_name)
        else:
            print(f"  ✗ 数据库 '{db_name}' 不存在")
            print(f"  可用数据库: {', '.join(databases)}")
        return

    if args.interactive:
        print("\n[步骤2] 选择探查范围:")
        print("  - 输入数据库编号 (如 1,2,3) 探查指定数据库")
        print("  - 输入 'all' 探查所有数据库")
        print("  - 输入 'search' 进入交互式搜索模式")
        choice = input("\n  请选择 > ").strip().lower()
        if choice == "all":
            selected_dbs = databases
        elif choice == "search":
            print(f"\n  可用数据库: {', '.join(databases)}")
            db_choice = input("  选择要搜索的数据库 > ").strip()
            try:
                idx = int(db_choice) - 1
                if 0 <= idx < len(databases):
                    interactive_search(databases[idx])
                    return
            except ValueError:
                if db_choice in databases:
                    interactive_search(db_choice)
                    return
            print("  无效选择")
            return
        else:
            try:
                indices = [int(x.strip()) - 1 for x in choice.split(",")]
                selected_dbs = [databases[i] for i in indices if 0 <= i < len(databases)]
            except ValueError:
                print("  无效输入，请使用数字编号或 'all'")
                return
        if not selected_dbs:
            print("  未选中任何数据库")
            return
    elif args.all:
        selected_dbs = databases
    elif args.db:
        selected_dbs = [db.strip() for db in args.db.split(",") if db.strip() in databases]
        if not selected_dbs:
            print("  ✗ 指定的数据库不存在")
            print(f"  可用数据库: {', '.join(databases)}")
            return
    else:
        # 默认行为：先做快速概览
        print("\n  💡 未指定参数，默认执行快速概览（--overview）")
        print("     使用 --help 查看所有选项")
        quick_overview(databases)
        return

    # 第三步：探查每个选中的数据库
    all_results = []
    for db in selected_dbs:
        output_subdir = os.path.join(OUTPUT_DIR, db)
        os.makedirs(output_subdir, exist_ok=True)
        try:
            result = explore_single_database(db, output_subdir)
            all_results.append(result)
        except Exception as e:
            print(f"  ✗ 探查 {db} 时出错: {e}")
            import traceback
            traceback.print_exc()

    # 第四步：生成汇总报告
    if all_results:
        print("\n[步骤4] 生成汇总报告...")
        report_path = generate_readable_report(all_results)
        print(f"\n{'='*60}")
        print("  ✅ 探查完成！")
        print(f"  报告文件: {report_path}")
        print(f"  所有输出在: {os.path.abspath(OUTPUT_DIR)}/")
        print(f"{'='*60}")
    else:
        print("\n  ⚠ 未生成任何结果")


if __name__ == "__main__":
    main()
