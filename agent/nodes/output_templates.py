"""
output_templates — 统一输出字段配置框架。

通过 FieldDescriptor + OutputTemplate 声明式配置模型，统一管理三个二级路由
（company_query / product_query / bidding_query）的输出字段筛选、空值处理和截断规则。

设计原则：
  - 配置驱动：新增 query_type 仅需声明 OutputTemplate，无需修改核心引擎
  - 三层分级：required（必出）/ conditional（条件出）/ optional（可选）
  - 集中管控：空值行为、文本截断、字段上限在此统一处理
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class FieldDescriptor:
    """单个输出字段描述符 — 全系统统一。"""
    key: str                         # 机器名，如 "company_name"
    label: str                       # 中文显示名，如 "企业名称"
    source_table: str                # 来源表名
    source_col: str                  # 来源列名
    null_behavior: str = "hide"      # "hide" | "show_placeholder" | "keep_null"
    max_chars: Optional[int] = None  # 截断阈值（char），超长则截断 + "\u2026"
    group: str = "default"           # 字段分组标签


@dataclass
class OutputTemplate:
    """输出模板 — 定义某 query_type 的字段筛选规则。"""
    route: str                       # "company_query" | "product_query" | "bidding_query"
    query_type: str                  # "supplier_recommend" | "price_inquiry" | ...
    required: list[str] = field(default_factory=list)
    conditional: dict[str, list[str]] = field(default_factory=dict)
    optional: list[str] = field(default_factory=list)
    display_order: list[str] = field(default_factory=list)


# ═════════════════════════════════════════════════════════
# 全局字段注册表
# ═════════════════════════════════════════════════════════
_FIELD_REGISTRY: dict[str, FieldDescriptor] = {}


def _register(fd: FieldDescriptor) -> FieldDescriptor:
    """注册字段描述符，全局唯一。"""
    if fd.key in _FIELD_REGISTRY:
        raise ValueError(f"字段 key '{fd.key}' 重复注册")
    _FIELD_REGISTRY[fd.key] = fd
    return fd


# ===== company_query 字段 =====
_register(FieldDescriptor("company_name", "企业名称", "company_info", "company_name",
    null_behavior="show_placeholder"))
_register(FieldDescriptor("credit_code", "统一社会信用代码", "company_info", "credit_code",
    null_behavior="show_placeholder"))
_register(FieldDescriptor("legal_person", "法定代表人", "company_info", "legal_person"))
_register(FieldDescriptor("registered_capital", "注册资本", "company_info", "registered_capital"))
_register(FieldDescriptor("establish_date", "成立日期", "company_info", "establish_date"))
_register(FieldDescriptor("business_status", "经营状态", "company_info", "business_status",
    null_behavior="show_placeholder"))
_register(FieldDescriptor("industry", "所属行业", "company_info", "industry",
    null_behavior="show_placeholder"))
_register(FieldDescriptor("company_type", "企业类型", "company_info", "company_type"))
_register(FieldDescriptor("company_level", "企业等级", "company_info", "company_level"))
_register(FieldDescriptor("province", "省份", "company_info", "province",
    null_behavior="show_placeholder"))
_register(FieldDescriptor("city", "城市", "company_info", "city"))
_register(FieldDescriptor("address", "企业地址", "company_info", "address", max_chars=100))
_register(FieldDescriptor("business_scope", "经营范围", "company_info", "business_scope",
    max_chars=200))
# penalty 字段（来源 company_penalty 表）
_register(FieldDescriptor("penalty_date", "处罚日期", "company_penalty", "penalty_date",
    null_behavior="show_placeholder"))
_register(FieldDescriptor("illegal_behavior", "违法行为", "company_penalty", "illegal_behavior",
    null_behavior="show_placeholder", max_chars=500))
_register(FieldDescriptor("penalty_result", "处罚结果", "company_penalty", "penalty_result",
    null_behavior="show_placeholder", max_chars=500))
_register(FieldDescriptor("law_enforcement_unit", "执法单位", "company_penalty",
    "law_enforcement_unit"))

# ===== bidding_query 字段 =====
_register(FieldDescriptor("project_name", "项目名称", "bid_project", "project_name",
    null_behavior="show_placeholder"))
_register(FieldDescriptor("project_number", "项目编号", "bid_project", "project_number",
    null_behavior="show_placeholder"))
_register(FieldDescriptor("purchaser", "采购人", "bid_project", "purchaser",
    null_behavior="show_placeholder"))
_register(FieldDescriptor("successful_bidder", "中标供应商", "bid_project", "successful_bidder",
    null_behavior="show_placeholder"))
_register(FieldDescriptor("winning_amount", "中标金额", "bid_project", "winning_amount",
    null_behavior="show_placeholder"))
_register(FieldDescriptor("winning_date", "中标日期", "bid_project", "winning_date",
    null_behavior="show_placeholder"))
_register(FieldDescriptor("subject_matter", "标的物", "bid_project", "subject_matter",
    max_chars=200))
_register(FieldDescriptor("agent", "代理机构", "bid_project", "agent"))
_register(FieldDescriptor("project_stage", "项目阶段", "bid_project", "project_stage"))
_register(FieldDescriptor("project_category", "项目类别", "bid_project", "project_category"))
_register(FieldDescriptor("budget_amount", "预算金额", "bid_project", "budget_amount"))
_register(FieldDescriptor("publish_date", "发布日期", "bid_project", "publish_date"))
_register(FieldDescriptor("source_url", "来源链接", "bid_project", "source_url"))


# ═════════════════════════════════════════════════════════
# 各路由输出模板
# ═════════════════════════════════════════════════════════

_COMPANY_OUTPUT_TEMPLATES: dict[str, OutputTemplate] = {
    "supplier_recommend": OutputTemplate(
        route="company_query",
        query_type="supplier_recommend",
        required=["company_name", "industry", "company_level", "province", "city"],
        conditional={
            "intent.need_penalty_check": ["penalty_date", "illegal_behavior", "penalty_result"],
        },
        optional=["legal_person", "registered_capital", "establish_date",
                  "business_scope", "address", "business_status",
                  "company_type", "credit_code"],
        display_order=["company_name", "industry", "company_level", "province",
                       "city", "registered_capital", "establish_date",
                       "business_scope", "address",
                       "business_status", "legal_person", "company_type",
                       "credit_code"],
    ),
    "penalty_check": OutputTemplate(
        route="company_query",
        query_type="penalty_check",
        required=["company_name", "credit_code", "penalty_date",
                  "illegal_behavior", "penalty_result"],
        optional=["law_enforcement_unit"],
        display_order=["company_name", "penalty_date", "illegal_behavior",
                       "penalty_result", "law_enforcement_unit", "credit_code"],
    ),
    "company_detail": OutputTemplate(
        route="company_query",
        query_type="company_detail",
        required=["company_name", "credit_code", "business_status"],
        conditional={
            "intent.need_penalty_check": ["penalty_date", "illegal_behavior", "penalty_result"],
        },
        optional=["legal_person", "registered_capital", "establish_date",
                  "industry", "company_type", "company_level",
                  "province", "city", "address", "business_scope"],
        display_order=["company_name", "credit_code", "legal_person",
                       "registered_capital", "establish_date", "business_status",
                       "industry", "company_type", "company_level",
                       "province", "city", "address",
                       "business_scope"],
    ),
    "mixed": OutputTemplate(
        route="company_query",
        query_type="mixed",
        required=["company_name", "industry", "company_level", "province", "city",
                  "credit_code", "penalty_date", "illegal_behavior", "penalty_result"],
        optional=["legal_person", "registered_capital", "establish_date",
                  "business_scope", "address", "business_status",
                  "company_type", "law_enforcement_unit"],
        display_order=["company_name", "industry", "company_level", "province",
                       "city", "registered_capital", "establish_date",
                       "business_scope", "address",
                       "business_status", "legal_person", "company_type",
                       "credit_code", "penalty_date", "illegal_behavior",
                       "penalty_result", "law_enforcement_unit"],
    ),
}

_BIDDING_OUTPUT_TEMPLATES: dict[str, OutputTemplate] = {
    "purchaser_query": OutputTemplate(
        route="bidding_query",
        query_type="purchaser_query",
        required=["project_name", "project_number", "successful_bidder",
                  "winning_amount", "winning_date", "purchaser"],
        optional=["subject_matter", "agent", "project_stage",
                  "project_category", "budget_amount", "province", "city",
                  "publish_date", "source_url"],
        display_order=["project_name", "project_number", "purchaser",
                       "successful_bidder", "winning_amount", "winning_date",
                       "subject_matter", "agent", "project_stage",
                       "project_category", "budget_amount", "province",
                       "city", "publish_date", "source_url"],
    ),
    "bidder_query": OutputTemplate(
        route="bidding_query",
        query_type="bidder_query",
        required=["project_name", "project_number", "purchaser",
                  "successful_bidder", "winning_amount", "winning_date"],
        optional=["subject_matter", "agent",
                  "project_stage", "project_category", "budget_amount",
                  "province", "city", "publish_date", "source_url"],
        display_order=["project_name", "project_number", "purchaser",
                       "successful_bidder", "winning_amount", "winning_date",
                       "subject_matter", "agent", "project_stage",
                       "project_category", "budget_amount", "province",
                       "city", "publish_date", "source_url"],
    ),
    "project_detail": OutputTemplate(
        route="bidding_query",
        query_type="project_detail",
        required=["project_name", "project_number", "purchaser",
                  "successful_bidder", "winning_amount", "budget_amount",
                  "winning_date", "agent", "subject_matter"],
        optional=["project_stage", "project_category", "publish_date",
                  "province", "city", "source_url"],
        display_order=["project_name", "project_number", "purchaser",
                       "successful_bidder", "winning_amount", "budget_amount",
                       "winning_date", "agent", "subject_matter",
                       "project_stage", "project_category", "publish_date",
                       "province", "city", "source_url"],
    ),
    "aggregation": OutputTemplate(
        route="bidding_query",
        query_type="aggregation",
        required=["project_name", "winning_amount", "winning_date"],
        optional=["project_number", "purchaser", "successful_bidder",
                  "subject_matter", "agent", "project_stage",
                  "project_category", "budget_amount", "province",
                  "city", "publish_date", "source_url"],
        display_order=["project_name", "project_number", "winning_amount",
                       "winning_date", "purchaser", "successful_bidder",
                       "subject_matter", "agent", "project_stage",
                       "project_category", "budget_amount",
                       "province", "city", "publish_date", "source_url"],
    ),
    "mixed": OutputTemplate(
        route="bidding_query",
        query_type="mixed",
        required=["project_name", "project_number", "winning_amount", "winning_date"],
        optional=["purchaser", "successful_bidder", "subject_matter",
                  "agent", "project_stage", "project_category",
                  "budget_amount", "province", "city", "publish_date", "source_url"],
        display_order=["project_name", "project_number", "winning_amount",
                       "winning_date", "purchaser", "successful_bidder",
                       "subject_matter", "agent", "project_stage",
                       "project_category", "budget_amount",
                       "province", "city", "publish_date", "source_url"],
    ),
}

# 路由 → 模板表映射
_ROUTE_TEMPLATES: dict[str, dict[str, OutputTemplate]] = {
    "company_query": _COMPANY_OUTPUT_TEMPLATES,
    "bidding_query": _BIDDING_OUTPUT_TEMPLATES,
}


# ═════════════════════════════════════════════════════════
# 运行时字段筛选引擎
# ═════════════════════════════════════════════════════════

def _eval_condition(expr: str, intent: Any) -> bool:
    """安全的条件表达式求值（仅支持 intent.xxx 布尔字段）。"""
    if expr.startswith("intent."):
        attr = expr[len("intent."):]  # e.g. "need_contact"
        return bool(getattr(intent, attr, False))
    return False


def _apply_output_template(
    records: list[dict],
    intent: Any,
    template: OutputTemplate,
    field_registry: dict[str, FieldDescriptor] = None,
    max_fields_per_record: int = 12,
) -> list[dict]:
    """根据 OutputTemplate 筛选并格式化输出字段。

    处理流程：
    ① 确定活跃字段集（required + 条件满足的 conditional + optional）
    ② 逐字段应用空值处理（hide / show_placeholder / keep_null）
    ③ 逐字段应用截断（max_chars → 追加 "\u2026"）
    ④ 按 display_order 排序
    ⑤ 单记录字段数超 max_fields_per_record 时，从 optional 尾部裁剪
    """
    if field_registry is None:
        field_registry = _FIELD_REGISTRY

    # ① 活跃字段
    active_keys: set[str] = set(template.required)

    for cond_expr, keys in template.conditional.items():
        if _eval_condition(cond_expr, intent):
            active_keys.update(keys)

    active_keys.update(template.optional)

    # ②③④ 逐记录格式化
    formatted = []
    for raw in records:
        row = {}
        for key in template.display_order:
            if key not in active_keys:
                continue
            fd = field_registry.get(key)
            if fd is None:
                continue

            value = raw.get(fd.source_col)

            # 空值处理（②）
            if value is None or (isinstance(value, str) and value.strip() == ""):
                if fd.null_behavior == "show_placeholder":
                    row[fd.label] = "未提供"
                elif fd.null_behavior == "keep_null":
                    row[fd.label] = None
                else:  # "hide"
                    continue
            else:
                # 截断（③）
                if fd.max_chars and isinstance(value, str) and len(value) > fd.max_chars:
                    value = value[:fd.max_chars] + "\u2026"
                row[fd.label] = value

        # ⑤ 字段上限裁剪
        if len(row) > max_fields_per_record:
            overflow = len(row) - max_fields_per_record
            keys_in_order = [k for k in template.display_order if k in row]
            for k in reversed(keys_in_order):
                if overflow <= 0:
                    break
                fd = field_registry.get(k)
                if fd is None:
                    continue
                k_label = fd.label
                if k_label in row and k not in template.required:
                    del row[k_label]
                    overflow -= 1

        if row:  # 全 NULL 的记录不输出
            formatted.append(row)

    return formatted


def get_template(sub_route: str, query_type: str) -> Optional[OutputTemplate]:
    """根据路由和 query_type 获取对应的输出模板。"""
    route_templates = _ROUTE_TEMPLATES.get(sub_route, {})
    return route_templates.get(query_type)
