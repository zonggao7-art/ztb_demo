"""Normalize MinerU ``content_list.json`` blocks for the three book corpus.

The MinerU heading level is deliberately treated as a hint only.  Structure is
recovered from the numbering systems used by the books, while every emitted
block keeps its source page for later citation metadata.

Numbering prefixes are necessary but not sufficient: MinerU frequently glues
several paragraphs (or several legal articles) into one block, so a line that
*starts* with ``一、`` / ``（一）`` / ``第X章`` can still be prose.  Every heading
rule therefore carries a prose guard, otherwise long paragraphs get absorbed
into breadcrumbs and silently dropped when they have no body.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Iterable, Mapping, Sequence


_CN_NUM = r"一二三四五六七八九十百千万〇零两0-9"
_CN_HEADING_NUM = r"一二三四五六七八九十百千万〇零两"
_CHAPTER_RE = re.compile(rf"^(第[{_CN_NUM}]+章)\s*(.*)$")
_SECTION_RE = re.compile(rf"^(第[{_CN_NUM}]+节)\s*(.*)$")
_PART_RE = re.compile(rf"^(第[{_CN_NUM}]+[编篇])\s*(.*)$")
_ARTICLE_RE = re.compile(rf"^(第[{_CN_NUM}]+条)\s*(【[^】]+】)?\s*(.*)$")
_CN_LEVEL_ONE_RE = re.compile(rf"^[{_CN_HEADING_NUM}]+[、.]\s*\S")
_CN_LEVEL_TWO_RE = re.compile(rf"^[（(][{_CN_HEADING_NUM}]+[）)]\s*\S")
_QUESTION_RE = re.compile(r"^\s*\d{1,4}\s*[.．、]\s*\S")
_HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
# 名称部分里再出现下一层级标记，说明这是目录条目或多段粘连正文
_NESTED_MARKER_RE = re.compile(rf"第[{_CN_NUM}]+[章节日]")
# 书3 合并块内部的下一条带条旨法条（前面必须是句末标点）
_INTERIOR_ARTICLE_RE = re.compile(
    rf"(?<=[。；;])\s*(第[{_CN_NUM}]+条\s*【[^】]{{1,40}}】)"
)

_DIRECTORY_MARKERS = {"目录", "目 录", "总目录"}
_FORBIDDEN_MARKERS = (
    "本电子版仅限用于个人学习与研究",
    "图书在版编目(CIP)数据",
    "版权信息",
)
_BOOK3_TOP_LEVELS = {"一、招标投标", "一、 招标投标", "二、政府采购", "二、 政府采购"}
_LEGAL_TITLE_SUFFIXES = (
    "法", "条例", "规定", "办法", "规则", "意见", "通知", "公告", "解释", "决定",
)

# 反向规则阈值：编号前缀之后仍要满足的“标题体量”约束
_MAX_STRUCT_HEADING_CHARS = 60    # 一、/（一）类小节标题
_MAX_BOOK3_ITEM_CHARS = 40        # 书3 子项标题（OCR 粘连条目普遍偏长）
_MAX_SECTION_NAME_CHARS = 40      # 第X章/节/编 的名称部分
_MAX_LEGAL_TITLE_CHARS = 80       # 书3 法规名
_MAX_QUESTION_CHARS = 220         # 书2 问句标题（须以 ？ 结尾）
_MAX_PLAIN_ITEM_CHARS = 40        # 书2 非问句结尾的编号标题


@dataclass(frozen=True)
class BookProfile:
    """Book-specific normalization policy."""

    key: str
    doc_name: str
    filename_markers: tuple[str, ...]
    body_start_page: int
    qa_mode: bool = False


@dataclass(frozen=True)
class SourceBlock:
    """A normalized source block with page provenance."""

    kind: str
    text: str
    page_idx: int
    level: int | None
    content_type: str
    source_ordinal: int
    source_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


BOOK_PROFILES: dict[str, BookProfile] = {
    "book1": BookProfile(
        key="book1",
        doc_name="招标投标法律解读与风险防范实务",
        filename_markers=("招标投标法律解读", "白如银"),
        body_start_page=15,
    ),
    "book2": BookProfile(
        key="book2",
        doc_name="政府采购、工程招标、投标与评标1200问（第3版）",
        filename_markers=("1200问", "刘海桑"),
        body_start_page=6,
        qa_mode=True,
    ),
    "book3": BookProfile(
        key="book3",
        doc_name="中华人民共和国招标投标法律法规全书：含相关政策",
        filename_markers=("法律法规全书", "法规中心"),
        body_start_page=12,
    ),
}


def detect_book_profile(path_or_name: str) -> BookProfile:
    """Identify one of the supported books from a path or filename."""

    matches = [
        profile
        for profile in BOOK_PROFILES.values()
        if any(marker in path_or_name for marker in profile.filename_markers)
    ]
    if len(matches) != 1:
        raise ValueError(f"无法识别三本书配置: {path_or_name}")
    return matches[0]


def _clean_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = _HTML_COMMENT_RE.sub("", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def _compact_len(text: str) -> int:
    return len(re.sub(r"\s+", "", text))


def _has_internal_terminator(text: str) -> bool:
    """句末标点出现在非末尾位置 => 这是段落而不是标题。"""
    return any(marker in text.strip()[:-1] for marker in ("。", "！", "？", "；", ";"))


def _is_prose(text: str, limit: int = _MAX_STRUCT_HEADING_CHARS) -> bool:
    stripped = text.strip()
    return _compact_len(stripped) > limit or _has_internal_terminator(stripped)


def _is_forbidden(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    return compact in {re.sub(r"\s+", "", marker) for marker in _DIRECTORY_MARKERS} or any(
        marker in text for marker in _FORBIDDEN_MARKERS
    )


def _is_joinable_title(text: str) -> bool:
    if not text or len(text) > 80:
        return False
    if text in _DIRECTORY_MARKERS or _QUESTION_RE.match(text):
        return False
    if any(regex.match(text) for regex in (_CHAPTER_RE, _SECTION_RE, _PART_RE, _ARTICLE_RE)):
        return False
    if text.endswith(("。", "；", "？", "?", "！", "!", "：", ":")):
        return False
    return True


def _legal_title(text: str, item: Mapping[str, Any]) -> bool:
    if item.get("text_level") != 1 or not 2 <= len(text) <= _MAX_LEGAL_TITLE_CHARS:
        return False
    if _is_prose(text, limit=_MAX_LEGAL_TITLE_CHARS):
        return False
    compact = re.sub(r"\s+", "", text)
    if compact.endswith(_LEGAL_TITLE_SUFFIXES):
        return True
    return compact.startswith("中华人民共和国") and any(
        suffix in compact for suffix in _LEGAL_TITLE_SUFFIXES
    )


def _numbered_section_heading(
    match: re.Match[str], level: int
) -> tuple[int, str, str] | None:
    """`第X章/节/编`：名称过长、含句末标点或粘连下一级标记时按正文处理。"""
    name = (match.group(2) or "").strip()
    if _compact_len(name) > _MAX_SECTION_NAME_CHARS:
        return None
    if name and _is_prose(name, limit=_MAX_SECTION_NAME_CHARS):
        return None
    if _NESTED_MARKER_RE.search(name):
        return None
    heading = " ".join(part for part in match.groups() if part).strip()
    return level, heading, ""


def _heading_level(
    text: str,
    item: Mapping[str, Any],
    profile: BookProfile,
) -> tuple[int, str, str] | None:
    """Return ``(level, heading, remainder)`` for a structural line."""

    chapter_level = 3 if profile.key == "book3" else 1
    section_level = 4 if profile.key == "book3" else 2

    part = _PART_RE.match(text)
    if part:
        return _numbered_section_heading(part, 1)

    chapter = _CHAPTER_RE.match(text)
    if chapter:
        return _numbered_section_heading(chapter, chapter_level)

    section = _SECTION_RE.match(text)
    if section:
        return _numbered_section_heading(section, section_level)

    if profile.key == "book2":
        # 1200问只承认“编号 + 问句”为标题；一、/（一）类在该书里是正文。
        if not _QUESTION_RE.match(text):
            return None
        stripped = text.strip()
        if stripped.endswith(("？", "?")):
            if _compact_len(stripped) <= _MAX_QUESTION_CHARS:
                return 3, stripped, ""
            return None
        if not _is_prose(stripped, limit=_MAX_PLAIN_ITEM_CHARS):
            return 3, stripped, ""
        return None

    if profile.key == "book3":
        compact = re.sub(r"\s+", "", text)
        if text in _BOOK3_TOP_LEVELS or compact in {"一、招标投标", "二、政府采购"}:
            prefix, name = re.split(r"、\s*", text, maxsplit=1)
            return 1, f"{prefix}、 {name}", ""
        if _legal_title(text, item):
            return 2, text, ""
        article = _ARTICLE_RE.match(text)
        if article:
            number, purpose, remainder = article.groups()
            return 4, f"{number}{purpose or ''}", remainder.strip()
        if _CN_LEVEL_ONE_RE.match(text):
            return None if _is_prose(text, _MAX_BOOK3_ITEM_CHARS) else (4, text, "")
        if _CN_LEVEL_TWO_RE.match(text):
            return None if _is_prose(text, _MAX_BOOK3_ITEM_CHARS) else (5, text, "")
        return None

    if _CN_LEVEL_ONE_RE.match(text):
        return None if _is_prose(text) else (3, text, "")
    if _CN_LEVEL_TWO_RE.match(text):
        return None if _is_prose(text) else (4, text, "")
    return None


def _expand_interior_articles(text: str) -> list[str]:
    """Split a merged block at internal `第X条【条旨】` boundaries（书3）。"""
    pieces = _INTERIOR_ARTICLE_RE.split(text)
    if len(pieces) <= 1:
        return [text]
    parts: list[str] = [pieces[0]]
    for index in range(1, len(pieces), 2):
        tail = pieces[index + 1] if index + 1 < len(pieces) else ""
        parts.append(pieces[index] + tail)
    return [cleaned for cleaned in (part.strip() for part in parts) if cleaned]


def _table_text(item: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("table_caption", "table_footnote"):
        value = item.get(key) or []
        if isinstance(value, str):
            value = [value]
        parts.extend(_clean_text(part) for part in value if _clean_text(part))
    return "\n".join(parts)


def normalize_content_list(
    items: Sequence[Mapping[str, Any]],
    profile: BookProfile,
) -> list[SourceBlock]:
    """Normalize MinerU blocks into deterministic, page-aware source blocks."""

    emitted: list[SourceBlock] = []
    state = {"ordinal": 0}
    index = 0

    def emit_heading(level: int, heading: str, page_idx: int, remainder: str) -> None:
        emitted.append(SourceBlock(
            kind="heading",
            text=heading.strip(),
            page_idx=page_idx,
            level=level,
            content_type="heading",
            source_ordinal=state["ordinal"],
        ))
        state["ordinal"] += 1
        if remainder:
            emit_text(remainder, page_idx)

    def emit_text(
        text: str, page_idx: int, content_type: str = "text", source_path: str = ""
    ) -> None:
        emitted.append(SourceBlock(
            kind="text",
            text=text,
            page_idx=page_idx,
            level=None,
            content_type=content_type,
            source_ordinal=state["ordinal"],
            source_path=source_path,
        ))
        state["ordinal"] += 1

    def classify(text: str, item: Mapping[str, Any], page_idx: int) -> None:
        structural = _heading_level(text, item, profile)
        if structural:
            level, heading, remainder = structural
            emit_heading(level, heading, page_idx, remainder)
        else:
            emit_text(text, page_idx)

    def join_book2_numbered_title(
        text: str, page_idx: int
    ) -> tuple[str, int] | None:
        """书2 章号/节号与名称常被切成两块，合并后返回新的文本与消费步长。"""
        numbered = _CHAPTER_RE.match(text) or _SECTION_RE.match(text)
        if not numbered or numbered.group(2).strip():
            return None
        if index + 1 >= len(items):
            return None
        next_item = items[index + 1]
        next_text = _clean_text(next_item.get("text", ""))
        next_page = int(next_item.get("page_idx", page_idx) or page_idx)
        if str(next_item.get("type", "text")) != "text":
            return None
        if next_page < profile.body_start_page or not _is_joinable_title(next_text):
            return None
        return f"{numbered.group(1)} {next_text}", 2

    while index < len(items):
        item = items[index]
        try:
            page_idx = int(item.get("page_idx", -1))
        except (TypeError, ValueError):
            page_idx = -1
        if page_idx < profile.body_start_page:
            index += 1
            continue

        item_type = str(item.get("type", "text"))
        if item_type == "table":
            text = _table_text(item)
            if text:
                emit_text(
                    text,
                    page_idx,
                    content_type="table_image",
                    source_path=str(item.get("img_path", "")),
                )
            index += 1
            continue
        if item_type != "text":
            index += 1
            continue

        text = _clean_text(item.get("text", ""))
        if not text or _is_forbidden(text):
            index += 1
            continue

        if profile.key == "book2":
            joined = join_book2_numbered_title(text, page_idx)
            if joined:
                text, step = joined
                index += step
                classify(text, item, page_idx)
                continue

        if profile.key == "book3":
            for piece in _expand_interior_articles(text):
                classify(piece, item, page_idx)
            index += 1
            continue

        classify(text, item, page_idx)
        index += 1

    return emitted


def render_normalized_markdown(blocks: Iterable[SourceBlock]) -> str:
    """Render normalized blocks without embedding provenance in the text."""

    parts: list[str] = []
    for block in blocks:
        if block.kind == "heading":
            parts.append(f"{'#' * int(block.level or 1)} {block.text}")
        else:
            parts.append(block.text)
    if not parts:
        return ""
    return "\n\n".join(parts).rstrip() + "\n"
