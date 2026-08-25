"""
测试套件：Bug1（项目编号100%查空）与 Bug2（视角识别错误）修复验证。

覆盖场景：
  - Bug1-A: 项目编号含破折号（_normalize_token 冲突）
  - Bug1-B: 项目编号含方括号（真实数据格式）
  - Bug1-C: credit_code 类似冲突（防止同类问题）
  - Bug1-D: 不存在硬过滤时 exact_tokens 仍正常添加
  - Bug1-E: 多个代码类 token 部分去重
  - Bug2-A: 项目视角 → project_detail
  - Bug2-B: 公司视角 → bidder_query
  - Bug2-C: 采购人视角 → purchaser_query
  - Bug2-D: 各 query_type 空结果引导话术
  - 集成: 字段映射表与回答模板的一致性

Bug1 用例直接测试生产模块 agent.nodes.price_inquiry 的
_build_constraint_conditions（不再使用本地冻结副本，生产回归即测试失败）。
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
    _build_constraint_conditions,
    _normalize_token,
)


# ── 渲染测试 ──
def _get_project_detail_template_keys():
    """从 answer_templates.py 获取 project_detail 模板的关键字段。"""
    return [
        "project_name", "project_number", "purchaser", "successful_bidder",
        "winning_amount", "budget_amount", "winning_date", "agent", "subject_matter",
    ]


# ═════════════════════════════════════════════════════════
# Bug 1 测试：项目编号 100% 查空
# ═════════════════════════════════════════════════════════

class TestBug1ProjectNumberDedup(unittest.TestCase):
    """验证 exact_tokens 中的归一化变体不会与 hard_filters 产生冲突条件。"""

    def setUp(self):
        self.classification = {
            "id": ["id"],
            "exact": ["project_number"],
            "semantic": ["project_name", "purchaser", "successful_bidder", "subject_matter"],
        }

    def test_bug1a_dashed_project_number_NOT_duplicated(self):
        """场景A：项目编号含破折号（如 AH2024-001），exact_tokens 中归一化变体被跳过。"""
        hf = HardFilters(project_number="AH2024-001", project_stage="结果公告")
        intent = SearchIntent(
            sub_route="bidding_query",
            query_type="project_detail",
            hard_filters=hf,
            exact_tokens=["AH2024-001", "AH2024001"],  # 一个原样，一个归一化变体
        )
        conditions, params = _build_constraint_conditions(
            "bid_project", self.classification, intent
        )

        # 应该有且仅有一个 project_number 条件
        pn_conditions = [c for c in conditions if "project_number" in c]
        self.assertEqual(len(pn_conditions), 1,
                         f"Expected 1 project_number condition, got {len(pn_conditions)}: {conditions}")

        # params 中也应该只有一个匹配值
        self.assertIn("AH2024-001", params,
                      f"Expected 'AH2024-001' in params, got {params}")
        self.assertNotIn("AH2024001", params,
                         "Normalized variant 'AH2024001' should NOT be in params")

    def test_bug1b_bracket_style_project_number(self):
        """场景B：真实数据格式 [350001]FJGGZY[GK]2024013，含方括号的项目编号。"""
        hf = HardFilters(
            project_number="[350001]FJGGZY[GK]2024013",
            project_stage="结果公告",
        )
        intent = SearchIntent(
            sub_route="bidding_query",
            query_type="project_detail",
            hard_filters=hf,
            exact_tokens=["[350001]FJGGZY[GK]2024013"],
        )
        conditions, params = _build_constraint_conditions(
            "bid_project", self.classification, intent
        )

        # 归一化后去除方括号 → "350001fjggzygk2024013"（生产版 _normalize_token 不转小写）
        normalized = _normalize_token("[350001]FJGGZY[GK]2024013")
        normalized_hf = _normalize_token(hf.project_number)
        self.assertEqual(normalized, normalized_hf,
                         "Normalized hard_filter and token should match")

        pn_conditions = [c for c in conditions if "project_number" in c]
        self.assertEqual(len(pn_conditions), 1,
                         f"Only 1 project_number condition expected, got {len(pn_conditions)}")

    def test_bug1c_credit_code_conflict_prevented(self):
        """场景C：credit_code 产生类似冲突时也应被去重。"""
        hf = HardFilters(credit_code="91340100077973914B")
        intent = SearchIntent(
            sub_route="company_query",
            query_type="company_detail",
            hard_filters=hf,
            exact_tokens=["91340100077973914B"],
        )
        classification_cc = {
            "id": ["id"],
            "exact": ["credit_code"],
        }
        conditions, params = _build_constraint_conditions(
            "company_info", classification_cc, intent
        )

        cc_conditions = [c for c in conditions if "credit_code" in c]
        self.assertEqual(len(cc_conditions), 1,
                         f"Only 1 credit_code condition expected, got {len(cc_conditions)}")

    def test_bug1d_exact_tokens_still_work_without_hard_filter(self):
        """场景D：没有 hard_filters 覆盖时，exact_tokens 应正常添加条件。"""
        hf = HardFilters(project_number=None)  # 无 hard_filter
        intent = SearchIntent(
            sub_route="bidding_query",
            query_type="project_detail",
            hard_filters=hf,
            exact_tokens=["AH2024-001"],
        )
        conditions, params = _build_constraint_conditions(
            "bid_project", self.classification, intent
        )

        pn_conditions = [c for c in conditions if "project_number" in c]
        self.assertEqual(len(pn_conditions), 1,
                         "Without hard_filter, exact_tokens should add 1 condition")
        self.assertIn("AH2024-001", params)

    def test_bug1e_multiple_code_like_tokens_partially_dedup(self):
        """场景E：exact_tokens 含多个代码 token，部分被覆盖，部分新增。"""
        hf = HardFilters(project_number="AH2024-001")
        intent = SearchIntent(
            sub_route="bidding_query",
            query_type="mixed",
            hard_filters=hf,
            exact_tokens=["AH2024-001", "AH2024-002", "91340100"],
        )
        conditions, params = _build_constraint_conditions(
            "bid_project", self.classification, intent
        )

        pn_conditions = [c for c in conditions if "project_number" in c]
        # 应有：1个硬过滤 + 2个新 exact token（AH2024-001被去重）
        self.assertEqual(len(pn_conditions), 3,
                         f"Expected 3 project_number conditions, got {len(pn_conditions)}")
        self.assertIn("AH2024-001", params)   # 硬过滤
        self.assertIn("AH2024-002", params)   # 新 token
        self.assertNotIn("AH2024001", params) # 归一化变体被去重


# ═════════════════════════════════════════════════════════
# Bug 2 测试：视角识别 → answer_templates 字段覆盖
# ═════════════════════════════════════════════════════════

class TestBug2AnswerTemplatePerspective(unittest.TestCase):
    """验证 project_detail 和 bidder_query 模板的视角正确性。"""

    def test_bug2a_project_detail_contains_all_9_fields(self):
        """场景A：project_detail 模板必须包含全部 9 个必出字段。"""
        from agent.nodes.answer_templates import ANSWER_TEMPLATES, render_answer

        tmpl = ANSWER_TEMPLATES.get("project_detail")
        self.assertIsNotNone(tmpl, "project_detail template must exist")

        required_fields = _get_project_detail_template_keys()
        for fname in required_fields:
            self.assertIn(
                f"{{{fname}}}", tmpl.single_template,
                f"project_detail template missing {{{fname}}}"
            )

        # 渲染测试
        record = {
            k: f"TEST_{k}" for k in required_fields
        }
        record["winning_amount"] = 1000000
        record["budget_amount"] = 1100000
        result = render_answer("project_detail", [record], entity="TEST")
        self.assertIn("TEST_project_name", result)
        self.assertIn("中标金额", result)
        self.assertIn("预算金额", result)
        self.assertIn("代理机构", result)
        self.assertIn("标的物", result)

    def test_bug2b_bidder_query_is_company_perspective(self):
        """场景B：bidder_query 从公司视角回答，不应包含项目视角特有字段。"""
        from agent.nodes.answer_templates import ANSWER_TEMPLATES, render_answer

        tmpl = ANSWER_TEMPLATES.get("bidder_query")
        self.assertIsNotNone(tmpl, "bidder_query template must exist")

        # bidder_query 模板不应包含 project_detail 特有的字段
        # （如 budget_amount, agent, subject_matter 不是其核心字段）
        # 验证模板确实没有 project_detail 的句子结构
        result = render_answer("bidder_query", [{
            "project_name": "TEST_PROJ",
            "project_number": "TEST_001",
            "purchaser": "TEST_PUR",
            "successful_bidder": "TEST_BIDDER",
            "winning_amount": 5000000,
            "winning_date": "2024-06-15",
        }], entity="TEST_BIDDER")
        # bidder 视角：主语是公司，不是项目
        self.assertIn("TEST_BIDDER", result)
        self.assertIn("中标了", result)
        # 不应包含 budget_amount / agent (这不是 bidder_query 的输出)
        self.assertNotIn("预算金额", result)
        self.assertNotIn("代理机构", result)

    def test_bug2c_purchaser_query_is_purchaser_perspective(self):
        """场景C：purchaser_query 从采购人视角回答。"""
        from agent.nodes.answer_templates import ANSWER_TEMPLATES, render_answer

        tmpl = ANSWER_TEMPLATES.get("purchaser_query")
        self.assertIsNotNone(tmpl, "purchaser_query template must exist")

        result = render_answer("purchaser_query", [{
            "project_name": "TEST_PROJ",
            "project_number": "TEST_002",
            "purchaser": "TEST_PURCHASER",
            "successful_bidder": "TEST_WINNER",
            "winning_amount": 1000000,
            "winning_date": "2024-01-01",
        }], entity="TEST_PURCHASER")
        self.assertIn("TEST_PURCHASER", result)
        self.assertIn("发包给", result)

    def test_bug2d_empty_result_guidance_per_query_type(self):
        """场景D：不同 query_type 的空结果引导话术不同。"""
        from agent.nodes.answer_templates import render_answer

        # project_detail 空结果应提示"项目编号拼写有误"（P0-10：仅 project_number 查询）
        r1 = render_answer("project_detail", [], entity="AH999")
        self.assertIn("项目编号拼写有误", r1)

        # company_detail 空结果应提示"公司名称存在差异"
        r2 = render_answer("company_detail", [], entity="不存在的公司")
        self.assertIn("公司名称存在差异", r2)

        # bidder_query 空结果应提示"公司名称写法不一致"
        r3 = render_answer("bidder_query", [], entity="不存在的中标公司")
        self.assertIn("公司名称写法", r3)


# ═════════════════════════════════════════════════════════
# 集成测试
# ═════════════════════════════════════════════════════════

class TestIntegration(unittest.TestCase):
    """集成级验证：字段映射表与回答模板的一致性。"""

    def test_output_templates_project_detail_required_matches_answer_temp(self):
        """验证 output_templates.py 的 project_detail required 字段与 answer_templates.py 一致。"""
        from agent.nodes.output_templates import get_template
        from agent.nodes.answer_templates import ANSWER_TEMPLATES

        ot = get_template("bidding_query", "project_detail")
        at = ANSWER_TEMPLATES["project_detail"]

        for fname in ot.required:
            self.assertIn(
                f"{{{fname}}}", at.single_template,
                f"output_templates required field '{fname}' missing in answer_template"
            )

    def test_output_templates_bidder_query_required_matches_answer_temp(self):
        """验证 output_templates.py 的 bidder_query required 字段与 answer_templates.py 一致。"""
        from agent.nodes.output_templates import get_template
        from agent.nodes.answer_templates import ANSWER_TEMPLATES

        ot = get_template("bidding_query", "bidder_query")
        at = ANSWER_TEMPLATES["bidder_query"]

        for fname in ot.required:
            self.assertIn(
                f"{{{fname}}}", at.single_template,
                f"output_templates required field '{fname}' missing in answer_template"
            )

    def test_credit_rating_is_removed_from_registry(self):
        """验证 credit_rating 已从字段注册表中移除。"""
        from agent.nodes.output_templates import _FIELD_REGISTRY
        self.assertNotIn("credit_rating", _FIELD_REGISTRY,
                         "credit_rating should be removed from field registry")

    def test_capability_guidance_loaded(self):
        """验证 CAPABILITY_GUIDANCE 常量可正常加载。"""
        from agent.nodes.answer_templates import CAPABILITY_GUIDANCE
        self.assertIn("三大核心能力", CAPABILITY_GUIDANCE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
