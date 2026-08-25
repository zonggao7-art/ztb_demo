"""
test_sub_route — 二级路由改造测试用例集。

覆盖：
  1. 意图分类单元测试（sub_route + query_type 正确性）
  2. 输出字段筛选单元测试（_apply_output_template() 正确性）
  3. 端到端集成测试（node_price_inquiry 真实检索）
  4. 性能基准测试（4 张表 FULLTEXT 查询耗时）
"""

import os
import sys
import time
import pytest

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent.nodes.output_templates import (
    _apply_output_template,
    _FIELD_REGISTRY,
    _eval_condition,
    get_template,
)
from agent.nodes.price_inquiry import (
    HardFilters,
    SearchIntent,
    _parse_unified_intent,
    _safe_parse_intent,
    _build_hard_conditions_extended,
    _build_order_clause,
    _extract_keywords,
    _get_classification,
    _query_company_data,
    _query_bidding_data,
    _query_all_tables,
    _SUB_ROUTE_MAP,
    _build_capability_boundary_answer,
)


# ═════════════════════════════════════════════════════════
# 辅助：构建测试用 SearchIntent
# ═════════════════════════════════════════════════════════

def _make_intent(
    sub_route: str = "all",
    query_type: str = "mixed",
    keywords: list = None,
    sort_by: str = None,
    aggregation: str = None,
    top_n: int = None,
    need_penalty_check: bool = False,
    need_contact: bool = False,
    **hard_filter_kwargs,
) -> SearchIntent:
    """快速构建 SearchIntent 用于测试。"""
    hf = HardFilters()
    for k, v in hard_filter_kwargs.items():
        setattr(hf, k, v)
    return SearchIntent(
        hard_filters=hf,
        semantic_keywords=keywords or [],
        sub_route=sub_route,
        query_type=query_type,
        sort_by=sort_by,
        aggregation=aggregation,
        top_n=top_n,
        need_penalty_check=need_penalty_check,
        need_contact=need_contact,
    )


# ═════════════════════════════════════════════════════════
# 单元测试：意图分类
# ═════════════════════════════════════════════════════════

class TestSubRouteClassification:
    """测试 _parse_unified_intent 的二级路由分类准确率（需要 LLM 连接）。

    注：由于依赖 LLM，以下测试用例用于手工验证，CI 中标记为 skip。
    """

    
    def test_company_query_supplier_recommend(self):
        """企业推荐场景。"""
        from agent.nodes.price_inquiry import _build_llm
        llm = _build_llm()
        intent = _parse_unified_intent("安徽软件信息行业中型及以上企业有哪些？", llm)
        intent = _safe_parse_intent(intent)
        assert intent.sub_route == "company_query"
        assert intent.query_type == "supplier_recommend"

    #  (reason="需要 LLM 连接，用于手工验证")
    def test_company_query_penalty_check(self):
        """不良记录核查。"""
        from agent.nodes.price_inquiry import _build_llm
        llm = _build_llm()
        intent = _parse_unified_intent("河源市赞爷餐饮管理服务有限公司是否有不良记录？", llm)
        intent = _safe_parse_intent(intent)
        assert intent.sub_route == "company_query"
        assert intent.query_type == "penalty_check"
        assert intent.need_penalty_check is True

    
    def test_product_query_intercepted_to_all(self):
        """产品类查询已下线：LLM 不再有 product_query 选项，应路由到 all 或 bidding。"""
        from agent.nodes.price_inquiry import _build_llm
        llm = _build_llm()
        intent = _parse_unified_intent("电剪刀的市场行情价怎么样？", llm)
        intent = _safe_parse_intent(intent)
        # product_query 已从 valid_routes 移除，_safe_parse_intent 会降级为 "all"
        assert intent.sub_route != "product_query"
        assert intent.sub_route in ("all", "bidding_query")

    
    def test_product_query_intercepted_to_all_2(self):
        """供应商搜索已下线：应降级路由。"""
        from agent.nodes.price_inquiry import _build_llm
        llm = _build_llm()
        intent = _parse_unified_intent("找几个防水涂料的供应商，要价格便宜的", llm)
        intent = _safe_parse_intent(intent)
        assert intent.sub_route != "product_query"
        assert intent.sub_route in ("all", "company_query")

    
    def test_bidding_query_purchaser(self):
        """采购方视角。"""
        from agent.nodes.price_inquiry import _build_llm
        llm = _build_llm()
        intent = _parse_unified_intent("福建师范大学招标过什么项目？", llm)
        intent = _safe_parse_intent(intent)
        assert intent.sub_route == "bidding_query"

    
    def test_bidding_query_aggregation(self):
        """聚合查询。"""
        from agent.nodes.price_inquiry import _build_llm
        llm = _build_llm()
        intent = _parse_unified_intent("福州怡富电梯有限公司2024年中标金额最大的项目是哪个？", llm)
        intent = _safe_parse_intent(intent)
        assert intent.sub_route == "bidding_query"
        assert intent.aggregation is not None

    def test_safe_parse_intent_default_fallback(self):
        """测试 _safe_parse_intent 默认值回填。"""
        bad_intent = SearchIntent(hard_filters=HardFilters(), sub_route="unknown")
        fixed = _safe_parse_intent(bad_intent)
        assert fixed.sub_route == "all"
        assert fixed.query_type == "mixed"

    def test_safe_parse_intent_none_fields(self):
        """测试 _safe_parse_intent 对 None 字段的处理。"""
        bad_intent = SearchIntent(hard_filters=None, sub_route="")
        fixed = _safe_parse_intent(bad_intent)
        assert fixed.sub_route == "all"
        assert fixed.hard_filters is not None
        assert fixed.semantic_keywords == []
        assert fixed.exact_tokens == []


# ═════════════════════════════════════════════════════════
# 单元测试：输出字段筛选
# ═════════════════════════════════════════════════════════

class TestOutputTemplate:
    """测试 _apply_output_template 的字段筛选逻辑。"""

    def test_supplier_recommend_required_fields(self):
        """供应商推荐模板：验证必出字段。"""
        template = get_template("company_query", "supplier_recommend")
        assert template is not None
        assert "company_name" in template.required
        assert "industry" in template.required
        assert "province" in template.required

        intent = _make_intent("company_query", "supplier_recommend")
        records = [{
            "company_name": "测试科技公司",
            "industry": "软件信息",
            "company_level": "中型企业",
            "province": "安徽",
            "city": "合肥",
            "legal_person": None,  # 空值应隐藏
            "registered_capital": None,  # 空值应隐藏
            "credit_code": "91110000XXXXXXXX",
        }]

        formatted = _apply_output_template(records, intent, template)
        assert len(formatted) == 1
        assert formatted[0]["企业名称"] == "测试科技公司"
        assert formatted[0]["所属行业"] == "软件信息"
        assert "法定代表人" not in formatted[0]  # optional 但值为 None

    def test_penalty_check_required_fields(self):
        """不良记录模板：验证必出字段。"""
        template = get_template("company_query", "penalty_check")
        assert template is not None
        assert "penalty_date" in template.required
        assert "illegal_behavior" in template.required

        intent = _make_intent("company_query", "penalty_check", need_penalty_check=True)
        records = [{
            "company_name": "测试公司",
            "credit_code": "91110000XXXXXXXX",
            "penalty_date": "2024-01-15",
            "illegal_behavior": "未按规定申报",
            "penalty_result": "罚款5000元",
        }]

        formatted = _apply_output_template(records, intent, template)
        assert len(formatted) == 1
        assert formatted[0]["企业名称"] == "测试公司"
        assert formatted[0]["违法行为"] == "未按规定申报"

    def test_product_template_removed_price_inquiry(self):
        """产品线已下线：get_template('product_query', ...) 应返回 None。"""
        template = get_template("product_query", "price_inquiry")
        assert template is None, "product_query 模板应已下线"

    def test_product_template_removed_supplier_search(self):
        """供应商搜索模板已下线。"""
        template = get_template("product_query", "supplier_search")
        assert template is None, "product_query supplier_search 模板应已下线"

    def test_product_template_removed_need_contact(self):
        """联系方式条件模板已下线。"""
        template = get_template("product_query", "supplier_search")
        assert template is None, "product_query 条件模板应已下线"

    def test_purchaser_query_required_fields(self):
        """采购方查询模板：验证必出字段。"""
        template = get_template("bidding_query", "purchaser_query")
        assert template is not None
        assert "project_name" in template.required
        assert "successful_bidder" in template.required

        intent = _make_intent("bidding_query", "purchaser_query")
        records = [{
            "project_name": "XX校区设备采购",
            "project_number": "ZB2024-001",
            "successful_bidder": "某科技公司",
            "winning_amount": "500000.00",
            "winning_date": "2024-06-01",
        }]

        formatted = _apply_output_template(records, intent, template)
        assert len(formatted) == 1
        assert formatted[0]["项目名称"] == "XX校区设备采购"
        assert formatted[0]["中标供应商"] == "某科技公司"

    def test_null_placeholder_behavior(self):
        """空值为 required 时显示占位符 '未提供'。"""
        template = get_template("company_query", "supplier_recommend")

        intent = _make_intent("company_query", "supplier_recommend")
        records = [{
            "company_name": None,
            "industry": None,
            "company_level": None,
            "province": None,
            "city": None,
        }]

        formatted = _apply_output_template(records, intent, template)
        assert len(formatted) == 1
        assert formatted[0]["企业名称"] == "未提供"
        assert formatted[0]["所属行业"] == "未提供"

    def test_text_truncation(self):
        """文本截断：超长文本应截断并追加 …。"""
        template = get_template("company_query", "company_detail")

        intent = _make_intent("company_query", "company_detail")
        long_text = "A" * 250  # 超过 business_scope 的 max_chars=200
        records = [{
            "company_name": "测试公司",
            "credit_code": "91110000XXXXXXXX",
            "business_status": "存续",
            "business_scope": long_text,
        }]

        formatted = _apply_output_template(records, intent, template)
        assert len(formatted) == 1
        assert len(formatted[0]["经营范围"]) < len(long_text)
        assert formatted[0]["经营范围"].endswith("\u2026")


# ═════════════════════════════════════════════════════════
# 单元测试：SQL 构造与硬过滤
# ═════════════════════════════════════════════════════════

class TestSQLGeneration:
    """测试 SQL 生成器逻辑（无需数据库连接）。"""

    def test_build_hard_conditions_company(self):
        """测试公司专用过滤条件生成。"""
        classification = _get_classification("company_info")
        intent = _make_intent(sub_route="company_query", industry="软件信息", province="广东")
        conditions, params = _build_hard_conditions_extended("company_info", classification, intent)

        assert "`industry` = %s" in conditions
        # P0 优化 5.2.3：地区改用 LIKE 前缀模糊匹配
        assert "`province` LIKE %s" in conditions
        assert "软件信息" in params
        # 地区 LIKE 前缀匹配，参数为 "广东%"
        assert "广东%" in params or "广东" in params

    def test_build_hard_conditions_product_removed(self):
        """产品线已下线：_get_classification('product_info') 应返回空 dict。"""
        classification = _get_classification("product_info")
        assert classification == {}, "product_info 已从 _HARDCODED_SCHEMA 移除"

    def test_build_hard_conditions_bidding(self):
        """测试招标专用过滤条件生成。"""
        classification = _get_classification("bid_project")
        intent = _make_intent(
            sub_route="bidding_query",
            successful_bidder="福州怡富电梯有限公司",
            project_stage="结果公告",
            time_range={"start": "2024-01-01", "end": "2024-12-31"},
        )
        conditions, params = _build_hard_conditions_extended("bid_project", classification, intent)

        assert "`successful_bidder` = %s" in conditions
        # P0-11：bid_project 仅开放 purchaser + successful_bidder，
        # project_stage 已被屏蔽，不在硬过滤条件中
        assert "`project_stage`" not in str(conditions)

    def test_build_order_clause_date_desc(self):
        """测试日期降序排序（替代已移除的 price_asc 测试）。"""
        intent = _make_intent(sort_by="date_desc")
        clause = _build_order_clause(intent)
        assert "DESC" in clause

    def test_build_order_clause_amount_desc(self):
        """测试金额降序排序。"""
        intent = _make_intent(sort_by="amount_desc")
        clause = _build_order_clause(intent)
        assert "winning_amount" in clause
        assert "DESC" in clause

    def test_build_order_clause_default(self):
        """测试默认排序（_score_）。"""
        intent = _make_intent(sort_by=None)
        clause = _build_order_clause(intent)
        assert "_score_" in clause


# ═════════════════════════════════════════════════════════
# 单元测试：数据模型与容错
# ═════════════════════════════════════════════════════════

class TestDataModel:
    """测试数据模型的正确性。"""

    def test_search_intent_default_values(self):
        """测试 SearchIntent 无参构造默认值。"""
        intent = SearchIntent(hard_filters=HardFilters())
        assert intent.sub_route == "all"
        assert intent.query_type == "mixed"
        assert intent.sort_by is None
        assert intent.aggregation is None
        assert intent.top_n is None
        assert intent.need_penalty_check is False
        assert intent.need_contact is False
        assert intent.semantic_keywords == []
        assert intent.exact_tokens == []

    def test_search_intent_from_dict(self):
        """测试 from_dict 构造。"""
        data = {
            "sub_route": "company_query",
            "query_type": "penalty_check",
            "need_penalty_check": True,
            "hard_filters": {
                "company_name": "测试公司",
                "credit_code": "91110000XXXXXXXX",
            },
            "semantic_keywords": ["测试"],
        }
        intent = SearchIntent.from_dict(data, "查询测试")
        assert intent.sub_route == "company_query"
        assert intent.query_type == "penalty_check"
        assert intent.need_penalty_check is True
        assert intent.hard_filters.company_name == "测试公司"
        assert intent.hard_filters.credit_code == "91110000XXXXXXXX"

    def test_hard_filters_default(self):
        """测试 HardFilters 默认值。"""
        hf = HardFilters()
        assert hf.company_name is None
        assert hf.province is None
        assert hf.industry is None
        assert hf.price_range is None
        assert hf.successful_bidder is None

    def test_keyword_extraction(self):
        """测试关键词提取兜底逻辑。"""
        keywords = _extract_keywords("防水涂料 供应商 价格")
        assert len(keywords) > 0

    def test_hardcoded_schema_completeness(self):
        """测试硬编码 schema 覆盖三张核心表（product_info 已于 2026-08-10 下线）。"""
        tables = ["company_info", "company_penalty", "bid_project"]
        for t in tables:
            classification = _get_classification(t)
            assert classification, f"表 {t} 缺少 schema 定义"
            assert "semantic" in classification, f"表 {t} 缺少 semantic 列分类"
            assert len(classification["semantic"]) > 0, f"表 {t} 无 semantic 列"

    def test_sub_route_map(self):
        """测试二级路由映射完整性（product_query 已于 2026-08-10 下线）。"""
        expected_routes = ["company_query", "bidding_query", "all"]
        for route in expected_routes:
            config = _SUB_ROUTE_MAP.get(route)
            assert config is not None, f"缺少路由 {route}"
            assert "tables" in config
            assert "query_fn" in config

    def test_field_registry_size(self):
        """测试字段注册表大小。"""
        assert len(_FIELD_REGISTRY) > 10  # 确保注册了足够字段

    def test_eval_condition(self):
        """测试条件表达式求值。"""
        intent = _make_intent(need_contact=True, need_penalty_check=False)
        assert _eval_condition("intent.need_contact", intent) is True
        assert _eval_condition("intent.need_penalty_check", intent) is False
        assert _eval_condition("intent.non_existent", intent) is False


# ═════════════════════════════════════════════════════════
# 集成测试：端到端（需要数据库连接）
# ═════════════════════════════════════════════════════════

class TestIntegration:
    """端到端集成测试（需要 MySQL 连接和有效数据）。"""

    
    def test_query_company_data(self):
        """测试公司查询正常返回。"""
        intent = _make_intent(
            sub_route="company_query",
            query_type="supplier_recommend",
            keywords=["科技"],
        )
        result = _query_company_data(intent)
        assert "records" in result
        assert "total_found" in result

    
    def test_query_product_data(self):
        """产品线已下线：product_query 被确定性拦截为能力边界引导。"""
        result = _build_capability_boundary_answer("防水涂料价格")
        assert result["business_result"]["query_type"] == "capability_boundary"
        assert "暂不支持产品价格查询" in result["business_result"]["answer"]

    
    def test_query_bidding_data(self):
        """测试招标查询正常返回。"""
        intent = _make_intent(
            sub_route="bidding_query",
            query_type="purchaser_query",
            keywords=["师范大学"],
        )
        result = _query_bidding_data(intent)
        assert "records" in result

    
    def test_query_all_tables(self):
        """测试 all 兜底模式。"""
        intent = _make_intent(
            sub_route="all",
            keywords=["测试"],
        )
        result = _query_all_tables(intent)
        assert "records" in result

    
    def test_node_price_inquiry_end_to_end(self):
        """测试 node_price_inquiry 端到端（需要完整 Agent 环境）。"""
        from agent.nodes.price_inquiry import node_price_inquiry
        state = {
            "messages": [
                type("Msg", (), {"content": "福建师范大学招标过什么项目？"})()
            ]
        }
        result = node_price_inquiry(state)
        assert "business_result" in result
        assert result["business_result"]["branch"] == "price_inquiry"
        assert "sub_route" in result["business_result"]


# ═════════════════════════════════════════════════════════
# 性能基准测试
# ═════════════════════════════════════════════════════════

class TestPerformance:
    """性能基准测试（需要数据库连接）。"""

    
    def test_fulltext_performance_company_info(self):
        """company_info FULLTEXT 查询耗时。"""
        start = time.perf_counter()
        intent = _make_intent(keywords=["科技"])
        _query_tables_single("company_info", intent)
        elapsed = time.perf_counter() - start
        assert elapsed < 5.0, f"company_info 查询过慢：{elapsed:.2f}s"

    
    def test_fulltext_performance_product_info(self):
        """product_info FULLTEXT 查询耗时。"""
        start = time.perf_counter()
        intent = _make_intent(keywords=["涂料"])
        _query_tables_single("product_info", intent)
        elapsed = time.perf_counter() - start
        assert elapsed < 5.0, f"product_info 查询过慢：{elapsed:.2f}s"

    
    def test_fulltext_performance_bid_project(self):
        """bid_project FULLTEXT 查询耗时。"""
        start = time.perf_counter()
        intent = _make_intent(keywords=["项目"])
        _query_tables_single("bid_project", intent)
        elapsed = time.perf_counter() - start
        assert elapsed < 5.0, f"bid_project 查询过慢：{elapsed:.2f}s"

    
    def test_fulltext_performance_company_penalty(self):
        """company_penalty FULLTEXT 查询耗时。"""
        start = time.perf_counter()
        intent = _make_intent(keywords=["违法"])
        _query_tables_single("company_penalty", intent)
        elapsed = time.perf_counter() - start
        assert elapsed < 5.0, f"company_penalty 查询过慢：{elapsed:.2f}s"


def _query_tables_single(table_name: str, intent: SearchIntent):
    """单表查询辅助函数（用于性能测试）。"""
    from agent.nodes.price_inquiry import _query_tables
    return _query_tables([table_name], intent)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
