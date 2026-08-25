"""确定性 SQL 构建器族 — 硬过滤、全文检索候选集、LIKE/全表/回表兜底 SQL。"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Optional

from .db import _CLEAN_DB
from .intent import _is_valid_company_name, _looks_like_code, _normalize_token
from .models import HardFilters, SearchIntent

logger = logging.getLogger(__name__)

def _build_fulltext_expression(cols: list[str]) -> str:
    cols_sql = ", ".join(f"`{c}`" for c in cols)
    return f"MATCH({cols_sql})"

def _build_search_term(
    intent: SearchIntent,
    *,
    keyword_override: Optional[list[str]] = None,
    include_exact_tokens: bool = True,
    table: str = "",
) -> str:
    """构造 MySQL 布尔全文检索的查询串。

    P1-2：全面移除"+"强制操作符，所有阶段统一 OR 语义；
    "全命中优先"交由 _hybrid_score 在 Python 侧重排序中加权，
    避免强制全词匹配造成召回断崖。

    P0-11：bid_project 检索词白名单过滤 — 仅允许 company_name 类关键词
    （含已知企业后缀）通过，阻止项目名称/标的物等非授权字段进入 FULLTEXT。
    """
    kws = [kw for kw in (keyword_override or intent.semantic_keywords) if kw]
    parts: list[str] = list(kws)

    if include_exact_tokens:
        for token in intent.exact_tokens:
            if token:
                # P1-2：不再使用 +"token" 强制短语，按 OR 语义参与全文检索
                parts.append(token)

    # P0-11：bid_project 中台湾控 — 仅保留公司名/项目编号类关键词
    if table == "bid_project":
        parts = [p for p in parts if _is_valid_company_name(p) or _looks_like_code(p)]
        if not parts:
            logger.info(
                "[MID_GUARD] bid_project 检索词全部被白名单过滤，阻断 FULLTEXT 查询。"
                " original_kws=%s",
                kws,
            )

    return " ".join(parts)

def _build_constraint_conditions(
    table: str,
    classification: dict[str, list[str]],
    intent: SearchIntent,
) -> tuple[list[str], list[Any]]:
    """P0-2：约束性硬过滤 — 所有召回阶段（含兜底与向量回表）均保留。

    包括：时间范围、数值区间（预算/价格/中标金额）、代码类精确匹配
    （credit_code、project_number、含编号特征的 exact_tokens）。
    这类条件语义明确、误杀风险低，召回为空时也不放宽。

    P0-11：bid_project 表仅开放 project_number 一个合法检索字段，
    屏蔽 time_range / winning_amount 等非授权字段的约束条件。"""
    conditions: list[str] = []
    params: list[Any] = []
    hf = intent.hard_filters

    # P0-11：bid_project 表仅开放 project_number 精确匹配
    is_bid_project = table == "bid_project"

    # 时间范围（bid_project 已屏蔽）
    if hf.time_range and classification.get("time") and not is_bid_project:
        start = hf.time_range.get("start")
        end = hf.time_range.get("end")
        time_col = classification["time"][0]
        if start:
            conditions.append(f"`{time_col}` >= %s")
            params.append(start)
        if end:
            conditions.append(f"`{time_col}` <= %s")
            params.append(end)

    # 预算范围（bid_project 已屏蔽）
    if hf.budget_range and classification.get("budget") and not is_bid_project:
        min_b = hf.budget_range.get("min")
        max_b = hf.budget_range.get("max")
        budget_col = classification["budget"][0]
        if min_b is not None:
            conditions.append(f"CONVERT(`{budget_col}`, DECIMAL(20,2)) >= %s")
            params.append(min_b)
        if max_b is not None:
            conditions.append(f"CONVERT(`{budget_col}`, DECIMAL(20,2)) <= %s")
            params.append(max_b)

    # 产品价格范围
    if hf.price_range:
        if hf.price_range.get("min") is not None:
            conditions.append("`price` >= %s")
            params.append(hf.price_range["min"])
        if hf.price_range.get("max") is not None:
            conditions.append("`price` <= %s")
            params.append(hf.price_range["max"])

    # 中标金额范围（bid_project 已屏蔽）
    if hf.winning_amount_range and not is_bid_project:
        if hf.winning_amount_range.get("min") is not None:
            conditions.append("`winning_amount` >= %s")
            params.append(hf.winning_amount_range["min"])
        if hf.winning_amount_range.get("max") is not None:
            conditions.append("`winning_amount` <= %s")
            params.append(hf.winning_amount_range["max"])

    # 代码类精确字段（P0-1：仅当具备编号/数字特征时才生效）
    if hf.credit_code and _looks_like_code(hf.credit_code):
        conditions.append("`credit_code` = %s")
        params.append(hf.credit_code)
    if hf.project_number and _looks_like_code(hf.project_number):
        conditions.append("`project_number` = %s")
        params.append(hf.project_number)

    # 代码类精确 token（P0-1 修复：公司名等实体名不再误映射到 project_number）
    if intent.exact_tokens and classification.get("exact"):
        exact_col = classification["exact"][0]
        covered_values: set[str] = set()
        if hf.project_number and _looks_like_code(hf.project_number):
            covered_values.add(_normalize_token(hf.project_number))
        if hf.credit_code and _looks_like_code(hf.credit_code):
            covered_values.add(_normalize_token(hf.credit_code))
        for token in intent.exact_tokens:
            if token and _looks_like_code(token):
                if _normalize_token(token) in covered_values:
                    continue
                conditions.append(f"`{exact_col}` = %s")
                params.append(token)

    # P0-11：bid_project 仅开放 project_number，project_name 已完全移除

    return conditions, params

def _build_preference_conditions(
    table: str,
    classification: dict[str, list[str]],
    intent: SearchIntent,
) -> tuple[list[str], list[Any]]:
    """P0-2：偏好性硬过滤 — 仅用于首遍过滤，召回为空时自动放宽。

    包括：实体名称（采购人/中标人/公司名）、地区、行业、企业等级、
    状态/阶段、品类等。这类条件与数据枚举值存在失配风险
    （如 LLM 输出"批发"而库中为"批发业"），不应在兜底/回表阶段全歼召回。

    P0-11：bid_project 表仅开放 purchaser + successful_bidder 两个合法检索字段，
    屏蔽 province / city / project_stage / project_category 等非授权字段。"""
    conditions: list[str] = []
    params: list[Any] = []
    hf = intent.hard_filters

    # P0-11：bid_project 表仅开放公司名（采购人/中标供应商）精确匹配
    is_bid_project = table == "bid_project"

    # ── 实体名称 ──
    if hf.purchaser and classification.get("purchaser"):
        conditions.append(f"`{classification['purchaser'][0]}` = %s")
        params.append(hf.purchaser)
    if hf.successful_bidder:
        conditions.append("`successful_bidder` = %s")
        params.append(hf.successful_bidder)
    # company_name 仅用于 company_info 等非项目表
    if hf.company_name and not is_bid_project:
        conditions.append("`company_name` = %s")
        params.append(hf.company_name)

    # ── 地区（bid_project 已屏蔽）──
    if hf.region and classification.get("region") and not is_bid_project:
        conditions.append(f"`{classification['region'][0]}` LIKE %s")
        params.append(f"{hf.region}%")
    if hf.province and not is_bid_project:
        conditions.append("`province` LIKE %s")
        params.append(f"{hf.province}%")
    if hf.city and not is_bid_project:
        conditions.append("`city` LIKE %s")
        params.append(f"{hf.city}%")

    # ── 状态/阶段（LIKE 前缀模糊匹配）──
    is_company_table = "company" in table.lower() or "companies" in table.lower()
    if hf.status and classification.get("status") and not is_company_table:
        conditions.append(f"`{classification['status'][0]}` LIKE %s")
        params.append(f"{hf.status}%")
    if hf.business_status:
        conditions.append("`business_status` LIKE %s")
        params.append(f"{hf.business_status}%")
    if hf.project_stage and not is_bid_project:
        conditions.append("`project_stage` LIKE %s")
        params.append(f"{hf.project_stage}%")
    if hf.project_category and not is_bid_project:
        conditions.append("`project_category` = %s")
        params.append(hf.project_category)

    # ── 行业/等级/品类 ──
    if hf.industry:
        conditions.append("`industry` = %s")
        params.append(hf.industry)
    if hf.company_level_values:
        # P0-4：多值等级展开为 IN 匹配（如“中大型”→ IN ('中型企业','大型企业')）
        placeholders = ", ".join(["%s"] * len(hf.company_level_values))
        conditions.append(f"`company_level` IN ({placeholders})")
        params.extend(hf.company_level_values)
    elif hf.company_level:
        conditions.append("`company_level` = %s")
        params.append(hf.company_level)
    if hf.category:
        conditions.append("`category` = %s")
        params.append(hf.category)

    return conditions, params

_PREFERENCE_FILTER_FIELDS = (
    "purchaser", "region", "province", "city", "status",
    "company_name", "industry", "company_level", "company_level_values",
    "business_status", "category", "successful_bidder",
    "project_category", "project_stage",
)

def _has_preference_filters(hf: HardFilters) -> bool:
    """判断硬过滤中是否含偏好性条件。"""
    return any(getattr(hf, name) for name in _PREFERENCE_FILTER_FIELDS)

def _strip_preference_filters(hf: HardFilters, query_type: str = "mixed") -> HardFilters:
    """P0-2：剥离偏好性过滤，仅保留约束性条件（时间/数值范围/代码类精确匹配）。

    保留 company_name / successful_bidder / purchaser 等核心实体字段，
    仅剥离行业、等级、地区、状态等辅助性偏好过滤。
    核心实体一旦缺失，宽松重试将返回无关记录（无脑召回），必须保留。
    """
    relaxed = replace(hf)
    # P0-11 修复：核心实体字段（公司名/中标供应商/采购人/项目编号）
    # 在宽松重试中必须保留，防止无差别无脑召回。
    keep_fields = {"company_name", "successful_bidder", "purchaser", "project_number"}
    for name in _PREFERENCE_FILTER_FIELDS:
        if name not in keep_fields:
            setattr(relaxed, name, None)
    return relaxed

def _build_hard_conditions_extended(
    table: str,
    classification: dict[str, list[str]],
    intent: SearchIntent,
) -> tuple[list[str], list[Any]]:
    """首遍过滤的完整硬过滤 = 约束性条件 + 偏好性条件。

    用于 FULLTEXT 候选集与 LIKE 降级等首遍检索阶段；
    向量回表与兜底阶段仅使用约束性条件（见 _build_constraint_conditions）。
    """
    conditions, params = _build_constraint_conditions(table, classification, intent)
    pref_conds, pref_params = _build_preference_conditions(table, classification, intent)
    return conditions + pref_conds, params + pref_params

def _build_like_fallback_sql(
    table: str,
    classification: dict[str, list[str]],
    intent: SearchIntent,
) -> Optional[tuple[str, tuple[Any, ...]]]:
    """P0 优化（5.2.2）：FULLTEXT 零召回时的 LIKE 降级 SQL。

    使用第一个语义关键词对所有语义列做 `LIKE '%kw%'` OR 匹配。
    作为首遍检索阶段保留完整硬过滤；若整条降级链仍为空，
    _execute_recall_chain_for_table 会自动放宽偏好性过滤重试（P0-2）。
    """
    semantic_cols = classification.get("semantic", [])[:4]
    if not semantic_cols:
        return None
    kws = [kw for kw in intent.semantic_keywords if kw]
    if not kws:
        return None

    id_col = classification["id"][0] if classification.get("id") else semantic_cols[0]
    like_term = f"%{kws[0]}%"

    # SELECT * 全字段回表（修复 P0-1 缺列缺陷）+ 计算列
    select_fields = ["*", f"`{id_col}` AS `_id_`", "0 AS `_score_`"]

    hard_conds, hard_params = _build_hard_conditions_extended(table, classification, intent)
    like_cond = " OR ".join(f"`{col}` LIKE %s" for col in semantic_cols)
    conds = hard_conds + [f"({like_cond})"]

    params: list[Any] = list(hard_params)
    params.extend([like_term] * len(semantic_cols))

    order_by = _build_order_clause(intent)
    sql = (
        f"SELECT {', '.join(select_fields)} "
        f"FROM `{table}` "
        f"WHERE {' AND '.join(conds)} "
        f"{order_by} "
        f"LIMIT 200"
    )
    return sql, tuple(params)

def _build_order_clause(intent: SearchIntent) -> str:
    """根据意图返回 ORDER BY 子句。"""
    sort_by = intent.sort_by
    if sort_by == "price_asc":
        return "ORDER BY `price` ASC"
    elif sort_by == "price_desc":
        return "ORDER BY `price` DESC"
    elif sort_by == "amount_desc":
        return "ORDER BY `winning_amount` DESC"
    elif sort_by == "amount_asc":
        return "ORDER BY `winning_amount` ASC"
    elif sort_by == "date_desc":
        return "ORDER BY `winning_date` DESC"
    elif sort_by == "date_asc":
        return "ORDER BY `winning_date` ASC"
    else:
        return "ORDER BY `_score_` DESC"

def _build_candidate_sql(
    table: str,
    classification: dict[str, list[str]],
    intent: SearchIntent,
    *,
    keyword_override: Optional[list[str]] = None,
    include_exact_tokens: bool = True,
    allow_empty_search: bool = False,
) -> Optional[tuple[str, tuple[Any, ...]]]:
    """构建候选集 SQL：硬过滤 + 全文检索，返回最多 200 条。"""
    semantic_cols = classification.get("semantic", [])[:4]
    if not semantic_cols:
        return None

    id_col = classification["id"][0] if classification.get("id") else semantic_cols[0]

    search_term = _build_search_term(
        intent,
        keyword_override=keyword_override,
        include_exact_tokens=include_exact_tokens,
        table=table,
    )
    hard_conds, hard_params = _build_hard_conditions_extended(table, classification, intent)

    # 选择返回字段：SELECT * 全字段回表（修复 P0-1 缺列缺陷）+ 计算列
    select_fields = ["*", f"`{id_col}` AS `_id_`"]

    params: list[Any] = list(hard_params)
    order_by = _build_order_clause(intent)

    if search_term:
        fulltext_expr = _build_fulltext_expression(semantic_cols)
        fulltext_cond = f"{fulltext_expr} AGAINST (%s IN BOOLEAN MODE)"
        select_fields.append(f"{fulltext_expr} AGAINST (%s IN BOOLEAN MODE) AS `_score_`")
        hard_conds.append(fulltext_cond)
        params.extend([search_term, search_term])
    else:
        select_fields.append("0 AS `_score_`")

    if not hard_conds and not allow_empty_search:
        logger.warning("[SQL_BUILD] db=%s table=%s 无任何过滤条件，跳过", _CLEAN_DB, table)
        return None

    where_clause = " AND ".join(hard_conds) if hard_conds else "1=1"
    sql = (
        f"SELECT {', '.join(select_fields)} "
        f"FROM `{table}` "
        f"WHERE {where_clause} "
        f"{order_by} "
        f"LIMIT 200"
    )
    return sql, tuple(params)

def _build_full_scan_sql(
    table: str,
    classification: dict[str, list[str]],
    intent: SearchIntent,
) -> Optional[tuple[str, tuple[Any, ...]]]:
    """P1-1：最终兜底，全表扫描但保留 LIMIT 与约束性硬过滤。

    P0-3：兜底阶段仅保留约束性条件（时间/数值范围/代码类精确匹配），
    偏好性条件（行业/等级/地区/实体名等）不得拦截最后防线；
    无约束条件时放弃兜底，避免返回随机无关行。
    """
    semantic_cols = classification.get("semantic", [])[:4]
    if not semantic_cols:
        return None

    id_col = classification["id"][0] if classification.get("id") else semantic_cols[0]
    # SELECT * 全字段回表（修复 P0-1 缺列缺陷）+ 计算列
    select_fields = ["*", f"`{id_col}` AS `_id_`", "0 AS `_score_`"]

    hard_conds, hard_params = _build_constraint_conditions(table, classification, intent)
    if not hard_conds:
        # 无约束条件时全表扫描将返回随机无关行，放弃兜底（由前序阶段与放宽重试负责召回）
        return None
    where_clause = " AND ".join(hard_conds)

    order_by = _build_order_clause(intent)
    if order_by == "ORDER BY `_score_` DESC":
        order_by = f"ORDER BY `{id_col}` DESC"

    sql = (
        f"SELECT {', '.join(select_fields)} "
        f"FROM `{table}` "
        f"WHERE {where_clause} "
        f"{order_by} "
        f"LIMIT 100"
    )
    return sql, tuple(hard_params)

def _build_vector_recall_sql(
    table: str,
    classification: dict[str, list[str]],
    intent: SearchIntent,
    source_ids: list[str],
) -> Optional[tuple[str, tuple[Any, ...]]]:
    """P1-3：对 Milvus 召回出的主键做回表查询。

    P0-2：回表仅保留约束性条件。语义召回的目的正是找到“文本相近但
    字段值不完全相等”的行，若叠加偏好性硬过滤（行业/等级/实体名精确匹配）
    会将召回候选全歼，使混合检索失去海选价值。
    """
    if not source_ids:
        return None

    semantic_cols = classification.get("semantic", [])[:4]
    if not semantic_cols:
        return None

    id_col = classification["id"][0] if classification.get("id") else semantic_cols[0]
    # SELECT * 全字段回表（修复 P0-1 缺列缺陷）+ 计算列
    select_fields = ["*", f"`{id_col}` AS `_id_`", "0 AS `_score_`"]

    hard_conds, hard_params = _build_constraint_conditions(table, classification, intent)
    placeholders = ", ".join(["%s"] * len(source_ids))
    hard_conds.append(f"`{id_col}` IN ({placeholders})")
    params: list[Any] = list(hard_params) + list(source_ids)
    sql = (
        f"SELECT {', '.join(select_fields)} "
        f"FROM `{table}` "
        f"WHERE {' AND '.join(hard_conds)} "
        f"LIMIT 100"
    )
    return sql, tuple(params)
