"""多表并行召回（异步版）— 阶段 3 交付物。

设计对照（与同步 recall.py 语义一一对应）：
  - safe_execute：异步 SQL 客户端封装。asyncio.wait_for + run_blocking 包装
    同步 _execute_sql_fetch_rows；语句超时后归还连接池并抛 _SQLTimeoutError，
    中止当前表的后续查询（连接已被归还，禁止再被本表复用）。
  - _execute_recall_chain_core_async：五级降级检索链（FULLTEXT_OR → LIKE →
    逐关键词拆分 → 全表扫描兜底），逐语句走 safe_execute。
  - _execute_recall_chain_for_table_async：P0-2 全硬过滤链零行时放宽偏好性过滤重试。
  - _query_table_async：单表完整召回链（语义召回 + 检索链 + 二次回表补列），
    每表独立获取一个池连接 → 三表并发时连接上限 = 并发表数（受池 maxconnections 约束）。
  - query_tables_async：gather_limited 限流并行召回；合并/排序/去重放入 CPU executor。
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import replace
from typing import Any, Optional

from agent.runtime.async_bridge import gather_limited, run_blocking
from .db import _CLEAN_DB, _get_settings
from .db_async import acquire
from .models import SearchIntent
from .recall import (
    _clean_result_row,
    _enrich_rows_full_columns,
    _execute_recall_chain_for_table,
    _log_recall_funnel,
    _merge_result_record,
    _query_semantic_rows,
    _rank_records,
    _execute_sql_fetch_rows,
)
from .schema import _get_classification
from .semantic import _semantic_recall_candidates
from .sql_builders import (
    _build_candidate_sql,
    _build_full_scan_sql,
    _build_like_fallback_sql,
    _build_vector_recall_sql,
    _has_preference_filters,
    _strip_preference_filters,
)

logger = logging.getLogger(__name__)

# safe_execute 在服务端 MAX_EXECUTION_TIME 之上附加的客户端缓冲（秒）
_SQL_TIMEOUT_CUSHION_S = 0.5


class _SQLTimeoutError(Exception):
    """单条 SQL 语句超时信号。

    safe_execute 超时路径已把连接归还池，抛出本异常通知上层
    立即中止当前表的后续查询，避免复用已被归还（可能仍在被旧线程使用）的连接。
    """


# ═════════════════════════════════════════════════════════
# SQL 客户端封装
# ═════════════════════════════════════════════════════════

async def safe_execute(
    conn: Any,
    sql: str,
    params: tuple[Any, ...],
    table: str,
    stage: str,
) -> tuple[list[dict[str, Any]], float]:
    """异步 SQL 执行封装：语句超时 + 超时后连接归还池。

    Returns:
        (rows, elapsed) 成功执行返回行集与耗时
    Raises:
        _SQLTimeoutError: 语句超时（连接已归还池）
        原异常: SQL 执行错误（连接仍可用，由降级链继续）
    """
    settings = _get_settings()
    timeout = max(0.1, settings.sql_stmt_timeout_s + _SQL_TIMEOUT_CUSHION_S)
    try:
        return await asyncio.wait_for(
            run_blocking(_execute_sql_fetch_rows, conn, sql, params),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "[SQL_TIMEOUT] 语句超时(%.1fs) table=%s stage=%s sql=%s",
            timeout, table, stage, re.sub(r"\s+", " ", sql)[:200],
        )
        # 归还连接池（pool.cache 放回 idle 缓存并校正连接计数）；
        # 服务端 MAX_EXECUTION_TIME 会终止仍在执行的旧语句，ping=4 保证自愈
        try:
            await run_blocking(conn.close)
            logger.info("[SQL_TIMEOUT] 连接已归还池 table=%s stage=%s", table, stage)
        except Exception as e:
            logger.warning("[SQL_TIMEOUT] 归还连接失败: %s", e)
        raise _SQLTimeoutError(f"SQL timeout: table={table} stage={stage}") from None
    except Exception:
        raise


# ═════════════════════════════════════════════════════════
# 多级降级检索链（异步版，镜像同步 recall.py）
# ═════════════════════════════════════════════════════════

async def _execute_recall_chain_core_async(
    conn: Any,
    table_name: str,
    classification: dict[str, list[str]],
    intent: SearchIntent,
) -> tuple[list[dict[str, Any]], int, float]:
    """P1-1 多级降级检索链（异步版）。

    Level 1: OR FULLTEXT
    Level 2: LIKE 通配
    Level 3: 逐关键词拆分重试（单关键词 FULLTEXT / LIKE）
    Level 4: 全表扫描兜底（LIMIT 100）
    """
    total_sql_time = 0.0
    sql_count = 0
    keywords = [kw for kw in intent.semantic_keywords if kw]

    strategies: list[tuple[int, str, Optional[tuple[str, tuple[Any, ...]]]]] = [
        (1, "FULLTEXT_OR", _build_candidate_sql(table_name, classification, intent)),
    ]
    strategies.append(
        (3, "LIKE_FALLBACK", _build_like_fallback_sql(table_name, classification, intent))
    )

    for stage, label, sql_tuple in strategies:
        if sql_tuple is None:
            continue
        try:
            rows, elapsed = await safe_execute(
                conn, sql_tuple[0], sql_tuple[1], table_name, label
            )
            total_sql_time += elapsed
            sql_count += 1
            if rows:
                for row in rows:
                    row["_recall_stage_"] = stage
                logger.info("[RECALL_CHAIN] table=%s stage=%s rows=%d", table_name, label, len(rows))
                return rows, sql_count, total_sql_time
        except _SQLTimeoutError:
            raise
        except Exception as e:
            if "fulltext" in str(e).lower():
                logger.warning("[FULLTEXT_MISSING] db=%s table=%s: %s", _CLEAN_DB, table_name, e)
            else:
                logger.debug("检索链阶段 %s 查询失败 %s.%s: %s", label, _CLEAN_DB, table_name, e)

    if len(keywords) > 1:
        split_rows: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for kw in keywords[:3]:
            retry_sql = _build_candidate_sql(
                table_name,
                classification,
                replace(intent, semantic_keywords=[kw]),
                include_exact_tokens=False,
            )
            if retry_sql is not None:
                try:
                    rows, elapsed = await safe_execute(
                        conn, retry_sql[0], retry_sql[1], table_name, f"FULLTEXT_RETRY_{kw}"
                    )
                    total_sql_time += elapsed
                    sql_count += 1
                    for row in rows:
                        row_id = str(row.get("_id_", ""))
                        if row_id and row_id not in seen_ids:
                            row["_recall_stage_"] = 4
                            split_rows.append(row)
                            seen_ids.add(row_id)
                except _SQLTimeoutError:
                    raise
                except Exception as e:
                    logger.debug("逐关键词 FULLTEXT 重试失败 %s.%s: %s", _CLEAN_DB, table_name, e)

            like_retry = _build_like_fallback_sql(
                table_name,
                classification,
                replace(intent, semantic_keywords=[kw]),
            )
            if like_retry is not None:
                try:
                    rows, elapsed = await safe_execute(
                        conn, like_retry[0], like_retry[1], table_name, f"LIKE_RETRY_{kw}"
                    )
                    total_sql_time += elapsed
                    sql_count += 1
                    for row in rows:
                        row_id = str(row.get("_id_", ""))
                        if row_id and row_id not in seen_ids:
                            row["_recall_stage_"] = 4
                            split_rows.append(row)
                            seen_ids.add(row_id)
                except _SQLTimeoutError:
                    raise
                except Exception as e:
                    logger.debug("逐关键词 LIKE 重试失败 %s.%s: %s", _CLEAN_DB, table_name, e)

        if split_rows:
            logger.info("[RECALL_CHAIN] table=%s stage=SPLIT_KEYWORD rows=%d", table_name, len(split_rows))
            return split_rows, sql_count, total_sql_time

    full_scan = _build_full_scan_sql(table_name, classification, intent)
    if full_scan is not None:
        try:
            rows, elapsed = await safe_execute(
                conn, full_scan[0], full_scan[1], table_name, "FULL_SCAN"
            )
            total_sql_time += elapsed
            sql_count += 1
            if rows:
                for row in rows:
                    row["_recall_stage_"] = 5
                logger.info("[RECALL_CHAIN] table=%s stage=FULL_SCAN rows=%d", table_name, len(rows))
                return rows, sql_count, total_sql_time
        except _SQLTimeoutError:
            raise
        except Exception as e:
            logger.debug("全表扫描兜底失败 %s.%s: %s", _CLEAN_DB, table_name, e)

    return [], sql_count, total_sql_time


async def _execute_recall_chain_for_table_async(
    conn: Any,
    table_name: str,
    classification: dict[str, list[str]],
    intent: SearchIntent,
) -> tuple[list[dict[str, Any]], int, float]:
    """P0-2 全硬过滤链零行时自动放宽偏好性过滤重试（异步版）。"""
    rows, sql_count, total_sql_time = await _execute_recall_chain_core_async(
        conn, table_name, classification, intent
    )
    if rows or not _has_preference_filters(intent.hard_filters):
        return rows, sql_count, total_sql_time

    try:
        await run_blocking(_log_recall_funnel, conn, table_name, classification, intent)
    except Exception as e:
        logger.debug("召回漏斗统计失败 %s.%s: %s", _CLEAN_DB, table_name, e)
    logger.info("[RECALL_RELAX] table=%s 全硬过滤链零行，放宽偏好性过滤重试", table_name)
    relaxed_intent = replace(
        intent, hard_filters=_strip_preference_filters(intent.hard_filters, intent.query_type)
    )
    relaxed_rows, relaxed_sql_count, relaxed_sql_time = await _execute_recall_chain_core_async(
        conn, table_name, classification, relaxed_intent
    )
    if relaxed_rows:
        logger.info("[RECALL_RELAX] table=%s 放宽后召回 %d 行", table_name, len(relaxed_rows))
    return relaxed_rows, sql_count + relaxed_sql_count, total_sql_time + relaxed_sql_time


async def _query_semantic_rows_async(
    conn: Any,
    table_name: str,
    classification: dict[str, list[str]],
    intent: SearchIntent,
    semantic_ids: dict[str, float],
) -> tuple[list[dict[str, Any]], int, float]:
    """Milvus 语义召回结果回表（异步版）。"""
    if not semantic_ids:
        return [], 0, 0.0

    sql_tuple = _build_vector_recall_sql(
        table_name, classification, intent, list(semantic_ids.keys())
    )
    if sql_tuple is None:
        return [], 0, 0.0

    try:
        rows, elapsed = await safe_execute(
            conn, sql_tuple[0], sql_tuple[1], table_name, "VECTOR_RECALL"
        )
    except _SQLTimeoutError:
        raise
    except Exception as e:
        logger.debug("Milvus 回表失败 %s.%s: %s", _CLEAN_DB, table_name, e)
        return [], 1, 0.0

    for row in rows:
        row["_vector_score_"] = semantic_ids.get(str(row.get("_id_", "")), 0.0)
        row["_recall_stage_"] = 0
    return rows, 1, elapsed


# ═════════════════════════════════════════════════════════
# 单表召回链
# ═════════════════════════════════════════════════════════

async def _query_table_async(table_name: str, intent: SearchIntent) -> dict[str, Any]:
    """对单表执行完整召回链（独立池连接，支持多表并行）。

    Returns:
        {"table", "rows", "sql_count", "total_sql_time"} — rows 为已清洗记录。
    """
    classification = _get_classification(table_name)
    if not classification:
        logger.warning("表 %s 无 schema 定义，跳过", table_name)
        return {"table": table_name, "rows": [], "sql_count": 0, "total_sql_time": 0.0}

    sql_count = 0
    total_sql_time = 0.0

    try:
        semantic_candidates = await run_blocking(_semantic_recall_candidates, intent, [table_name])
    except Exception as e:
        logger.warning("表 %s 语义召回失败: %s", table_name, e)
        semantic_candidates = {}

    try:
        async with acquire() as conn:
            try:
                semantic_rows, sc, st = await _query_semantic_rows_async(
                    conn,
                    table_name,
                    classification,
                    intent,
                    semantic_candidates.get(table_name, {}),
                )
                sql_count += sc
                total_sql_time += st
            except _SQLTimeoutError:
                return {"table": table_name, "rows": [], "sql_count": sql_count, "total_sql_time": total_sql_time}

            try:
                recall_rows, rc, rt = await _execute_recall_chain_for_table_async(
                    conn, table_name, classification, intent
                )
                sql_count += rc
                total_sql_time += rt
            except _SQLTimeoutError:
                return {"table": table_name, "rows": [], "sql_count": sql_count, "total_sql_time": total_sql_time}

            rows = semantic_rows + recall_rows
            if rows:
                try:
                    await run_blocking(_enrich_rows_full_columns, conn, table_name, classification, rows)
                except Exception as e:
                    logger.debug("[COLUMN_ENRICH] 异步二次回表失败 %s.%s: %s", _CLEAN_DB, table_name, e)

            cleaned = [_clean_result_row(row) for row in rows]
            logger.info(
                "[QUERY_TABLE_ASYNC] table=%s rows=%d sql_count=%d sql_time=%.3fs",
                table_name, len(cleaned), sql_count, total_sql_time,
            )
            return {
                "table": table_name,
                "rows": cleaned,
                "sql_count": sql_count,
                "total_sql_time": total_sql_time,
            }
    except asyncio.TimeoutError:
        logger.warning("[DB_POOL] 表 %s 获取连接超时，跳过该表", table_name)
        return {"table": table_name, "rows": [], "sql_count": sql_count, "total_sql_time": total_sql_time}
    except Exception as e:
        logger.debug("查询 %s 时出错: %s", table_name, e)
        return {"table": table_name, "rows": [], "sql_count": sql_count, "total_sql_time": total_sql_time}


# ═════════════════════════════════════════════════════════
# 多表并行召回入口
# ═════════════════════════════════════════════════════════

def _merge_and_rank(
    table_results: list[Any],
    intent: SearchIntent,
    top_k: int = 20,
) -> tuple[list[dict[str, Any]], list[str], int, float]:
    """合并多表召回结果 + 去重 + 混合重排序（CPU executor 中执行）。"""
    record_map: dict[tuple[str, str], dict[str, Any]] = {}
    queried_tables: list[str] = []
    sql_count = 0
    total_sql_time = 0.0

    for res in table_results:
        if isinstance(res, BaseException):
            continue
        if not isinstance(res, dict):
            continue
        table_name = res.get("table")
        rows = res.get("rows") or []
        for row in rows:
            row["_source_db"] = _CLEAN_DB
            row["_source_table"] = table_name
            _merge_result_record(record_map, row)
        if rows:
            queried_tables.append(f"{_CLEAN_DB}.{table_name}")
        sql_count += int(res.get("sql_count", 0) or 0)
        total_sql_time += float(res.get("total_sql_time", 0.0) or 0.0)

    results = list(record_map.values())
    ranked = _rank_records(results, intent, top_k=top_k)

    logger.info(
        "[SQL_PROFILE] summary(async): tables=%d sql_count=%d total_sql_time=%.3fs "
        "candidate_rows=%d returned_rows=%d",
        len(table_results),
        sql_count,
        total_sql_time,
        len(results),
        len(ranked),
    )
    return ranked, list(dict.fromkeys(queried_tables)), sql_count, total_sql_time


async def query_tables_async(
    tables: list[str],
    intent: SearchIntent,
    *,
    deadline: Any = None,
    progress_callback=None,
) -> dict[str, Any]:
    """多表并行召回入口（阶段 3）。

    每表独立协程 + 独立池连接，gather_limited(limit=price_recall_concurrency)
    限制同时执行的表数；单表失败不阻断其余表；合并/去重/排序在 CPU executor。
    返回结构与同步 _query_tables 一致。
    """
    settings = _get_settings()
    limit = settings.price_recall_concurrency

    async def query_one(table_name: str):
        result = await _query_table_async(table_name, intent)
        if progress_callback and not isinstance(result, BaseException) and result.get("rows"):
            progress_callback(
                result.get("table", table_name),
                result.get("rows", []),
                "partial",
            )
        return result

    table_results = await gather_limited(
        [query_one(table_name) for table_name in tables],
        limit=limit,
        return_exceptions=True,
    )

    if progress_callback:
        for result in table_results:
            if isinstance(result, BaseException) or not isinstance(result, dict):
                continue
            rows = result.get("rows") or []
            if rows:
                progress_callback(result.get("table", ""), rows, "final")

    ranked, queried_tables, sql_count, total_sql_time = await run_blocking(
        _merge_and_rank, table_results, intent
    )

    return {
        "records": ranked,
        "total_found": len(ranked),
        "queried_tables": queried_tables,
        "sql_count": sql_count,
        "total_sql_time": total_sql_time,
    }
