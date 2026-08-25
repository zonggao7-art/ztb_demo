"""硬编码表 Schema 与列分类工具（快速路径，跳过 information_schema 查询）。"""

_HARDCODED_SCHEMA: dict[str, dict[str, list[str]]] = {
    "company_info": {
        "id": ["id"],
        "semantic": ["company_name", "business_scope", "industry", "address"],
        "time": ["establish_date"],
        "region": ["province", "city", "district"],
        "exact": ["credit_code"],
        "text": ["company_name", "business_scope", "industry", "address",
                 "legal_person", "registered_capital"],
    },
    "company_penalty": {
        "id": ["id"],
        "semantic": ["company_name", "illegal_behavior", "penalty_result"],
        "time": ["penalty_date"],
        "exact": ["credit_code"],
        "text": ["company_name", "illegal_behavior", "penalty_result",
                 "law_enforcement_unit"],
    },
    # P0-11：bid_project 仅开放 project_number（精确匹配）与 company_name（purchaser / successful_bidder）
    # 两个合法检索字段，永久屏蔽 project_name / subject_matter / agent / project_category 等非授权字段。
    # 注意：此变更要求 MySQL bid_project 表具备 FULLTEXT 索引 (purchaser, successful_bidder)。
    # 若当前索引包含 project_name 或 subject_matter，需执行迁移：
    #   ALTER TABLE bid_project DROP INDEX ft_semantic;
    #   ALTER TABLE bid_project ADD FULLTEXT INDEX ft_semantic (purchaser, successful_bidder);
    "bid_project": {
        "id": ["id"],
        "semantic": ["purchaser", "successful_bidder"],
        "time": ["winning_date", "publish_date"],
        "budget": ["winning_amount", "budget_amount"],
        "purchaser": ["purchaser"],
        "region": ["province", "city", "district"],
        "status": ["project_stage"],
        "exact": ["project_number"],
        "text": ["purchaser", "successful_bidder"],
    },
}

def _get_classification(table_name: str) -> dict[str, list[str]]:
    """获取表列分类（优先使用硬编码 schema）。"""
    return _HARDCODED_SCHEMA.get(table_name, {})

def _semantic_columns(classification: dict[str, list[str]]) -> list[str]:
    ordered: list[str] = []
    for key in ("id", "semantic", "time", "budget", "purchaser", "region", "status", "exact"):
        for col in classification.get(key, []):
            if col not in ordered:
                ordered.append(col)
    return ordered
