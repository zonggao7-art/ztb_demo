
"""模型只能提出建议，真正的执行权限由程序代码授予"""
from __future__ import annotations

from .context import RunStopped
from .contracts import ExecutionDecision
from datetime import date
from agent.tools.strict_sql import TOOL_PUBLIC_FIELDS


'''检验模型提出的任务，工具，参数，结果是不是符合要求的'''
'''
第一层 Pydantic结构约束
第二层 能力与工具授权
第三层 字段约束
第四层 用户输入与原文绑定
第五层 任务依赖限制
第六层 实际调用参数完全匹配
第七层 工具返回结果回验
'''


'''
八、一个完整例子
用户输入：
模型提出：
validate_decision 会检查：
company_penalty 是否存在
是否允许 query_company_penalty
是否只有允许的 company_name 字段
company_name 是否来自用户原文
是否提供了公司名称
之后 task_arguments 生成：
模型实际调用时，即使发送：
也会因为多了未批准的 province 而触发：
即使工具返回了一条不属于该企业的记录，validate_result_scope 仍会拒绝它。
九、一句话总结
policy.py 是执行层的程序化授权闸门：

模型可以提出“想做什么”，但只有经过能力白名单、字段白名单、输入绑定、参数一致性和结果范围验证后，系统才允许真正执行和使用结果。
'''


'''定义每种业务能力可以使用什么工具'''
CAPABILITIES = {
    # 完整 RAG 工具负责检索和作答；外层不再重复生成法规答案。
    "public_kb_qa": {"knowledge_qa"},
    "company_registration": {"query_company_registration"},
    "company_business_scope": {"query_company_business_scope"},
    "company_penalty": {"query_company_penalty"},
    "project_award": {"query_project_award"},
    "company_award_history": {"query_company_award_history"},
    "general_chat": set(),
}
# Deliberately retain P0-11: bid_project has only three authorized filters.
# A tool's broader legacy schema is NOT permission to use every parameter.

'''每种业务能力，每条检索路线允许使用什么字段进行检索'''
FIELDS = {
    "public_kb_qa": {"question"},
    # 工商检索只按公司全称。省市、行业等可以返回，但不能用作筛选条件。
    "company_registration": {"company_name"},
    "company_business_scope": {"company_name"},
    "company_penalty": {"company_name"},
    "project_award": {"project_number"},
    "company_award_history": {"company_name"},
    "general_chat": set(),
}

'''校验模型提出的执行方案是否符合要求'''
def validate_decision(ctx, proposal, tools):
    decision = ExecutionDecision.model_validate(proposal)
    enabled = {t.name for t in tools}
    if decision.execution_mode in {"clarify", "unsupported"}:
        if decision.tasks or decision.suggested_tools:
            raise RunStopped("non_executable_decision")
        return decision
    if decision.missing_fields:
        raise RunStopped("unresolved_requirements")
    allowed = set()
    tasks = {t.task_id: t for t in decision.tasks}
    for task in decision.tasks:
        allowed |= CAPABILITIES[task.capability]
        if task.capability != "general_chat" and not CAPABILITIES[task.capability] & enabled:
            raise RunStopped("capability_unavailable")
        fields = {r.field for r in task.input_refs}
        if not fields <= FIELDS[task.capability]:
            raise RunStopped("unsupported_filter")
        if task.capability in {
            "company_registration", "company_business_scope",
            "company_penalty", "company_award_history",
        } and "company_name" not in fields:
            raise RunStopped("missing_entity")
        if task.capability == "project_award" and "project_number" not in fields:
            raise RunStopped("missing_entity")
        if task.capability == "public_kb_qa" and "question" not in fields:
            raise RunStopped("missing_question")
        for ref in task.input_refs:
            if ref.source_id.startswith("u") and ref.source_id != "u0":
                raise RunStopped("history_not_supported")
            if ref.source_id == "u0":
                if not ref.value or ref.value not in ctx.question:
                    raise RunStopped("unbound_input")
                if ref.field in {"time_start", "time_end"}:
                    if len(ref.value) != 10:
                        raise RunStopped("invalid_date")
                    date.fromisoformat(ref.value)
            else:
                parent, _, field = ref.source_id.partition(".")
                if (parent not in task.depends_on or tasks[parent].capability != "project_award"
                        or field != "successful_bidder" or ref.field != "company_name" or ref.value):
                    raise RunStopped("invalid_dependency")
    if not set(decision.suggested_tools) <= allowed & enabled:
        raise RunStopped("tool_not_allowed")
    if any(t.capability == "general_chat" for t in decision.tasks) and len(decision.tasks) != 1:
        raise RunStopped("general_chat_scope_mismatch")
    if decision.execution_mode == "static":
        branches = {"knowledge_qa": {"public_kb_qa"}, "price_inquiry":
                    {"company_registration", "company_business_scope", "company_penalty",
                     "project_award", "company_award_history"},
                    "general_chat": {"general_chat"}}
        if any(t.depends_on or t.capability not in branches[decision.static_branch] for t in decision.tasks):
            raise RunStopped("static_scope_mismatch")
    return decision


def task_arguments(ctx, task):
    args = {"task_id": task.task_id}
    for ref in task.input_refs:
        if ref.source_id.startswith("u") and ref.source_id != "u0":
            raise RunStopped("history_not_supported")
        if ref.source_id == "u0":
            value = ref.value
        else:
            parent, _, field = ref.source_id.partition(".")
            # Only successfully retrieved, model-visible records can supply entities.
            rows = ctx.ledger.for_task(parent, "record")
            values = {str(e["payload"].get(field, "")).strip() for e in rows}
            values.discard("")
            if len(values) != 1:
                raise RunStopped("ambiguous_dependency")
            value = values.pop()
        args[ref.field] = [value] if ref.field == "keywords" else value
    return args



'''检验模型的实际工具调用，第二道防线，第一道是检验模型提出的计划是否合法'''
def validate_call(ctx, name, args, tools):
    task_id = args.get("task_id")
    task = next((t for t in ctx.decision.tasks if t.task_id == task_id), None)
    '''防止模型调错工具'''
    if task is None or name not in CAPABILITIES[task.capability] or name not in tools:
        raise RunStopped("tool_not_allowed")
    expected = task_arguments(ctx, task)
    # 名称是查询值，不在执行层审核主体真实性或名称后缀。
    # 非空、长度等技术约束由工具入参及 SQL 层负责。
    from agent.nodes.price_inquiry.intent import _looks_like_code
    if "project_number" in expected and not _looks_like_code(expected["project_number"]):
        raise RunStopped("invalid_project_number")
    clean = {k: v for k, v in args.items() if v is not None}
    # top_k is a bounded retrieval limit, never a claim about total population.
    top_k = clean.pop("top_k", None)
    if top_k is not None and type(top_k) is not int:
        raise RunStopped("invalid_top_k")
    if clean != expected:
        raise RunStopped("scope_mismatch")
    parsed = tools[name].args_schema.model_validate({**expected, **({"top_k": top_k} if top_k is not None else {})})
    return task, parsed.model_dump(exclude_none=True)




'''校验工具返回的数据是否符合要求'''
def validate_result_scope(name, args, result):
    """Validate returned fields too; a tool response cannot change the approved subject."""
    if not result.get("ok") or name not in TOOL_PUBLIC_FIELDS:
        return
    if not result.get("metadata", {}).get("exact_scope"):
        raise RunStopped("unverified_query_scope")
    if not isinstance(result.get("data", {}).get("records"), list):
        raise RunStopped("invalid_tool_protocol")
    for row in result["data"]["records"]:
        if not isinstance(row, dict) or not set(row) <= set(TOOL_PUBLIC_FIELDS[name]):
            raise RunStopped("unauthorized_result_field")
        for field, value in args.items():
            if field in {"task_id", "top_k"}:
                continue
            column = (
                "successful_bidder"
                if field == "company_name" and name == "query_company_award_history"
                else field
            )
            if field in {"time_start", "time_end"}:
                date_value = str(row.get("establish_date") or "")[:10]
                if not date_value or (date_value < value if field == "time_start" else date_value > value):
                    raise RunStopped("result_scope_mismatch")
            elif str(row.get(column) or "").strip() != str(value).strip():
                raise RunStopped("result_scope_mismatch")
