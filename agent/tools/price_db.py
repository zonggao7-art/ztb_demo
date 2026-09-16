"""SQL 结构化检索工具 — 对接 price_inquiry 已验证的查询路径。

设计（用户确认的决策）：工具直接接收结构化参数并构建 SearchIntent，
工具内部零 LLM 调用 —— 调用方 Agent 生成参数时天然完成意图解析。
现有 price_inquiry 节点继续走自己的 NL 意图解析路径，两条路径共用同一底层函数。

安全边界（蓝图 §5.4）：只读、表白名单、只走 queries.py/recall.py 的
白名单查询路径，不暴露任意 SQL。
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

from langchain_core.tools import StructuredTool

from public_kb.config import Settings

from ..nodes.price_inquiry import (
    _CLEAN_DB,
    _is_valid_company_name,
    _looks_like_code,
    _normalize_intent_enums,
    _query_bidding_data,
    _query_company_data,
    _query_penalty_by_company_name,
    _query_tables,
    SearchIntent,
)
from .base import ERR_INVALID_PARAMS, make_error_result, make_tool_result, wrap_async_tool, wrap_sync_tool
from .registry import GLOBAL_TOOL_REGISTRY, ToolMeta
from .schemas import (
    ALLOWED_TABLES,
    QueryCompanyAwardHistoryInput,
    QueryCompanyBusinessScopeInput,
    QueryCompanyPenaltyInput,
    QueryCompanyRegistrationInput,
    QueryProjectAwardInput,
    SearchBusinessDataInput,
)

logger = logging.getLogger(__name__)


# ── 公共辅助 ──

def _invalid(message: str) -> dict:
    return make_error_result(ERR_INVALID_PARAMS, message)


def _top_k(top_k: int | None) -> int:
    return top_k if top_k is not None else Settings().agent_tool_default_top_k


def _build_intent(
    *,
    sub_route: str,
    query_type: str,
    hard_filters: dict[str, Any],
    semantic_keywords: list[str] | None = None,
    exact_tokens: list[str] | None = None,
    sort_by: str | None = None,
    top_n: int | None = None,
) -> SearchIntent:
    """结构化参数 → SearchIntent；枚举归一化失败时降级为原始过滤（P0-4 语义）。"""
    intent = SearchIntent.from_dict(
        {
            "hard_filters": hard_filters,
            "semantic_keywords": semantic_keywords or [],
            "exact_tokens": exact_tokens or [],
            "sub_route": sub_route,
            "query_type": query_type,
            "sort_by": sort_by,
            "top_n": top_n,
        }
    )
    try:
        intent = _normalize_intent_enums(intent)
    except Exception as e:  # 归一化依赖外部枚举数据，失败不阻断查询
        logger.warning("[TOOL] 枚举归一化失败，降级为原始过滤: %s", e)
    return intent


def _records_result(result: dict[str, Any], top_k: int) -> dict:
    """统一查询结果 → ToolResult（records 截断 + 元信息）。"""
    records = result.get("records", []) or []
    data: dict[str, Any] = {"records": records[:top_k]}
    if not records:
        data["note"] = "未检索到匹配记录，可尝试放宽过滤条件或更换关键词"
    return make_tool_result(
        data=data,
        metadata={
            "source": f"mysql.{_CLEAN_DB}",
            "queried_tables": result.get("queried_tables", []),
            "row_count": len(records),
            "sql_count": result.get("sql_count", 0),
            "total_sql_time": result.get("total_sql_time", 0.0),
        },
    )


def _select_record_fields(result: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """为业务工具裁剪返回字段，避免共享数据表扩大对模型的可见范围。"""
    selected = deepcopy(result)
    records = (selected.get("data") or {}).get("records")
    if isinstance(records, list):
        selected["data"]["records"] = [
            {field: row[field] for field in fields if field in row}
            for row in records
            if isinstance(row, dict)
        ]
    return selected


def _validate_company_name(name: str, field_label: str) -> str | None:
    """P0-11 校验下沉：公司名必须通过工商主体名称格式校验。"""
    if not name or not name.strip():
        return f"{field_label} 不能为空"
    if not _is_valid_company_name(name.strip()):
        return (
            f"{field_label}「{name}」未通过工商主体名称格式校验，"
            "请提供完整的公司全称（如「XX有限公司」），不要使用简称或口语化描述"
        )
    return None


# ── 六条稳定业务线中的 SQL 工具 ──

QUERY_COMPANY_REGISTRATION_DESC = (
    "企业工商信息查询：按用户提供的主体名称精确查询统一社会信用代码、法定代表人、"
    "注册资本、成立日期、经营状态、行业和所在地区。只用于工商信息，不返回经营范围。"
)

_REGISTRATION_FIELDS = (
    "company_name", "credit_code", "legal_person", "registered_capital",
    "establish_date", "business_status", "industry", "province", "city",
)


def _query_company_registration_impl(company_name: str, top_k: int | None = None) -> dict:
    result = _query_company_info_impl(company_name=company_name, top_k=top_k)
    return _select_record_fields(result, _REGISTRATION_FIELDS)


async def _query_company_registration_async_impl(*args: Any, **kwargs: Any) -> dict:
    from ..runtime import run_blocking

    return await run_blocking(_query_company_registration_impl, *args, **kwargs)


QUERY_COMPANY_BUSINESS_SCOPE_DESC = (
    "企业经营范围查询：按用户提供的主体名称精确查询经营范围。"
    "只返回企业名称和经营范围，不返回其他工商字段。"
)

_BUSINESS_SCOPE_FIELDS = ("company_name", "business_scope")


def _query_company_business_scope_impl(company_name: str, top_k: int | None = None) -> dict:
    result = _query_company_info_impl(company_name=company_name, top_k=top_k)
    return _select_record_fields(result, _BUSINESS_SCOPE_FIELDS)


async def _query_company_business_scope_async_impl(*args: Any, **kwargs: Any) -> dict:
    from ..runtime import run_blocking

    return await run_blocking(_query_company_business_scope_impl, *args, **kwargs)


QUERY_PROJECT_AWARD_DESC = (
    "项目中标情况查询：仅按用户本轮提供或前一步工具明确返回的项目编号精确查询项目名称、采购人、中标供应商、"
    "中标金额和中标日期。不接受项目名称、企业名称或采购人作为检索条件。"
)


def _query_project_award_impl(project_number: str, top_k: int | None = None) -> dict:
    return _query_bid_records_impl(project_number=project_number, top_k=top_k)


async def _query_project_award_async_impl(*args: Any, **kwargs: Any) -> dict:
    from ..runtime import run_blocking

    return await run_blocking(_query_project_award_impl, *args, **kwargs)


QUERY_COMPANY_AWARD_HISTORY_DESC = (
    "企业中标历史查询：仅当用户明确查询某主体作为中标企业/供应商的历史时使用，"
    "按主体名称精确查询历史中标项目。不得用于查询采购人、招标人或发包人的历史，"
    "不接受项目编号作为检索条件。"
)


def _query_company_award_history_impl(company_name: str, top_k: int | None = None) -> dict:
    return _query_bid_records_impl(company_name=company_name, top_k=top_k)


async def _query_company_award_history_async_impl(*args: Any, **kwargs: Any) -> dict:
    from ..runtime import run_blocking

    return await run_blocking(_query_company_award_history_impl, *args, **kwargs)


def _query_company_info_impl(
    company_name: str,
    top_k: int | None = None,
) -> dict:
    bad = _validate_company_name(company_name, "company_name")
    if bad:
        return _invalid(bad)

    hard_filters: dict[str, Any] = {"company_name": company_name.strip()}

    intent = _build_intent(
        sub_route="company_query",
        query_type="company_detail",
        hard_filters=hard_filters,
        semantic_keywords=[company_name.strip()],
        exact_tokens=[company_name.strip()],
    )
    result = _query_company_data(intent)
    return _records_result(result, _top_k(top_k))


async def _query_company_info_async_impl(*args: Any, **kwargs: Any) -> dict:
    """同步查询路径在线程池中执行（SQL 全链路已带超时与连接池管理）。"""
    from ..runtime import run_blocking

    return await run_blocking(_query_company_info_impl, *args, **kwargs)


# ── query_company_penalty ──

QUERY_COMPANY_PENALTY_DESC = (
    "企业风控黑名单查询：按用户提供的主体名称精确查询行政处罚/不良记录"
    "（处罚日期、违法行为、处罚结果、执法单位等）。hybrid不审核主体真实性或名称后缀。"
)


def _query_company_penalty_impl(company_name: str, top_k: int | None = None) -> dict:
    bad = _validate_company_name(company_name, "company_name")
    if bad:
        return _invalid(bad)

    records = _query_penalty_by_company_name(company_name.strip())
    limited = records[: _top_k(top_k) if top_k is not None else 50]
    data: dict[str, Any] = {"records": limited}
    if not limited:
        data["note"] = (
            f"未查询到「{company_name.strip()}」的行政处罚记录，"
            "可能该企业无不良记录或未收录，请核对公司全称"
        )
    return make_tool_result(
        data=data,
        metadata={
            "source": f"mysql.{_CLEAN_DB}.company_penalty",
            "row_count": len(limited),
        },
    )


async def _query_company_penalty_async_impl(company_name: str, top_k: int | None = None) -> dict:
    from ..runtime import run_blocking

    return await run_blocking(_query_company_penalty_impl, company_name, top_k)


def _query_bid_records_impl(
    project_number: str | None = None,
    company_name: str | None = None,
    purchaser: str | None = None,
    time_start: str | None = None,
    time_end: str | None = None,
    region: str | None = None,
    province: str | None = None,
    winning_amount_min: float | None = None,
    winning_amount_max: float | None = None,
    sort_by: str | None = None,
    top_k: int | None = None,
) -> dict:
    pn = (project_number or "").strip()
    cn = (company_name or "").strip()
    pc = (purchaser or "").strip()

    if not pn and not cn and not pc:
        return _invalid(
            "必须提供查询主体：project_number（项目编号）、company_name（中标供应商全称）"
            "或 purchaser（采购人全称）至少其一"
        )

    hard_filters: dict[str, Any] = {}
    if pn:
        if not _looks_like_code(pn):
            return _invalid(
                f"project_number「{pn}」不是有效的项目编号格式（应为字母+数字编码，如 AH2024-001）"
            )
        hard_filters["project_number"] = pn
    if cn:
        bad = _validate_company_name(cn, "company_name")
        if bad:
            return _invalid(bad)
        # bid_project 表只开放 purchaser/successful_bidder 两个主体检索字段（P0-11）
        hard_filters["successful_bidder"] = cn
    if pc:
        bad = _validate_company_name(pc, "purchaser")
        if bad:
            return _invalid(bad)
        hard_filters["purchaser"] = pc
    if region:
        hard_filters["region"] = region
    if province:
        hard_filters["province"] = province
    if time_start or time_end:
        hard_filters["time_range"] = {"start": time_start, "end": time_end}
    if winning_amount_min is not None or winning_amount_max is not None:
        hard_filters["winning_amount_range"] = {
            "min": winning_amount_min,
            "max": winning_amount_max,
        }

    intent = _build_intent(
        sub_route="bidding_query",
        query_type="project_detail" if pn else "bid_records",
        hard_filters=hard_filters,
        semantic_keywords=[cn or pc] if (cn or pc) else [],
        exact_tokens=[pn or cn or pc],
        sort_by=sort_by,
    )
    result = _query_bidding_data(intent)
    return _records_result(result, _top_k(top_k))


async def _query_bid_records_async_impl(*args: Any, **kwargs: Any) -> dict:
    from ..runtime import run_blocking

    return await run_blocking(_query_bid_records_impl, *args, **kwargs)


# ── search_business_data ──

SEARCH_BUSINESS_DATA_DESC = (
    "业务数据通用检索（长尾查询兜底）：对 company_info / company_penalty / bid_project "
    "三张核心表执行全文/LIKE 候选检索。"
    "仅返回候选，不保证精确条件或完整覆盖；不能替代带日期/金额硬过滤的精确查询；keywords 提供 1~5 个关键词。"
)


def _search_business_data_impl(
    keywords: list[str],
    exact_tokens: list[str] | None = None,
    tables: list[str] | None = None,
    top_k: int | None = None,
) -> dict:
    kws = [k.strip() for k in (keywords or []) if k and k.strip()]
    if not kws:
        return _invalid("keywords 不能为空，请提供 1~5 个检索关键词")
    if len(kws) > 5:
        kws = kws[:5]

    if tables:
        invalid_tables = [t for t in tables if t not in ALLOWED_TABLES]
        if invalid_tables:
            return _invalid(
                f"tables 包含非法表名 {invalid_tables}，仅允许 {list(ALLOWED_TABLES)}"
            )
        target_tables = list(dict.fromkeys(tables))
    else:
        target_tables = list(ALLOWED_TABLES)

    tokens = [t.strip() for t in (exact_tokens or []) if t and t.strip()]

    intent = _build_intent(
        sub_route="all",
        query_type="mixed",
        hard_filters={},
        semantic_keywords=kws,
        exact_tokens=tokens,
    )
    result = _query_tables(target_tables, intent)
    return _records_result(result, _top_k(top_k))


async def _search_business_data_async_impl(*args: Any, **kwargs: Any) -> dict:
    from ..runtime import run_blocking

    return await run_blocking(_search_business_data_impl, *args, **kwargs)


# ── 注册 ──

def register_price_db_tools(registry=GLOBAL_TOOL_REGISTRY) -> None:
    """向注册中心注册 SQL 检索工具。"""
    specs = [
        (
            "query_company_registration",
            QUERY_COMPANY_REGISTRATION_DESC,
            QueryCompanyRegistrationInput,
            _query_company_registration_impl,
            _query_company_registration_async_impl,
            {"price", "sql", "company", "baseline"},
        ),
        (
            "query_company_business_scope",
            QUERY_COMPANY_BUSINESS_SCOPE_DESC,
            QueryCompanyBusinessScopeInput,
            _query_company_business_scope_impl,
            _query_company_business_scope_async_impl,
            {"price", "sql", "company", "baseline"},
        ),
        (
            "query_project_award",
            QUERY_PROJECT_AWARD_DESC,
            QueryProjectAwardInput,
            _query_project_award_impl,
            _query_project_award_async_impl,
            {"price", "sql", "bidding", "baseline"},
        ),
        (
            "query_company_award_history",
            QUERY_COMPANY_AWARD_HISTORY_DESC,
            QueryCompanyAwardHistoryInput,
            _query_company_award_history_impl,
            _query_company_award_history_async_impl,
            {"price", "sql", "bidding", "baseline"},
        ),
        (
            "query_company_penalty",
            QUERY_COMPANY_PENALTY_DESC,
            QueryCompanyPenaltyInput,
            _query_company_penalty_impl,
            _query_company_penalty_async_impl,
            {"price", "sql", "risk"},
        ),
        (
            "search_business_data",
            SEARCH_BUSINESS_DATA_DESC,
            SearchBusinessDataInput,
            _search_business_data_impl,
            _search_business_data_async_impl,
            {"price", "sql", "fallback"},
        ),
    ]
    for name, desc, schema, sync_fn, async_fn, tags in specs:
        registry.register(
            StructuredTool.from_function(
                func=wrap_sync_tool(name, sync_fn),
                coroutine=wrap_async_tool(name, async_fn),
                args_schema=schema,
                name=name,
                description=desc,
                response_format="content_and_artifact",
            ),
            ToolMeta(name=name, description=desc, tags=frozenset(tags)),
        )
