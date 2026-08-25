"""
answer_templates — 自然语言回答模板引擎。

根据设计文档（three_core_modules_design_and_feasibility.md）定义的
标准回答模板，为每个 query_type 提供自然语言渲染能力。

设计原则：
  - 模板驱动：每个 query_type 绑定一个回答模板，禁止临时拼装
  - 自然语言优先：回答是连贯的叙述文本，仅当记录数≥3 时回退到编号列表
  - 必须包含数据来源行：每条回答末尾附（数据来源：ztb_clean.{table_name}）
  - 空结果 ≠ 沉默：必须给"可能原因 + 下一步建议"，引导用户到其他核心功能

与 output_templates.py 的关系：
  - output_templates.py 负责字段筛选、空值处理、截断
  - answer_templates.py 负责将筛选后的字段渲染为自然语言
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

# ═════════════════════════════════════════════════════════
# 模板数据结构
# ═════════════════════════════════════════════════════════

@dataclass
class AnswerTemplate:
    """单个 query_type 的回答模板定义。"""
    query_type: str                                  # 如 "company_detail"
    source_table: str                                # 数据来源表（用于末尾标注）
    total_rows: int                                  # 源表总行数
    single_template: str                             # 单条记录时的自然语言模板
    multi_template: str = ""                         # 多条记录时的列表模板（可选）
    item_line: str = ""                              # 多条记录时每条的行模板
    empty_template: str = ""                         # 空结果时的回答模板
    not_found_template: str = ""                     # 实体不存在时的回答模板


# ═════════════════════════════════════════════════════════
# 值格式化
# ═════════════════════════════════════════════════════════

def _fmt_value(value: Any) -> str:
    """安全格式化字段值。"""
    if value is None:
        return "未提供"
    s = str(value)
    return s.strip() if s.strip() else "未提供"


def _fmt_amount(value: Any) -> str:
    """格式化金额字段，对 0 值做特殊处理。"""
    if value is None:
        return "金额未公开"
    try:
        amount = float(value)
        if amount == 0:
            return "金额未公开"
        return f"{amount:,.2f}"
    except (ValueError, TypeError):
        return str(value)


# ═════════════════════════════════════════════════════════
# 各 query_type 的回答模板（按设计文档 §1-§3 定义）
# ═════════════════════════════════════════════════════════

ANSWER_TEMPLATES: dict[str, AnswerTemplate] = {
    # ── §1.1 query①：查 XX 公司的工商信息 ──
    "company_detail": AnswerTemplate(
        query_type="company_detail",
        source_table="ztb_clean.company_info",
        total_rows=38911,
        single_template=(
            "经查询，{company_name}（统一社会信用代码：{credit_code}）是一家成立于 {establish_date} 的"
            "{company_type}企业，法定代表人 {legal_person}，注册资本 {registered_capital}。\n\n"
            "该公司注册地址为 {address}，所属行业为「{industry}」，企业等级为 {company_level}，"
            "目前经营状态为 {business_status}。\n\n"
            "其经营范围为：{business_scope}。\n\n"
            "（数据来源：ztb_clean.company_info）"
        ),
        empty_template=(
            '系统中未收录"{entity}"的工商信息。这可能因为：\n'
            "① 公司名称存在差异（建议核对工商登记全称）；\n"
            "② 该公司未被系统收录。\n\n"
            '如需查询其他信息，可尝试："{entity}有无不良记录" 或 "{entity}中标情况"。'
        ),
    ),

    # ── §1.2 query②：XX 公司是做什么行业的？/经营范围？ ──
    "company_industry": AnswerTemplate(
        query_type="company_industry",
        source_table="ztb_clean.company_info",
        total_rows=38911,
        single_template=(
            "{company_name} 所属行业为「{industry}」，企业等级为 {company_level}，"
            "注册地为 {province}{city}。\n\n"
            "其经营范围为：{business_scope}。\n\n"
            "（数据来源：ztb_clean.company_info）"
        ),
        empty_template=(
            '系统中未收录"{entity}"的行业信息。这可能因为公司名称存在差异。\n'
            '建议核对工商登记全称后重试，或查询"{entity}的工商信息"获取更多详情。'
        ),
    ),

    # ── §2.1：XX 公司有无不良记录？/被处罚过吗？ ──
    "penalty_check": AnswerTemplate(
        query_type="penalty_check",
        source_table="ztb_clean.company_penalty",
        total_rows=1805,
        single_template=(
            "经查询，{company_name}（统一社会信用代码：{credit_code}）存在不良记录。\n\n"
            "处罚日期：{penalty_date}\n"
            "执法单位：{law_enforcement_unit}\n"
            "违法事实：{illegal_behavior}\n"
            "处罚结果：{penalty_result}\n\n"
            "（数据来源：ztb_clean.company_penalty）"
        ),
        multi_template=(
            "经查询，{company_name}（统一社会信用代码：{credit_code}）存在 {N} 条不良记录"
            "（按处罚日期倒序排列）：\n\n"
            "{items}\n"
            "（数据来源：ztb_clean.company_penalty，共 {N} 条记录）"
        ),
        item_line=(
            "【{index}】处罚日期：{penalty_date}\n"
            "    执法单位：{law_enforcement_unit}\n"
            "    违法事实：{illegal_behavior}\n"
            "    处罚结果：{penalty_result}"
        ),
        empty_template=(
            "经查询，在系统收录的数据范围内，{company_name}（统一社会信用代码：{credit_code}）"
            "暂未发现不良记录或处罚信息。\n\n"
            "（数据来源：ztb_clean.company_penalty，收录 1,805 条处罚记录）"
        ),
        not_found_template=(
            '系统中未收录"{company_name}"的不良记录信息。这可能因为：\n\n'
            "① 该公司确无处罚记录；\n\n"
            "② 本系统暂未收录该公司的处罚信息。\n\n"
            "③ 公司名称存在差异（建议核对工商登记全称）。\n\n"
            '如需查询该公司的工商登记信息，请提问"查{company_name}的工商信息"。'
        ),
    ),

    # ── §3.1 query①：XX 公司中标了什么项目？/中标历史？ ──
    "bidder_query": AnswerTemplate(
        query_type="bidder_query",
        source_table="ztb_clean.bid_project",
        total_rows=17742,
        single_template=(
            "根据系统收录的招投标数据，{successful_bidder} 在 {winning_date} 中标了 "
            "{purchaser} 的「{project_name}」（项目编号：{project_number}），"
            "中标金额为 {winning_amount} 元。\n\n"
            "（数据来源：ztb_clean.bid_project）"
        ),
        multi_template=(
            "根据系统收录的招投标数据，{successful_bidder} 共中标 {N} 个项目，"
            "最近的中标记录如下：\n\n"
            "{items}\n\n"
            "如需查看某个项目的详细信息，请提供项目名称或编号。\n\n"
            "（数据来源：ztb_clean.bid_project，共 {N} 条记录）"
        ),
        item_line=(
            "{index} {winning_date} | {purchaser}「{project_name}」"
            "| 中标金额 {winning_amount} 元"
        ),
        empty_template=(
            '在系统收录的 17,742 条项目记录中，暂未查询到"{entity}"的中标信息。\n\n'
            "这可能因为：\n"
            "① 该公司未在收录的区域/时段内中标；\n"
            "② 公司名称写法与系统中标供应商名称不一致（建议使用工商登记全称重试）。\n\n"
            "（数据来源：ztb_clean.bid_project）"
        ),
    ),

    # ── §3.2 query②：XX 项目（名称/编号）的中标情况 ──
    "project_detail": AnswerTemplate(
        query_type="project_detail",
        source_table="ztb_clean.bid_project",
        total_rows=17742,
        single_template=(
            "项目「{project_name}」（项目编号：{project_number}）由 {purchaser} 采购，"
            "于 {winning_date} 确定中标结果。\n\n"
            "中标供应商：{successful_bidder}\n"
            "中标金额：{winning_amount} 元\n"
            "预算金额：{budget_amount} 元\n"
            "代理机构：{agent}\n"
            "标的物：{subject_matter}\n\n"
            "（数据来源：ztb_clean.bid_project）"
        ),
        multi_template=(
            '根据条件共匹配到 {N} 个相关项目，列表如下：\n\n'
            "{items}\n\n"
            "如需查看某个项目的详细中标情况，请提供准确的项目编号（如 AH2024-001）。\n\n"
            "（数据来源：ztb_clean.bid_project）"
        ),
        item_line=(
            "{index} [{project_number}] {project_name} | {purchaser} | "
            "中标 {successful_bidder} | {winning_date} | "
            "预算 {budget_amount} | {agent} | {subject_matter}"
        ),
        empty_template=(
            '在系统收录的 17,742 条项目记录中，暂未查询到项目编号"{entity}"的中标情况。\n\n'
            "这可能因为：\n"
            "① 项目编号拼写有误（项目编号格式通常为 AH2024-001 或 ZB-2024-123）；\n"
            "② 该项目未被系统收录。\n\n"
            "（数据来源：ztb_clean.bid_project）"
        ),
    ),

    # ── purchaser_query（采购人视角） ──
    "purchaser_query": AnswerTemplate(
        query_type="purchaser_query",
        source_table="ztb_clean.bid_project",
        total_rows=17742,
        single_template=(
            "{purchaser} 于 {winning_date} 将「{project_name}」（项目编号：{project_number}）"
            "发包给 {successful_bidder}，中标金额为 {winning_amount} 元。\n\n"
            "（数据来源：ztb_clean.bid_project）"
        ),
        multi_template=(
            "{purchaser} 共发布 {N} 个项目，最近的中标记录如下：\n\n"
            "{items}\n\n"
            "（数据来源：ztb_clean.bid_project，共 {N} 条记录）"
        ),
        item_line=(
            "{index} {winning_date} | 「{project_name}」"
            "| 中标 {successful_bidder} | {winning_amount} 元"
        ),
        empty_template=(
            '在系统收录的 17,742 条项目记录中，暂未查询到"{entity}"的采购项目记录。\n\n'
            "（数据来源：ztb_clean.bid_project）"
        ),
    ),

    # ── aggregation（聚合查询） ──
    "aggregation": AnswerTemplate(
        query_type="aggregation",
        source_table="ztb_clean.bid_project",
        total_rows=17742,
        single_template=(
            "查询结果：{winning_amount} 元\n"
            "（数据来源：ztb_clean.bid_project）"
        ),
        multi_template=(
            "聚合查询结果（共 {N} 条记录）：\n\n"
            "{items}\n\n"
            "（数据来源：ztb_clean.bid_project）"
        ),
        item_line=(
            "{index} {winning_date} | 「{project_name}」"
            "| {successful_bidder} | {winning_amount} 元"
        ),
        empty_template=(
            "暂未查询到符合条件的聚合结果。\n"
            "（数据来源：ztb_clean.bid_project）"
        ),
    ),

    # ── mixed（混合查询回退） ──
    "mixed": AnswerTemplate(
        query_type="mixed",
        source_table="ztb_clean",
        total_rows=0,
        single_template=(
            "为您查询到以下记录：\n\n{record_text}\n\n（数据来源：ztb_clean）"
        ),
        empty_template=(
            '暂未查询到与"{entity}"相关的记录。\n\n'
            "建议尝试：\n"
            "  1. 使用更精确的关键词\n"
            "  2. 尝试不同的查询方式\n"
            "  （数据来源：ztb_clean）"
        ),
    ),
}


# ═════════════════════════════════════════════════════════
# 运行时渲染引擎
# ═════════════════════════════════════════════════════════

def _resolve_template(query_type: str) -> Optional[AnswerTemplate]:
    """获取指定 query_type 的回答模板。"""
    return ANSWER_TEMPLATES.get(query_type)


def _build_record_context(record: dict[str, Any], template: AnswerTemplate) -> dict[str, str]:
    """将原始记录的列名映射为模板占位符名，并做格式化处理。

    处理：
    - 金额字段（winning_amount, budget_amount）：0 值显示"金额未公开"
    - None 值：显示"未提供"
    - 日期类型：转字符串
    """
    ctx: dict[str, str] = {}
    # 通用值格式化
    for k, v in record.items():
        if k.startswith("_"):
            continue
        if k in ("winning_amount", "budget_amount"):
            ctx[k] = _fmt_amount(v)
        else:
            ctx[k] = _fmt_value(v)
    return ctx


def _render_single(record: dict[str, Any], template: AnswerTemplate) -> str:
    """渲染单条记录的自然语言回答。"""
    ctx = _build_record_context(record, template)
    return template.single_template.format(**ctx)


def _render_multi(records: list[dict[str, Any]], template: AnswerTemplate) -> str:
    """渲染多条记录的自然语言回答（列表模式）。"""
    if not template.multi_template:
        # 无 multi_template 时逐条渲染拼接
        parts = []
        for i, rec in enumerate(records, 1):
            parts.append(f"【{i}】\n{_render_single(rec, template)}")
        return "\n\n".join(parts)

    items = []
    for i, rec in enumerate(records, 1):
        ctx = _build_record_context(rec, template)
        ctx["index"] = str(i)
        items.append(template.item_line.format(**ctx))

    first_ctx = _build_record_context(records[0], template)
    first_ctx["N"] = str(len(records))
    first_ctx["items"] = "\n".join(items)
    return template.multi_template.format(**first_ctx)


def _render_empty(template: AnswerTemplate, entity: str) -> str:
    """渲染空结果回答。"""
    if template.empty_template:
        return template.empty_template.format(entity=entity)
    return (
        f'在系统收录的 {template.total_rows:,} 条记录中，暂未查询到"{entity}"的相关信息。\n\n'
        f"（数据来源：{template.source_table}）"
    )


def _render_not_found(template: AnswerTemplate, entity: str) -> str:
    """渲染实体不存在时的回答。"""
    if template.not_found_template:
        return template.not_found_template.format(
            company_name=entity,
            entity=entity,
        )
    return _render_empty(template, entity)


def render_answer(
    query_type: str,
    records: list[dict[str, Any]],
    *,
    entity: str = "",
    sub_route: str = "",
) -> str:
    """回答模板渲染入口。

    根据 query_type 选择对应的 AnswerTemplate，将查询结果渲染为自然语言文本。

    Args:
        query_type: 查询类型（如 "company_detail", "penalty_check", "bidder_query" 等）
        records: 格式化后的查询记录列表
        entity: 用户查询的实体名称（用于空结果引导）
        sub_route: 二级路由（company_query / bidding_query），用于回退

    Returns:
        自然语言回答文本
    """
    template = _resolve_template(query_type)
    if template is None:
        # 回退到 mixed 模板
        template = _resolve_template("mixed")
        if template is None:
            return "抱歉，暂不支持该类型的查询。"
        # mixed 模板的特殊处理
        if not records:
            return template.empty_template.format(entity=entity or "该查询")
        parts = []
        for i, rec in enumerate(records[:10], 1):
            ctx = _build_record_context(rec, template)
            parts.append(
                f"【{i}】" + " | ".join(
                    f"{k}: {v}" for k, v in ctx.items() if v and v != "未提供"
                )
            )
        record_text = "\n".join(parts)
        return template.single_template.format(record_text=record_text)

    # 空结果处理
    if not records:
        if template.not_found_template:
            return _render_not_found(template, entity)
        return _render_empty(template, entity)

    # 单条 / 多条
    if len(records) == 1:
        return _render_single(records[0], template)
    else:
        return _render_multi(records, template)


# ═════════════════════════════════════════════════════════
# 能力边界引导（产品查询下线后的统一话术）
# ═════════════════════════════════════════════════════════

CAPABILITY_GUIDANCE = """本系统当前专注于以下三大核心能力：

1. 企业工商情报查询 — "XX公司详情/工商信息"
2. 企业风控黑名单查询 — "XX公司有无不良记录/处罚"
3. 招投标中标情报查询 — "XX公司中标了哪些项目"（使用公司名称查询）
   或 "查项目编号 AH2024-001 的中标情况"（需提供项目编号精确匹配）

如需查询单个项目的详细中标情况，请提供准确的项目编号（格式通常为 AH2024-001 或 ZB-2024-123）。
如需查询某家企业的全部中标历史，可使用公司名称进行查询。"""
