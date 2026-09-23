from __future__ import annotations

import json
from pathlib import Path

import pytest

from public_kb.normalize import (
    BOOK_PROFILES,
    detect_book_profile,
    normalize_content_list,
    render_normalized_markdown,
)


def _shape(blocks):
    return [(b.kind, b.level, b.text, b.page_idx) for b in blocks]


def test_book2_joins_split_chapter_and_section_and_recognizes_unlevelled_question():
    items = [
        {"type": "text", "text": "封面", "page_idx": 0, "text_level": 1},
        {"type": "text", "text": "第一章", "page_idx": 6},
        {"type": "text", "text": "政府采购", "page_idx": 6, "text_level": 1},
        {"type": "text", "text": "第一节", "page_idx": 7, "text_level": 1},
        {"type": "text", "text": "政府采购概述", "page_idx": 7, "text_level": 1},
        {"type": "text", "text": "1.什么是采购？", "page_idx": 7},
        {"type": "text", "text": "答：采购是取得资源的活动。", "page_idx": 7},
    ]

    blocks = normalize_content_list(items, BOOK_PROFILES["book2"])

    assert _shape(blocks) == [
        ("heading", 1, "第一章 政府采购", 6),
        ("heading", 2, "第一节 政府采购概述", 7),
        ("heading", 3, "1.什么是采购？", 7),
        ("text", None, "答：采购是取得资源的活动。", 7),
    ]


def test_book1_discards_directory_and_demotes_false_headings():
    items = [
        {"type": "text", "text": "第四节 联合体投标", "page_idx": 8, "text_level": 1},
        {"type": "text", "text": "第一章 招标投标基础知识", "page_idx": 15, "text_level": 1},
        {"type": "text", "text": "正文加粗但没有编号", "page_idx": 15, "text_level": 1},
        {"type": "text", "text": "这是正文内容，必须保留下来用于检索。", "page_idx": 15},
    ]

    blocks = normalize_content_list(items, BOOK_PROFILES["book1"])

    assert _shape(blocks) == [
        ("heading", 1, "第一章 招标投标基础知识", 15),
        ("text", None, "正文加粗但没有编号", 15),
        ("text", None, "这是正文内容，必须保留下来用于检索。", 15),
    ]


def test_book3_splits_article_heading_from_body_and_drops_total_directory():
    items = [
        {"type": "text", "text": "总目录", "page_idx": 5, "text_level": 1},
        {"type": "text", "text": "一、招标投标", "page_idx": 6, "text_level": 1},
        {"type": "text", "text": "一、 招标投标", "page_idx": 12, "text_level": 1},
        {"type": "text", "text": "中华人民共和国招标投标法", "page_idx": 14, "text_level": 1},
        {
            "type": "text",
            "text": "第四十一条【中标人的投标应符合的条件】中标人的投标应当符合下列条件之一：",
            "page_idx": 17,
            "text_level": 1,
        },
    ]

    blocks = normalize_content_list(items, BOOK_PROFILES["book3"])

    assert _shape(blocks) == [
        ("heading", 1, "一、 招标投标", 12),
        ("heading", 2, "中华人民共和国招标投标法", 14),
        ("heading", 4, "第四十一条【中标人的投标应符合的条件】", 17),
        ("text", None, "中标人的投标应当符合下列条件之一：", 17),
    ]


def test_table_caption_and_footnote_are_preserved_as_table_image_metadata():
    items = [
        {"type": "text", "text": "第一章 正文", "page_idx": 15},
        {
            "type": "table",
            "img_path": "images/table.jpg",
            "table_caption": ["建设项目名称："],
            "table_footnote": ["注：情况说明可附另页。"],
            "page_idx": 16,
        },
    ]

    blocks = normalize_content_list(items, BOOK_PROFILES["book1"])

    assert blocks[-1].kind == "text"
    assert blocks[-1].content_type == "table_image"
    assert blocks[-1].text == "建设项目名称：\n注：情况说明可附另页。"
    assert blocks[-1].source_path == "images/table.jpg"


def test_long_sentence_never_becomes_heading_even_if_mineru_marked_it():
    sentence = "这是一条跨页断句形成的长句，它不应进入面包屑，而应当作为普通正文继续保留。"
    items = [
        {"type": "text", "text": "第一章 总则", "page_idx": 15},
        {"type": "text", "text": sentence, "page_idx": 16, "text_level": 1},
    ]

    blocks = normalize_content_list(items, BOOK_PROFILES["book1"])

    assert blocks[-1].kind == "text"
    assert blocks[-1].text == sentence


def test_book1_arabic_numbered_body_item_is_not_promoted_to_heading():
    numbered_body = "3.设置的投标人资格条件应当结合项目实际并保持合理。"
    items = [
        {"type": "text", "text": "第一章 总则", "page_idx": 15},
        {"type": "text", "text": numbered_body, "page_idx": 16, "text_level": 1},
    ]

    blocks = normalize_content_list(items, BOOK_PROFILES["book1"])

    assert blocks[-1].kind == "text"
    assert blocks[-1].text == numbered_body


def test_rendered_markdown_has_no_page_comments_and_uses_normalized_levels():
    items = [
        {"type": "text", "text": "第一章", "page_idx": 6},
        {"type": "text", "text": "政府采购", "page_idx": 6},
        {"type": "text", "text": "第一节", "page_idx": 7},
        {"type": "text", "text": "政府采购概述", "page_idx": 7},
        {"type": "text", "text": "1.什么是采购？", "page_idx": 7},
        {"type": "text", "text": "答：采购是取得资源的活动。", "page_idx": 7},
    ]

    markdown = render_normalized_markdown(
        normalize_content_list(items, BOOK_PROFILES["book2"])
    )

    assert markdown == (
        "# 第一章 政府采购\n\n"
        "## 第一节 政府采购概述\n\n"
        "### 1.什么是采购？\n\n"
        "答：采购是取得资源的活动。\n"
    )
    assert "<!--" not in markdown


def test_profile_detection_uses_book_directory_name():
    assert detect_book_profile("政府采购、工程招标、投标与评标1200问（第3版）").key == "book2"
    with pytest.raises(ValueError, match="无法识别"):
        detect_book_profile("未知书籍")


def test_numbered_prefix_paragraph_stays_body_text():
    merged = (
        "（一）关于使用国有资金的项目。16号令第二条第（一）项中“预算资金”，"
        "是指《预算法》规定的预算资金，包括一般公共预算和一般公共预算管理的政府性基金。"
    )
    items = [
        {"type": "text", "text": "第一章 总则", "page_idx": 15},
        {"type": "text", "text": merged, "page_idx": 16, "text_level": 1},
    ]

    blocks = normalize_content_list(items, BOOK_PROFILES["book1"])

    assert [(b.kind, b.text) for b in blocks][1] == ("text", merged)


def test_book3_numbered_prefix_paragraph_stays_body_text():
    merged = (
        "(三)违反决策程序和规定，决定药品集中采购重大事项的；"
        "(四)违法违规进行行政委托,或者设置不合理条件限制医疗机构参加采购活动。"
    )
    items = [
        {"type": "text", "text": "一、 招标投标", "page_idx": 12, "text_level": 1},
        {"type": "text", "text": merged, "page_idx": 345, "text_level": 1},
    ]

    blocks = normalize_content_list(items, BOOK_PROFILES["book3"])

    assert blocks[-1].kind == "text"
    assert blocks[-1].text == merged
    assert blocks[-1].page_idx == 345


def test_book2_glued_form_template_is_not_a_question_heading():
    template = (
        "1. 根据已收到 （项目名称） （项目编号）的招标文件，遵照《中华人民共和国招标投标法》"
        "及有关规定，我方愿以人民币报价承担本标段货物的供货、运输及安装任务，工期为90日历天。"
    )
    items = [
        {"type": "text", "text": "第一章", "page_idx": 6},
        {"type": "text", "text": "政府采购", "page_idx": 6, "text_level": 1},
        {"type": "text", "text": template, "page_idx": 317, "text_level": 1},
    ]

    blocks = normalize_content_list(items, BOOK_PROFILES["book2"])

    assert [(b.kind, b.text) for b in blocks] == [
        ("heading", "第一章 政府采购"),
        ("text", template),
    ]


def test_book3_splits_multiple_articles_merged_into_one_block():
    body_a = "中标人的投标应当符合下列条件之一：能够最大限度地满足招标文件中规定的各项综合评价标准。"
    body_b = "评标委员会应当按照招标文件确定的评标标准和方法，对投标文件进行评审和比较。"
    items = [
        {"type": "text", "text": "一、 招标投标", "page_idx": 12, "text_level": 1},
        {"type": "text", "text": "中华人民共和国招标投标法", "page_idx": 14, "text_level": 1},
        {"type": "text", "text": "第一章 总则", "page_idx": 14, "text_level": 1},
        {
            "type": "text",
            "text": f"第四十一条【中标条件】{body_a}第四十八条【评标方法】{body_b}",
            "page_idx": 17,
            "text_level": 1,
        },
    ]

    blocks = normalize_content_list(items, BOOK_PROFILES["book3"])

    assert _shape(blocks) == [
        ("heading", 1, "一、 招标投标", 12),
        ("heading", 2, "中华人民共和国招标投标法", 14),
        ("heading", 3, "第一章 总则", 14),
        ("heading", 4, "第四十一条【中标条件】", 17),
        ("text", None, body_a, 17),
        ("heading", 4, "第四十八条【评标方法】", 17),
        ("text", None, body_b, 17),
    ]


def test_book3_article_without_label_is_not_split_inside_prose():
    quoted = (
        "《招标投标法实施条例》第二条规定，招标投标法第三条规定"
        "的工程建设项目中的工程，是指构成工程建设的建筑物、构筑物以及附属设施。"
    )
    items = [
        {"type": "text", "text": "一、 招标投标", "page_idx": 12, "text_level": 1},
        {"type": "text", "text": quoted, "page_idx": 20},
    ]

    blocks = normalize_content_list(items, BOOK_PROFILES["book3"])

    assert blocks[-1].kind == "text"
    assert blocks[-1].text == quoted


def test_chapter_title_with_glued_next_heading_stays_body_text():
    glued = "第一章 招标投标基础知识第一节 招标投标概述一、招标投标的起源与发展"
    items = [
        {"type": "text", "text": glued, "page_idx": 15, "text_level": 1},
        {"type": "text", "text": "真实正文，必须保留。" * 8, "page_idx": 15},
    ]

    blocks = normalize_content_list(items, BOOK_PROFILES["book1"])

    assert blocks[0].kind == "text"


@pytest.mark.parametrize(
    ("profile_key", "filename_fragment", "expected_min_headings"),
    [
        ("book1", "招标投标法律解读", 500),
        ("book2", "1200问", 900),
        ("book3", "法律法规全书", 700),
    ],
)
def test_real_content_lists_normalize_without_front_matter(
    profile_key, filename_fragment, expected_min_headings
):
    root = Path(__file__).resolve().parents[1] / "DATA" / "raw_data"
    matches = [
        p for p in root.rglob("*_content_list.json")
        if filename_fragment in p.name
    ]
    if not matches:
        pytest.skip("本机未提供 DATA 三本书")
    assert len(matches) == 1
    items = json.loads(matches[0].read_text(encoding="utf-8"))

    blocks = normalize_content_list(items, BOOK_PROFILES[profile_key])
    headings = [b for b in blocks if b.kind == "heading"]

    assert len(headings) >= expected_min_headings
    assert min(b.page_idx for b in blocks) >= BOOK_PROFILES[profile_key].body_start_page
    assert not any("本电子版仅限用于" in b.text for b in blocks)
    assert not any(b.text.strip() in {"总目录", "目录", "目 录"} for b in blocks)
