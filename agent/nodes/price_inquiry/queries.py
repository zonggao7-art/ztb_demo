"""业务专用查询 — 公司/处罚/招标/聚合/全表五条查询路径。"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import pymysql

from .db import _CLEAN_DB, _get_connection, _release_connection
from .models import SearchIntent
from .recall import _clean_result_row, _query_tables
from .sql_builders import _build_order_clause

logger = logging.getLogger(__name__)

def _query_penalty_by_company_name(company_name: str) -> list[dict[str, Any]]:
    """P0-1：直接用公司名查询 company_penalty 表（不依赖 company_info）。

    用于 penalty_check 查询，避免目标企业不在 company_info 中时无法获取处罚记录。
    P0-11 修复：使用精确匹配替代 LIKE 模糊匹配，杜绝无差别无脑召回。
    """
    conn = _get_connection(_CLEAN_DB)
    if conn is None:
        raise ConnectionError("company_penalty connection unavailable")
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                """SELECT * FROM `company_penalty`
                   WHERE `company_name` = %s
                   ORDER BY `penalty_date` DESC
                   LIMIT 50""",
                (company_name,),
            )
            rows = cur.fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            clean_row = _clean_result_row(row)
            clean_row["_source_db"] = _CLEAN_DB
            clean_row["_source_table"] = "company_penalty"
            results.append(clean_row)
        return results
    except Exception as e:
        raise ConnectionError("company_penalty query failed") from e
    finally:
        _release_connection(conn)

def _query_company_data(intent: SearchIntent) -> dict[str, Any]:
    """Keep company profiles and penalties as separate, source-tagged records."""
    needs_penalty = intent.need_penalty_check or intent.query_type == "penalty_check"
    result = _query_tables(["company_info"], intent)
    statuses = dict(result.get("table_status", {}))
    # Older callers without a status contract cannot certify a successful empty query.
    statuses.setdefault("company_info", "success" if result.get("records") else "failed")
    rows = [dict(r, _source_table="company_info") for r in result.get("records", [])]
    if needs_penalty:
        target = intent.hard_filters.company_name or (intent.exact_tokens[0] if intent.exact_tokens else "")
        try:
            if not target:
                raise ValueError("missing_company")
            penalties = _query_penalty_by_company_name(target)
            statuses["company_penalty"] = "success" if penalties else "empty"
            rows.extend(dict(r, _source_table="company_penalty") for r in penalties)
        except TimeoutError:
            statuses["company_penalty"] = "timeout"
        except Exception:
            statuses["company_penalty"] = "failed"
        result["sql_count"] = result.get("sql_count", 0) + 1
    result.update(records=rows, total_found=len(rows), table_status=statuses,
                  queried_tables=[f"{_CLEAN_DB}.{t}" for t in statuses])
    return result

def _query_bidding_data(intent: SearchIntent) -> dict[str, Any]:
    """招投标历史交易查询：bid_project。"""
    # 聚合查询特殊处理
    if intent.aggregation:
        aggregation_result = _query_bidding_aggregation(intent)
        if aggregation_result:
            return aggregation_result

    return _query_tables(["bid_project"], intent)

def _query_bidding_aggregation(intent: SearchIntent) -> Optional[dict[str, Any]]:
    """竞价聚合查询（跳过 FULLTEXT，直接走聚合 SQL）。"""
    conn = _get_connection(_CLEAN_DB)
    if conn is None:
        return None

    hf = intent.hard_filters
    conditions: list[str] = []
    params: list[Any] = []

    if hf.successful_bidder:
        conditions.append("`successful_bidder` = %s")
        params.append(hf.successful_bidder)
    if hf.purchaser:
        conditions.append("`purchaser` = %s")
        params.append(hf.purchaser)
    if hf.province:
        # 地区类（P0 优化 5.2.3：LIKE 前缀模糊匹配，兼容简称/全称差异）
        conditions.append("`province` LIKE %s")
        params.append(f"{hf.province}%")
    if hf.time_range:
        if hf.time_range.get("start"):
            conditions.append("`winning_date` >= %s")
            params.append(hf.time_range["start"])
        if hf.time_range.get("end"):
            conditions.append("`winning_date` <= %s")
            params.append(hf.time_range["end"])
    if hf.project_stage:
        # 状态类（P0 优化 5.2.3：LIKE 前缀模糊匹配）
        conditions.append("`project_stage` LIKE %s")
        params.append(f"{hf.project_stage}%")
    if hf.winning_amount_range:
        if hf.winning_amount_range.get("min") is not None:
            conditions.append("`winning_amount` >= %s")
            params.append(hf.winning_amount_range["min"])
        if hf.winning_amount_range.get("max") is not None:
            conditions.append("`winning_amount` <= %s")
            params.append(hf.winning_amount_range["max"])

    top_n = intent.top_n or 1
    order_clause = _build_order_clause(intent)

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    try:
        sql = (
            f"SELECT project_name, project_number, purchaser, successful_bidder, "
            f"winning_amount, winning_date, subject_matter, agent, project_stage, "
            f"project_category, province, city, publish_date "
            f"FROM `bid_project` "
            f"WHERE {where_clause} "
            f"{order_clause} "
            f"LIMIT {top_n}"
        )

        total_sql_time = 0.0
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            start = time.perf_counter()
            cur.execute(sql, tuple(params))
            total_sql_time = time.perf_counter() - start
            rows = cur.fetchall()

        if not rows:
            return None

        results: list[dict] = []
        queried_tables = [f"{_CLEAN_DB}.bid_project"]
        for row in rows:
            clean_row = _clean_result_row(row)
            clean_row["_source_db"] = _CLEAN_DB
            clean_row["_source_table"] = "bid_project"
            results.append(clean_row)

        logger.info(
            "[AGGREGATION] aggregation=%s top_n=%d rows=%d sql_time=%.3fs",
            intent.aggregation, top_n, len(results), total_sql_time,
        )

        return {
            "records": results,
            "total_found": len(results),
            "queried_tables": queried_tables,
            "sql_count": 1,
            "total_sql_time": total_sql_time,
            "aggregation": {
                "aggregation_type": intent.aggregation,
                "top_n": top_n,
            },
        }
    except Exception as e:
        logger.warning("聚合查询失败: %s", e)
        return None
    finally:
        _release_connection(conn)

def _query_all_tables(intent: SearchIntent) -> dict[str, Any]:
    """all 兜底模式：遍历 3 张核心表（product_info 已下线）。"""
    return _query_tables(
        ["company_info", "company_penalty", "bid_project"],
        intent,
    )
