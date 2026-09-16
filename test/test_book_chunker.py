from __future__ import annotations

from public_kb.chunk_ids import compute_chunk_uid
from public_kb.chunker import SemanticChunker
from public_kb.normalize import SourceBlock
from public_kb.text_cleaner import TextCleaner


def _h(text: str, page: int, level: int, ordinal: int) -> SourceBlock:
    return SourceBlock("heading", text, page, level, "heading", ordinal)


def _t(text: str, page: int, ordinal: int, content_type: str = "text") -> SourceBlock:
    return SourceBlock("text", text, page, None, content_type, ordinal)


def test_cleaner_strips_html_comments_without_removing_surrounding_text():
    raw = "# 第一章 总则\n\n这是正文前半部分。<!-- page: 12 -->这是正文后半部分。"

    cleaned = TextCleaner.clean(raw)

    assert "<!--" not in cleaned
    assert "这是正文前半部分。这是正文后半部分。" in cleaned


def test_chunk_blocks_packs_short_qa_units_and_keeps_questions_with_answers():
    blocks = [
        _h("第一章 政府采购", 6, 1, 0),
        _h("第一节 政府采购概述", 7, 2, 1),
        _h("1.什么是采购？", 7, 3, 2),
        _t("答：采购是取得资源的活动。", 7, 3),
        _h("2.什么是政府采购？", 8, 3, 4),
        _t("答：政府采购是使用财政性资金开展的采购活动。", 8, 5),
    ]

    docs = SemanticChunker(max_chars=2000, overlap_chars=100, min_chars=200).chunk_blocks(
        blocks, "book2", qa_mode=True
    )

    assert len(docs) == 1
    text = docs[0].page_content
    assert "1.什么是采购？\n答：采购是取得资源的活动。" in text
    assert "2.什么是政府采购？\n答：政府采购是使用财政性资金开展的采购活动。" in text
    assert docs[0].metadata["chapter"] == "第一章 政府采购 > 第一节 政府采购概述"
    assert text.startswith("【第一章 政府采购 > 第一节 政府采购概述】")


def test_chunk_blocks_keeps_article_heading_and_first_sentence_together():
    blocks = [
        _h("一、 招标投标", 12, 1, 0),
        _h("中华人民共和国招标投标法", 14, 2, 1),
        _h("第一章 总则", 14, 3, 2),
        _h("第一条【立法目的】", 14, 4, 3),
        _t("为了规范招标投标活动，制定本法。", 14, 4),
    ]

    docs = SemanticChunker(max_chars=2000, overlap_chars=100, min_chars=200).chunk_blocks(
        blocks, "book3"
    )

    assert len(docs) == 1
    assert "第一条【立法目的】\n为了规范招标投标活动，制定本法。" in docs[0].page_content
    assert docs[0].metadata["chapter"] == "一、 招标投标 > 中华人民共和国招标投标法 > 第一章 总则"


def test_short_units_from_different_subsections_pack_under_same_root_with_local_breadcrumbs():
    blocks = [
        _h("第一章 总则", 1, 1, 0),
        _h("第一节 范围", 1, 2, 1),
        _h("一、适用对象", 1, 3, 2),
        _t("甲类主体适用本规则。" * 8, 1, 3),
        _h("第二节 原则", 2, 2, 4),
        _h("一、公开原则", 2, 3, 5),
        _t("采购活动应当公开透明。" * 8, 2, 6),
    ]

    docs = SemanticChunker(max_chars=1000, overlap_chars=50, min_chars=200).chunk_blocks(
        blocks, "book"
    )

    assert len(docs) == 1
    assert docs[0].metadata["chapter"] == "第一章 总则"
    assert "【第一章 总则 > 第一节 范围】" in docs[0].page_content
    assert "【第一章 总则 > 第二节 原则】" in docs[0].page_content


def test_chunk_metadata_has_page_range_structural_id_uid_and_global_indexes():
    long_a = "甲条内容。" * 180
    long_b = "乙条内容。" * 180
    blocks = [
        _h("第一章 总则", 15, 1, 0),
        _h("一、适用范围", 15, 3, 1),
        _t(long_a, 15, 2),
        _h("二、基本原则", 16, 3, 3),
        _t(long_b, 17, 4),
    ]

    docs = SemanticChunker(max_chars=500, overlap_chars=20, min_chars=100).chunk_blocks(
        blocks, "book1"
    )

    assert len(docs) >= 4
    assert [d.metadata["chunk_index"] for d in docs] == list(range(len(docs)))
    assert all(d.metadata["page_start"] <= d.metadata["page_end"] for d in docs)
    assert all(d.metadata["structural_id"].startswith("book1|p") for d in docs)
    assert len({d.metadata["structural_id"] for d in docs}) == len(docs)
    assert all(d.metadata["chunk_uid"] == compute_chunk_uid(d.page_content, d.metadata) for d in docs)


def test_plain_markdown_chunking_uses_global_indexes_and_breadcrumb_prefix():
    markdown = "# 第一章 总则\n正文甲。\n## 第一节 范围\n正文乙。"

    docs = SemanticChunker(max_chars=2000, overlap_chars=100, min_chars=0).chunk(
        markdown, "sample.md"
    )

    assert [d.metadata["chunk_index"] for d in docs] == [0, 1]
    assert docs[0].page_content.startswith("【第一章 总则】")
    assert docs[1].page_content.startswith("【第一章 总则 > 第一节 范围】")


def test_empty_blocks_return_no_documents():
    assert SemanticChunker().chunk_blocks([], "empty") == []


def test_consecutive_headings_without_body_are_not_dropped():
    blocks = [
        _h("第一章 总则", 1, 1, 0),
        _h("一、适用范围", 1, 3, 1),
        _h("二、基本原则", 1, 3, 2),
        _h("三、公开透明", 2, 3, 3),
        _t("采购活动应当遵循公开、公平、公正原则。", 2, 4),
    ]

    docs = SemanticChunker(max_chars=2000, overlap_chars=100, min_chars=0).chunk_blocks(
        blocks, "book"
    )

    joined = "\n".join(doc.page_content for doc in docs)
    assert "一、适用范围" in joined
    assert "二、基本原则" in joined
    assert "三、公开透明" in joined
    assert "采购活动应当遵循公开、公平、公正原则。" in joined


def test_heading_rescued_after_parent_with_only_headings_below_it():
    blocks = [
        _h("第二章 招标", 5, 1, 0),
        _h("第一节 招标代理", 5, 2, 1),
        _h("一、委托招标的优势", 5, 3, 2),
        _t("委托招标可以借助专业机构力量。" * 20, 6, 3),
        _h("第二节 招标公告", 9, 2, 4),
    ]

    docs = SemanticChunker(max_chars=2000, overlap_chars=100, min_chars=0).chunk_blocks(
        blocks, "book"
    )

    joined = "\n".join(doc.page_content for doc in docs)
    assert "第二节 招标公告" in joined
    assert "一、委托招标的优势" in joined
