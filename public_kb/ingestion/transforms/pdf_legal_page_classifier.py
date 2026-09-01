# 功能：法规 PDF 页面三档分类器（三档路由 T1）。
"""
Legal-page classifier for tiered PDF routing (T1).

输入 PageProfile，输出 PageRouteDecision（含 tier / label / parser / 置信度 /
reason），对应三档路由计划 §6 T1 的分类输出：

  text            单栏文本页                        → Tier A  fast_text
  two_col_text    双栏条文页（列序置信度达标）        → Tier A  fast_text（含 reflow）
  table_regular   规整有框表格页                    → Tier B  table_extractor
  table_complex   无框/嵌套/跨页/图片密集表格页       → Tier C  mineru
  visual_or_scan  无文本层/图片占比过高              → Tier C  mineru
  uncertain       特征冲突或置信度低                 → Tier C  mineru

原则（对齐三档计划）：
  1. uncertain 一律 Tier C；
  2. 双栏只有列序置信度达标才进 Tier A；
  3. 存在图片密集/公式/扫描/低文本特征时不进 Tier A；
  4. 分类结果只作路由依据，不强制下游消费。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from .pdf_page_profile import PageProfile


@dataclass(frozen=True)
class PageRouteDecision:
    """一页的路由决定（三档路由计划 §5.2 新增中间结构）。"""

    page_idx: int
    page_label: str          # text / two_col_text / table_regular / ...
    tier: str                # A / B / C
    reason: str
    confidence: float
    parser: str              # fast_text / table_extractor / mineru
    features: dict


_TIER_A = "A"
_TIER_B = "B"
_TIER_C = "C"

_PARSER_FAST = "fast_text"
_PARSER_TABLE = "table_extractor"
_PARSER_MINERU = "mineru"


class LegalPageClassifier:
    """把 PageProfile 分为三档路由的页面级分类器。"""

    def __init__(
        self,
        *,
        min_text_chars: int = 50,
        image_area_ratio: float = 0.35,
        two_col_confidence: float = 0.80,
        table_min_lines: int = 5,
    ) -> None:
        self._min_text_chars = min_text_chars
        self._image_area_ratio = image_area_ratio
        self._two_col_confidence = two_col_confidence
        self._table_min_lines = table_min_lines

    def classify(self, profile: PageProfile) -> PageRouteDecision:
        """对单页画像分类。"""
        features = {
            "text_chars": profile.text_chars,
            "img_ratio": profile.img_ratio,
            "two_col_gap": profile.two_col_gap,
            "table_hlines": profile.table_hlines,
            "table_vlines": profile.table_vlines,
            "formula_hint": profile.formula_hint,
        }

        # 1) 低文本 / 图片密集 / 公式 → Tier C
        if profile.text_chars < self._min_text_chars:
            return self._decide(
                profile, "visual_or_scan", _TIER_C, _PARSER_MINERU,
                "文本量不足，疑似扫描/空白页", 0.95, features,
            )
        if profile.img_ratio > self._image_area_ratio:
            return self._decide(
                profile, "visual_or_scan", _TIER_C, _PARSER_MINERU,
                "内容图片占比过高", 0.90, features,
            )
        if profile.formula_hint:
            return self._decide(
                profile, "uncertain", _TIER_C, _PARSER_MINERU,
                "含公式字体特征", 0.80, features,
            )

        # 2) 表格页
        if profile.table_candidate:
            if self._is_regular_table(profile):
                return self._decide(
                    profile, "table_regular", _TIER_B, _PARSER_TABLE,
                    "检测到规整有框表格", 0.85, features,
                )
            return self._decide(
                profile, "table_complex", _TIER_C, _PARSER_MINERU,
                "无框/复杂表格候选", 0.85, features,
            )

        # 3) 双栏页（列序置信度达标才进 Tier A）
        if profile.has_two_col:
            confidence = self._two_col_confidence_score(profile)
            if confidence >= self._two_col_confidence:
                return self._decide(
                    profile, "two_col_text", _TIER_A, _PARSER_FAST,
                    "双栏条文页，列序置信度达标", confidence, features,
                )
            return self._decide(
                profile, "uncertain", _TIER_C, _PARSER_MINERU,
                "双栏列序置信度不足", confidence, features,
            )

        # 4) 单栏文本页
        return self._decide(
            profile, "text", _TIER_A, _PARSER_FAST,
            "单栏纯文本页", 0.90, features,
        )

    @staticmethod
    def _is_regular_table(profile: PageProfile) -> bool:
        """有框且行列线索明显 → 规整表格；否则视为复杂表格。"""
        return profile.table_hlines >= 2 and profile.table_vlines >= 2

    @staticmethod
    def _two_col_confidence_score(profile: PageProfile) -> float:
        """估算双栏列序置信度。

        判据取「块平衡」与「弱侧文本块数」的较大者：
          - 块平衡：两侧块数接近 → 高分（干净的 2+2 双栏）；
          - 弱侧块数：OCR 双栏页一侧常被合并成少而大的块、另一侧拆成多而
            小的块（实测 book2 左 8~10 / 右 45~51），块数平衡失效；但弱侧
            仍有 ≥4 块即说明两侧都有成块文本 → 高分；
          - 正文 + 边注/侧栏布局：弱侧只有 1-2 块，两项都不满足 → 低分 → C。
        """
        split_x = profile.two_col_split_x
        left = sum(1 for x in profile.x_starts if x < split_x)
        right = profile.n_blocks - left
        balance = 1.0 - abs(left - right) / max(1, profile.n_blocks)
        side_min = min(left, right)
        return round(
            max(
                0.55 + 0.4 * balance,
                0.55 + 0.4 * min(1.0, side_min / 5.0),
            ),
            3,
        )

    @staticmethod
    def _decide(
        profile: PageProfile,
        label: str,
        tier: str,
        parser: str,
        reason: str,
        confidence: float,
        features: dict,
    ) -> PageRouteDecision:
        return PageRouteDecision(
            page_idx=profile.page_idx,
            page_label=label,
            tier=tier,
            reason=reason,
            confidence=round(confidence, 3),
            parser=parser,
            features=features,
        )


def classify_page(
    profile: PageProfile, classifier: LegalPageClassifier | None = None
) -> PageRouteDecision:
    """便捷函数：用（可选传入的）分类器对单页画像分类。"""
    return (classifier or LegalPageClassifier()).classify(profile)
