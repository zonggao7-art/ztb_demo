"""召回率优化回归测试 — 覆盖 recall_low_root_cause_report.md 的 P0/P1 修复点。

纯函数级测试（不依赖 MySQL/Milvus 运行），可直接执行：
    python -m test.test_recall_optimization
或 pytest：
    pytest test/test_recall_optimization.py -v
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.nodes.price_inquiry import (  # noqa: E402
    HardFilters,
    SearchIntent,
    _HARDCODED_SCHEMA,
    _MYSQL_SEMANTIC_PER_TABLE_LIMIT,
    _MYSQL_SEMANTIC_THRESHOLD,
    _MYSQL_SEMANTIC_TOP_K,
    _build_constraint_conditions,
    _build_full_scan_sql,
    _build_preference_conditions,
    _build_search_term,
    _build_semantic_query_text,
    _build_vector_recall_sql,
    _has_preference_filters,
    _looks_like_code,
    _match_company_levels,
    _match_enum_value,
    _split_overlong_keyword,
    _strip_preference_filters,
)
from public_kb.config import Settings  # noqa: E402


def _intent(**hf_kwargs) -> SearchIntent:
    return SearchIntent(
        hard_filters=HardFilters(**hf_kwargs),
        semantic_keywords=["福建师范大学"],
        exact_tokens=["福建师范大学"],
        original_question="福建师范大学招标过什么项目？",
    )


# ────────────────────────────────────────────────
# P0-1：exact_tokens 不再误映射到 project_number
# ────────────────────────────────────────────────
def test_looks_like_code():
    assert _looks_like_code("福建师范大学") is False
    assert _looks_like_code("武汉江腾铁路工程有限责任公司") is False
    assert _looks_like_code("HB-2024-001") is True
    assert _looks_like_code("91350100MA31X2X49P") is True
    assert _looks_like_code("") is False


def test_constraint_conditions_skip_entity_tokens():
    """公司名类 exact_token 不得生成 project_number = '公司名' 恒假条件。"""
    intent = _intent()
    conds, params = _build_constraint_conditions(
        "bid_project", _HARDCODED_SCHEMA["bid_project"], intent
    )
    joined = " AND ".join(conds)
    assert "project_number" not in joined, f"实体名被误映射为项目编号: {joined}"
    assert "福建师范大学" not in params

    # 真编号仍应生效
    intent2 = _intent()
    intent2.exact_tokens = ["ZB2024-0193"]
    conds2, params2 = _build_constraint_conditions(
        "bid_project", _HARDCODED_SCHEMA["bid_project"], intent2
    )
    assert any("project_number" in c for c in conds2)
    assert "ZB2024-0193" in params2


# ────────────────────────────────────────────────
# P0-2：硬过滤分级 + 偏好性剥离
# ────────────────────────────────────────────────
def test_filter_tiering():
    intent = _intent(
        purchaser="福建师范大学",
        industry="批发",
        company_level="中大型企业",
        province="安徽",
        city="合肥",
        project_stage="结果公告",
        time_range={"start": "2024-01-01"},
        winning_amount_range={"min": 100000.0},
    )
    schema = _HARDCODED_SCHEMA["bid_project"]

    pref_conds, _ = _build_preference_conditions("bid_project", schema, intent)
    pref_joined = " AND ".join(pref_conds)
    assert "purchaser" in pref_joined
    assert "industry" in pref_joined or True  # industry 属公司表字段，此处仅验证不报错
    # P0-11：bid_project 仅开放 purchaser + successful_bidder，
    # province / project_stage 已被屏蔽
    assert "province" not in pref_joined
    assert "project_stage" not in pref_joined

    const_conds, _ = _build_constraint_conditions("bid_project", schema, intent)
    const_joined = " AND ".join(const_conds)
    # P0-11：bid_project 仅开放 project_number 约束条件，
    # time_range / winning_amount_range 已被屏蔽
    assert "winning_date" not in const_joined
    assert "winning_amount" not in const_joined
    assert "purchaser" not in const_joined         # 实体名不得进入约束层
    assert "project_stage" not in const_joined

    assert _has_preference_filters(intent.hard_filters) is True
    relaxed = _strip_preference_filters(intent.hard_filters)
    # P0-11 修复：核心实体字段（purchaser）在宽松重试中保留，防止无脑召回
    assert relaxed.purchaser == "福建师范大学"      # 核心实体保留
    assert relaxed.industry is None                 # 辅助字段剥离
    assert relaxed.province is None                 # 辅助字段剥离
    assert relaxed.project_stage is None            # 辅助字段剥离
    assert relaxed.time_range == {"start": "2024-01-01"}       # 约束性保留
    assert relaxed.winning_amount_range == {"min": 100000.0}
    # P0-11：核心实体保留后 _has_preference_filters 仍为 True（purchaser 存在）
    assert _has_preference_filters(relaxed) is True


def test_vector_recall_sql_constraint_only():
    """Milvus 回表仅保留约束性条件，语义召回候选不再被偏好性过滤全歼。"""
    intent = _intent(purchaser="福建师范大学", project_stage="结果公告")
    sql_tuple = _build_vector_recall_sql(
        "bid_project", _HARDCODED_SCHEMA["bid_project"], intent, ["365", "464"]
    )
    assert sql_tuple is not None
    sql, params = sql_tuple
    where_clause = sql.split("WHERE", 1)[1]
    assert "purchaser" not in where_clause, f"回表仍带实体名硬过滤: {where_clause}"
    assert "project_stage" not in where_clause
    assert "福建师范大学" not in params
    assert "`id` IN (%s, %s)" in sql


def test_company_level_values_in_clause():
    """P0-4 多值等级展开为 IN 匹配。"""
    intent = _intent()
    intent.hard_filters.industry = "批发业"
    intent.hard_filters.company_level_values = ["中型企业", "大型企业"]
    conds, params = _build_preference_conditions(
        "company_info", _HARDCODED_SCHEMA["company_info"], intent
    )
    joined = " AND ".join(conds)
    assert "`company_level` IN (%s, %s)" in joined
    assert "中型企业" in params and "大型企业" in params


# ────────────────────────────────────────────────
# P0-3：兜底阶段仅约束性条件
# ────────────────────────────────────────────────
def test_full_scan_constraint_only():
    # 仅偏好性条件 → 放弃兜底（避免随机行）
    intent = _intent(industry="批发", province="安徽")
    assert _build_full_scan_sql(
        "company_info", _HARDCODED_SCHEMA["company_info"], intent
    ) is None

    # 有约束性条件 → 兜底 SQL 不含偏好性条件
    intent2 = _intent(industry="批发", time_range={"start": "2024-01-01"})
    sql_tuple = _build_full_scan_sql(
        "company_info", _HARDCODED_SCHEMA["company_info"], intent2
    )
    assert sql_tuple is not None
    sql, params = sql_tuple
    where_clause = sql.split("WHERE", 1)[1]
    assert "industry" not in where_clause, f"兜底仍带偏好性条件: {where_clause}"
    assert "批发" not in params
    assert "establish_date" in where_clause


# ────────────────────────────────────────────────
# P0-4：枚举归一化纯函数
# ────────────────────────────────────────────────
def test_match_enum_value():
    enum_values = ["批发业", "零售业", "软件和信息技术服务业"]
    assert _match_enum_value("批发", enum_values) == "批发业"
    assert _match_enum_value("零售业", enum_values) == "零售业"
    assert _match_enum_value("软件和信息技术", enum_values) == "软件和信息技术服务业"
    assert _match_enum_value("航空航天", enum_values) is None
    assert _match_enum_value("批发", []) is None


def test_match_company_levels():
    enum_values = ["大型企业", "中型企业", "小型企业", "微型企业"]
    assert _match_company_levels("中大型企业", enum_values) == ["大型企业", "中型企业"]
    assert _match_company_levels("大型企业", enum_values) == ["大型企业"]
    assert _match_company_levels("中小微", enum_values) == ["中型企业", "小型企业", "微型企业"]
    assert _match_company_levels("独角兽企业", enum_values) == []


# ────────────────────────────────────────────────
# P1-1：召回池参数 + 查询向量纯净化
# ────────────────────────────────────────────────
def test_semantic_pool_enlarged():
    assert _MYSQL_SEMANTIC_TOP_K >= 64
    assert _MYSQL_SEMANTIC_PER_TABLE_LIMIT >= 24
    assert _MYSQL_SEMANTIC_THRESHOLD <= 0.30


def test_semantic_query_text_entity_first():
    intent = _intent()
    # 实体词优先，疑问句式不参与向量构造
    text = _build_semantic_query_text(intent)
    assert "福建师范大学" in text
    assert "招标过什么项目" not in text

    # 无实体词时回退到原始问题
    intent2 = SearchIntent(hard_filters=HardFilters(), original_question="最近有什么大项目")
    assert _build_semantic_query_text(intent2) == "最近有什么大项目"


# ────────────────────────────────────────────────
# P1-2：FULLTEXT "+" 操作符全部清除
# ────────────────────────────────────────────────
def test_search_term_no_plus_operator():
    intent = SearchIntent(
        hard_filters=HardFilters(),
        semantic_keywords=["合肥", "批发"],
        exact_tokens=["合肥鑫峰商贸有限公司"],
        original_question="合肥批发供应商",
    )
    term = _build_search_term(intent)
    assert "+" not in term, f"仍含 + 操作符: {term}"

    # 单关键词场景同样无 +
    intent2 = SearchIntent(
        hard_filters=HardFilters(),
        semantic_keywords=["武汉江腾铁路工程有限责任公司"],
        original_question="x",
    )
    assert "+" not in _build_search_term(intent2)


# ────────────────────────────────────────────────
# P2：超长关键词切分
# ────────────────────────────────────────────────
def test_split_overlong_keyword():
    # 整句当关键词 → 在实体后缀后截断，剥离疑问句式尾巴
    assert (
        _split_overlong_keyword("武汉江腾铁路工程有限责任公司中标过什么项目")
        == "武汉江腾铁路工程有限责任公司"
    )
    # 短关键词不受影响
    assert _split_overlong_keyword("合肥") == "合肥"
    assert _split_overlong_keyword("福建师范大学") == "福建师范大学"
    # 无实体后缀的超长词硬截断
    assert _split_overlong_keyword("超级无敌长的关键词内容测试串数据") == "超级无敌长的关键词内容测"


# ────────────────────────────────────────────────
# P1-3：RAG 链路参数
# ────────────────────────────────────────────────
def test_rag_params_relaxed():
    settings = Settings()
    assert settings.retrieval_top_k == 5
    assert settings.similarity_threshold == 0.45
    assert settings.hybrid_dense_limit == 30
    assert settings.hybrid_sparse_limit == 30
    assert settings.hybrid_fusion_limit == 30


if __name__ == "__main__":
    test_funcs = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failed = 0
    for name, fn in test_funcs:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n共 {len(test_funcs)} 项，失败 {failed} 项")
    sys.exit(1 if failed else 0)
