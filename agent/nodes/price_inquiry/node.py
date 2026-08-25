"""询价节点入口 — 三层查询守卫、二级路由分发、引导话术与输出渲染。"""

from __future__ import annotations

import logging
import re
import time
from concurrent.futures import TimeoutError as FutureTimeoutError

from langchain_core.messages import AIMessage

from ..answer_templates import CAPABILITY_GUIDANCE, render_answer
from ..output_templates import _apply_output_template, get_template
from ...state import AgentState
from . import queries as _queries_module
from .db import _get_settings
from .enum_norm import _normalize_intent_enums
from .intent import (
    _build_llm,
    _extract_project_number_candidate,
    _is_valid_company_name,
    _looks_like_code,
    _parse_unified_intent,
    _safe_parse_intent,
)
from .models import SearchIntent
from .recall import _sql_executor

logger = logging.getLogger(__name__)

_SUB_ROUTE_MAP: dict[str, dict] = {
    "company_query": {
        "tables": ["company_info", "company_penalty"],
        "query_fn": "_query_company_data",
    },
    "bidding_query": {
        "tables": ["bid_project"],
        "query_fn": "_query_bidding_data",
    },
    "all": {
        "tables": ["company_info", "company_penalty", "bid_project"],
        "query_fn": "_query_all_tables",
    },
}

def _get_query_fn(fn_name: str):
    """通过函数名获取查询函数（查询实现位于 queries 模块）。"""
    return getattr(_queries_module, fn_name, _queries_module._query_all_tables)

def _build_capability_boundary_answer(question: str) -> dict:
    """产品查询功能线已正式下线，返回能力边界说明 + 三大核心功能引导。

    本系统当前专注于以下三大核心能力：
      1. 企业工商情报查询 — "XX公司详情/工商信息"
      2. 企业风控黑名单查询 — "XX公司有无不良记录/处罚"
      3. 招投标中标情报查询 — "XX公司中标了什么项目"
    """
    answer = (
        f"抱歉，本系统暂不支持产品价格查询功能。\n\n"
        f"{CAPABILITY_GUIDANCE}\n\n"
        f"如需查询产品价格或供应商推荐，建议访问专业采购平台获取最新报价信息。"
    )
    logger.info("[CAPABILITY_BOUNDARY] product_query 被确定性拦截，返回能力引导")
    return {
        "business_result": {
            "branch": "price_inquiry",
            "sub_route": "product_query",
            "query_type": "capability_boundary",
            "answer": answer,
            "data": {
                "records": [],
                "tables": [],
                "intent": {
                    "sub_route": "product_query",
                    "query_type": "capability_boundary",
                },
            },
        },
        "messages": [AIMessage(content=answer)],
    }

_UNIFIED_GUIDANCE_TEXT = (
    "您好！我是您的招投标查询助手，目前可以帮您查询以下几类信息：\n\n"
    "① 查询某个项目的中标情况\n"
    "   请提供「项目编号」，例如：[项目编号]的中标情况\n\n"
    "② 查询某个公司的中标历史\n"
    "   请提供「公司全称」，例如：[公司全称]的中标历史\n\n"
    "③ 查询某个公司的工商情况、不良记录或经营范围\n"
    "   请提供「公司全称 + 关键词」，例如：\n"
    '   "[公司全称]的工商情况"\n'
    '   "[公司全称]的不良记录"\n'
    '   "[公司全称]的经营范围"\n\n'
    "请问您想了解哪方面的信息呢？"
)

_QUERY_INTENT_KEYWORDS = re.compile(
    r"查|问|是?什么|哪些|哪个|如何|怎么|多少|有无|有没有|"
    r"查询|了解|看看|帮忙|请问|推荐|帮我|"
    r"中标|招标|采购|投标|发包|工商|不良|处罚|违法|"
    r"详情|记录|历史|介绍|信息|情况|"
    r"中标情况|中标历史|不良记录|工商信息|经营范围"
)

def _has_valid_query_entity(intent: SearchIntent) -> bool:
    """P0-11：前置校验 — 检查是否提取到有效的查询实体。

    合法实体：
    - project_number（格式含字母+数字，如 AH2024-001）→ 精确匹配
    - company_name（工商主体名称格式校验通过，用于 company_query）
    - purchaser（采购人名称格式校验通过，用于 bidding_query）
    - successful_bidder（中标供应商名称格式校验通过，用于 bidding_query）

    未通过格式校验的实体名将视为无效，触发统一引导话术，
    防止 LLM 将项目名片段/口语化表达误提取为"企业名"。"""
    hf = intent.hard_filters

    if hf.project_number and _looks_like_code(hf.project_number):
        return True
    # P0-11：公司名必须通过工商主体名称格式校验
    if hf.company_name and _is_valid_company_name(hf.company_name):
        return True
    if hf.purchaser and _is_valid_company_name(hf.purchaser):
        return True
    if hf.successful_bidder and _is_valid_company_name(hf.successful_bidder):
        return True
    return False

def _build_unified_guidance(
    question: str,
    intent: SearchIntent,
    reason: str = "no_valid_entity",
) -> dict:
    """P0-11：统一引导话术 — 当查询无法提取有效实体时触发。

    包含全链路日志埋点，记录所有触发引导流程的查询内容，
    用于后续迭代优化校验规则。
    """
    logger.info(
        "[QUERY_GUARD] reason=%s sub_route=%s query_type=%s question=%.100s",
        reason,
        intent.sub_route,
        intent.query_type,
        question,
    )

    answer = _UNIFIED_GUIDANCE_TEXT
    return {
        "business_result": {
            "branch": "price_inquiry",
            "sub_route": intent.sub_route,
            "query_type": "unified_guidance",
            "answer": answer,
            "data": {
                "records": [],
                "tables": [],
                "intent": {
                    "sub_route": intent.sub_route,
                    "query_type": intent.query_type,
                    "guard_reason": reason,
                },
            },
        },
        "messages": [AIMessage(content=answer)],
    }

def node_price_inquiry(state: AgentState) -> dict:
    """智能询价节点 — 统一意图解析 + 二级路由分发。

    流程：统一意图解析 → 二级路由分发 → SQL 检索 → 输出字段筛选。
    包含全局超时控制，防止单次业务调用无限阻塞。
    """
    node_start = time.perf_counter()
    settings = _get_settings()
    total_timeout = settings.node_total_timeout

    messages = state.get("messages", [])
    if not messages:
        return {
            "business_result": {
                "branch": "price_inquiry",
                "answer": "抱歉，没有收到您的问题，请重新输入。",
                "data": {"records": []},
            },
        }

    question = str(messages[-1].content)
    logger.info("price_inquiry: 查询 — %s", question[:80])

    # Step 1：统一意图解析（一次 LLM 调用完成 sub_route + hard_filters + query_type）
    llm_start = time.perf_counter()
    llm = _build_llm()
    intent = _parse_unified_intent(question, llm)
    intent = _safe_parse_intent(intent)
    # P0-4：枚举值归一化（失败时降级为原始过滤，不阻断主流程）
    try:
        intent = _normalize_intent_enums(intent)
    except Exception as e:
        logger.warning("[ENUM_NORM] 枚举归一化异常，降级为原始过滤: %s", e)

    # ── 确定性拦截：product_query 功能线已砍除，先于一切 SQL 执行 ──
    if intent.sub_route == "product_query":
        return _build_capability_boundary_answer(question)

    # ── P0-11：前置校验 — 多层次查询合法性拦截 ──
    # 第一层：project_detail 必须提供 project_number
    if intent.query_type == "project_detail" and not intent.hard_filters.project_number:
        logger.info(
            "[QUERY_GUARD] project_detail 未提供 project_number，触发引导。question=%.80s",
            question,
        )
        return _build_unified_guidance(question, intent, reason="project_detail_no_number")

    # P0-12：后置修正 — 若意图中已提取到有效 project_number，
    # 但 LLM 未将其归类为 project_detail，则强制修正路由和查询类型。
    # 确保"只要识别到项目编号即触发项目查询"的宽松意图策略落地。
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

    # P0-12：确定性兜底 — 若 LLM 未能提取到项目编号，但原始文本中
    # 包含明显项目编号格式的字符串，直接注入到 intent 中。
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
    # P0-12修正：若已有有效项目编号，跳过裸实体检查（项目编号本身就是合法的查询信号）
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
        return _build_unified_guidance(question, intent, reason="bare_entity_no_intent")

    # 第三层：bidding_query / company_query / all 必须提取到有效实体
    # P0-11 修复：all 路由也纳入前置校验，防止无实体时无脑遍历全表
    if intent.sub_route in ("bidding_query", "company_query", "all") and not has_entity:
        logger.info(
            "[QUERY_GUARD] %s 未提取到有效查询实体，触发引导。question=%.80s",
            intent.sub_route,
            question,
        )
        return _build_unified_guidance(question, intent, reason="no_entity_for_route")

    llm_elapsed = time.perf_counter() - llm_start

    # 检查 LLM 阶段是否已接近总超时
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
        return {
            "business_result": {
                "branch": "price_inquiry",
                "answer": answer,
                "data": {"records": [], "error": "node_timeout_partial", "elapsed": round(llm_elapsed, 2)},
            },
            "messages": [AIMessage(content=answer)],
        }

    # Step 2：二级路由分发（带全局超时保护）
    route_config = _SUB_ROUTE_MAP.get(intent.sub_route, _SUB_ROUTE_MAP["all"])
    query_fn = _get_query_fn(route_config["query_fn"])

    remaining_timeout = max(5, total_timeout - llm_elapsed)
    query_start = time.perf_counter()

    # 使用线程池实现跨平台超时控制
    query_future = _sql_executor.submit(query_fn, intent)
    try:
        query_result = query_future.result(timeout=remaining_timeout)
        query_elapsed = time.perf_counter() - query_start
    except FutureTimeoutError:
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

    records = query_result.get("records", [])
    tables = query_result.get("queried_tables", [])
    sql_count = query_result.get("sql_count", 0)
    total_sql_time = query_result.get("total_sql_time", 0.0)

    # ── P0-11：后置回溯 — bid_project 召回结果校验 ──
    # 当 bidding_query 以公司名检索时，验证返回结果的 successful_bidder/purchaser
    # 是否确实匹配目标公司名；若全不匹配则视为盲目召回，返回统一引导话术
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
    # 当 company_query 以公司名检索时，验证返回结果的 company_name
    # 是否确实匹配目标公司名；若全不匹配则视为盲目召回，返回统一引导话术。
    # 此校验覆盖企业工商信息查询、经营范围查询、不良记录查询三大场景。
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
        "sql_time=%.3fs raw_rows=%d formatted_rows=%d total_time=%.3fs",
        intent.sub_route,
        intent.query_type,
        route_config["tables"],
        sql_count,
        total_sql_time,
        len(records),
        len(formatted_records),
        node_elapsed,
    )

    # Step 4：自然语言回答渲染（answer_templates 引擎）
    # 提取实体名用于空结果引导
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
        return {
            "business_result": {
                "branch": "price_inquiry",
                "sub_route": intent.sub_route,
                "query_type": intent.query_type,
                "answer": answer,
                "data": {
                    "records": [],
                    "tables": route_config["tables"],
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

    return {
        "business_result": {
            "branch": "price_inquiry",
            "sub_route": intent.sub_route,
            "query_type": intent.query_type,
            "answer": answer,
            "data": {
                "records": formatted_records,
                "tables": tables,
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
                },
            },
        },
        "messages": [AIMessage(content=answer)],
    }
