"""M4 清洗规则保护条款号的单元测试。"""

from __future__ import annotations

from public_kb.ingestion.transforms.cleaner import TextCleaner


# ── 短行白名单：第X条/第X章 不被删 ────────────────────────

# 正文需 >10 字符，避免被既有"短行(<10)删除"规则正常清理，混淆断言目标
_BODY = "本条正文内容足够长，确保可以通过既有清洗规则。"


def test_legal_heading_short_line_is_kept():
    raw = f"第一条\n{_BODY}\n"
    cleaned = TextCleaner.clean(raw)
    assert "第一条" in cleaned
    assert _BODY in cleaned


def test_legal_heading_with_md_prefix_is_kept():
    raw = f"## 第一条\n{_BODY}\n"
    cleaned = TextCleaner.clean(raw)
    assert "第一条" in cleaned


def test_legal_chapter_heading_is_kept():
    raw = f"第一章 总则\n{_BODY}\n"
    cleaned = TextCleaner.clean(raw)
    assert "第一章 总则" in cleaned


def test_noise_short_line_is_still_removed():
    # 非法律标题的 8 字符短噪声行仍应被删除（原行为不变）
    raw = f"xx噪声行\n{_BODY}\n"
    cleaned = TextCleaner.clean(raw)
    assert "xx噪声行" not in cleaned
    assert _BODY in cleaned


def test_plain_number_line_is_still_removed():
    # 纯页码行仍被删除（原行为不变）
    raw = f"12\n{_BODY}\n"
    cleaned = TextCleaner.clean(raw)
    assert "12" not in cleaned


def test_page_number_after_heading_is_still_removed():
    raw = f"## 第一条\n12\n{_BODY}\n"
    cleaned = TextCleaner.clean(raw)
    assert "第一条" in cleaned
    assert "12" not in str(cleaned.split("第一条")[1])  # 页码行被删，正文保留


# ── 页眉去重豁免：章节标题页眉不被删 ──────────────────────

def test_repeating_legal_heading_header_is_preserved():
    # 章节标题作为页眉反复出现（>=5 次）→ 不因"重复行"被删除
    header = "第一编 总则"
    raw = "\n".join([header] * 6 + [_BODY])
    cleaned = TextCleaner.clean(raw)
    assert header in cleaned  # 章节标题被保留


def test_repeating_noise_header_is_still_removed():
    # 非法律标题的普通页眉反复出现（>=5 次）→ 仍被删除（原行为不变）
    header = "机械工业出版社"
    raw = "\n".join([header] * 6 + [_BODY])
    cleaned = TextCleaner.clean(raw)
    assert header not in cleaned
    assert _BODY in cleaned