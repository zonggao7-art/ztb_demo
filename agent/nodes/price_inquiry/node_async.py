# -*- coding: utf-8 -*-
"""询价节点（异步版，阶段 3）— MySQL 有界连接池 + 三表并行召回。

与同步版 agent.nodes.price_inquiry.node.node_price_inquiry 语义对齐：
  - business_result / messages 结构完全一致；
  - 复用 node.py 的守卫函数（_build_capability_boundary_answer /
    _build_unified_guidance / _has_valid_query_entity）与二级路由表；
  - 差异仅在 I/O：
    - LLM 意图解析走 await _parse_unified_intent_async（ainvoke）；
    - SQL 检索走 await query_tables_async（三表并行 + 池连接 + 语句级超时），
      替代同步的 _sql_executor 线程池提交。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time

from langchain_core.messages import AIMessage

from ..answer_templates import render_answer
from ..output_templates import _apply_output_template, get_template
from ...streaming import EventType
from ...streaming.context import _STREAM_ACTIVE, emit
from ...state import AgentState
from .db import _get_settings
from .enum_norm import _normalize_intent_enums
from .intent import (
    _build_llm,
    _extract_project_number_candidate,
    _is_valid_company_name,
    _looks_like_code,
    _parse_unified_intent_async,
    _safe_parse_intent,
)
from .node import (
    _QUERY_INTENT_KEYWORDS,
    _SUB_ROUTE_MAP,
    _build_capability_boundary_answer,
    _build_unified_guidance,
    _has_valid_query_entity,
)
from .recall_async import query_tables_async

logger = logging.getLogger(__name__)


async def node_price_inquiry_async(state: AgentState) -> dict:
    """智能询价节点（异步版）— 意图解析 ainvoke + 三表并行召回。

    流程与同步 node_price_inquiry 完全一致：
    统一意图解析 → 二级路由分发 → SQL 检索 → 输出字段筛选，
    保留全部 P0-11/P0-12 前置/后置守卫与业务语义。
    """
    node_start = time.perf_counter()
    settings = _get_settings()
    total_timeout = settings.node_total_timeout

    messages = state.get("messages", [])
    final_sent = False

    def finalize(payload: dict):
        nonlocal final_sent
        if not final_sent:
            emit(EventType.FINAL, payload)
            final_sent = True

    def _emit_table(table_name: str, rows: list[dict], phase: str):
        emit(EventType.TABLE, {
            "route": "price_inquiry",
            "table": table_name,
            "phase": phase,
            "records": rows,
            "display_count": len(rows),
        })

    if not messages:
        answer = "抱歉，没有收到您的问题，请重新输入。"
        finalize({"answer": answer, "business_result": {"branch": "price_inquiry"}})
        return {
            "business_result": {
                "branch": "price_inquiry",
                "answer": "抱歉，没有收到您的问题，请重新输入。",
                "data": {"records": []},
            },
        }

    question = str(messages[-1].content)
    logger.info("price_inquiry(async): 查询 — %s", question[:80])

    # Step 1：统一意图解析（一次 LLM ainvoke 完成 sub_route + hard_filters + query_type）
    llm_start = time.perf_counter()
    llm = _build_llm()
    intent = await _parse_unified_intent_async(question, llm)
    intent = _safe_parse_intent(intent)
    # P0-4：枚举值归一化（失败时降级为原始过滤，不阻断主流程）
    try:
        intent = _normalize_intent_enums(intent)
    except Exception as e:
        logger.warning("[ENUM_NORM] 枚举归一化异常，降级为原始过滤: %s", e)

    # ── 确定性拦截：product_query 功能线已砍除，先于一切 SQL 执行 ──
    if intent.sub_route == "product_query":
        result = _build_capability_boundary_answer(question)
        finalize({"answer": result["business_result"]["answer"], "business_result": {"branch": "price_inquiry", "sub_route": intent.sub_route}})
        return result

    # ── P0-11：前置校验 — 多层次查询合法性拦截（与同步节点逐条一致）──
    # 第一层：project_detail 必须提供 project_number
    if intent.query_type == "project_detail" and not intent.hard_filters.project_number:
        logger.info(
            "[QUERY_GUARD] project_detail 未提供 project_number，触发引导。question=%.80s",
            question,
        )
        result = _build_unified_guidance(question, intent, reason="project_detail_no_number")
        finalize({"answer": result["business_result"]["answer"], "business_result": {"branch": "price_inquiry"}})
        return result

    # P0-12：后置修正 — LLM 未归类为 project_detail 但已提取到有效编号则强制修正
    if intent.hard_filters.project_number and _looks_like_code(intent.hard_filters.project_number):
        if intent.query_type != "project_detail":
            logger.info(
                "[PROJECT_NUMBER_CORRECT] LLM 分类为 '%s'，但已提取到项目编号 '%s'，"
                "强制修正为 project_detail。question=%.50s",
                intent.query_type,
                intent.hard_filters.project_number,
                question,
            )
            intent.query_type = "project_detail"
            intent.sub_route = "bidding_query"

    # P0-12：确定性兜底 — LLM 未提取到编号但原始文本含项目编号格式则注入
    if not intent.hard_filters.project_number or not _looks_like_code(intent.hard_filters.project_number):
        extracted_pn = _extract_project_number_candidate(question)
        if extracted_pn:
            logger.info(
                "[PROJECT_NUMBER_INJECT] LLM 未提取到，但确定性扫描发现 '%s'，"
                "注入 intent。question=%.50s",
                extracted_pn,
                question,
            )
            intent.hard_filters.project_number = extracted_pn
            intent.query_type = "project_detail"
            intent.sub_route = "bidding_query"

    # 第二层：裸实体名校验 — 用户仅输入实体名而无查询意图关键词
    has_entity = _has_valid_query_entity(intent)
    has_intent_kw = bool(_QUERY_INTENT_KEYWORDS.search(question))
    has_project_number = (
        intent.hard_filters.project_number
        and _looks_like_code(intent.hard_filters.project_number)
    )
    if has_entity and not has_intent_kw and not has_project_number:
        logger.info(
            "[QUERY_GUARD] 检测到裸实体名无查询意图，触发引导。entity_keys=%s question=%.80s",
            [
                k for k in ("project_number", "company_name", "purchaser", "successful_bidder")
                if getattr(intent.hard_filters, k)
            ],
            question,
        )
        result = _build_unified_guidance(question, intent, reason="bare_entity_no_intent")
        finalize({"answer": result["business_result"]["answer"], "business_result": {"branch": "price_inquiry"}})
        return result

    # 第三层：bidding_query / company_query / all 必须提取到有效实体
    if intent.sub_route in ("bidding_query", "company_query", "all") and not has_entity:
        logger.info(
            "[QUERY_GUARD] %s 未提取到有效查询实体，触发引导。question=%.80s",
            intent.sub_route,
            question,
        )
        result = _build_unified_guidance(question, intent, reason="no_entity_for_route")
        finalize({"answer": result["business_result"]["answer"], "business_result": {"branch": "price_inquiry"}})
        return result

    llm_elapsed = time.perf_counter() - llm_start
    emit(EventType.STAGE, {"stage": "intent_done", "sub_route": intent.sub_route, "query_type": intent.query_type})

    # LLM 阶段已接近总超时 → 降级返回（与同步节点一致）
    if llm_elapsed > total_timeout * 0.6:
        logger.warning(
            "[NODE_TIMEOUT] LLM 意图解析耗时 %.2fs 已超过总超时 %ds 的 60%%，降级返回",
            llm_elapsed, total_timeout,
        )
        answer = (
            "抱歉，当前查询处理耗时过长，请稍后重试或简化您的问题。\n\n"
            "建议尝试：\n"
            "  1. 使用更简洁的关键词\n"
            "  2. 缩小查询范围（如指定地区或时间）"
        )
        finalize({"answer": answer, "business_result": {"branch": "price_inquiry"}})
        return {
            "business_result": {
                "branch": "price_inquiry",
                "answer": answer,
                "data": {"records": [], "error": "node_timeout_partial", "elapsed": round(llm_elapsed, 2)},
            },
            "messages": [AIMessage(content=answer)],
        }

    # Step 2：二级路由分发 — 异步三表并行召回（有界池 + 语句超时）
    route_config = _SUB_ROUTE_MAP.get(intent.sub_route, _SUB_ROUTE_MAP["all"])
    tables = route_config["tables"]

    remaining_timeout = max(5, total_timeout - llm_elapsed)
    query_start = time.perf_counter()
    try:
        query_result = await asyncio.wait_for(
            query_tables_async(tables, intent, progress_callback=_emit_table)
            if "progress_callback" in inspect.signature(query_tables_async).parameters
            else query_tables_async(tables, intent),
            timeout=remaining_timeout,
        )
        query_elapsed = time.perf_counter() - query_start
    except (asyncio.TimeoutError, TimeoutError):
        query_elapsed = time.perf_counter() - query_start
        logger.warning(
            "[NODE_TIMEOUT] SQL 查询阶段超时: elapsed=%.2fs timeout=%ds sub_route=%s",
            query_elapsed, remaining_timeout, intent.sub_route,
        )
        answer = (
            f"抱歉，查询「{question[:40]}」超时，请简化查询条件后重试。\n\n"
            f"建议尝试：\n"
            f"  1. 使用更精确的产品名称或公司名称\n"
            f"  2. 添加地区或时间范围缩小检索范围\n"
            f"  3. 减少关键词数量"
        )
        emit(EventType.ERROR, {"code": "sql_timeout", "message": answer, "retryable": True})
        finalize({"answer": answer, "business_result": {"branch": "price_inquiry"}})
        return {
            "business_result": {
                "branch": "price_inquiry",
                "sub_route": intent.sub_route,
                "answer": answer,
                "data": {
                    "records": [],
                    "error": "node_timeout_sql",
                    "elapsed": round(query_elapsed, 2),
                    "timeout": remaining_timeout,
                },
            },
            "messages": [AIMessage(content=answer)],
        }
    except Exception as e:
        # 单表失败已由 query_tables_async 内部兜底，此处仅防整体性异常
        logger.warning("[QUERY_ASYNC] 并行召回整体异常: %s", e)
        query_result = {
            "records": [], "queried_tables": [], "sql_count": 0,
            "total_sql_time": 0.0, "total_found": 0,
        }

    records = query_result.get("records", [])
    queried_tables = query_result.get("queried_tables", [])
    sql_count = query_result.get("sql_count", 0)
    total_sql_time = query_result.get("total_sql_time", 0.0)

    # ── P0-11：后置回溯 — bid_project 召回结果校验（与同步节点逐条一致）──
    if intent.sub_route == "bidding_query" and records:
        target_company = intent.hard_filters.successful_bidder or intent.hard_filters.purchaser or ""
        if target_company and _is_valid_company_name(target_company):
            verified = [
                r for r in records
                if target_company in str(r.get("successful_bidder", ""))
                or target_company in str(r.get("purchaser", ""))
            ]
            if not verified:
                logger.info(
                    "[POST_GUARD] bid_project 召回 %d 条但无一匹配目标公司 '%s'，"
                    "视为盲目召回，触发引导话术。",
                    len(records),
                    target_company[:30],
                )
                return _build_unified_guidance(
                    question,
                    intent,
                    reason=f"post_recall_no_match:{target_company[:20]}",
                )
            elif len(verified) < len(records):
                logger.info(
                    "[POST_GUARD] bid_project 召回 %d 条，经后置校验保留 %d 条匹配 '%s' 的记录",
                    len(records),
                    len(verified),
                    target_company[:30],
                )
                records = verified

    # ── P0-11：后置回溯 — company_info 召回结果校验 ──
    if intent.sub_route == "company_query" and records:
        target_company = intent.hard_filters.company_name or ""
        if target_company and _is_valid_company_name(target_company):
            verified = [
                r for r in records
                if target_company in str(r.get("company_name", ""))
            ]
            if not verified:
                logger.info(
                    "[POST_GUARD] company_query 召回 %d 条但无一匹配目标公司 '%s'，"
                    "视为盲目召回，触发引导话术。",
                    len(records),
                    target_company[:30],
                )
                return _build_unified_guidance(
                    question,
                    intent,
                    reason=f"post_recall_company_no_match:{target_company[:20]}",
                )
            elif len(verified) < len(records):
                logger.info(
                    "[POST_GUARD] company_query 召回 %d 条，经后置校验保留 %d 条匹配 '%s' 的记录",
                    len(records),
                    len(verified),
                    target_company[:30],
                )
                records = verified

    # Step 3：输出字段筛选
    template = get_template(intent.sub_route, intent.query_type)
    if template and records:
        formatted_records = _apply_output_template(records, intent, template)
    else:
        formatted_records = records

    # 性能日志
    node_elapsed = time.perf_counter() - node_start
    logger.info(
        "[SUB_ROUTE] sub_route=%s query_type=%s tables=%s sql_count=%d "
        "sql_time=%.3fs raw_rows=%d formatted_rows=%d total_time=%.3fs (async)",
        intent.sub_route,
        intent.query_type,
        tables,
        sql_count,
        total_sql_time,
        len(records),
        len(formatted_records),
        node_elapsed,
    )

    # Step 4：自然语言回答渲染（answer_templates 引擎）
    hf = intent.hard_filters
    entity = (
        hf.company_name or
        hf.successful_bidder or
        hf.purchaser or
        hf.project_number or
        (intent.exact_tokens[0] if intent.exact_tokens else "") or
        (intent.semantic_keywords[0] if intent.semantic_keywords else "") or
        question[:30]
    )

    if not records and not formatted_records:
        answer = render_answer(
            intent.query_type, [], entity=entity, sub_route=intent.sub_route
        )
        finalize({"answer": answer, "business_result": {"branch": "price_inquiry"}})
        return {
            "business_result": {
                "branch": "price_inquiry",
                "sub_route": intent.sub_route,
                "query_type": intent.query_type,
                "answer": answer,
                "data": {
                    "records": [],
                    "tables": tables,
                    "intent": {
                        "sub_route": intent.sub_route,
                        "query_type": intent.query_type,
                        "semantic_keywords": intent.semantic_keywords,
                    },
                },
            },
            "messages": [AIMessage(content=answer)],
        }

    answer = render_answer(
        intent.query_type,
        records,  # 使用原始列名记录，answer_templates 自行格式化
        entity=entity,
        sub_route=intent.sub_route,
    )

    if _STREAM_ACTIVE.get():
        step = max(1, len(answer) // 8)
        for start in range(0, len(answer), step):
            delta = answer[start:start + step]
            emit(EventType.TOKEN, {"delta": delta, "synthetic": True})

    finalize({
        "answer": answer,
        "business_result": {
            "branch": "price_inquiry",
            "records": len(formatted_records),
        },
    })
    return {
        "business_result": {
            "branch": "price_inquiry",
            "sub_route": intent.sub_route,
            "query_type": intent.query_type,
            "answer": answer,
            "data": {
                "records": formatted_records,
                "tables": queried_tables,
                "total_found": query_result.get("total_found", 0),
                "intent": {
                    "sub_route": intent.sub_route,
                    "query_type": intent.query_type,
                    "semantic_keywords": intent.semantic_keywords,
                    "exact_tokens": intent.exact_tokens,
                },
                "meta": {
                    "total_hits": len(records),
                    "displayed_hits": len(formatted_records),
                    "sql_count": sql_count,
                    "total_sql_time": round(total_sql_time, 3),
                    "node_elapsed": round(node_elapsed, 3),
                    "async": True,
                },
            },
        },
        "messages": [AIMessage(content=answer)],
    }
