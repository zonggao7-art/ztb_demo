# -*- coding: utf-8 -*-
"""基于 raw_tables 三张原始表，生成三份独立测试集（各 500 个问答对）。

生成结果：
    testset_bid_project.jsonl      项目中标情况 + 公司中标历史（各 250）
    testset_company_info.jsonl     工商信息 + 经营范围/行业（各 250）
    testset_company_penalty.jsonl  处罚记录（500）

字段契约（必填字段 = 字段映射表中非 hide / 非移除 的展示字段）：
    bid_project  项目视角 : project_name, project_number, purchaser, successful_bidder,
                           winning_amount, budget_amount, winning_date, agent, subject_matter
    bid_project  中标人视角: successful_bidder, project_name, project_number, purchaser,
                           winning_amount, winning_date
    company_info 工商信息  : company_name, credit_code, legal_person, registered_capital,
                           establish_date, business_status, industry, company_type, company_level
    company_info 经营范围  : company_name, industry, company_level, business_scope, province, city
    company_penalty       : company_name, credit_code, penalty_date, law_enforcement_unit,
                           illegal_behavior, penalty_result

金额特殊处理：winning_amount == 0 时显示「金额未公开」。

单主体约束：涉及公司的问题（中标历史/工商信息/经营范围/处罚记录）仅使用
以「公司」结尾且不含分隔符的单主体公司名，杜绝联合体与多公司合写；
问题模板内嵌真实公司名，不再拼接多余的「公司」。
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from typing import Any, Dict, List

RAW = "raw_tables"

# ── 必填字段契约 ─────────────────────────────────────────────
BID_PROJECT_TYPE_A_FIELDS = [
    "project_name", "project_number", "purchaser", "successful_bidder",
    "winning_amount", "budget_amount", "winning_date", "agent", "subject_matter",
]
BID_PROJECT_TYPE_B_FIELDS = [
    "successful_bidder", "project_name", "project_number", "purchaser",
    "winning_amount", "winning_date",
]
COMPANY_INFO_TYPE_A_FIELDS = [
    "company_name", "credit_code", "legal_person", "registered_capital",
    "establish_date", "business_status", "industry", "company_type", "company_level",
]
COMPANY_INFO_TYPE_B_FIELDS = [
    "company_name", "industry", "company_level", "business_scope", "province", "city",
]
COMPANY_PENALTY_FIELDS = [
    "company_name", "credit_code", "penalty_date", "law_enforcement_unit",
    "illegal_behavior", "penalty_result",
]

BID_PROJECT_TABLE = "bid_project"
COMPANY_INFO_TABLE = "company_info"
COMPANY_PENALTY_TABLE = "company_penalty"


# ── 工具函数 ─────────────────────────────────────────────────
def load(name: str) -> List[Dict[str, str]]:
    with open(f"{RAW}/{name}", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    for i, r in enumerate(rows):
        for k in list(r.keys()):
            r[k] = (r[k] or "").strip()
        r["_idx"] = str(i)
    return rows


def is_zero_amount(v: str) -> bool:
    try:
        return float(v) == 0.0
    except (TypeError, ValueError):
        return v in ("", "0", "0.0", "0.00")


def amount_display(v: str) -> str:
    return "金额未公开" if is_zero_amount(v) else f"{v}元"


def sql_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def sample_id(*parts: str) -> str:
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


def even_sample(items: List[Any], n: int) -> List[Any]:
    """在有序列表中确定性均匀采样 n 项（不重复，尽量覆盖全表）。"""
    if n >= len(items):
        return list(items)
    return [items[int(i * (len(items) - 1) / (n - 1))] for i in range(n)]


def has_all(row: Dict[str, str], fields: List[str]) -> bool:
    for f in fields:
        v = row.get(f, "")
        if not v or v == "-":
            return False
    return True


# 多主体分隔符（联合体、多公司合写时使用）
MULTI_ENTITY_SEPS = ["、", ",", "，", ";", "；"]


def is_single_company(v: str) -> bool:
    """是否为单主体公司名：以「公司」结尾，且不含多主体分隔符。"""
    return bool(v) and v.endswith("公司") and not any(s in v for s in MULTI_ENTITY_SEPS)


# ── 问题模板（仅五种固定格式，实体直接内嵌真实公司名） ─────────
BID_A_QUESTIONS = [
    "项目编号为{entity}的项目中标情况怎样？",
]

BID_B_QUESTIONS = [
    "{entity}的中标历史？",
]

COMPANY_A_QUESTIONS = [
    "查询{entity}的工商信息。",
]

COMPANY_B_QUESTIONS = [
    "查询{entity}的经营范围。",
]

PENALTY_QUESTIONS = [
    "查询{entity}的不良记录/处罚记录。",
]


def pick_template(templates: List[str], i: int) -> str:
    return templates[i % len(templates)]


# ── 答案渲染 ─────────────────────────────────────────────────
def render_bid_a(row: Dict[str, str]) -> str:
    return (
        f"项目「{row['project_name']}」（项目编号：{row['project_number']}）"
        f"由 {row['purchaser']} 采购，于 {row['winning_date']} 确定中标结果。\n\n"
        f"中标供应商：{row['successful_bidder']}\n"
        f"中标金额：{amount_display(row['winning_amount'])}\n"
        f"预算金额：{row['budget_amount']}元\n"
        f"代理机构：{row['agent']}\n"
        f"标的物：{row['subject_matter']}\n\n"
        f"（数据来源：ztb_clean.bid_project）"
    )


def _bid_b_line(rec: Dict[str, str]) -> str:
    return (
        f"项目编号 {rec['project_number']} | {rec['winning_date']} | "
        f"采购人 {rec['purchaser']} | 项目「{rec['project_name']}」 | "
        f"中标金额 {amount_display(rec['winning_amount'])}"
    )


def render_bid_b(company: str, records: List[Dict[str, str]]) -> str:
    if len(records) == 1:
        rec = records[0]
        return (
            f"根据系统收录的招投标数据，{company} 在 {rec['winning_date']} 中标了 "
            f"{rec['purchaser']} 的「{rec['project_name']}」（项目编号：{rec['project_number']}），"
            f"中标金额为 {amount_display(rec['winning_amount'])}。\n\n"
            f"（数据来源：ztb_clean.bid_project）"
        )
    shown = records[:5]
    more = len(records) - len(shown)
    lines = [f"① {_bid_b_line(shown[0])}"]
    for i, rec in enumerate(shown[1:], start=2):
        lines.append(f"{'①②③④⑤'[i - 1] if i <= 5 else i} {_bid_b_line(rec)}")
    return (
        f"根据系统收录的招投标数据，{company} 共中标 {len(records)} 个项目，"
        f"最近的中标记录如下（{'前5条' if more else '共' + str(len(records)) + '条'}）：\n\n"
        + "\n".join(lines) + "\n\n"
        f"（数据来源：ztb_clean.bid_project，共 {len(records)} 条记录）"
    )


def render_company_a(row: Dict[str, str]) -> str:
    return (
        f"经查询，{row['company_name']}（统一社会信用代码：{row['credit_code']}）"
        f"是一家成立于 {row['establish_date']} 的 {row['company_type']}企业，"
        f"法定代表人 {row['legal_person']}，注册资本 {row['registered_capital']}。\n\n"
        f"该公司所属行业为「{row['industry']}」，企业等级为 {row['company_level']}，"
        f"目前经营状态为 {row['business_status']}。\n\n"
        f"（数据来源：ztb_clean.company_info）"
    )


def render_company_b(row: Dict[str, str]) -> str:
    return (
        f"{row['company_name']} 所属行业为「{row['industry']}」，"
        f"企业等级为 {row['company_level']}，注册地为 {row['province']}{row['city']}。\n\n"
        f"其经营范围为：{row['business_scope']}。\n\n"
        f"（数据来源：ztb_clean.company_info）"
    )


def _penalty_line(rec: Dict[str, str]) -> str:
    return (
        f"统一社会信用代码 {rec['credit_code']} | 处罚日期 {rec['penalty_date']} | "
        f"执法单位 {rec['law_enforcement_unit']} | 违法事实 {rec['illegal_behavior']} | "
        f"处罚结果 {rec['penalty_result']}"
    )


def render_penalty(company: str, records: List[Dict[str, str]]) -> str:
    if len(records) == 1:
        rec = records[0]
        return (
            f"经查询，{company}（统一社会信用代码：{rec['credit_code']}）存在不良记录。\n\n"
            f"处罚日期：{rec['penalty_date']}\n"
            f"执法单位：{rec['law_enforcement_unit']}\n"
            f"违法事实：{rec['illegal_behavior']}\n"
            f"处罚结果：{rec['penalty_result']}\n\n"
            f"（数据来源：ztb_clean.company_penalty）"
        )
    lines = []
    for i, rec in enumerate(records, start=1):
        lines.append(f"{'①②③④⑤⑥'[i - 1] if i <= 6 else i} {_penalty_line(rec)}")
    return (
        f"经查询，{company} 存在 {len(records)} 条不良记录：\n\n"
        + "\n".join(lines) + "\n\n"
        "（数据来源：ztb_clean.company_penalty）"
    )


# ── 各测试集生成 ─────────────────────────────────────────────
def gen_bid_project(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    # 类型 A：按项目编号（唯一键）
    zero_idx = [i for i, r in enumerate(rows) if is_zero_amount(r["winning_amount"])]
    nonzero_idx = [i for i, r in enumerate(rows) if not is_zero_amount(r["winning_amount"])]
    zero_pick = even_sample(zero_idx, 8)
    nonzero_pick = even_sample(nonzero_idx, 250 - len(zero_pick))
    type_a_indices = sorted(set(zero_pick + nonzero_pick))

    # 类型 B：按中标供应商（单主体公司，剔除联合体）
    by_bidder: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for r in rows:
        by_bidder[r["successful_bidder"]].append(r)
    bidder_pick = even_sample(
        sorted(k for k in by_bidder if is_single_company(k)), 250
    )

    for idx, i in enumerate(type_a_indices):
        row = rows[i]
        out.append({
            "sample_id": sample_id("bid_project", "project", row["project_number"]),
            "question": pick_template(BID_A_QUESTIONS, idx).format(entity=row["project_number"]),
            "expected_sql": (
                f"SELECT {', '.join(BID_PROJECT_TYPE_A_FIELDS)} FROM {BID_PROJECT_TABLE} "
                f"WHERE project_number = {sql_quote(row['project_number'])}"
            ),
            "expected_fields": list(BID_PROJECT_TYPE_A_FIELDS),
            "ground_truth": [{f: row[f] for f in BID_PROJECT_TYPE_A_FIELDS}],
            "answer_text": render_bid_a(row),
            "difficulty": "简单",
            "source_file": "bid_project.csv",
        })

    for idx, bidder in enumerate(bidder_pick):
        recs = sorted(by_bidder[bidder], key=lambda r: r["winning_date"], reverse=True)
        shown = recs[:5]
        out.append({
            "sample_id": sample_id("bid_project", "bidder", bidder),
            "question": pick_template(BID_B_QUESTIONS, idx).format(entity=bidder),
            "expected_sql": (
                f"SELECT {', '.join(BID_PROJECT_TYPE_B_FIELDS)} FROM {BID_PROJECT_TABLE} "
                f"WHERE successful_bidder = {sql_quote(bidder)} ORDER BY winning_date DESC"
            ),
            "expected_fields": list(BID_PROJECT_TYPE_B_FIELDS),
            "ground_truth": [{f: r[f] for f in BID_PROJECT_TYPE_B_FIELDS} for r in shown],
            "answer_text": render_bid_b(bidder, recs),
            "difficulty": "简单" if len(recs) == 1 else "中等",
            "source_file": "bid_project.csv",
        })

    return out


def gen_company_info(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    type_a_rows = [r for r in rows if is_single_company(r["company_name"]) and has_all(r, COMPANY_INFO_TYPE_A_FIELDS)]
    type_b_rows = [r for r in rows if is_single_company(r["company_name"]) and has_all(r, COMPANY_INFO_TYPE_B_FIELDS)]
    type_a_pick = even_sample(type_a_rows, 250)
    type_b_pick = even_sample(type_b_rows, 250)

    for idx, row in enumerate(type_a_pick):
        out.append({
            "sample_id": sample_id("company_info", "detail", row["_idx"]),
            "question": pick_template(COMPANY_A_QUESTIONS, idx).format(entity=row["company_name"]),
            "expected_sql": (
                f"SELECT {', '.join(COMPANY_INFO_TYPE_A_FIELDS)} FROM {COMPANY_INFO_TABLE} "
                f"WHERE company_name = {sql_quote(row['company_name'])}"
            ),
            "expected_fields": list(COMPANY_INFO_TYPE_A_FIELDS),
            "ground_truth": [{f: row[f] for f in COMPANY_INFO_TYPE_A_FIELDS}],
            "answer_text": render_company_a(row),
            "difficulty": "简单",
            "source_file": "company_info.csv",
        })

    for idx, row in enumerate(type_b_pick):
        out.append({
            "sample_id": sample_id("company_info", "industry", row["_idx"]),
            "question": pick_template(COMPANY_B_QUESTIONS, idx).format(entity=row["company_name"]),
            "expected_sql": (
                f"SELECT {', '.join(COMPANY_INFO_TYPE_B_FIELDS)} FROM {COMPANY_INFO_TABLE} "
                f"WHERE company_name = {sql_quote(row['company_name'])}"
            ),
            "expected_fields": list(COMPANY_INFO_TYPE_B_FIELDS),
            "ground_truth": [{f: row[f] for f in COMPANY_INFO_TYPE_B_FIELDS}],
            "answer_text": render_company_b(row),
            "difficulty": "简单",
            "source_file": "company_info.csv",
        })

    return out


def gen_company_penalty(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    by_company: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for r in rows:
        by_company[r["company_name"]].append(r)
    company_pick = even_sample(
        sorted(k for k in by_company if is_single_company(k)), 500
    )

    out: List[Dict[str, Any]] = []
    for idx, company in enumerate(company_pick):
        recs = sorted(by_company[company], key=lambda r: r["penalty_date"], reverse=True)
        out.append({
            "sample_id": sample_id("company_penalty", "penalty", company),
            "question": pick_template(PENALTY_QUESTIONS, idx).format(entity=company),
            "expected_sql": (
                f"SELECT {', '.join(COMPANY_PENALTY_FIELDS)} FROM {COMPANY_PENALTY_TABLE} "
                f"WHERE company_name = {sql_quote(company)} ORDER BY penalty_date DESC"
            ),
            "expected_fields": list(COMPANY_PENALTY_FIELDS),
            "ground_truth": [{f: r[f] for f in COMPANY_PENALTY_FIELDS} for r in recs],
            "answer_text": render_penalty(company, recs),
            "difficulty": "简单" if len(recs) == 1 else "中等",
            "source_file": "company_penalty.csv",
        })

    return out


# ── 校验与输出 ───────────────────────────────────────────────
def display_value(field: str, value: str) -> str:
    if field == "winning_amount":
        return amount_display(value)
    return value


def validate(data: List[Dict[str, Any]], required_fields: List[str], label: str) -> List[str]:
    errors: List[str] = []
    for item in data:
        sid = item.get("sample_id", "?")
        gt = item.get("ground_truth")
        if not isinstance(gt, list) or not gt:
            errors.append(f"{label}: {sid} ground_truth 为空")
            continue

        # 结构校验：每个记录含全部必填字段且非空
        for rec in gt:
            for f in required_fields:
                v = rec.get(f, "")
                if not v or v == "-":
                    errors.append(f"{label}: {sid} 缺少/空字段 {f}")

        # 期望字段与契约一致
        if item.get("expected_fields") != list(required_fields):
            errors.append(f"{label}: {sid} expected_fields 不一致")

        # 文本校验：answer_text 需包含每个必填字段的展示值
        text = item.get("answer_text", "")
        for rec in gt:
            for f in required_fields:
                dv = display_value(f, rec.get(f, ""))
                if dv and dv not in text:
                    errors.append(f"{label}: {sid} answer_text 缺少 {f}={dv}")

        # 基础字段存在性
        for key in ("sample_id", "question", "expected_sql", "ground_truth",
                    "answer_text", "difficulty", "source_file"):
            if not item.get(key):
                errors.append(f"{label}: {sid} 缺少键 {key}")
    return errors


def _check_unique_ids(data: List[Dict[str, Any]], label: str) -> List[str]:
    seen = set()
    errors = []
    for item in data:
        sid = item["sample_id"]
        if sid in seen:
            errors.append(f"{label}: sample_id 重复 {sid}")
        seen.add(sid)
    return errors


def check_question_format(data: List[Dict[str, Any]], template: str,
                          entity_field: str, label: str) -> List[str]:
    """校验问题与指定固定格式完全一致。"""
    errors: List[str] = []
    for item in data:
        entity = item["ground_truth"][0][entity_field]
        expected = template.format(entity=entity)
        if item.get("question") != expected:
            errors.append(
                f"{label}: {item.get('sample_id', '?')} 问题格式不符 "
                f"{item.get('question')!r}，应为 {expected!r}"
            )
    return errors


def check_single_entity(data: List[Dict[str, Any]], entity_field: str,
                        label: str, company: bool = True) -> List[str]:
    """校验每个问题仅对应单个主体（公司名或项目编号）。"""
    errors: List[str] = []
    for item in data:
        sid = item.get("sample_id", "?")
        ent = item["ground_truth"][0][entity_field] if item.get("ground_truth") else ""
        if company:
            if not is_single_company(ent):
                errors.append(f"{label}: {sid} 非单主体公司 {ent!r}")
        else:
            if not ent:
                errors.append(f"{label}: {sid} 主体为空")
    return errors


def write_jsonl(path: str, data: List[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def main() -> None:
    bid_rows = load("bid_project.csv")
    info_rows = load("company_info.csv")
    penalty_rows = load("company_penalty.csv")

    bid_data = gen_bid_project(bid_rows)
    info_data = gen_company_info(info_rows)
    penalty_data = gen_company_penalty(penalty_rows)

    checks: List[str] = []
    checks += validate(bid_data[:250], BID_PROJECT_TYPE_A_FIELDS, "bid_project.项目")
    checks += validate(bid_data[250:], BID_PROJECT_TYPE_B_FIELDS, "bid_project.中标人")
    checks += validate(info_data[:250], COMPANY_INFO_TYPE_A_FIELDS, "company_info.工商")
    checks += validate(info_data[250:], COMPANY_INFO_TYPE_B_FIELDS, "company_info.经营范围")
    checks += validate(penalty_data, COMPANY_PENALTY_FIELDS, "company_penalty")

    # 问题格式合规校验（仅五种固定格式）
    checks += check_question_format(bid_data[:250], BID_A_QUESTIONS[0], "project_number", "bid_project.项目")
    checks += check_question_format(bid_data[250:], BID_B_QUESTIONS[0], "successful_bidder", "bid_project.中标人")
    checks += check_question_format(info_data[:250], COMPANY_A_QUESTIONS[0], "company_name", "company_info.工商")
    checks += check_question_format(info_data[250:], COMPANY_B_QUESTIONS[0], "company_name", "company_info.经营范围")
    checks += check_question_format(penalty_data, PENALTY_QUESTIONS[0], "company_name", "company_penalty")

    # 单主体校验（每个问题仅对应一个主体，杜绝联合体/多公司）
    checks += check_single_entity(bid_data[:250], "project_number", "bid_project.项目", company=False)
    checks += check_single_entity(bid_data[250:], "successful_bidder", "bid_project.中标人")
    checks += check_single_entity(info_data[:250], "company_name", "company_info.工商")
    checks += check_single_entity(info_data[250:], "company_name", "company_info.经营范围")
    checks += check_single_entity(penalty_data, "company_name", "company_penalty")

    specs = [
        ("bid_project", bid_data),
        ("company_info", info_data),
        ("company_penalty", penalty_data),
    ]
    for label, data in specs:
        if len(data) != 500:
            checks.append(f"{label}: 数量 {len(data)} != 500")
        checks += _check_unique_ids(data, label)

    write_jsonl("testset_bid_project.jsonl", bid_data)
    write_jsonl("testset_company_info.jsonl", info_data)
    write_jsonl("testset_company_penalty.jsonl", penalty_data)

    print("== 生成结果 ==")
    for label, data in specs:
        print(f"  {label}: {len(data)} 条")

    if checks:
        print(f"\n[FAIL] 校验发现 {len(checks)} 处问题：")
        for e in checks[:60]:
            print("  -", e)
        if len(checks) > 60:
            print(f"  ... 其余 {len(checks) - 60} 处省略")
        raise SystemExit(1)

    print("\n[PASS] 三份测试集全部通过校验：各 500 条，答案均包含对应全部必填字段。")


if __name__ == "__main__":
    main()