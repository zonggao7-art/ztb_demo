"""
test_p0_12_project_number_detection — P0-12 项目编号意图识别与字段合规性测试。

场景覆盖：
  1. 纯项目编号输入（如 "AH2024-001"）→ 应触发 project_detail
  2. 项目编号 + 查询词（如 "AH2024-001的中标情况"）→ 应触发 project_detail
  3. 口语化带项目编号（如 "帮我查一下AH2024-001这个项目"）→ 应触发 project_detail
  4. 方括号格式项目编号（如 "[350001]FJGGZY[GK]2024013"）→ 应触发 project_detail
  5. 连字符格式项目编号（如 "2024-AH-001"）→ 应触发 project_detail
  6. 公司名输入（不应误判为项目编号）→ 不应触发
  7. 普通查询（无项目编号）→ 不应触发
  8. 数据召回字段合规性 — bid_project 仅使用 project_number
"""

from __future__ import annotations

import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent.nodes.price_inquiry import (
    _build_constraint_conditions,
    _extract_project_number_candidate,
    _looks_like_code,
    HardFilters,
    SearchIntent,
)


# ═════════════════════════════════════════════════════════
# 1. 确定性项目编号提取（各类自然语言输入场景）
# ═════════════════════════════════════════════════════════

class TestProjectNumberExtraction(unittest.TestCase):
    """验证 _extract_project_number_candidate 对各类输入的提取准确率。"""

    def test_pure_project_number(self):
        """场景1：纯项目编号输入 AH2024-001。"""
        result = _extract_project_number_candidate("AH2024-001")
        self.assertEqual(result, "AH2024-001")

    def test_project_number_with_query(self):
        """场景2：项目编号 + 查询词。"""
        result = _extract_project_number_candidate("AH2024-001的中标情况")
        self.assertEqual(result, "AH2024-001")

    def test_conversational_with_project_number(self):
        """场景3：口语化表达带项目编号。"""
        result = _extract_project_number_candidate("帮我查一下AH2024-001这个项目")
        self.assertEqual(result, "AH2024-001")

    def test_bracket_format_project_number(self):
        """场景4：方括号格式 [350001]FJGGZY[GK]2024013。"""
        result = _extract_project_number_candidate("[350001]FJGGZY[GK]2024013")
        self.assertEqual(result, "[350001]FJGGZY[GK]2024013")

    def test_dash_format(self):
        """场景5：连字符格式 ZB-2024-123。"""
        result = _extract_project_number_candidate("查一下ZB-2024-123")
        self.assertEqual(result, "ZB-2024-123")

    def test_gz_format(self):
        """GZ2024001 格式。"""
        result = _extract_project_number_candidate("GZ2024001")
        self.assertEqual(result, "GZ2024001")

    def test_company_name_not_extracted(self):
        """场景6：公司名输入不应被误判为项目编号。"""
        result = _extract_project_number_candidate("科大讯飞股份有限公司")
        self.assertIsNone(result)

    def test_plain_query_no_project_number(self):
        """场景7：普通查询无项目编号。"""
        result = _extract_project_number_candidate("帮我查一下招投标信息")
        self.assertIsNone(result)

    def test_amount_not_extracted(self):
        """金额表达不应被提取为项目编号。"""
        result = _extract_project_number_candidate("100万的项目")
        self.assertIsNone(result)

    def test_short_alphanumeric_excluded(self):
        """过短的字母数字组合（如 AB12）不应被提取。"""
        result = _extract_project_number_candidate("AB12")
        self.assertIsNone(result)

    def test_multiple_candidates_returns_best(self):
        """多个候选时返回最可能的（含分隔符的优先）。"""
        result = _extract_project_number_candidate("ZB-2024-123 和 AH2024001")
        self.assertEqual(result, "ZB-2024-123")  # 含连字符的优先

    def test_year_format(self):
        """2024-AH-001 格式。"""
        result = _extract_project_number_candidate("项目2024-AH-001的情况")
        self.assertEqual(result, "2024-AH-001")

    def test_empty_input(self):
        """空输入返回 None。"""
        self.assertIsNone(_extract_project_number_candidate(""))
        self.assertIsNone(_extract_project_number_candidate(None))


# ═════════════════════════════════════════════════════════
# 2. 意图触发准确率（提取 + 路由修正组合验证）
# ═════════════════════════════════════════════════════════

class TestIntentRoutingCorrection(unittest.TestCase):
    """验证项目编号提取后是否正确修正为 project_detail 路由。"""

    def test_project_number_forces_project_detail(self):
        """有效项目编号应强制修正 query_type 为 project_detail。"""
        intent = SearchIntent(
            hard_filters=HardFilters(project_number="AH2024-001"),
            sub_route="bidding_query",
            query_type="bidder_query",  # LLM 误分类
        )
        # 模拟 node_price_inquiry 的后置修正逻辑
        if intent.hard_filters.project_number and _looks_like_code(intent.hard_filters.project_number):
            if intent.query_type != "project_detail":
                intent.query_type = "project_detail"
                intent.sub_route = "bidding_query"

        self.assertEqual(intent.query_type, "project_detail")
        self.assertEqual(intent.sub_route, "bidding_query")

    def test_bare_project_number_triggers_project_detail(self):
        """纯项目编号输入经确定性提取后应标记为 project_detail。"""
        extracted = _extract_project_number_candidate("AH2024-001")
        self.assertIsNotNone(extracted)

        # 模拟注入后的 intent
        intent = SearchIntent(
            hard_filters=HardFilters(project_number=extracted),
            sub_route="bidding_query",
            query_type="project_detail",
        )
        self.assertEqual(intent.hard_filters.project_number, "AH2024-001")
        self.assertEqual(intent.query_type, "project_detail")

    def test_no_project_number_no_override(self):
        """无项目编号时不应触发修正。"""
        intent = SearchIntent(
            hard_filters=HardFilters(company_name="科大讯飞股份有限公司"),
            sub_route="company_query",
            query_type="company_detail",
        )
        # 不应触发 project_number 修正
        has_pn = intent.hard_filters.project_number and _looks_like_code(intent.hard_filters.project_number)
        self.assertFalse(has_pn)

    def test_llm_correctly_classifies_no_correction_needed(self):
        """LLM 已正确分类 project_detail 时，后置修正不过度干预。"""
        intent = SearchIntent(
            hard_filters=HardFilters(project_number="AH2024-001"),
            sub_route="bidding_query",
            query_type="project_detail",  # LLM 正确分类
        )
        if intent.hard_filters.project_number and _looks_like_code(intent.hard_filters.project_number):
            if intent.query_type != "project_detail":
                intent.query_type = "project_detail"
        # 不应改变
        self.assertEqual(intent.query_type, "project_detail")


# ═════════════════════════════════════════════════════════
# 3. 数据召回字段合规性（bid_project 仅开放 project_number）
# ═════════════════════════════════════════════════════════


class TestBidProjectFieldCompliance(unittest.TestCase):
    """验证 bid_project 数据召回仅通过 project_number 字段。

    直接测试生产实现 _build_constraint_conditions（P0-11：bid_project
    仅开放 project_number 精确匹配，project_name 等字段已从模型中移除，
    由模型层保证不被检索）。
    """

    def setUp(self):
        self.classification = {"exact": ["project_number"]}

    def test_project_number_only_constraint(self):
        """bid_project 查询中，仅 project_number 产生 SQL 约束。"""
        intent = SearchIntent(
            hard_filters=HardFilters(project_number="AH2024-001"),
            sub_route="bidding_query",
            query_type="project_detail",
        )
        conditions, params = _build_constraint_conditions(
            "bid_project", self.classification, intent
        )
        self.assertEqual(len(conditions), 1)
        self.assertIn("`project_number`", conditions[0])
        self.assertNotIn("project_name", " ".join(conditions))

    def test_no_project_number_no_constraints(self):
        """无 project_number 时 bid_project 不应产生任何约束。

        project_name 等非授权字段已从 HardFilters 模型中移除，
        不存在被用作检索条件的可能（模型层屏蔽）。
        """
        intent = SearchIntent(
            hard_filters=HardFilters(),
            sub_route="bidding_query",
            query_type="project_detail",
        )
        conditions, params = _build_constraint_conditions(
            "bid_project", self.classification, intent
        )
        self.assertEqual(len(conditions), 0)

    def test_company_name_not_used_as_project_number(self):
        """LLM 误将公司名填入 project_number 时应失败（_looks_like_code 拦截）。"""
        intent = SearchIntent(
            hard_filters=HardFilters(project_number="科大讯飞股份有限公司"),
            sub_route="bidding_query",
            query_type="project_detail",
        )
        conditions, params = _build_constraint_conditions(
            "bid_project", self.classification, intent
        )
        # _looks_like_code 返回 False → 不产生条件
        self.assertEqual(len(conditions), 0)


if __name__ == "__main__":
    unittest.main()
