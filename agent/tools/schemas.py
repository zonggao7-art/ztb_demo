"""工具入参 schema — pydantic v2 模型，同时生成 LLM 可见的 tool schema。

字段 docstring / description 会直接成为 LLM 的函数参数说明，
编写时以「让调用方 Agent 一次填对参数」为标准。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# SQL 工具可访问的表白名单（蓝图 §5.4 安全边界）
ALLOWED_TABLES = ("company_info", "company_penalty", "bid_project")


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    task_id: str | None = Field(default=None, pattern=r"^t[1-6]$",
                               description="混合模式必须关联 Router 批准的 task_id；独立工具调用可省略")


class SearchPublicKBInput(ToolInput):
    """search_public_kb 入参。"""

    question: str = Field(min_length=1, max_length=4000, description="检索问题，如「招标方式有哪些」「评标委员会如何组成」")
    top_k: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description="返回的法规证据片段数上限（1~20），默认由系统配置决定",
    )


class KnowledgeQAInput(ToolInput):
    """knowledge_qa 入参。"""

    question: str = Field(min_length=1, max_length=4000, description="招投标专业知识问题，将基于权威法规知识库生成带引用的回答")


class QueryCompanyRegistrationInput(ToolInput):
    """query_company_registration 入参。"""

    company_name: str = Field(
        min_length=1,
        max_length=80,
        description="用户提供的主体名称，按原文精确查询企业工商登记信息，不查询经营范围",
    )
    top_k: int | None = Field(default=None, ge=1, le=50, description="返回记录数上限（默认系统配置）")


class QueryCompanyBusinessScopeInput(ToolInput):
    """query_company_business_scope 入参。"""

    company_name: str = Field(
        min_length=1,
        max_length=80,
        description="用户提供的主体名称，按原文精确查询企业经营范围",
    )
    top_k: int | None = Field(default=None, ge=1, le=50, description="返回记录数上限（默认系统配置）")


class QueryCompanyPenaltyInput(ToolInput):
    """query_company_penalty 入参。"""

    company_name: str = Field(min_length=1, max_length=80, description="用户提供的主体名称，精确匹配，不审核真实性或名称后缀")
    top_k: int | None = Field(default=None, ge=1, le=100, description="返回处罚记录数上限（默认 50）")


class QueryProjectAwardInput(ToolInput):
    """query_project_award 入参。"""

    project_number: str = Field(
        min_length=1,
        max_length=50,
        description="用户本轮原文或已核验前序记录中的项目编号；仅按项目编号精确查询，不接受项目名称或采购人",
    )
    top_k: int | None = Field(default=None, ge=1, le=50, description="返回记录数上限（默认系统配置）")


class QueryCompanyAwardHistoryInput(ToolInput):
    """query_company_award_history 入参。"""

    company_name: str = Field(
        min_length=1,
        max_length=80,
        description="用户明确要查询其作为中标企业/供应商时的主体名称；不得填入采购人、招标人或发包人",
    )
    top_k: int | None = Field(default=None, ge=1, le=50, description="返回记录数上限（默认系统配置）")


class SearchBusinessDataInput(ToolInput):
    """search_business_data 入参。"""

    keywords: list[str] = Field(
        min_length=1, max_length=5,
        description="检索关键词列表（1~5 个），全文/LIKE 候选检索；不具备日期或金额硬过滤",
    )
    exact_tokens: list[str] | None = Field(
        default=None,
        description="必须精确出现的实体 token（如公司全称、项目编号），用于提升排序权重（可选）",
    )
    tables: list[str] | None = Field(
        default=None,
        description=(
            "限定检索的表，可选值 company_info / company_penalty / bid_project；"
            "缺省检索全部三张表"
        ),
    )
    top_k: int | None = Field(default=None, ge=1, le=50, description="返回记录数上限（默认系统配置）")
