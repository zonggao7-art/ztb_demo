"""云服务器连通性测试 — MySQL + Milvus。

目的：验证 .env 中的 8.130.174.43:3306 (MySQL) 与 8.130.174.43:19530 (Milvus)
是否可以从本机连通，并打印诊断信息。

用法：
    python test/cloud_connectivity_check.py

输出：
    - TCP 可达性（用 socket 直接探测端口）
    - MySQL 握手 + 协议握手（pymysql.connect）
    - Milvus 集合列表（pymilvus.MilvusClient）
"""
from __future__ import annotations

import os
import socket
import sys
import time
from urllib.parse import urlparse

# 让脚本可直接 python test/cloud_connectivity_check.py 跑
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 加载 .env
from dotenv import load_dotenv

load_dotenv(os.path.join(_ROOT, ".env"))

MYSQL_HOST = os.getenv("MYSQL_HOST", "")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_CLEAN_DB = os.getenv("MYSQL_CLEAN_DB", "ztb_clean")

MILVUS_HOST = os.getenv("MILVUS_HOST", "")
MILVUS_PORT = int(os.getenv("MILVUS_PORT", "19530"))


def _print_header(title: str) -> None:
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def _tcp_probe(host: str, port: int, timeout: float = 5.0) -> bool:
    """裸 TCP 探测：能建立三次握手即视为端口可达。"""
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            return True
    except socket.timeout:
        print(f"  ✗ TCP 超时（>{timeout}s）")
        return False
    except OSError as e:
        print(f"  ✗ TCP 失败：{e}")
        return False


def check_mysql() -> None:
    _print_header(f"MySQL  {MYSQL_HOST}:{MYSQL_PORT}")
    print(f"  user = {MYSQL_USER!r}")
    print(f"  db   = {MYSQL_CLEAN_DB!r}")
    print(f"  pass = {'*' * len(MYSQL_PASSWORD) if MYSQL_PASSWORD else '(空)'}")

    print("\n[1/3] TCP 握手探测 ...")
    if not _tcp_probe(MYSQL_HOST, MYSQL_PORT):
        print("  ✗ 端口不可达，后续步骤跳过")
        return

    print("  ✓ TCP 端口可达")

    print("\n[2/3] MySQL 协议握手（pymysql）...")
    try:
        import pymysql
    except ImportError:
        print("  ✗ pymysql 未安装")
        return

    start = time.perf_counter()
    try:
        conn = pymysql.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            connect_timeout=10,
            read_timeout=10,
            write_timeout=10,
            charset="utf8mb4",
        )
        elapsed = (time.perf_counter() - start) * 1000
        print(f"  ✓ 握手成功（{elapsed:.0f}ms）")

        with conn.cursor() as cur:
            cur.execute("SELECT VERSION(), @@hostname, @@max_connections")
            ver, hostname, max_conn = cur.fetchone()
            print(f"     server_version = {ver}")
            print(f"     hostname       = {hostname}")
            print(f"     max_connections = {max_conn}")

        print("\n[3/3] 切换到目标数据库 + 行数采样 ...")
        conn.select_db(MYSQL_CLEAN_DB)
        with conn.cursor() as cur:
            cur.execute("SHOW TABLES")
            tables = [row[0] for row in cur.fetchall()]
            print(f"  ✓ 数据库 {MYSQL_CLEAN_DB!r} 含 {len(tables)} 张表：{tables}")

            for t in tables[:5]:
                cur.execute(f"SELECT COUNT(*) FROM `{t}`")
                cnt = cur.fetchone()[0]
                print(f"     - {t:30s} {cnt:>10,} 行")

        conn.close()
    except pymysql.err.OperationalError as e:
        code = e.args[0] if e.args else None
        print(f"  ✗ OperationalError (code={code}): {e}")
        if code == 1045:
            print("     → 含义：账号或密码错误")
            print("     → 解决：请确认 MYSQL_USER / MYSQL_PASSWORD 是否与云服务器一致")
        elif code == 2003:
            print("     → 含义：无法连接到服务器（端口未开放或被防火墙拦截）")
        elif code == 1044:
            print("     → 含义：用户无权访问该数据库")
    except Exception as e:
        print(f"  ✗ {type(e).__name__}: {e}")


def check_milvus() -> None:
    _print_header(f"Milvus  {MILVUS_HOST}:{MILVUS_PORT}")
    print(f"  uri  = http://{MILVUS_HOST}:{MILVUS_PORT}")

    print("\n[1/2] TCP 握手探测 ...")
    if not _tcp_probe(MILVUS_HOST, MILVUS_PORT):
        print("  ✗ 端口不可达，后续步骤跳过")
        return

    print("  ✓ TCP 端口可达")

    print("\n[2/2] pymilvus 连接 + 列举集合 ...")
    try:
        from pymilvus import MilvusClient
    except ImportError:
        print("  ✗ pymilvus 未安装")
        return

    uri = f"http://{MILVUS_HOST}:{MILVUS_PORT}"
    start = time.perf_counter()
    try:
        client = MilvusClient(uri=uri, timeout=10)
        elapsed = (time.perf_counter() - start) * 1000
        print(f"  ✓ 连接成功（{elapsed:.0f}ms）")

        collections = client.list_collections()
        print(f"  ✓ 集合数：{len(collections)}")
        for name in collections:
            try:
                stats = client.get_collection_stats(name)
                row_count = stats.get("row_count", "?")
                print(f"     - {name:35s} {row_count:>10} 行")
            except Exception as e:
                print(f"     - {name:35s} (统计失败：{e})")

    except Exception as e:
        print(f"  ✗ {type(e).__name__}: {e}")
        msg = str(e).lower()
        if "unauthorized" in msg or "401" in msg:
            print("     → 含义：Milvus 启用了认证，需要 token")
        elif "connection refused" in msg:
            print("     → 含义：gRPC 端口未对外开放")


def main() -> int:
    print("云服务器连通性测试")
    print(f"  MySQL  → {MYSQL_HOST}:{MYSQL_PORT}  user={MYSQL_USER!r}  db={MYSQL_CLEAN_DB!r}")
    print(f"  Milvus → {MILVUS_HOST}:{MILVUS_PORT}")

    check_mysql()
    check_milvus()

    print("\n" + "=" * 60)
    print("  测试结束")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())