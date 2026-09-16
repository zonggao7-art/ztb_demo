"""执行层使用的严格数据契约。

本模块只负责定义和校验结构化数据，不负责调用模型、执行工具或生成最终答案。
模型可以提出执行建议，但只有这里的 Pydantic 校验和后续程序逻辑才能授予执行资格。
契约按数据流分为四组：输入引用、执行决策、任务结果、可拼装的答案块。
"""
from __future__ import annotations

from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_core import PydanticCustomError

# 执行层支持的业务能力。它们是比具体工具更高层的业务分类。
Capability = Literal[
    "public_kb_qa",
    "company_registration",
    "company_business_scope",
    "company_penalty",
    "project_award",
    "company_award_history",
    "general_chat",
]
# 执行层允许模型建议的工具名称；建议不等于实际调用授权。
ToolName = Literal[
    "knowledge_qa",
    "query_company_registration",
    "query_company_business_scope",
    "query_company_penalty",
    "query_project_award",
    "query_company_award_history",
]
# 程序向用户追问时使用的标准化缺失字段名称。
MissingField = Literal["company_name", "project_number", "time_range", "amount_unit",
                       "task_scope", "entity", "evidence", "service"]


class StrictModel(BaseModel):
    """所有契约模型的共同基类，拒绝未知字段并清理字符串首尾空白。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class InputRef(StrictModel):
    """描述任务输入的来源，以及传给任务的精确值或依赖引用。"""

    # 目标任务中的输入参数名，例如 company_name 或 project_number。
    field: str = Field(min_length=1, max_length=40)
    # u0 表示当前用户；t1.xxx 表示本轮前置任务字段；不支持历史消息引用。
    source_id: str = Field(min_length=1, max_length=60,
                            description="u0=current user only; t1.successful_bidder=prior tool field in this request; no conversation history")
    # 直接来自用户时保存原文片段；依赖其他任务时为空，由执行器解析引用。
    value: str = Field(max_length=4000, description="Exact user substring; empty for a dependency reference")


class TaskSpec(StrictModel):
    """描述一个待执行任务，以及它的输入和前置任务。"""

    # 限制任务数量和格式，便于依赖图保持小而可控。
    task_id: str = Field(pattern=r"^t[1-6]$")
    # 任务所属能力决定可使用的业务执行路径。
    capability: Capability
    # 将用户输入或前置任务结果绑定到能力所需的字段。
    input_refs: list[InputRef] = Field(default_factory=list, max_length=12,
                                     description="一个任务对应一个主体和一种能力；同一字段只能绑定一次。多家公司拆成不同task_id。general_chat使用空列表。")
    # 任务必须按列表顺序声明；前置任务只能引用已经出现的 task_id。
    depends_on: list[str] = Field(default_factory=list, max_length=5)


class ExecutionDecision(StrictModel):
    """模型对当前请求的执行模式建议，供程序验证后路由。"""

    # static 走既有单分支流程，react 执行任务图，clarify 追问，unsupported 拒绝执行。
    execution_mode: Literal["static", "react", "clarify", "unsupported"]
    # 仅 static 模式使用的既有分支；react、clarify、unsupported 必须为空。
    static_branch: Literal["knowledge_qa", "price_inquiry", "general_chat"] | None = None
    # react 或 static 的具体任务列表；clarify/unsupported 通常为空。
    tasks: list[TaskSpec] = Field(default_factory=list, max_length=6,
                                 description="static/react必须有1~6个任务，问候也要填写一个general_chat任务；clarify/unsupported必须为空。")
    # 模型认为可能需要的工具，仅作为建议，不能绕过程序授权。
    suggested_tools: list[ToolName] = Field(default_factory=list, max_length=6)
    # 记录选择该模式的机器可读原因，便于审计和测试。
    reason_code: Literal["EXISTING_BRANCH_COVERS", "MULTI_TARGET", "CROSS_CAPABILITY",
                         "RESULT_DEPENDENT", "MISSING_INPUT", "OUT_OF_SCOPE"]
    # clarify 模式必须明确列出需要用户补充的字段。
    missing_fields: list[MissingField] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def check_shape(self):
        """校验模式、任务、静态分支和任务依赖之间的结构一致性。"""

        # 可执行模式必须有任务；否则决策无法落地。
        if self.execution_mode in {"static", "react"} and not self.tasks:
            raise PydanticCustomError("tasks_required", "static/react必须包含至少一个任务，问候也不例外")
        # static 分支只能和 static 模式成对出现，避免路由字段含义冲突。
        if (self.execution_mode == "static") != (self.static_branch is not None):
            raise PydanticCustomError("static_branch_mismatch", "仅static填写static_branch，其他模式必须为null")
        # clarify 没有缺失字段就无法形成有效追问。
        if self.execution_mode == "clarify" and not self.missing_fields:
            raise PydanticCustomError("missing_fields_required", "clarify必须列出missing_fields")
        seen = set()
        for task in self.tasks:
            # 任务 ID 不能重复，且依赖必须指向当前任务之前已声明的任务。
            if task.task_id in seen or not set(task.depends_on) <= seen:
                raise PydanticCustomError("invalid_task_dependencies", "任务编号不能重复，依赖只能引用前面的任务")
            # 同一任务中一个目标字段只能绑定一次，避免输入来源歧义。
            if len({r.field for r in task.input_refs}) != len(task.input_refs):
                raise PydanticCustomError("duplicate_binding", "同一任务不能重复绑定同一字段；多个主体应拆成多个任务")
            seen.add(task.task_id)
        return self


class RecordFact(StrictModel):
    """答案中的结构化记录事实，只引用工具返回的记录字段。"""

    kind: Literal["record_fact"]
    record_ref: str = Field(max_length=80)
    fields: list[str] = Field(min_length=1, max_length=12)


class LegalExcerpt(StrictModel):
    """答案中的法规证据引用，可复用已展示的原文片段。"""

    kind: Literal["legal_excerpt"]
    evidence_id: str = Field(max_length=80)
    quote: str = Field(default="", max_length=6000,
                       description="留空：由程序引用该evidence_id已展示的原文片段。不要手工转抄；非空时必须逐字匹配。")


class RagAnswer(StrictModel):
    """复用本任务的完整 RAG 答案。正文由程序取回，外层模型不转抄。"""

    kind: Literal["rag_answer"]


class Comparison(StrictModel):
    """答案中的多条记录对比，要求至少包含两条记录。"""

    kind: Literal["comparison"]
    record_refs: list[str] = Field(min_length=2, max_length=6)
    field: str = Field(max_length=40)


class Limitation(StrictModel):
    """答案中的受限说明，例如无匹配、证据不足或工具失败。"""

    kind: Literal["limitation"]
    reason: Literal["no_match", "insufficient_evidence", "missing_input",
                    "tool_failed", "unsupported", "limited_sample"]


# 带 kind 判别字段的答案块联合类型，保证每个块只能匹配一种结构。
Block = Annotated[RecordFact | LegalExcerpt | RagAnswer | Comparison | Limitation, Field(discriminator="kind")]


class TaskResult(StrictModel):
    """单个任务的结构化结果，由一个或多个答案块组成。"""

    task_id: str = Field(pattern=r"^t[1-6]$")
    blocks: list[Block] = Field(min_length=1, max_length=20)


class AnswerCandidate(StrictModel):
    """最终答案候选：汇总任务结果，并保留仍需补充的输入要求。"""

    task_results: list[TaskResult] = Field(min_length=1, max_length=6)
    missing_requirements: list[MissingField] = Field(default_factory=list, max_length=8)


class FinishAction(StrictModel):
    """统一主 Agent 的结束动作。

    模型只决定是否结束以及结束原因；最终正文仍由程序根据已经核验的工具结果生成。
    """

    status: Literal["complete", "clarify", "unsupported", "partial"]
    missing_fields: list[MissingField] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def check_finish_shape(self):
        if self.status == "clarify" and not self.missing_fields:
            raise PydanticCustomError("missing_fields_required", "clarify必须列出missing_fields")
        if self.status in {"complete", "unsupported"} and self.missing_fields:
            raise PydanticCustomError("unexpected_missing_fields", "complete/unsupported不能包含missing_fields")
        return self
