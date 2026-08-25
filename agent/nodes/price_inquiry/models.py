"""数据模型 — 硬过滤条件与结构化查询意图（HardFilters / SearchIntent）。"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from typing import Any, Optional

@dataclass
class HardFilters:
    """硬过滤条件 — 支持三类数据源的专用过滤。

    注意（2026-08-15 清理）：产品线已下线，产品专用字段（product_name /
    supplier_name 等）与从未被 SQL 构建读取的 company_type / project_name
    已移除；LLM 若仍输出这些键，from_dict 会直接忽略。
    """
    # ── 通用 ──
    time_range: Optional[dict[str, str]] = None
    budget_range: Optional[dict[str, float]] = None
    purchaser: Optional[str] = None
    region: Optional[str] = None
    province: Optional[str] = None
    city: Optional[str] = None
    status: Optional[str] = None

    # ── 公司专用 ──
    company_name: Optional[str] = None
    credit_code: Optional[str] = None
    industry: Optional[str] = None
    company_level: Optional[str] = None
    # P0-4：枚举归一化后展开的多值企业等级（如“中大型”→ [中型企业, 大型企业]）
    company_level_values: Optional[list[str]] = None
    business_status: Optional[str] = None

    # ── 产品专用（保留 category / price_range 供通用引擎兼容） ──
    category: Optional[str] = None
    price_range: Optional[dict[str, float]] = None

    # ── 招标专用 ──
    successful_bidder: Optional[str] = None
    agent: Optional[str] = None
    project_number: Optional[str] = None
    project_category: Optional[str] = None
    project_stage: Optional[str] = None
    winning_amount_range: Optional[dict[str, float]] = None

@dataclass
class SearchIntent:
    """结构化查询意图 — 扩展版，支持二级路由与统一解析。"""
    hard_filters: HardFilters
    semantic_keywords: list[str] = dataclass_field(default_factory=list)
    exact_tokens: list[str] = dataclass_field(default_factory=list)
    original_question: str = ""

    # ── 路由与排序 ──
    sub_route: str = "all"
    query_type: str = "mixed"
    sort_by: Optional[str] = None
    aggregation: Optional[str] = None
    top_n: Optional[int] = None
    need_penalty_check: bool = False
    need_contact: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any], question: str = "") -> SearchIntent:
        """从解析后的字典构造 SearchIntent（兼容旧接口）。"""
        hf = data.get("hard_filters") or {}
        return cls(
            hard_filters=HardFilters(
                time_range=hf.get("time_range"),
                budget_range=hf.get("budget_range"),
                purchaser=hf.get("purchaser"),
                region=hf.get("region"),
                province=hf.get("province"),
                city=hf.get("city"),
                status=hf.get("status"),
                company_name=hf.get("company_name"),
                credit_code=hf.get("credit_code"),
                industry=hf.get("industry"),
                company_level=hf.get("company_level"),
                business_status=hf.get("business_status"),
                category=hf.get("category"),
                price_range=hf.get("price_range"),
                successful_bidder=hf.get("successful_bidder"),
                agent=hf.get("agent"),
                project_number=hf.get("project_number"),
                project_category=hf.get("project_category"),
                project_stage=hf.get("project_stage"),
                winning_amount_range=hf.get("winning_amount_range"),
            ),
            semantic_keywords=data.get("semantic_keywords") or [],
            exact_tokens=data.get("exact_tokens") or [],
            original_question=question,
            sub_route=data.get("sub_route", "all"),
            query_type=data.get("query_type", "mixed"),
            sort_by=data.get("sort_by"),
            aggregation=data.get("aggregation"),
            top_n=data.get("top_n"),
            need_penalty_check=data.get("need_penalty_check", False),
            need_contact=data.get("need_contact", False),
        )
