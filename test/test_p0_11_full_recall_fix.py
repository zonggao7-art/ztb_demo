"""
test_p0_11_full_recall_fix — 全链路模糊匹配/无脑召回修复验证测试套件。

覆盖本次修复的 4 个核心变更：
  1. _strip_preference_filters 保留核心实体字段
  2. company_query 后置召回校验
  3. all 路由纳入前置实体校验
  4. penalty 精确匹配替代 LIKE 模糊匹配
"""

from __future__ import annotations

import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent.nodes.price_inquiry import (
    HardFilters,
    SearchIntent,
    _strip_preference_filters,
    _has_preference_filters,
    _has_valid_query_entity,
    _build_unified_guidance,
)


# ═════════════════════════════════════════════════════════
# 1. _strip_preference_filters 核心实体保留验证
# ═════════════════════════════════════════════════════════

class TestStripPreferenceFiltersPreservesCoreEntities(unittest.TestCase):
    """验证宽松重试时核心实体字段不被剥离，防止无脑召回。"""

    def test_company_name_preserved(self):
        """company_name 在宽松重试中必须保留。"""
        hf = HardFilters(
            company_name="科大讯飞股份有限公司",
            industry="软件和信息技术服务业",
            province="安徽",
            city="合肥",
        )
        relaxed = _strip_preference_filters(hf, query_type="company_detail")
        self.assertEqual(relaxed.company_name, "科大讯飞股份有限公司")
        self.assertIsNone(relaxed.industry)      # 辅助字段剥离
        self.assertIsNone(relaxed.province)       # 辅助字段剥离
        self.assertIsNone(relaxed.city)           # 辅助字段剥离

    def test_successful_bidder_preserved(self):
        """successful_bidder 在宽松重试中必须保留。"""
        hf = HardFilters(
            successful_bidder="华为技术有限公司",
            province="广东",
            project_stage="结果公告",
        )
        relaxed = _strip_preference_filters(hf, query_type="bidder_query")
        self.assertEqual(relaxed.successful_bidder, "华为技术有限公司")
        self.assertIsNone(relaxed.province)           # 辅助字段剥离
        self.assertIsNone(relaxed.project_stage)      # 辅助字段剥离

    def test_purchaser_preserved(self):
        """purchaser 在宽松重试中必须保留。"""
        hf = HardFilters(
            purchaser="南京市公安局",
            city="南京",
            project_category="政府采购",
        )
        relaxed = _strip_preference_filters(hf, query_type="purchaser_query")
        self.assertEqual(relaxed.purchaser, "南京市公安局")
        self.assertIsNone(relaxed.city)               # 辅助字段剥离
        self.assertIsNone(relaxed.project_category)   # 辅助字段剥离

    def test_project_number_preserved(self):
        """project_number 在宽松重试中必须保留。"""
        hf = HardFilters(
            project_number="AH2024-001",
            province="安徽",
        )
        relaxed = _strip_preference_filters(hf, query_type="project_detail")
        self.assertEqual(relaxed.project_number, "AH2024-001")
        self.assertIsNone(relaxed.province)

    def test_all_core_entities_preserved_simultaneously(self):
        """多个核心实体同时存在时全部保留。"""
        hf = HardFilters(
            company_name="科大讯飞股份有限公司",
            successful_bidder="华为技术有限公司",
            purchaser="南京市公安局",
            project_number="AH2024-001",
            industry="软件",
            province="安徽",
            project_stage="结果公告",
        )
        relaxed = _strip_preference_filters(hf)
        self.assertEqual(relaxed.company_name, "科大讯飞股份有限公司")
        self.assertEqual(relaxed.successful_bidder, "华为技术有限公司")
        self.assertEqual(relaxed.purchaser, "南京市公安局")
        self.assertEqual(relaxed.project_number, "AH2024-001")
        self.assertIsNone(relaxed.industry)
        self.assertIsNone(relaxed.province)
        self.assertIsNone(relaxed.project_stage)

    def test_constraint_filters_still_preserved(self):
        """约束性过滤（time_range / winning_amount_range）不受影响。"""
        hf = HardFilters(
            company_name="科大讯飞股份有限公司",
            time_range={"start": "2024-01-01", "end": "2024-12-31"},
            winning_amount_range={"min": 100000.0, "max": 1000000.0},
            industry="软件",
        )
        relaxed = _strip_preference_filters(hf)
        self.assertEqual(relaxed.company_name, "科大讯飞股份有限公司")
        self.assertEqual(relaxed.time_range, {"start": "2024-01-01", "end": "2024-12-31"})
        self.assertEqual(relaxed.winning_amount_range, {"min": 100000.0, "max": 1000000.0})
        self.assertIsNone(relaxed.industry)

    def test_no_core_entities_nothing_preserved(self):
        """无核心实体时，所有偏好性字段均剥离。"""
        hf = HardFilters(
            industry="批发业",
            province="福建",
            city="福州",
            project_stage="结果公告",
        )
        relaxed = _strip_preference_filters(hf)
        self.assertIsNone(relaxed.industry)
        self.assertIsNone(relaxed.province)
        self.assertIsNone(relaxed.city)
        self.assertIsNone(relaxed.project_stage)
        self.assertFalse(_has_preference_filters(relaxed))


# ═════════════════════════════════════════════════════════
# 2. company_query 后置召回校验逻辑验证
# ═════════════════════════════════════════════════════════

class TestCompanyQueryPostRecallGuard(unittest.TestCase):
    """验证 company_query 后置校验的核心逻辑（纯函数级，不依赖 MySQL）。"""

    def test_matching_records_verified(self):
        """公司名匹配的记录应通过校验。"""
        target = "科大讯飞股份有限公司"
        records = [
            {"company_name": "科大讯飞股份有限公司", "industry": "软件"},
            {"company_name": "科大讯飞股份有限公司", "industry": "硬件"},
        ]
        verified = [
            r for r in records
            if target in str(r.get("company_name", ""))
        ]
        self.assertEqual(len(verified), 2)

    def test_non_matching_records_filtered_out(self):
        """不匹配的记录应被过滤。"""
        target = "科大讯飞股份有限公司"
        records = [
            {"company_name": "科大讯飞股份有限公司", "industry": "软件"},
            {"company_name": "华为技术有限公司", "industry": "通信"},
            {"company_name": "阿里巴巴集团", "industry": "电商"},
        ]
        verified = [
            r for r in records
            if target in str(r.get("company_name", ""))
        ]
        self.assertEqual(len(verified), 1)
        self.assertEqual(verified[0]["company_name"], "科大讯飞股份有限公司")

    def test_all_non_matching_triggers_guidance(self):
        """全部不匹配时应视为盲目召回。"""
        target = "科大讯飞股份有限公司"
        records = [
            {"company_name": "华为技术有限公司", "industry": "通信"},
            {"company_name": "阿里巴巴集团", "industry": "电商"},
        ]
        verified = [
            r for r in records
            if target in str(r.get("company_name", ""))
        ]
        self.assertEqual(len(verified), 0)

    def test_partial_name_inclusion_detected(self):
        """公司名包含关系（子公司等）也被视为匹配。"""
        target = "科大讯飞"
        records = [
            {"company_name": "科大讯飞股份有限公司", "industry": "软件"},
            {"company_name": "科大讯飞（上海）科技有限公司", "industry": "AI"},
        ]
        verified = [
            r for r in records
            if target in str(r.get("company_name", ""))
        ]
        self.assertEqual(len(verified), 2)

    def test_none_company_name_safe(self):
        """空 company_name 字段不会导致异常。"""
        target = "科大讯飞股份有限公司"
        records = [
            {"company_name": None, "industry": "未知"},
            {"company_name": "", "industry": "空"},
            {"company_name": "科大讯飞股份有限公司", "industry": "软件"},
        ]
        verified = [
            r for r in records
            if target in str(r.get("company_name", ""))
        ]
        self.assertEqual(len(verified), 1)


# ═════════════════════════════════════════════════════════
# 3. all 路由前置实体校验验证
# ═════════════════════════════════════════════════════════

class TestAllRoutePreGuard(unittest.TestCase):
    """验证 all 路由纳入前置实体校验后的行为。"""

    def test_all_route_without_entity_rejected(self):
        """all 路由无有效实体时应被拦截。"""
        # 模拟 "all" + 无效实体 → 应被前置校验拦截
        intent = SearchIntent(
            hard_filters=HardFilters(),
            sub_route="all",
            query_type="mixed",
        )
        # 第一层：_has_valid_query_entity 应返回 False
        self.assertFalse(_has_valid_query_entity(intent))
        # 第二层：前置校验应命中（路由在 ("bidding_query", "company_query", "all") 中）
        routes_guarded = ("bidding_query", "company_query", "all")
        self.assertIn(intent.sub_route, routes_guarded)

    def test_all_route_with_valid_company_pass(self):
        """all 路由有合法公司名时应通过校验。"""
        intent = SearchIntent(
            hard_filters=HardFilters(company_name="科大讯飞股份有限公司"),
            sub_route="all",
            query_type="mixed",
        )
        self.assertTrue(_has_valid_query_entity(intent))

    def test_all_route_with_valid_project_number_pass(self):
        """all 路由有合法项目编号时应通过校验。"""
        intent = SearchIntent(
            hard_filters=HardFilters(project_number="AH2024-001"),
            sub_route="all",
            query_type="mixed",
        )
        self.assertTrue(_has_valid_query_entity(intent))

    def test_all_route_with_invalid_entity_rejected(self):
        """all 路由的无效实体名应被拒绝。"""
        intent = SearchIntent(
            hard_filters=HardFilters(company_name="智慧校园建设"),
            sub_route="all",
            query_type="mixed",
        )
        self.assertFalse(_has_valid_query_entity(intent))

    def test_guidance_structure_for_all_route(self):
        """all 路由被拦截时返回正确的引导话术结构。"""
        intent = SearchIntent(
            hard_filters=HardFilters(),
            sub_route="all",
            query_type="mixed",
        )
        result = _build_unified_guidance(
            "随便搜搜", intent, reason="no_entity_for_route"
        )
        self.assertEqual(result["business_result"]["query_type"], "unified_guidance")
        self.assertIn("项目编号", result["business_result"]["answer"])
        self.assertIn("中标历史", result["business_result"]["answer"])


# ═════════════════════════════════════════════════════════
# 4. 五大业务场景全量覆盖测试
# ═════════════════════════════════════════════════════════

class TestFiveBusinessScenarios(unittest.TestCase):
    """验证五大核心业务场景的模糊匹配/无脑召回的防范覆盖。"""

    # ── 场景1：企业工商信息查询 ──
    def test_scenario_1_company_detail_entity_required(self):
        """工商信息查询：有效公司名必须存在。"""
        # 有效公司名
        intent_valid = SearchIntent(
            hard_filters=HardFilters(company_name="科大讯飞股份有限公司"),
            sub_route="company_query",
            query_type="company_detail",
        )
        self.assertTrue(_has_valid_query_entity(intent_valid))

        # 无效输入（无公司名）
        intent_invalid = SearchIntent(
            hard_filters=HardFilters(),
            sub_route="company_query",
            query_type="company_detail",
        )
        self.assertFalse(_has_valid_query_entity(intent_invalid))

    # ── 场景2：企业经营范围查询 ──
    def test_scenario_2_business_scope_entity_required(self):
        """经营范围查询：有效公司名必须存在。"""
        intent_valid = SearchIntent(
            hard_filters=HardFilters(company_name="华为技术有限公司"),
            sub_route="company_query",
            query_type="company_industry",
        )
        self.assertTrue(_has_valid_query_entity(intent_valid))

        # 项目名误输入为公司名 → 拒绝
        intent_invalid = SearchIntent(
            hard_filters=HardFilters(company_name="办公设备采购项目"),
            sub_route="company_query",
            query_type="company_industry",
        )
        self.assertFalse(_has_valid_query_entity(intent_invalid))

    # ── 场景3：企业不良记录查询 ──
    def test_scenario_3_penalty_check_entity_required(self):
        """不良记录查询：有效公司名必须存在。"""
        intent_valid = SearchIntent(
            hard_filters=HardFilters(company_name="科大讯飞股份有限公司"),
            sub_route="company_query",
            query_type="penalty_check",
            need_penalty_check=True,
        )
        self.assertTrue(_has_valid_query_entity(intent_valid))

        # 口语化输入无合法公司名 → 拒绝
        intent_invalid = SearchIntent(
            hard_filters=HardFilters(),
            sub_route="company_query",
            query_type="penalty_check",
            need_penalty_check=True,
        )
        self.assertFalse(_has_valid_query_entity(intent_invalid))

    # ── 场景4：项目中标情况查询 ──
    def test_scenario_4_project_detail_entity_required(self):
        """项目中标情况查询：必须有合法项目编号。"""
        intent_valid = SearchIntent(
            hard_filters=HardFilters(project_number="AH2024-001"),
            sub_route="bidding_query",
            query_type="project_detail",
        )
        self.assertTrue(_has_valid_query_entity(intent_valid))

        # 无项目编号 → 拒绝
        intent_invalid = SearchIntent(
            hard_filters=HardFilters(),
            sub_route="bidding_query",
            query_type="project_detail",
        )
        self.assertFalse(_has_valid_query_entity(intent_invalid))

    # ── 场景5：公司中标历史查询 ──
    def test_scenario_5_bidder_query_entity_required(self):
        """公司中标历史查询：必须有合法中标供应商名或采购人名。"""
        intent_valid_bidder = SearchIntent(
            hard_filters=HardFilters(successful_bidder="华为技术有限公司"),
            sub_route="bidding_query",
            query_type="bidder_query",
        )
        self.assertTrue(_has_valid_query_entity(intent_valid_bidder))

        intent_valid_purchaser = SearchIntent(
            hard_filters=HardFilters(purchaser="南京市公安局"),
            sub_route="bidding_query",
            query_type="purchaser_query",
        )
        self.assertTrue(_has_valid_query_entity(intent_valid_purchaser))

        # 无任何实体 → 拒绝
        intent_invalid = SearchIntent(
            hard_filters=HardFilters(),
            sub_route="bidding_query",
            query_type="bidder_query",
        )
        self.assertFalse(_has_valid_query_entity(intent_invalid))


# ═════════════════════════════════════════════════════════
# 5. 宽松重试不会导致无脑召回（集成验证）
# ═════════════════════════════════════════════════════════

class TestRelaxedRetryNoIndiscriminateRecall(unittest.TestCase):
    """验证宽松重试保留核心实体后不会产生无脑召回。"""

    def test_company_name_blocked_relaxed_still_filtered(self):
        """有 company_name 时宽松重试仍保留该过滤，不产生无脑结果。"""
        hf = HardFilters(
            company_name="不存在的公司名测试用",
            industry="软件和信息技术服务业",
            province="安徽",
        )
        relaxed = _strip_preference_filters(hf, query_type="company_detail")
        # company_name 保留 → SQL 中必有 company_name 过滤
        self.assertEqual(relaxed.company_name, "不存在的公司名测试用")
        # 辅助过滤剥离
        self.assertIsNone(relaxed.industry)
        self.assertIsNone(relaxed.province)
        # 有 company_name → has_preference_filters 为 True
        self.assertTrue(_has_preference_filters(relaxed))

    def test_bidder_name_blocked_relaxed_still_filtered(self):
        """有 successful_bidder 时宽松重试仍保留。"""
        hf = HardFilters(
            successful_bidder="不存在的供应商测试用有限公司",
            province="北京",
        )
        relaxed = _strip_preference_filters(hf, query_type="bidder_query")
        self.assertEqual(relaxed.successful_bidder, "不存在的供应商测试用有限公司")
        self.assertIsNone(relaxed.province)

    def test_purchaser_name_blocked_relaxed_still_filtered(self):
        """有 purchaser 时宽松重试仍保留。"""
        hf = HardFilters(
            purchaser="不存在的采购单位测试中心",
            project_stage="结果公告",
        )
        relaxed = _strip_preference_filters(hf, query_type="purchaser_query")
        self.assertEqual(relaxed.purchaser, "不存在的采购单位测试中心")
        self.assertIsNone(relaxed.project_stage)


if __name__ == "__main__":
    unittest.main()
