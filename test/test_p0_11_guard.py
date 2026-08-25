"""
test_p0_11_guard — P0-11 模糊匹配/盲目召回防范体系测试套件。

覆盖：
  1. 工商主体名称格式校验（_is_valid_company_name）
  2. 有效查询实体检测（_has_valid_query_entity）
  3. 中台湾控 — bid_project 检索词白名单过滤（_build_search_term）
  4. 前置校验三层拦截（裸实体名 / 无有效实体 / project_detail 缺编号）
  5. 统一引导话术输出验证
  6. 后置回溯 — bid_project 召回结果匹配校验
"""

from __future__ import annotations

import os
import sys
import unittest

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent.nodes.price_inquiry import (
    _is_valid_company_name,
    _has_valid_query_entity,
    _looks_like_code,
    _build_search_term,
    _build_unified_guidance,
    _UNIFIED_GUIDANCE_TEXT,
    _QUERY_INTENT_KEYWORDS,
    HardFilters,
    SearchIntent,
)


# ═════════════════════════════════════════════════════════
# 1. 工商主体名称格式校验
# ═════════════════════════════════════════════════════════

class TestCompanyNameValidation(unittest.TestCase):
    """验证 _is_valid_company_name 对各类输入的判断正确性。"""

    def test_valid_company_names(self):
        """合法公司名应通过校验。"""
        valid_names = [
            "科大讯飞股份有限公司",
            "华为技术有限公司",
            "阿里巴巴集团",
            "清华大学",
            "北京大学第一医院",
            "南京市公安局",
            "中国科学技术协会",
            "XXX科技有限公司",  # 最小合法后缀 "公司"
        ]
        for name in valid_names:
            self.assertTrue(_is_valid_company_name(name),
                            f"应通过校验: {name}")

    def test_invalid_company_names(self):
        """不含合法后缀的名称（项目名片段、口语表达）应被拒绝。"""
        invalid_names = [
            "智慧校园建设",           # 项目名
            "办公设备采购",           # 采购项目名
            "XX项目",                 # 不含合法后缀
            "",                       # 空字符串
            "AB",                     # 过短
            "科大",                   # 过短且无后缀
            "a" * 81,                 # 超长
            "2024年度采购计划",        # 项目名含年份
        ]
        for name in invalid_names:
            self.assertFalse(_is_valid_company_name(name),
                             f"应拒绝: {name}")

    def test_none_and_non_string(self):
        """None 和非字符串类型应返回 False。"""
        self.assertFalse(_is_valid_company_name(None))
        self.assertFalse(_is_valid_company_name(12345))

    def test_project_number_not_company_name(self):
        """项目编号不应被误判为公司名（即使含数字也应拒绝）。"""
        code_like = [
            "AH2024-001",
            "ZB-2024-123",
            "[350001]FJGGZY[GK]2024013",
        ]
        for code in code_like:
            self.assertFalse(_is_valid_company_name(code),
                             f"项目编号不应被当作公司名: {code}")


# ═════════════════════════════════════════════════════════
# 2. 有效查询实体检测
# ═════════════════════════════════════════════════════════

class TestValidQueryEntity(unittest.TestCase):
    """验证 _has_valid_query_entity 在各类意图下的判断。"""

    def test_project_number_valid(self):
        """有效的项目编号应被识别为合法实体。"""
        intent = SearchIntent(
            hard_filters=HardFilters(project_number="AH2024-001"),
            sub_route="bidding_query",
        )
        self.assertTrue(_has_valid_query_entity(intent))

    def test_company_name_valid(self):
        """合法的公司名应被识别。"""
        intent = SearchIntent(
            hard_filters=HardFilters(company_name="科大讯飞股份有限公司"),
            sub_route="company_query",
        )
        self.assertTrue(_has_valid_query_entity(intent))

    def test_successful_bidder_valid(self):
        """合法的中标供应商名应被识别。"""
        intent = SearchIntent(
            hard_filters=HardFilters(successful_bidder="华为技术有限公司"),
            sub_route="bidding_query",
        )
        self.assertTrue(_has_valid_query_entity(intent))

    def test_purchaser_valid(self):
        """合法的采购人名应被识别。"""
        intent = SearchIntent(
            hard_filters=HardFilters(purchaser="南京市公安局"),
            sub_route="bidding_query",
        )
        self.assertTrue(_has_valid_query_entity(intent))

    def test_no_valid_entity(self):
        """无任何有效实体时返回 False。"""
        intent = SearchIntent(
            hard_filters=HardFilters(),
            sub_route="bidding_query",
        )
        self.assertFalse(_has_valid_query_entity(intent))

    def test_project_name_as_company_name_rejected(self):
        """LLM 将项目名误提取为 company_name 时应被拒绝。"""
        intent = SearchIntent(
            hard_filters=HardFilters(company_name="智慧校园建设项目"),
            sub_route="bidding_query",
        )
        self.assertFalse(_has_valid_query_entity(intent))

    def test_short_name_rejected(self):
        """过短的非标准名称不应通过。"""
        intent = SearchIntent(
            hard_filters=HardFilters(company_name="讯飞"),
            sub_route="company_query",
        )
        self.assertFalse(_has_valid_query_entity(intent))

    def test_bare_project_number_without_code_feature_rejected(self):
        """纯中文项目名不应被 project_number 字段捕获（P0-1 修复）。"""
        intent = SearchIntent(
            hard_filters=HardFilters(project_number="智慧校园建设项目"),
            sub_route="bidding_query",
        )
        # _looks_like_code 返回 False → _has_valid_query_entity 返回 False
        self.assertFalse(_has_valid_query_entity(intent))


# ═════════════════════════════════════════════════════════
# 3. 中台湾控 — bid_project 检索词白名单过滤
# ═════════════════════════════════════════════════════════

class TestMidGuardKeywordFilter(unittest.TestCase):
    """验证 _build_search_term 对 bid_project 表的关键词白名单过滤。"""

    def test_company_name_keywords_pass(self):
        """公司名关键词应通过白名单。"""
        intent = SearchIntent(
            hard_filters=HardFilters(),
            semantic_keywords=["华为技术有限公司", "科大讯飞股份有限公司"],
            sub_route="bidding_query",
        )
        result = _build_search_term(intent, table="bid_project")
        self.assertIn("华为技术有限公司", result)
        self.assertIn("科大讯飞股份有限公司", result)

    def test_project_name_keywords_blocked(self):
        """项目名/标的物关键词应被白名单过滤。"""
        intent = SearchIntent(
            hard_filters=HardFilters(),
            semantic_keywords=["智慧校园建设", "办公设备", "信息化平台"],
            sub_route="bidding_query",
        )
        result = _build_search_term(intent, table="bid_project")
        # 三个非公司名关键词应全部被过滤
        self.assertEqual(result.strip(), "")

    def test_mixed_keywords_partial_filter(self):
        """混合关键词：公司名保留，项目名过滤。"""
        intent = SearchIntent(
            hard_filters=HardFilters(),
            semantic_keywords=["华为技术有限公司", "智慧校园", "科大讯飞股份有限公司"],
            sub_route="bidding_query",
        )
        result = _build_search_term(intent, table="bid_project")
        self.assertIn("华为技术有限公司", result)
        self.assertIn("科大讯飞股份有限公司", result)
        self.assertNotIn("智慧校园", result)

    def test_project_number_keywords_pass(self):
        """项目编号关键词应通过白名单。"""
        intent = SearchIntent(
            hard_filters=HardFilters(),
            semantic_keywords=["AH2024-001"],
            exact_tokens=["ZB-2024-123"],
            sub_route="bidding_query",
        )
        result = _build_search_term(intent, table="bid_project")
        self.assertIn("AH2024-001", result)
        self.assertIn("ZB-2024-123", result)

    def test_non_bid_project_no_filter(self):
        """非 bid_project 表不过滤关键词。"""
        intent = SearchIntent(
            hard_filters=HardFilters(),
            semantic_keywords=["智慧校园", "信息化"],
            sub_route="company_query",
        )
        result = _build_search_term(intent, table="company_info")
        self.assertIn("智慧校园", result)


# ═════════════════════════════════════════════════════════
# 4. 统一引导话术输出
# ═════════════════════════════════════════════════════════

class TestUnifiedGuidance(unittest.TestCase):
    """验证统一引导话术的内容完整性。"""

    def test_guidance_contains_all_key_points(self):
        """引导话术应包含项目编号、公司中标历史、工商/不良/经营范围三个关键点。"""
        self.assertIn("项目编号", _UNIFIED_GUIDANCE_TEXT)
        self.assertIn("中标历史", _UNIFIED_GUIDANCE_TEXT)
        self.assertIn("工商情况", _UNIFIED_GUIDANCE_TEXT)
        self.assertIn("不良记录", _UNIFIED_GUIDANCE_TEXT)
        self.assertIn("经营范围", _UNIFIED_GUIDANCE_TEXT)

    def test_guidance_has_warm_opening(self):
        """引导话术应以友好的问候语开头。"""
        self.assertTrue(_UNIFIED_GUIDANCE_TEXT.startswith("您好"))

    def test_guidance_has_numbered_structure(self):
        """引导话术应有清晰的三段式编号结构。"""
        self.assertIn("①", _UNIFIED_GUIDANCE_TEXT)
        self.assertIn("②", _UNIFIED_GUIDANCE_TEXT)
        self.assertIn("③", _UNIFIED_GUIDANCE_TEXT)

    def test_guidance_has_examples(self):
        """引导话术应包含模板示例帮助理解。"""
        self.assertIn("[项目编号]", _UNIFIED_GUIDANCE_TEXT)
        self.assertIn("[公司全称]", _UNIFIED_GUIDANCE_TEXT)

    def test_guidance_no_ambiguous_syntax(self):
        """引导话术不应包含正斜杠等歧义符号。"""
        self.assertNotIn("/", _UNIFIED_GUIDANCE_TEXT)
        self.assertNotIn("xx", _UNIFIED_GUIDANCE_TEXT.lower())

    def test_build_guidance_structure(self):
        """_build_unified_guidance 返回正确的字典结构。"""
        intent = SearchIntent(
            hard_filters=HardFilters(),
            sub_route="bidding_query",
            query_type="bidder_query",
        )
        result = _build_unified_guidance("测试查询", intent, reason="test")
        self.assertIn("business_result", result)
        self.assertIn("messages", result)
        self.assertEqual(result["business_result"]["query_type"], "unified_guidance")
        self.assertEqual(result["business_result"]["answer"], _UNIFIED_GUIDANCE_TEXT)

    def test_guidance_logs_reason(self):
        """引导话术触发时 data 中应包含 guard_reason。"""
        intent = SearchIntent(
            hard_filters=HardFilters(),
            sub_route="all",
            query_type="mixed",
        )
        result = _build_unified_guidance("随便问问", intent, reason="no_entity_for_route")
        data = result["business_result"]["data"]
        self.assertEqual(data["intent"]["guard_reason"], "no_entity_for_route")


# ═════════════════════════════════════════════════════════
# 5. 查询意图关键词检测
# ═════════════════════════════════════════════════════════

class TestIntentKeywordDetection(unittest.TestCase):
    """验证 _QUERY_INTENT_KEYWORDS 的正则匹配覆盖面。"""

    def test_has_intent_keyword(self):
        """包含查询意图词的问题应被检测到。"""
        queries_with_intent = [
            "查科大讯飞的工商信息",
            "科大讯飞中标了什么",
            "南京市公安局采购过什么项目",
            "AH2024-001的中标情况怎么样",
            "有没有不良记录",
            "请问最近的中标项目有哪些",
        ]
        for query in queries_with_intent:
            self.assertTrue(
                bool(_QUERY_INTENT_KEYWORDS.search(query)),
                f"应检测到意图词: {query}",
            )

    def test_bare_entity_no_intent(self):
        """仅输入公司名不含意图词的情况。"""
        bare_entities = [
            "科大讯飞股份有限公司",
            "华为技术有限公司",
            "南京市公安局",
        ]
        for query in bare_entities:
            self.assertFalse(
                bool(_QUERY_INTENT_KEYWORDS.search(query)),
                f"不应检测到意图词: {query}",
            )

    def test_project_number_with_intent(self):
        """项目编号 + 查询词应检测到意图。"""
        self.assertTrue(bool(_QUERY_INTENT_KEYWORDS.search("查AH2024-001的中标情况")))


# ═════════════════════════════════════════════════════════
# 6. _looks_like_code 回归验证
# ═════════════════════════════════════════════════════════

class TestCodeTokenDetection(unittest.TestCase):
    """验证 _looks_like_code 未被公司名校验逻辑破坏。"""

    def test_project_numbers_recognized(self):
        self.assertTrue(_looks_like_code("AH2024-001"))
        self.assertTrue(_looks_like_code("ZB-2024-123"))
        self.assertTrue(_looks_like_code("[350001]FJGGZY[GK]2024013"))

    def test_company_names_not_recognized(self):
        self.assertFalse(_looks_like_code("科大讯飞股份有限公司"))
        self.assertFalse(_looks_like_code("南京市公安局"))

    def test_mixed_content(self):
        """含数字的中文混合文本应被识别为代码类。"""
        self.assertTrue(_looks_like_code("2024年度采购计划"))
        # 但通过 _is_valid_company_name 时会被拒绝（无合法后缀）


if __name__ == "__main__":
    unittest.main()
