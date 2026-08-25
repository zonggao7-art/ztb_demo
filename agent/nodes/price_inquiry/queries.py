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
        return []
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
        logger.debug("直接查询 company_penalty 失败: %s", e)
        return []
    finally:
        _release_connection(conn)

def _query_company_data(intent: SearchIntent) -> dict[str, Any]:
    """公司信息查询：company_info + company_penalty 双路查询。

    penalty_check 查询时优先直接查 company_penalty（按 company_name），
    避免依赖 company_info 中可能不存在的目标企业。
    """
    hf = intent.hard_filters

    # ── P0-1：penalty_check 直接查 company_penalty ──
    if intent.need_penalty_check:
        target_company = hf.company_name or (
            intent.exact_tokens[0] if intent.exact_tokens else None
        )
        if target_company:
            direct_penalty = _query_penalty_by_company_name(target_company)
            if direct_penalty:
                logger.info(
                    "[PENALTY_DIRECT] 直接查 company_penalty 命中 %d 条 (company=%s)",
                    len(direct_penalty), target_company[:30],
                )
                return {
                    "records": direct_penalty,
                    "total_found": len(direct_penalty),
                    "queried_tables": [f"{_CLEAN_DB}.company_penalty"],
                    "sql_count": 1,
                    "total_sql_time": 0.0,
                }

    # ── 原有逻辑：company_info 主查询 + credit_code 联查 ──
    tables = ["company_info"]
    result = _query_tables(tables, intent)

    # ── P0-1b：penalty_check 精确匹配过滤 ──
    # 当直接查 company_penalty 无结果时，降级到 company_info 路径。
    # 但语义召回可能返回无关企业，必须过滤保留目标企业。
    if intent.need_penalty_check and hf.company_name and result["records"]:
        target = hf.company_name
        matched = [rec for rec in result["records"] if rec.get("company_name") == target]
        if not matched:
            logger.info(
                "[PENALTY_FILTER] company_info 中未找到精确匹配 '%s'，返回空结果", target[:30]
            )
            result["records"] = []
        else:
            result["records"] = matched

    # 条件联查 company_penalty
    if intent.need_penalty_check and result["records"]:
        credit_codes = set()
        for rec in result["records"]:
            cc = rec.get("credit_code")
            if cc and cc not in ("", "None", "未提供"):
                credit_codes.add(cc)

        if credit_codes:
            penalty_results: list[dict] = []
            conn = _get_connection(_CLEAN_DB)
            if conn:
                try:
                    with conn.cursor(pymysql.cursors.DictCursor) as cur:
                        for cc in credit_codes:
                            try:
                                cur.execute(
                                    "SELECT * FROM `company_penalty` WHERE `credit_code` = %s ORDER BY `penalty_date` DESC",
                                    (cc,)
                                )
                                for row in cur.fetchall():
                                    clean_row = _clean_result_row(row)
                                    clean_row["_source_db"] = _CLEAN_DB
                                    clean_row["_source_table"] = "company_penalty"
                                    penalty_results.append(clean_row)
                            except Exception as e:
                                logger.debug("联查 company_penalty 失败: %s", e)
                finally:
                    _release_connection(conn)

            # 合并 penalty 数据到主结果
            if penalty_results:
                # 将 penalty 数据按 credit_code 合并到对应 company_info 记录
                penalty_by_cc: dict[str, list[dict]] = {}
                for pr in penalty_results:
                    cc = pr.get("credit_code", "")
                    if cc:
                        penalty_by_cc.setdefault(cc, []).append(pr)

                merged_records = []
                for rec in result["records"]:
                    cc = rec.get("credit_code", "")
                    penalties = penalty_by_cc.get(cc, [])
                    if penalties:
                        # 将第一个 penalty 记录的关键字段合并到 company_info 记录
                        p = penalties[0]
                        rec["penalty_date"] = p.get("penalty_date", "")
                        rec["illegal_behavior"] = p.get("illegal_behavior", "")
                        rec["penalty_result"] = p.get("penalty_result", "")
                        rec["law_enforcement_unit"] = p.get("law_enforcement_unit", "")
                    merged_records.append(rec)

                result["records"] = merged_records
                result["queried_tables"].append(f"{_CLEAN_DB}.company_penalty")

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
