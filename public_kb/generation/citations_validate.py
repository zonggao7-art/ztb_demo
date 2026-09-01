# 功能：引用溯源子系统（M5 Step B 拆分）。
"""引用溯源模块 — 由原 citations.py(430行) 按职责拆分为：
  citations_models.py    pydantic 数据模型 + 标记解析 + 集合计算
  citations_build.py     引用构建 + 渲染
  citations_validate.py  CitationValidator 校验器
  citations.py           门面 re-export（既有导入路径保持可用）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..config import CitationRuleConfig
from .citations_models import (
    Citation,
    RuleResult,
    CitationValidationReport,
    _compute_cited_sets,
    _UNKNOWN_CHAPTER,
    _UNKNOWN_DOC_NAME,
    parse_citation_markers,
)


# ============================================================
#  校验器
# ============================================================

class CitationValidator:
    """引用来源校验器 — 规则集可经 CitationRuleConfig 启停。"""

    def __init__(self, config: Optional[CitationRuleConfig] = None) -> None:
        self._config = config or CitationRuleConfig()

    def validate(
        self,
        citations: List[Citation],
        answer: str,
        context_chunk_ids: Optional[List[Optional[int]]] = None,
        *,
        is_refusal: bool = False,
    ) -> CitationValidationReport:
        """对一次回答执行全部启用的引用校验规则。

        Args:
            citations: 标准化引用列表。
            answer: LLM 回答文本。
            context_chunk_ids: 进入 LLM 上下文的全部 chunk 的 chunk_id
                （顺序对应上下文编号），用于 R5 无遗漏校验。
            is_refusal: 是否拒答（拒答时 citations 合法为空）。

        Returns:
            CitationValidationReport 结构化报告。
        """
        context_ids = context_chunk_ids or []
        cited, uncited, unknown = _compute_cited_sets(citations, answer)

        # 各规则判定（R7 关闭时也计算，仅不计入 all_passed）
        r1 = CitationValidator._make_rule(
            "R1_chunk_id_present", "chunk_id 完整性",
            "每条引用必须携带 Milvus 行级 chunk_id",
            [c.context_index for c in citations if c.chunk_id is None],
            "缺失 chunk_id 的引用",
        )
        r2 = CitationValidator._make_rule(
            "R2_chunk_uid_present", "chunk_uid 完整性",
            "每条引用必须携带内容派生稳定标识 chunk_uid",
            [c.context_index for c in citations if not c.chunk_uid],
            "缺失 chunk_uid 的引用",
        )
        r3 = CitationValidator._make_rule(
            "R3_source_location_present", "数据源位置可定位",
            "doc_name/chapter 必须非空且非占位值",
            [
                c.context_index for c in citations
                if not c.doc_name or c.doc_name == _UNKNOWN_DOC_NAME
                or not c.chapter or c.chapter == _UNKNOWN_CHAPTER
            ],
            "数据源位置缺失的引用",
        )
        r4 = CitationValidator._make_rule(
            "R4_full_text_present", "原文片段完整",
            "每条引用必须携带非空完整原文",
            [c.context_index for c in citations if not c.text.strip()],
            "原文缺失的引用",
        )
        r5 = self._check_context_fully_cited(citations, context_ids)
        r6 = CitationValidator._make_rule(
            "R6_no_unknown_markers", "引用标记有效性",
            "回答中【来源N】标记必须全部解析到有效引用，无错误关联",
            unknown, "无效标记",
        )
        r7 = CitationValidator._make_rule(
            "R7_all_context_marked", "上下文全部被标记引用",
            "严格模式：上下文 chunk 必须全部被回答【来源N】标记引用",
            uncited, "未被回答标记引用的上下文块",
        )

        rules = [r1, r2, r3, r4, r5, r6, r7]

        # 按 CitationRuleConfig 应用启停：关闭的规则不参与 all_passed 判定
        flags = {
            "R1": self._config.require_chunk_id,
            "R2": self._config.require_chunk_uid,
            "R3": self._config.require_source_location,
            "R4": self._config.require_full_text,
            "R5": self._config.check_context_completeness,
            "R6": self._config.check_marker_validity,
            "R7": self._config.enforce_all_context_cited,
        }
        for r in rules:
            r.enabled = flags.get(r.rule_id[:2], True)
            if not r.enabled:
                r.passed = True

        # 拒答场景：空 citations 是合法输出，不因 R1-R4 判空失败（空集规则自然通过）
        all_passed = is_refusal or all(
            r.passed for r in rules if r.enabled
        )

        return CitationValidationReport(
            all_passed=all_passed,
            is_refusal=is_refusal,
            context_chunks=len(context_ids),
            cited_markers=cited,
            uncited_chunks=uncited,
            unknown_markers=unknown,
            rules=rules,
        )

    # ── 规则实现 ──────────────────────────────────────────

    @staticmethod
    def _make_rule(
        rule_id: str,
        name: str,
        description: str,
        bad: List[int],
        detail_prefix: str,
    ) -> RuleResult:
        """构造"坏项列表"类规则结果（R1-R4 / R6-R7 共用）。

        规则默认启用（enabled=True），启停由 validate 按 CitationRuleConfig 统一应用。
        """
        return RuleResult(
            rule_id=rule_id,
            name=name,
            description=description,
            enabled=True,
            passed=not bad,
            detail=f"{detail_prefix}: {bad}" if bad else "",
        )

    @staticmethod
    def _check_context_fully_cited(
        citations: List[Citation],
        context_chunk_ids: List[Optional[int]],
    ) -> RuleResult:
        if not context_chunk_ids:
            # 上下文为空（拒答路径）：引用也必须为空，否则属凭空引用
            if citations:
                return RuleResult(
                    rule_id="R5_context_fully_cited",
                    name="上下文无遗漏引用",
                    description="进入 LLM 上下文的全部 chunk 必须出现在 citations 中",
                    enabled=True,
                    passed=False,
                    detail=f"上下文为空但引用非空: {[c.chunk_id for c in citations]}",
                )
            return RuleResult(
                rule_id="R5_context_fully_cited",
                name="上下文无遗漏引用",
                description="进入 LLM 上下文的全部 chunk 必须出现在 citations 中",
                enabled=True,
                passed=True,
                detail="上下文为空",
            )
        cited_ids = {c.chunk_id for c in citations}
        missing = [
            cid for cid in context_chunk_ids
            if cid is not None and cid not in cited_ids
        ]
        # 数量一致性：citations 不允许凭空多出未进入上下文的引用
        extra = [c.chunk_id for c in citations if c.chunk_id not in set(context_chunk_ids)]
        passed = not missing and not extra
        return RuleResult(
            rule_id="R5_context_fully_cited",
            name="上下文无遗漏引用",
            description="所有用于生成回答的 chunk 均可完整溯源，无遗漏、无凭空引用",
            enabled=True,
            passed=passed,
            detail=(
                f"上下文缺失引用: {missing}; 引用中凭空多出: {extra}"
                if missing or extra else ""
            ),
        )
