"""工具入参 schema — pydantic v2 模型，同时生成 LLM 可见的 tool schema。

字段 docstring / description 会直接成为 LLM 的函数参数说明，
编写时以「让调用方 Agent 一次填对参数」为标准。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# SQL 工具可访问的表白名单（蓝图 §5.4 安全边界）
ALLOWED_TABLES = ("company_info", "company_penalty", "bid_project")


class SearchPublicKBInput(BaseModel):
    """search_public_kb 入参。"""

    question: str = Field(description="检索问题，如「招标方式有哪些」「评标委员会如何组成」")
    top_k: int | None = Field(
        default=None,
        ge=1,
        le=20,
        description="返回的法规证据片段数上限（1~20），默认由系统配置决定",
    )


class KnowledgeQAInput(BaseModel):
    """knowledge_qa 入参。"""

    question: str = Field(description="招投标专业知识问题，将基于权威法规知识库生成带引用的回答")


class QueryCompanyInfoInput(BaseModel):
    """query_company_info 入参。"""

    company_name: str = Field(description="公司全称（工商主体名称，如「XX有限公司」）")
    industry: str | None = Field(default=None, description="所属行业（可选）")
    region: str | None = Field(default=None, description="地区（可选）")
    province: str | None = Field(default=None, description="省份（可选）")
    city: str | None = Field(default=None, description="城市（可选）")
    business_status: str | None = Field(default=None, description="经营状态，如 存续/在业/注销（可选）")
    time_start: str | None = Field(default=None, description="成立/记录时间范围起点，格式 YYYY-MM-DD（可选）")
    time_end: str | None = Field(default=None, description="成立/记录时间范围终点，格式 YYYY-MM-DD（可选）")
    top_k: int | None = Field(default=None, ge=1, le=50, description="返回记录数上限（默认系统配置）")


class QueryCompanyPenaltyInput(BaseModel):
    """query_company_penalty 入参。"""

    company_name: str = Field(description="公司全称（工商主体名称，精确匹配）")
    top_k: int | None = Field(default=None, ge=1, le=100, description="返回处罚记录数上限（默认 50）")


class QueryBidRecordsInput(BaseModel):
    """query_bid_records 入参。"""

    project_number: str | None = Field(
        default=None,
        description="项目编号（字母+数字编码，如 AH2024-001）；提供时按项目精确查询",
    )
    company_name: str | None = Field(
        default=None,
        description="中标供应商/投标人公司全称；与 purchaser 至少提供其一（无 project_number 时）",
    )
    purchaser: str | None = Field(
        default=None,
        description="采购人（招标方）公司全称；与 company_name 至少提供其一（无 project_number 时）",
    )
    time_start: str | None = Field(default=None, description="中标日期范围起点，格式 YYYY-MM-DD（可选）")
    time_end: str | None = Field(default=None, description="中标日期范围终点，格式 YYYY-MM-DD（可选）")
    region: str | None = Field(default=None, description="地区（可选）")
    province: str | None = Field(default=None, description="省份（可选）")
    winning_amount_min: float | None = Field(default=None, description="中标金额下限（万元/元，与库内单位一致）（可选）")
    winning_amount_max: float | None = Field(default=None, description="中标金额上限（可选）")
    sort_by: str | None = Field(
        default=None,
        description="排序字段（可选）：winning_amount_desc / winning_amount_asc / winning_date_desc",
    )
    top_k: int | None = Field(default=None, ge=1, le=50, description="返回记录数上限（默认系统配置）")


class SearchBusinessDataInput(BaseModel):
    """search_business_data 入参。"""

    keywords: list[str] = Field(
        description="检索关键词列表（1~5 个），将走语义+全文多级降级召回",
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
