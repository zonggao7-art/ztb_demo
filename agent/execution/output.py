"""最终答案的发布关口——整条流水线的最后一关。

它把 AI 模型提交的结构化答案方案（AnswerCandidate）逐块验收后，渲染成用户看到的最终回复。

在整条回答流程中的位置：
    1. 路由层判断用户想干什么，把请求拆成 1~6 个小任务（查法规、查企业、查处罚、
       查中标、关键词兜底搜索、闲聊引导）；
    2. 执行层逐个调用工具，工具返回的原始结果统一存进"证据暂存区"（台账，ledger）；
    3. AI 模型看着这些证据，提交一份"答案方案"——方案里不允许模型自己写内容，
       只能声明"这个任务展示哪份材料、用什么形式"（发布知识库完整答案 / 列出某条
       记录的某些字段 / 摘录某段法规原文 / 声明这次办不成）；
    4. 本模块接手：逐块验收这份方案，把最终展示给用户的文本一字一字拼出来；
    5. 任何一步验收不过，整个请求作废，用户只会收到固定的兜底话术（见 safe_failure）。

核心原则一句话概括：最终答案里没有一个字是模型自由发挥的——正文要么是程序取回的
RAG 完整答案（知识库问答），要么是台账里经校验的数据库字段，要么是逐字核验过的
原文摘录，要么是固定话术；模型只负责"选用哪些块、怎么组织"，不负责"写内容"。

本模块存在的意义：AI 模型只有"选用哪些材料"的权力，没有"编写内容"的权力，
因此不存在自由发挥、编造引用的空间。宁可不给答案，也不给没通过核验的答案。
"""

from __future__ import annotations
import html
import re
from decimal import Decimal, InvalidOperation

from public_kb.citations import Citation, CitationValidator
from public_kb.config import CitationRuleConfig
from agent.tools.strict_sql import PUBLIC_FIELDS
from agent.nodes.general_chat import GENERAL_GUIDANCE
from .contracts import AnswerCandidate
from .context import RunStopped
from .rag_result import validate_rag_result

# 用来在知识库答案正文里找"【来源2】"这类引用标记的正则。
RAG_MARKER = re.compile(r"【\s*来源\s*(\d+)\s*】")

# 允许出现在最终答案里的数据库字段白名单（由 strict_sql 中各表的公开字段汇总而来）。
# 模型想展示某条数据库记录时，只能从这个白名单里挑字段，白名单之外的内容一律不许出现。
FIELDS = set().union(*map(set, PUBLIC_FIELDS.values()))

# 各种"这次办不成"情况下的固定说明文案。宁可向用户讲清楚局限，也不编一个听起来圆满的答案。
LIMITATIONS = {
    "no_match": "系统暂未收录该名称或编号对应的匹配记录；不代表现实中不存在，也不代表企业无风险。",
    "insufficient_evidence": "证据不足，无法完成该项核验。",
    "missing_input": "必要条件不明确，请补充后再核验。",
    "tool_failed": "数据服务未成功返回，无法判断是否存在记录。",
    "action_rejected": "该次工具申请未通过程序校验，没有执行数据查询。",
    "unsupported": "当前能力范围不支持这项核验。",
    "limited_sample": "仅展示本次检索样本，不代表完整总体，不据此作总量或资格结论。",
}

# 每种任务在最终答案里的中文小标题。
LABELS = {
    "public_kb_qa": "法规证据",
    "company_registration": "企业工商信息",
    "company_business_scope": "企业经营范围",
    "company_penalty": "处罚记录",
    "project_award": "项目中标情况",
    "company_award_history": "企业中标历史",
    "general_chat": "功能引导",
}

# 数据库字段对应的中文显示名。
FIELD_LABELS = {"company_name": "企业名称", "credit_code": "统一社会信用代码", "legal_person": "法定代表人",
                "registered_capital": "注册资本原始记录", "establish_date": "成立日期", "business_status": "经营状态",
                "industry": "行业", "province": "省份", "city": "城市", "business_scope": "经营范围",
                "penalty_date": "处罚日期", "illegal_behavior": "违法行为记录", "penalty_result": "处罚结果",
                "law_enforcement_unit": "执法单位", "project_number": "项目编号", "project_name": "项目名称",
                "purchaser": "采购人", "successful_bidder": "中标供应商", "winning_amount": "中标金额原始值",
                "winning_date": "中标日期"}

# 用户只是打招呼或询问"你能做什么"时的固定回复。
GENERAL = GENERAL_GUIDANCE


def safe(value):
    # 数据库记录和检索原文只是"原材料"，不能让原材料里的特殊符号破坏答案排版，
    # 更不能冒充引用。这里做三件事：HTML 转义防止注入；把【来源换成全角［来源，
    # 防止原材料里藏一个假的"【来源5】"冒充真引用；转义 Markdown 符号防止原文改变格式。
    text = html.escape(str(value), quote=False).replace("【来源", "［来源")
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~])", r"\\\1", text)


def conservative_candidate(ctx):
    """不经过 AI 模型的保底答案方案：直接用台账里已有的证据拼一份。

    当模型提交的答案方案没通过校验、没法用时，就用这个函数自己拼一个最保守的
    方案，保证用户仍然能得到有依据的回答，而不是整个请求失败。

    拼装规则（每个任务独立处理）：
    - 有已验证的知识库完整答案 → 用"完整答案"块；
    - 有数据库记录 → 挑白名单字段列出来；
    - 有法规原文 → 摘录模型实际看到过的片段；
    - 什么证据都没有 → 根据工具回执判断原因，写一条对应的固定说明。
    """
    results = []
    for task in ctx.decision.tasks:
        blocks = []
        if task.capability == "public_kb_qa" and task.task_id in ctx.ledger.rag_answers:
            results.append({"task_id": task.task_id, "blocks": [{"kind": "rag_answer"}]})
            continue
        # 每个任务最多取 8 条证据，防止答案过长。
        for entry in ctx.ledger.for_task(task.task_id)[:8]:
            if entry["kind"] == "record":
                fields = [k for k in entry["payload"] if k in FIELDS][:12]
                if fields:
                    blocks.append({"kind": "record_fact", "record_ref": entry["id"], "fields": fields})
            elif entry["payload"].get("text"):
                # 只摘录模型实际看到过的片段（有长度上限）；完整原文仍留在台账里供引用核对。
                blocks.append({"kind": "legal_excerpt", "evidence_id": entry["id"],
                               "quote": entry.get("visible_text", entry["payload"]["text"])[:1500]})
        receipts = [r for r in ctx.receipts if r["task_id"] == task.task_id]
        if not blocks:
            # 什么证据都没有时才允许说"办不成"，而且原因要有工具回执支撑：
            # 精确条件查过且确认为空 → 未检索到；跑过工具但没拿到证据 → 工具失败；
            # 连证据都没有 → 证据不足。
            exact_empty = any(r.get("ok") and r.get("empty") and r.get("exact_scope") for r in receipts)
            reason = "no_match" if exact_empty else "tool_failed" if receipts else "insufficient_evidence"
            blocks.append({"kind": "limitation", "reason": reason})
        results.append({"task_id": task.task_id, "blocks": blocks})
    return AnswerCandidate.model_validate({"task_results": results})


def render(ctx, candidate):
    """核心发布流程：逐块验收答案方案，拼出最终回复文本。

    每种块都有各自的验收规则，原则只有一条：出现在答案里的每个字，都必须能追溯
    到台账里程序核实过的材料。任何一块验收不过就中止请求，转入失败兜底。
    """
    # 先把方案重新过一遍严格校验，格式不对直接拒绝。
    candidate = AnswerCandidate.model_validate(candidate)
    tasks = {t.task_id: t for t in ctx.decision.tasks}
    ids = [t.task_id for t in candidate.task_results]
    # 方案必须覆盖本次请求拆出的每一个任务，不能漏、不能重复。
    if len(set(ids)) != len(ids) or set(ids) != set(tasks):
        raise RunStopped("incomplete_task_coverage")
    lines, record_rows, citations, citation_ids = [], [], [], {}
    partial = bool(candidate.missing_requirements or ctx.failure_code)
    has_rag_answer = False
    for result in candidate.task_results:
        task = tasks[result.task_id]
        # 台账里存有知识库完整答案的任务，只允许用"完整答案"块原样发布，不许改写。
        if task.task_id in ctx.ledger.rag_answers and [b.kind for b in result.blocks] != ["rag_answer"]:
            raise RunStopped("rag_answer_required")
        scope = next((r.value for r in task.input_refs if r.field in {"company_name", "project_number"} and r.value), "")
        # 每个任务一个小标题，例如"t1 · 中标记录 · 某公司"。
        lines.append(f"{result.task_id} · {LABELS[task.capability]}" + (f" · {safe(scope)}" if scope else ""))
        for block in result.blocks:
            if block.kind == "rag_answer":
                # ── 知识库完整答案：只能是法规问答任务，且台账里确实存有该答案。
                if task.capability != "public_kb_qa" or task.task_id not in ctx.ledger.rag_answers:
                    raise RunStopped("unknown_rag_answer")
                # 发布前再校验一次（双保险），并取回校验过的正文和引用列表。
                text, sources, is_refusal = validate_rag_result(ctx.ledger.rag_answers[task.task_id])
                # 多个任务的引用要接在同一个编号序列后面，所以本任务的编号统一加偏移。
                offset = len(citations)
                citations.extend(c.model_copy(update={"context_index": c.context_index + offset}) for c in sources)
                # 先转义普通正文，再恢复程序确认过的引用标记（编号加偏移）。只替换一遍，
                # 避免编号变了之后又被当成原文、触发下一轮替换的连锁问题。
                parts, end = [], 0
                for match in RAG_MARKER.finditer(text):
                    parts.extend([safe(text[end:match.start()]), f"【来源{int(match[1]) + offset}】"])
                    end = match.end()
                parts.append(safe(text[end:]))
                lines.append("".join(parts))
                partial |= is_refusal
                has_rag_answer = True
            elif block.kind == "record_fact":
                # ── 数据库记录：从台账取数。取数时会校验这条记录确实属于本任务，
                # 防止拿别的任务的记录来充数。
                entry = ctx.ledger.get(block.record_ref, result.task_id, "record")
                row = entry["payload"]
                # 想展示的字段必须在白名单内、确实存在于这条记录里，且不能重复。
                if not set(block.fields) <= FIELDS & set(row) or len(set(block.fields)) != len(block.fields):
                    raise RunStopped("invalid_fact_field")
                selected = {k: row[k] for k in block.fields}
                record_rows.append({"task_id": result.task_id, "record_ref": block.record_ref, **selected})
                lines.append("；".join(f"{FIELD_LABELS[k]}：{safe(v) if v is not None and str(v).strip() else '未收录该字段'}"
                                      for k, v in selected.items()))
                if "winning_amount" in selected:
                    # 金额只展示原始记录值；单位未经程序核定时，明确声明不作换算或比较。
                    lines.append("金额单位：" + (safe(entry["metadata"].get("amount_unit"))
                                  if entry["metadata"].get("amount_unit") else "尚未核定，不作换算或比较。"))
            elif block.kind == "legal_excerpt":
                # ── 法规原文摘录：引用必须逐字出现在两处——(1) 模型实际看到过的片段，
                # (2) 完整原文。两头都对得上才允许引用，防止模型转抄时改字、断章取义。
                entry = ctx.ledger.get(block.evidence_id, result.task_id, "legal")
                source = entry["payload"]
                visible_text = entry.get("visible_text", source.get("text", ""))
                quote = visible_text if block.quote == "" else block.quote
                if not quote.strip() or quote not in visible_text or quote not in source.get("text", ""):
                    raise RunStopped("quote_not_in_source")
                # 同一条证据只分配一个引用编号，重复引用时复用编号。
                if block.evidence_id not in citation_ids:
                    idx = len(citations) + 1
                    # 引用的元数据（文档名、章节号等）缺什么就少填什么，绝不编造标识。
                    c = Citation.model_validate({**{k: v for k, v in source.items()
                        if k in Citation.model_fields}, "context_index": idx})
                    citations.append(c)
                    citation_ids[block.evidence_id] = idx
                idx = citation_ids[block.evidence_id]
                lines.append(f"检索原文摘录（适用性仍需核验）：{safe(quote)}【来源{idx}】")
            elif block.kind == "comparison":
                # ── 金额对比：仅支持对比中标金额，且系统配置了统一金额单位才允许。
                # 每条记录的单位都必须与配置一致，金额必须能转成有效数字。
                entries = [ctx.ledger.get(eid, result.task_id, "record") for eid in block.record_refs]
                if block.field != "winning_amount" or not ctx.settings.agent_amount_unit:
                    raise RunStopped("comparison_not_supported")
                values = []
                for entry in entries:
                    if entry["metadata"].get("amount_unit") != ctx.settings.agent_amount_unit:
                        raise RunStopped("unknown_amount_unit")
                    try:
                        value = Decimal(str(entry["payload"][block.field]))
                        if not value.is_finite():
                            raise ValueError()
                        values.append(value)
                    except (KeyError, ValueError, InvalidOperation) as exc:
                        raise RunStopped("invalid_comparison") from exc
                lines.append("本次样本金额：" + "、".join(str(v) for v in values) + ctx.settings.agent_amount_unit)
            else:
                # ── "办不成"说明：文案是写死的。声称"未检索到记录"必须有工具回执证明
                # （工具成功跑完 + 确实是精确条件 + 结果确实为空），防止模型没查就
                # 下"查无记录"的结论。
                if block.reason == "no_match" and not any(
                        r["task_id"] == result.task_id and r.get("ok") and r.get("empty")
                        and r.get("exact_scope") for r in ctx.receipts):
                    raise RunStopped("unverified_empty")
                # 除"未检索到"和"仅展示样本"两种中性说明外，其余都算"部分完成"。
                partial |= block.reason not in {"no_match", "limited_sample"}
                lines.append(LIMITATIONS[block.reason])
        lines.append("")
    # 补漏：模型在本次请求里看到过的每条法规来源，无论有没有被选用，都必须出现在
    # 引用列表里（对应引用规则 R5/R7），保证来源可追溯、可回查。
    for eid, entry in ctx.ledger.entries.items():
        if entry["kind"] == "legal" and eid not in citation_ids and not entry["metadata"].get("rag_answer"):
            source = entry["payload"]
            idx = len(citations) + 1
            citations.append(Citation.model_validate({**{k: v for k, v in source.items()
                if k in Citation.model_fields}, "context_index": idx}))
            citation_ids[eid] = idx
            lines.append(f"其他检索来源（未据此作适用性结论）：{safe(source.get('doc_name'))}【来源{idx}】")
    if not has_rag_answer or record_rows:
        lines.append("以上仅为已收录数据与法规原文的核验线索；不自动认定违法、无风险或具备投标资格。")
    if record_rows:
        lines.append(LIMITATIONS["limited_sample"])
    answer = "\n".join(lines)
    # 发布前最后一道整体校验：没有知识库完整答案时（纯数据库场景），要求引用列表里
    # 每个编号都在正文中真的出现过；一次引用都没有则按拒答场景校验。
    report = CitationValidator(CitationRuleConfig(enforce_all_context_cited=not has_rag_answer)).validate(
        citations, answer, [c.chunk_id for c in citations], is_refusal=not citations)
    if not report.all_passed:
        raise RunStopped("citation_invalid")
    # partial = 答案里带保留意见（有任务查不到、证据不足或含拒答），上层可据此调整展示。
    status = "partial" if partial else "complete"
    execution = ctx.summary()
    return {"branch": ctx.fallback_branch or ctx.decision.static_branch or "react", "answer": answer,
            "data": {"records": record_rows, "citations": [c.to_dict() for c in citations],
                     "citation_display": "compact" if has_rag_answer else "full",
                     "citation_validation": report.to_dict(), "status": status,
                     "task_results": candidate.model_dump()["task_results"], "execution": execution},
            "execution_status": status, "execution": execution}


def safe_failure(ctx, code="verification_failed"):
    """失败出口：任何一步核验没通过，请求都会落到这里。

    用户只会看到一句固定的兜底话术，不附带任何业务结论——宁可这次不给答案，
    也不给一个没通过核验的答案。
    """
    ctx.failure_code = code
    ctx.stage("request_failed", code=code)
    return {"branch": "fallback", "answer": "本次请求未通过完整核验，暂不提供业务结论。请补充明确条件或稍后重试。",
            "data": {}, "execution_status": "insufficient_evidence",
            "execution": {**ctx.summary(), "failure_code": code}}
