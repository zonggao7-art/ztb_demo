"""Hybrid exact read-only adapter. No relaxed recall, arbitrary SQL or silent empty errors.

P0-11 bid filters remain restricted. Pool slots stay owned by the worker until
the real query finishes, even if the awaiting coroutine has been cancelled.
"""
from __future__ import annotations

import threading
from decimal import Decimal
from datetime import date, datetime
import pymysql
from dbutils.pooled_db import PooledDB

from agent.execution.context import current_run, RunStopped
from .base import make_tool_result

_pool = None
_pool_lock = threading.Lock()
TABLES = {
    # 六条业务线中的五个 SQL 工具。
    "query_company_registration": "company_info",
    "query_company_business_scope": "company_info",
    "query_company_penalty": "company_penalty",
    "query_project_award": "bid_project",
    "query_company_award_history": "bid_project",
}
PUBLIC_FIELDS = {
    "company_info": ["company_name", "credit_code", "legal_person", "registered_capital",
                     "establish_date", "business_status", "industry", "province", "city", "business_scope"],
    "company_penalty": ["company_name", "penalty_date", "illegal_behavior", "penalty_result", "law_enforcement_unit"],
    "bid_project": ["project_number", "project_name", "purchaser", "successful_bidder", "winning_amount", "winning_date"],
}

# 工具级返回字段是发布边界。共用一张表不代表工具可以看见该表的全部公开字段。
TOOL_PUBLIC_FIELDS = {
    "query_company_registration": [
        "company_name", "credit_code", "legal_person", "registered_capital",
        "establish_date", "business_status", "industry", "province", "city",
    ],
    "query_company_business_scope": ["company_name", "business_scope"],
    "query_company_penalty": PUBLIC_FIELDS["company_penalty"],
    "query_project_award": PUBLIC_FIELDS["bid_project"],
    "query_company_award_history": PUBLIC_FIELDS["bid_project"],
}

# 每个工具各自拥有固定检索字段和数据库列映射。
TOOL_FILTER_COLUMNS = {
    "query_company_registration": {"company_name": "company_name"},
    "query_company_business_scope": {"company_name": "company_name"},
    "query_company_penalty": {"company_name": "company_name"},
    "query_project_award": {"project_number": "project_number"},
    "query_company_award_history": {"company_name": "successful_bidder"},
}


def get_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            from agent.nodes.price_inquiry.db import _mysql_base_kwargs, _CLEAN_DB
            ctx = current_run()
            _pool = PooledDB(pymysql, mincached=0, maxcached=4,
                            maxconnections=ctx.settings.mysql_max_pool_size, blocking=False,
                            setsession=["SET SESSION TRANSACTION READ ONLY"],
                            database=_CLEAN_DB, **_mysql_base_kwargs())
        return _pool


def query(name, args):
    ctx = current_run()
    ctx.check()
    if name == "search_business_data":
        return search(args)
    table = TABLES[name]
    filters = {k: v for k, v in args.items() if k not in {"task_id", "top_k"} and v is not None}
    filter_columns = TOOL_FILTER_COLUMNS[name]
    if not filters or not set(filters) <= set(filter_columns):
        raise RunStopped("unsupported_filter")
    # 即使绕过外层 schema 直接调用，也不允许空值/超长参数进入数据库。
    # 名称的真实性、后缀和组织形式不是查询准入条件。
    for key, value in filters.items():
        max_length = 50 if key == "project_number" else 80
        if not isinstance(value, str) or not value.strip() or len(value) > max_length:
            raise RunStopped("invalid_query_input")
    conn = get_pool().connection()
    try:
        ctx.check()
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            timeout_ms = max(1, int(min(ctx.remaining(), ctx.settings.agent_tool_timeout_s) * 1000))
            cur.execute("SET SESSION MAX_EXECUTION_TIME=%s", (timeout_ms,))
            # Introspection only checks availability; identifiers are from fixed code whitelists.
            cur.execute(f"SHOW COLUMNS FROM `{table}`")
            columns = {row["Field"] for row in cur.fetchall()}
            fields = [f for f in TOOL_PUBLIC_FIELDS[name] if f in columns]
            conditions, params = [], []
            for key, value in filters.items():
                column = filter_columns[key]
                if column not in columns:
                    raise RunStopped("schema_unavailable")
                conditions.append(f"`{column}` = %s")
                params.append(value)
            if not fields:
                raise RunStopped("schema_unavailable")
            limit = min(args.get("top_k") or ctx.settings.agent_tool_default_top_k, 50)
            sql = ("SELECT " + ",".join(f"`{f}`" for f in fields) + f" FROM `{table}` WHERE "
                   + " AND ".join(conditions) + " LIMIT %s")
            cur.execute(sql, (*params, limit))
            rows = list(cur.fetchall())
            ctx.check()
        # Driver values become stable JSON snapshots before evidence identifiers are derived.
        rows = [{k: str(v) if isinstance(v, (Decimal, date, datetime)) else v for k, v in r.items()} for r in rows]
        return make_tool_result(data={"records": rows}, metadata={
            "source": f"mysql.ztb_clean.{table}", "queried_tables": [table],
            "row_count": len(rows), "sql_count": 1, "exact_scope": True,
            "complete_population": False, "amount_unit": ctx.settings.agent_amount_unit,
        })
    finally:
        # Rollback the read-only transaction BEFORE returning to the pool.
        try:
            conn.rollback()
        finally:
            conn.close()


def search(args):
    """Candidate search over the same three tables, without silent error recovery."""
    ctx = current_run()
    words = args.get("keywords") or []
    tables = args.get("tables") or list(PUBLIC_FIELDS)
    if not 1 <= len(words) <= 5 or not set(tables) <= set(PUBLIC_FIELDS):
        raise RunStopped("invalid_search_scope")
    if args.get("exact_tokens"):
        raise RunStopped("unsupported_filter")
    conn = get_pool().connection()
    rows, count = [], 0
    search_fields = {"company_info": {"company_name", "business_scope", "industry", "address"},
                     "company_penalty": {"company_name", "illegal_behavior", "penalty_result"},
                     "bid_project": {"purchaser", "successful_bidder"}}
    limit = min(args.get("top_k") or ctx.settings.agent_tool_default_top_k, 50)
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            for table in tables:
                ctx.check()
                cur.execute("SET SESSION MAX_EXECUTION_TIME=%s", (max(1, int(min(
                    ctx.remaining(), ctx.settings.sql_stmt_timeout_s) * 1000)),))
                cur.execute(f"SHOW COLUMNS FROM `{table}`")
                columns = {r["Field"] for r in cur.fetchall()}
                fields = [f for f in PUBLIC_FIELDS[table] if f in columns]
                searchable = search_fields[table] & columns
                if not fields or not searchable:
                    raise RunStopped("schema_unavailable")
                cur.execute(f"SHOW INDEX FROM `{table}`")
                indexes = {}
                for row in cur.fetchall():
                    if row.get("Index_type") == "FULLTEXT":
                        indexes.setdefault(row["Key_name"], []).append((row["Seq_in_index"], row["Column_name"]))
                fulltext = next((sorted(cols) for cols in indexes.values()
                                 if {c for _, c in cols} <= searchable), None)
                params = []
                if fulltext:
                    match = ",".join(f"`{column}`" for _, column in fulltext)
                    condition = f"MATCH({match}) AGAINST (%s IN NATURAL LANGUAGE MODE)"
                    params.append(" ".join(words))
                else:
                    clauses = []
                    for column in sorted(searchable):
                        for word in words:
                            clauses.append(f"`{column}` LIKE %s ESCAPE '='")
                            escaped = word.replace("=", "==").replace("%", "=%").replace("_", "=_")
                            params.append(f"%{escaped}%")
                    condition = " OR ".join(clauses)
                ctx.check()
                cur.execute("SELECT " + ",".join(f"`{f}`" for f in fields) +
                            f" FROM `{table}` WHERE ({condition}) LIMIT %s", (*params, limit))
                rows.extend(dict(r) for r in cur.fetchall())
                count += 1
                ctx.check()
        rows = [{k: str(v) if isinstance(v, (Decimal, date, datetime)) else v for k, v in r.items()} for r in rows]
        return make_tool_result(data={"records": rows[:limit]}, metadata={
            "source": "mysql.ztb_clean", "queried_tables": tables, "row_count": len(rows[:limit]),
            "sql_count": count, "exact_scope": False, "complete_population": False,
            "amount_unit": ctx.settings.agent_amount_unit})
    finally:
        try:
            conn.rollback()
        finally:
            conn.close()
