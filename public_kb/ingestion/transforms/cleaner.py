# 功能：清理 MinerU Markdown 噪声、公式和页眉页脚等干扰内容。
"""
文本清洗器 — 对 MinerU 产出的 Markdown 进行后处理去噪。

清洗规则（按序执行）：
1. 去除页眉页脚 —— 连续出现的重复短行（如每页顶部的书名/章节名）
2. 去除页码 —— 独立的纯数字行
3. 去除过短行 —— 长度 < 10 字符的孤立行
4. 压缩多余空行 —— 连续 3+ 空行压缩为 2 个
"""

from __future__ import annotations

import re
from collections import Counter


class TextCleaner:
    """Markdown 文本清洗器，输入原始 MD 文本，输出去噪后的干净文本。"""

    # 匹配独立的纯数字行（页码）
    _PAGE_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$")

    # 法律文本标题（任务 M4）：短行白名单 + 页眉去重豁免
    # 匹配 "第一章" "第X节" "第X条" "第X款" "第X项" 及其带 # 前缀的标题行。
    # 这些行即使很短、或反复出现（如页眉/页脚），也不得被清洗规则丢弃，
    # 否则会破坏章节结构（正文与页眉同文时连真实标题也会被误删）。
    _LEGAL_HEADING_RE = re.compile(
        r"^#{0,6}\s*第[一二三四五六七八九十百千\d]+[章节条款项部分编]"
    )

    @staticmethod
    def clean(raw_markdown: str) -> str:
        """对原始 Markdown 文本执行全链路清洗。

        Args:
            raw_markdown: MinerU 解析产出的原始 Markdown 字符串。

        Returns:
            清洗后的干净 Markdown 字符串。
        """
        lines: list[str] = raw_markdown.split("\n")

        # ── 步骤 1: 检测并移除重复出现的页眉行 ──
        lines = TextCleaner._remove_repeating_headers(lines)

        # ── 步骤 2: 移除页码行 ──
        lines = [
            line for line in lines
            if not TextCleaner._PAGE_NUMBER_RE.match(line)
        ]

        # ── 步骤 3: 移除过短行（< 10 字符）──
        # 保留 Markdown 标题行、分隔线，以及法律文本标题（第X章/第X条等，
        # 任务 M4：防止误删章节结构）
        lines = [
            line for line in lines
            if len(line.strip()) >= 10
            or line.strip().startswith("#")
            or line.strip() in ("---", "***", "___")
            or line.strip() == ""
            or TextCleaner._LEGAL_HEADING_RE.match(line.strip())
        ]

        # ── 步骤 4: 压缩连续空行 ──
        cleaned: list[str] = []
        blank_count = 0
        for line in lines:
            if line.strip() == "":
                blank_count += 1
                if blank_count <= 2:
                    cleaned.append(line)
            else:
                blank_count = 0
                cleaned.append(line)

        return "\n".join(cleaned)

    @staticmethod
    def _remove_repeating_headers(lines: list[str]) -> list[str]:
        """识别并移除在所有页面上重复出现的行（页眉/页脚）。

        算法：统计每一行的出现频次，若某行在文档中出现 >= 5 次
        且长度 < 80 字符，则认为它是页眉/页脚行，全部移除。
        任务 M4 豁免：合法法律文本标题（第X章/第X条等）即使反复出现
        （如章节标题页眉"第一章 总则"），也不被当作页眉删除，
        否则会破坏章节结构。
        """
        stripped = [line.strip() for line in lines]
        counter = Counter(
            s for s in stripped if s and len(s) < 80
        )
        repeated = {
            s for s, count in counter.items()
            if count >= 5 and not TextCleaner._LEGAL_HEADING_RE.match(s)
        }

        if not repeated:
            return lines

        return [
            line for line in lines
            if line.strip() not in repeated
        ]
