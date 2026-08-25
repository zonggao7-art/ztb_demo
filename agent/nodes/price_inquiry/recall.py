"""多级降级检索执行链 — SQL 超时执行、混合重排序、回表补列与通用查询引擎。"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import replace
from typing import Any, Optional

import pymysql

from .db import _CLEAN_DB, _get_connection, _get_settings, _release_connection
from .models import SearchIntent
from .schema import _get_classification
from .semantic import _semantic_recall_candidates
from .sql_builders import (
    _build_candidate_sql,
    _build_constraint_conditions,
    _build_full_scan_sql,
    _build_like_fallback_sql,
    _build_vector_recall_sql,
    _has_preference_filters,
    _strip_preference_filters,
)

logger = logging.getLogger(__name__)

_RECALL_STAGE_WEIGHTS = {
    0: 1.08,  # Milvus 语义召回
    1: 1.00,  # OR FULLTEXT
    2: 0.92,  # AND FULLTEXT
    3: 0.82,  # LIKE 回退
    4: 0.72,  # 逐关键词拆分
    5: 0.55,  # 全表扫描兜底
}

def _profile_execute(cur: Any, sql: str, params: tuple[Any, ...]) -> float:
    """执行 SQL 并记录耗时；返回耗时秒数。"""
    start = time.perf_counter()
    cur.execute(sql, params)
    elapsed = time.perf_counter() - start
    logger.info(
        "[SQL_PROFILE] cost=%.3fs sql=%s params=%s",
        elapsed,
        re.sub(r"\s+", " ", sql).strip(),
        params,
    )
    return elapsed

def _hybrid_score(intent: SearchIntent, text: str) -> float:
    """基于语义关键词与精确 token 对文本打分。"""
    score = 0.0
    text_lower = text.lower()
    for kw in intent.semantic_keywords:
        if kw in text_lower:
            score += 1.0 * text_lower.count(kw.lower())
    for token in intent.exact_tokens:
        if token in text:
            score += 10.0
    return score

def _rank_records(
    records: list[dict[str, Any]],
    intent: SearchIntent,
    top_k: int = 20,
) -> list[dict[str, Any]]:
    """对候选记录进行混合重排序并截断。"""
    # 当 sort_by 为金额/时间排序时，降低关键词得分权重
    kw_weight = 0.3 if intent.sort_by and intent.sort_by not in ("relevance", None) else 1.0

    scored = []
    for rec in records:
        text_parts = [str(v) for k, v in rec.items()
                      if k not in {"_source_db", "_source_table", "_score_", "_vector_score_",
                                   "_recall_stage_", "_hybrid_score_"} and v is not None]
        text = " ".join(text_parts)
        mysql_score = float(rec.get("_score_", 0.0) or 0.0)
        vector_score = float(rec.get("_vector_score_", 0.0) or 0.0) * 2.0
        recall_stage = int(rec.get("_recall_stage_", 1) or 1)
        stage_weight = _RECALL_STAGE_WEIGHTS.get(recall_stage, 1.0)
        semantic_score = _hybrid_score(intent, text) * kw_weight
        rec = dict(rec)
        rec["_hybrid_score_"] = (mysql_score + vector_score + semantic_score) * stage_weight
        scored.append(rec)

    scored.sort(key=lambda x: x["_hybrid_score_"], reverse=True)
    return scored[:top_k]

def _clean_result_row(row: dict[str, Any]) -> dict[str, Any]:
    clean_row: dict[str, Any] = {}
    for k, v in row.items():
        if k in {"_score_", "_vector_score_"}:
            clean_row[k] = float(v or 0.0)
        elif k == "_recall_stage_":
            clean_row[k] = int(v or 1)
        elif isinstance(v, bytes):
            clean_row[k] = v.decode("utf-8", errors="replace")
        elif hasattr(v, "isoformat"):
            clean_row[k] = v.isoformat()
        else:
            clean_row[k] = str(v) if v is not None else ""
    return clean_row

def _merge_result_record(
    record_map: dict[tuple[str, str], dict[str, Any]],
    row: dict[str, Any],
) -> None:
    table_name = str(row.get("_source_table", ""))
    row_id = str(row.get("_id_", ""))
    if not table_name or not row_id:
        return

    key = (table_name, row_id)
    existing = record_map.get(key)
    if existing is None:
        record_map[key] = row
        return

    merged = dict(existing)
    merged["_score_"] = max(
        float(existing.get("_score_", 0.0) or 0.0),
        float(row.get("_score_", 0.0) or 0.0),
    )
    merged["_vector_score_"] = max(
        float(existing.get("_vector_score_", 0.0) or 0.0),
        float(row.get("_vector_score_", 0.0) or 0.0),
    )
    merged["_recall_stage_"] = min(
        int(existing.get("_recall_stage_", 5) or 5),
        int(row.get("_recall_stage_", 5) or 5),
    )
    for field, value in row.items():
        if field not in merged or merged[field] in ("", None):
            merged[field] = value
    record_map[key] = merged

def _execute_sql_fetch_rows(
    conn: pymysql.Connection,
    sql: str,
    params: tuple[Any, ...],
) -> tuple[list[dict[str, Any]], float]:
    with conn.cursor(pymysql.cursors.DictCursor) as cur:
        elapsed = _profile_execute(cur, sql, params)
        rows = cur.fetchall()
        # P0-9：pymysql DictCursor.fetchall() 非空返回 list，空结果返回 tuple，
        # 类型不一致导致 _query_tables 中 semantic_rows + recall_rows 的
        # TypeError: can only concatenate tuple (not "list") to tuple
        if not isinstance(rows, list):
            rows = list(rows)
    return rows, elapsed

_sql_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sql-query")

def _execute_sql_with_timeout(
    conn: pymysql.Connection,
    sql: str,
    params: tuple[Any, ...],
    table: str,
    stage: str,
) -> tuple[list[dict[str, Any]], float]:
    """带超时的 SQL 执行包装器。

    使用 ThreadPoolExecutor 实现跨平台超时控制，超时后返回空结果并记录日志。
    """
    settings = _get_settings()
    timeout = settings.sql_query_timeout

    future = _sql_executor.submit(_execute_sql_fetch_rows, conn, sql, params)
    try:
        rows, elapsed = future.result(timeout=timeout)
        return rows, elapsed
    except FutureTimeoutError:
        logger.warning(
            "[SQL_TIMEOUT] 查询超时(%ds): table=%s stage=%s sql=%s",
            timeout, table, stage, re.sub(r"\s+", " ", sql)[:200],
        )
        return [], 0.0

def _execute_recall_chain_core(
    conn: pymysql.Connection,
    table_name: str,
    classification: dict[str, list[str]],
    intent: SearchIntent,
) -> tuple[list[dict[str, Any]], int, float]:
    """P1-1：多级降级检索链。

    Level 1: OR FULLTEXT
    Level 2: LIKE 通配
    Level 3: 逐关键词拆分重试（单关键词 FULLTEXT / LIKE）
    Level 4: 全表扫描兜底（LIMIT 100）

    阶段编号（写入 _recall_stage_）与 _RECALL_STAGE_WEIGHTS 对应：
    1=FULLTEXT_OR、3=LIKE_FALLBACK、4=SPLIT_KEYWORD、5=FULL_SCAN。
    """
    total_sql_time = 0.0
    sql_count = 0
    keywords = [kw for kw in intent.semantic_keywords if kw]

    strategies: list[tuple[int, str, Optional[tuple[str, tuple[Any, ...]]]]] = [
        (1, "FULLTEXT_OR", _build_candidate_sql(table_name, classification, intent)),
    ]
    strategies.append((3, "LIKE_FALLBACK", _build_like_fallback_sql(table_name, classification, intent)))

    for stage, label, sql_tuple in strategies:
        if sql_tuple is None:
            continue
        try:
            rows, elapsed = _execute_sql_with_timeout(conn, sql_tuple[0], sql_tuple[1], table_name, label)
            total_sql_time += elapsed
            sql_count += 1
            if rows:
                for row in rows:
                    row["_recall_stage_"] = stage
                logger.info("[RECALL_CHAIN] table=%s stage=%s rows=%d", table_name, label, len(rows))
                return rows, sql_count, total_sql_time
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
                    rows, elapsed = _execute_sql_with_timeout(conn, retry_sql[0], retry_sql[1], table_name, f"FULLTEXT_RETRY_{kw}")
                    total_sql_time += elapsed
                    sql_count += 1
                    for row in rows:
                        row_id = str(row.get("_id_", ""))
                        if row_id and row_id not in seen_ids:
                            row["_recall_stage_"] = 4
                            split_rows.append(row)
                            seen_ids.add(row_id)
                except Exception as e:
                    logger.debug("逐关键词 FULLTEXT 重试失败 %s.%s: %s", _CLEAN_DB, table_name, e)

            like_retry = _build_like_fallback_sql(
                table_name,
                classification,
                replace(intent, semantic_keywords=[kw]),
            )
            if like_retry is not None:
                try:
                    rows, elapsed = _execute_sql_with_timeout(conn, like_retry[0], like_retry[1], table_name, f"LIKE_RETRY_{kw}")
                    total_sql_time += elapsed
                    sql_count += 1
                    for row in rows:
                        row_id = str(row.get("_id_", ""))
                        if row_id and row_id not in seen_ids:
                            row["_recall_stage_"] = 4
                            split_rows.append(row)
                            seen_ids.add(row_id)
                except Exception as e:
                    logger.debug("逐关键词 LIKE 重试失败 %s.%s: %s", _CLEAN_DB, table_name, e)

        if split_rows:
            logger.info("[RECALL_CHAIN] table=%s stage=SPLIT_KEYWORD rows=%d", table_name, len(split_rows))
            return split_rows, sql_count, total_sql_time

    full_scan = _build_full_scan_sql(table_name, classification, intent)
    if full_scan is not None:
        try:
            rows, elapsed = _execute_sql_with_timeout(conn, full_scan[0], full_scan[1], table_name, "FULL_SCAN")
            total_sql_time += elapsed
            sql_count += 1
            if rows:
                for row in rows:
                    row["_recall_stage_"] = 5
                logger.info("[RECALL_CHAIN] table=%s stage=FULL_SCAN rows=%d", table_name, len(rows))
                return rows, sql_count, total_sql_time
        except Exception as e:
            logger.debug("全表扫描兜底失败 %s.%s: %s", _CLEAN_DB, table_name, e)

    return [], sql_count, total_sql_time

def _log_recall_funnel(
    conn: pymysql.Connection,
    table_name: str,
    classification: dict[str, list[str]],
    intent: SearchIntent,
) -> None:
    """P0-3：召回为空时输出过滤漏斗数据，量化硬过滤的淘汰规模。"""
    try:
        constraint_conds, constraint_params = _build_constraint_conditions(
            table_name, classification, intent
        )
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM `{table_name}`")
            total_row = cur.fetchone()
            total_rows = int(total_row[0]) if total_row else 0
            where_clause = " AND ".join(constraint_conds) if constraint_conds else "1=1"
            cur.execute(
                f"SELECT COUNT(*) FROM `{table_name}` WHERE {where_clause}",
                tuple(constraint_params),
            )
            kept_row = cur.fetchone()
            kept_rows = int(kept_row[0]) if kept_row else 0
        logger.info(
            "[RECALL_FUNNEL] table=%s total_rows=%d constraint_only_rows=%d",
            table_name,
            total_rows,
            kept_rows,
        )
    except Exception as e:
        logger.debug("召回漏斗统计失败 %s.%s: %s", _CLEAN_DB, table_name, e)

def _execute_recall_chain_for_table(
    conn: pymysql.Connection,
    table_name: str,
    classification: dict[str, list[str]],
    intent: SearchIntent,
) -> tuple[list[dict[str, Any]], int, float]:
    """多级降级检索链入口（P0-2：全硬过滤链零行时自动放宽偏好性过滤重试）。

    首遍使用完整硬过滤（约束性 + 偏好性）执行五级降级链；
    若全部阶段零行且存在偏好性过滤，则剥离偏好性条件重跑一遍，
    避免枚举失配/实体名差异导致的零召回。
    """
    rows, sql_count, total_sql_time = _execute_recall_chain_core(
        conn, table_name, classification, intent
    )
    if rows or not _has_preference_filters(intent.hard_filters):
        return rows, sql_count, total_sql_time

    _log_recall_funnel(conn, table_name, classification, intent)
    logger.info(
        "[RECALL_RELAX] table=%s 全硬过滤链零行，放宽偏好性过滤重试", table_name
    )
    relaxed_intent = replace(
        intent, hard_filters=_strip_preference_filters(intent.hard_filters, intent.query_type)
    )
    relaxed_rows, relaxed_sql_count, relaxed_sql_time = _execute_recall_chain_core(
        conn, table_name, classification, relaxed_intent
    )
    if relaxed_rows:
        logger.info(
            "[RECALL_RELAX] table=%s 放宽后召回 %d 行", table_name, len(relaxed_rows)
        )
    return relaxed_rows, sql_count + relaxed_sql_count, total_sql_time + relaxed_sql_time

def _query_semantic_rows(
    conn: pymysql.Connection,
    table_name: str,
    classification: dict[str, list[str]],
    intent: SearchIntent,
    semantic_ids: dict[str, float],
) -> tuple[list[dict[str, Any]], int, float]:
    if not semantic_ids:
        return [], 0, 0.0

    sql_tuple = _build_vector_recall_sql(
        table_name, classification, intent, list(semantic_ids.keys())
    )
    if sql_tuple is None:
        return [], 0, 0.0

    try:
        rows, elapsed = _execute_sql_with_timeout(conn, sql_tuple[0], sql_tuple[1], table_name, "VECTOR_RECALL")
    except Exception as e:
        logger.debug("Milvus 回表失败 %s.%s: %s", _CLEAN_DB, table_name, e)
        return [], 1, 0.0

    for row in rows:
        row["_vector_score_"] = semantic_ids.get(str(row.get("_id_", "")), 0.0)
        row["_recall_stage_"] = 0
    return rows, 1, elapsed

def _enrich_rows_full_columns(
    conn: pymysql.Connection,
    table_name: str,
    classification: dict[str, list[str]],
    rows: list[dict[str, Any]],
) -> None:
    """按主键二次回表，用 SELECT * 补齐全部字段。

    召回阶段的三类 SQL 构建器（_build_candidate_sql / _build_full_scan_sql /
    _build_vector_recall_sql）只取 id + semantic 列用于搜索和排序，
    但输出模板声明的字段（如 credit_code、business_status、legal_person、
    registered_capital 等）分布在 time/exact/text 等其他分类列中，从未被 SELECT。

    此函数在命中行确定后做二次回表，以 OutputTemplate 声明的字段契约补齐所有列，
    使"未提供"仅出现在数据真空的字段，而非因 SQL 缺列导致的系统性问题。
    """
    if not rows:
        return

    id_col = classification.get("id", ["id"])[0]
    ids = [row.get("_id_") for row in rows if row.get("_id_") is not None]
    if not ids:
        return

    # 去重
    unique_ids = list(dict.fromkeys(ids))

    try:
        placeholders = ", ".join(["%s"] * len(unique_ids))
        sql = f"SELECT * FROM `{table_name}` WHERE `{id_col}` IN ({placeholders})"

        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(sql, tuple(unique_ids))
            full_rows = cur.fetchall()

        if not full_rows:
            return

        # 构建 id → 全量行 的查找表
        full_by_id: dict[Any, dict[str, Any]] = {}
        for fr in full_rows:
            full_by_id[fr.get(id_col)] = fr

        # 将全量字段合并到原始行（保留 _score_、_source_* 等召回元数据）
        for row in rows:
            rid = row.get("_id_")
            if rid is None:
                continue
            full = full_by_id.get(rid)
            if full is None:
                continue
            for k, v in full.items():
                if k not in row or row[k] is None or (isinstance(row[k], str) and row[k].strip() == ""):
                    row[k] = v

        logger.debug(
            "[COLUMN_ENRICH] table=%s ids=%d full_fetched=%d merged=%d",
            table_name, len(unique_ids), len(full_rows), len(rows),
        )
    except Exception as e:
        logger.warning(
            "[COLUMN_ENRICH] 二次回表补齐字段失败 table=%s: %s，降级使用原始字段",
            table_name, e,
        )

def _query_tables(tables: list[str], intent: SearchIntent) -> dict[str, Any]:
    """遍历指定表列表执行检索（通用查询引擎）。"""
    record_map: dict[tuple[str, str], dict[str, Any]] = {}
    queried_tables: list[str] = []
    sql_count = 0
    total_sql_time = 0.0

    conn = _get_connection(_CLEAN_DB)
    if conn is None:
        logger.error("无法连接数据库 %s", _CLEAN_DB)
        return {"records": [], "total_found": 0, "queried_tables": [], "sql_count": 0, "total_sql_time": 0.0}

    semantic_candidates = _semantic_recall_candidates(intent, tables)

    try:
        for table_name in tables:
            classification = _get_classification(table_name)
            if not classification:
                logger.warning("表 %s 无 schema 定义，跳过", table_name)
                continue

            semantic_rows, semantic_sql_count, semantic_sql_time = _query_semantic_rows(
                conn,
                table_name,
                classification,
                intent,
                semantic_candidates.get(table_name, {}),
            )
            sql_count += semantic_sql_count
            total_sql_time += semantic_sql_time

            recall_rows, recall_sql_count, recall_sql_time = _execute_recall_chain_for_table(
                conn, table_name, classification, intent
            )
            sql_count += recall_sql_count
            total_sql_time += recall_sql_time

            rows = semantic_rows + recall_rows
            if not rows:
                continue

            # ── SELECT 缺列修复：按主键二次回表取全部字段 ──
            _enrich_rows_full_columns(conn, table_name, classification, rows)

            for row in rows:
                row["_source_db"] = _CLEAN_DB
                row["_source_table"] = table_name
                clean_row = _clean_result_row(row)
                _merge_result_record(record_map, clean_row)
            queried_tables.append(f"{_CLEAN_DB}.{table_name}")

    except Exception as e:
        logger.debug("查询 %s 时出错: %s", _CLEAN_DB, e)
    finally:
        _release_connection(conn)

    results = list(record_map.values())
    ranked = _rank_records(results, intent, top_k=20)

    logger.info(
        "[SQL_PROFILE] summary: dbs=1 sql_count=%d total_sql_time=%.3fs "
        "candidate_rows=%d returned_rows=%d",
        sql_count,
        total_sql_time,
        len(results),
        len(ranked),
    )

    return {
        "records": ranked,
        "total_found": len(ranked),
        "queried_tables": list(set(queried_tables)),
        "sql_count": sql_count,
        "total_sql_time": total_sql_time,
    }
