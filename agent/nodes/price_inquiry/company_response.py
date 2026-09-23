"""Render each company's data source independently, including query failures."""
from langchain_core.messages import AIMessage
from ..answer_templates import render_answer


def company_response(intent, result):
    company = intent.hard_filters.company_name or ""
    statuses = dict(result.get("table_status", {}))
    groups = {"company_info": [], "company_penalty": []}
    for row in result.get("records", []):
        source = str(row.get("_source_table", "")).rsplit(".", 1)[-1]
        if source not in groups:
            raise ValueError("company_record_source_required")
        if company and row.get("company_name") != company:
            raise ValueError("company_record_scope_mismatch")
        groups[source].append(row)
    needs_penalty = intent.need_penalty_check or intent.query_type == "penalty_check"
    selected = ["company_info"] + (["company_penalty"] if needs_penalty else [])
    sections, partial = [], False
    for table in selected:
        rows = groups[table]
        status = statuses.get(table, "success" if rows else "failed")
        if status not in {"success", "empty"}:
            partial = True
            label = "工商信息" if table == "company_info" else "处罚记录"
            sections.append(f"{label}：查询未成功完成，暂时无法核验，请稍后重试。")
        elif rows:
            query_type = ("penalty_check" if table == "company_penalty" else
                          "company_industry" if intent.query_type == "company_industry" else "company_detail")
            sections.append(render_answer(query_type, rows, entity=company))
        elif table == "company_penalty":
            sections.append(
                f"处罚记录：本次在已收录数据中按企业名称“{company}”未检索到处罚记录。"
                "这不代表现实中不存在处罚，也不代表企业无风险。\n"
                "（数据来源：ztb_clean.company_penalty）"
            )
        else:
            sections.append(render_answer("company_detail", [], entity=company))
        statuses[table] = status
    records = [row for table in selected for row in groups[table]]
    answer = "\n\n".join(sections)
    return {
        "business_result": {
            "branch": "price_inquiry", "sub_route": "company_query",
            "query_type": intent.query_type, "answer": answer,
            "execution_status": "partial" if partial else "complete",
            "data": {"records": records, "table_status": statuses,
                     "tables": [f"ztb_clean.{t}" for t in selected],
                     "total_found": len(records),
                     "meta": {"sql_count": result.get("sql_count", 0),
                              "total_sql_time": result.get("total_sql_time", 0)}},
        },
        "messages": [AIMessage(content=answer)],
    }
