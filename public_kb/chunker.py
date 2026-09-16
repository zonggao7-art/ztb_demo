"""Page-aware semantic chunking for Markdown and normalized MinerU blocks."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable, List, Sequence

from langchain_core.documents import Document

from .chunk_ids import compute_chunk_uid
from .normalize import SourceBlock


@dataclass(frozen=True)
class _Unit:
    path: tuple[str, ...]
    group_path: tuple[str, ...]
    text: str
    page_start: int
    page_end: int
    content_type: str
    source_ordinals: tuple[int, ...]


class SemanticChunker:
    """Split semantic units, pack short siblings, and attach stable metadata."""

    _HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)")
    _SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；])[ \t]*")
    _QA_HEADING_RE = re.compile(r"^\s*\d{1,4}\s*[.．、]")
    _ARTICLE_HEADING_RE = re.compile(r"^第[一二三四五六七八九十百千万〇零两0-9]+条")

    def __init__(
        self,
        max_chars: int = 500,
        overlap_chars: int = 50,
        min_chars: int = 200,
    ) -> None:
        if max_chars <= 0:
            raise ValueError("max_chars 必须大于 0")
        if overlap_chars < 0 or overlap_chars >= max_chars:
            raise ValueError("overlap_chars 必须满足 0 <= overlap_chars < max_chars")
        if min_chars < 0 or min_chars > max_chars:
            raise ValueError("min_chars 必须满足 0 <= min_chars <= max_chars")
        self._max_chars = max_chars
        self._overlap_chars = overlap_chars
        self._min_chars = min_chars

    def chunk(self, markdown_text: str, doc_name: str) -> List[Document]:
        """Chunk ordinary Markdown while preserving the historical public API."""

        if not markdown_text.strip():
            return []
        blocks: list[SourceBlock] = []
        ordinal = 0
        for line in markdown_text.splitlines():
            heading = self._HEADING_RE.match(line)
            if heading:
                blocks.append(SourceBlock(
                    kind="heading",
                    text=heading.group(2).strip(),
                    page_idx=-1,
                    level=len(heading.group(1)),
                    content_type="heading",
                    source_ordinal=ordinal,
                ))
                ordinal += 1
                continue
            text = line.strip()
            if text:
                blocks.append(SourceBlock(
                    kind="text",
                    text=text,
                    page_idx=-1,
                    level=None,
                    content_type="text",
                    source_ordinal=ordinal,
                ))
                ordinal += 1
        return self.chunk_blocks(blocks, doc_name)

    def chunk_blocks(
        self,
        blocks: Sequence[SourceBlock],
        doc_name: str,
        *,
        qa_mode: bool = False,
    ) -> List[Document]:
        """Chunk normalized, page-aware source blocks.

        A heading plus its following body is atomic. Atomic siblings sharing a
        parent breadcrumb are packed toward ``max_chars``; this keeps Q&A and
        legal articles intact while avoiding hundreds of tiny chunks.
        """

        if not blocks:
            return []
        units = self._build_units(blocks, qa_mode=qa_mode)
        fragments = [fragment for unit in units for fragment in self._split_unit(unit)]
        packs = self._pack_units(fragments)
        return self._documents_from_packs(packs, doc_name)

    def _build_units(
        self,
        blocks: Sequence[SourceBlock],
        *,
        qa_mode: bool,
    ) -> list[_Unit]:
        stack: list[str] = []
        stack_pages: list[int] = []
        stack_ordinals: list[int] = []
        used: list[bool] = []
        current_path: tuple[str, ...] = ("前言",)
        heading_page = -1
        body: list[SourceBlock] = []
        units: list[_Unit] = []

        def flush() -> None:
            nonlocal body
            if not body:
                return
            leaf = current_path[-1] if current_path else ""
            body_text = "\n".join(
                block.text.strip() for block in body if block.text.strip()
            ).strip()
            if not body_text:
                body = []
                return
            text = f"{leaf}\n{body_text}" if leaf and leaf != "前言" and body_text != leaf else body_text
            pages = [block.page_idx for block in body if block.page_idx >= 0]
            if heading_page >= 0:
                pages.append(heading_page)
            page_start = min(pages) if pages else -1
            page_end = max(pages) if pages else -1
            content_types = {block.content_type for block in body}
            if qa_mode and self._QA_HEADING_RE.match(leaf):
                content_type = "qa_pair"
            elif self._ARTICLE_HEADING_RE.match(leaf):
                content_type = "legal_article"
            elif content_types == {"table_image"}:
                content_type = "table_image"
            elif "table_image" in content_types:
                content_type = "mixed"
            else:
                content_type = "text"
            group_path = current_path[:-1] if len(current_path) > 1 else current_path
            units.append(_Unit(
                path=current_path,
                group_path=group_path or current_path,
                text=text,
                page_start=page_start,
                page_end=page_end,
                content_type=content_type,
                source_ordinals=tuple(block.source_ordinal for block in body),
            ))
            for index in range(len(used)):
                used[index] = True
            body = []

        def rescue(index: int) -> None:
            """Keep a heading that never produced a unit and left the stack.

            Without this, a misclassified title (or a title with no body) would
            silently delete text from the corpus.
            """
            if used[index] or not stack[index]:
                return
            used[index] = True
            body.append(SourceBlock(
                kind="text",
                text=stack[index],
                page_idx=stack_pages[index],
                level=None,
                content_type="text",
                source_ordinal=stack_ordinals[index],
            ))

        for block in blocks:
            if block.kind != "heading":
                if block.text.strip():
                    body.append(block)
                continue
            flush()
            level = max(1, int(block.level or 1))
            target = level - 1
            for index in range(target, len(stack)):
                rescue(index)
            del stack[target:]
            del stack_pages[target:]
            del stack_ordinals[target:]
            del used[target:]
            while len(stack) < target:
                stack.append("")
                stack_pages.append(block.page_idx)
                stack_ordinals.append(block.source_ordinal)
                used.append(True)
            stack.append(block.text.strip())
            stack_pages.append(block.page_idx)
            stack_ordinals.append(block.source_ordinal)
            used.append(False)
            current_path = tuple(part for part in stack if part)
            heading_page = block.page_idx
        flush()
        for index in range(len(stack)):
            rescue(index)
        flush()
        return units

    def _split_unit(self, unit: _Unit) -> list[_Unit]:
        prefix = _breadcrumb_prefix(unit.group_path)
        available = max(1, self._max_chars - len(prefix) - 1)
        if len(unit.text) <= available:
            return [unit]
        pieces = _split_by_sentence(
            unit.text,
            max_chars=available,
            overlap_chars=min(self._overlap_chars, max(0, available - 1)),
        )
        return [
            _Unit(
                path=unit.path,
                group_path=unit.group_path,
                text=piece,
                page_start=unit.page_start,
                page_end=unit.page_end,
                content_type=unit.content_type,
                source_ordinals=unit.source_ordinals,
            )
            for piece in pieces
        ]

    def _pack_units(self, units: Sequence[_Unit]) -> list[list[_Unit]]:
        if not units:
            return []
        if self._min_chars == 0:
            return [[unit] for unit in units]

        packs: list[list[_Unit]] = []
        current: list[_Unit] = []
        for unit in units:
            if not current:
                current = [unit]
                continue
            same_parent = _same_root(current, [unit])
            candidate = [*current, unit]
            if same_parent and _pack_length(candidate) <= self._max_chars:
                current.append(unit)
            else:
                packs.append(current)
                current = [unit]
        if current:
            packs.append(current)

        merged: list[list[_Unit]] = []
        for pack in packs:
            if (
                merged
                and _pack_length(pack) < self._min_chars
                and _same_root(merged[-1], pack)
                and _pack_length([*merged[-1], *pack]) <= self._max_chars
            ):
                merged[-1].extend(pack)
            else:
                merged.append(list(pack))
        return merged

    def _documents_from_packs(
        self,
        packs: Sequence[Sequence[_Unit]],
        doc_name: str,
    ) -> list[Document]:
        documents: list[Document] = []
        page_ordinals: dict[int, int] = {}
        for chunk_index, pack in enumerate(packs):
            if self._min_chars == 0 and len(pack) == 1:
                chapter_path = pack[0].path
            else:
                chapter_path = _common_prefix([unit.group_path for unit in pack])
            if not chapter_path:
                chapter_path = pack[0].group_path or pack[0].path
            chapter = " > ".join(chapter_path) or "前言"
            text = _render_pack_body(pack, chapter_path)
            page_content = f"【{chapter}】\n{text}" if chapter else text
            pages = [
                page
                for unit in pack
                for page in (unit.page_start, unit.page_end)
                if page >= 0
            ]
            page_start = min(pages) if pages else -1
            page_end = max(pages) if pages else -1
            types = {unit.content_type for unit in pack}
            content_type = next(iter(types)) if len(types) == 1 else "mixed"
            metadata: dict[str, object] = {
                "doc_name": doc_name,
                "chapter": chapter,
                "chunk_index": chunk_index,
                "content_type": content_type,
                "source_ordinals": sorted({
                    ordinal for unit in pack for ordinal in unit.source_ordinals
                }),
            }
            if page_start >= 0:
                page_ordinal = page_ordinals.get(page_start, 0)
                page_ordinals[page_start] = page_ordinal + 1
                metadata.update({
                    "page_start": page_start,
                    "page_end": page_end,
                    "page_number": page_start + 1,
                    "page_ordinal": page_ordinal,
                    "structural_id": f"{doc_name}|p{page_start}|{page_ordinal}",
                })
            metadata["chunk_uid"] = compute_chunk_uid(page_content, metadata)
            documents.append(Document(page_content=page_content, metadata=metadata))
        return documents


def _breadcrumb_prefix(path: Sequence[str]) -> str:
    chapter = " > ".join(path) or "前言"
    return f"【{chapter}】"


def _pack_length(pack: Sequence[_Unit]) -> int:
    path = _common_prefix([unit.group_path for unit in pack])
    prefix = _breadcrumb_prefix(path or pack[0].group_path)
    body = _render_pack_body(pack, path or pack[0].group_path)
    return len(prefix) + 1 + len(body)


def _render_pack_body(
    pack: Sequence[_Unit],
    chapter_path: Sequence[str],
) -> str:
    parts: list[str] = []
    previous_group: tuple[str, ...] | None = None
    chapter_path = tuple(chapter_path)
    for unit in pack:
        if unit.group_path != chapter_path and unit.group_path != previous_group:
            parts.append(_breadcrumb_prefix(unit.group_path))
        parts.append(unit.text)
        previous_group = unit.group_path
    return "\n\n".join(parts).strip()


def _same_root(left: Sequence[_Unit], right: Sequence[_Unit]) -> bool:
    left_path = left[0].group_path
    right_path = right[0].group_path
    return bool(left_path and right_path and left_path[0] == right_path[0])


def _common_prefix(paths: Iterable[Sequence[str]]) -> tuple[str, ...]:
    paths = [tuple(path) for path in paths]
    if not paths:
        return ()
    prefix = list(paths[0])
    for path in paths[1:]:
        limit = min(len(prefix), len(path))
        index = 0
        while index < limit and prefix[index] == path[index]:
            index += 1
        del prefix[index:]
        if not prefix:
            break
    return tuple(prefix)


def _split_by_sentence(
    text: str,
    max_chars: int = 500,
    overlap_chars: int = 50,
) -> List[str]:
    """Split text at sentence boundaries and hard-wrap oversized sentences."""

    if not text.strip():
        return []
    sentences = [
        sentence.strip()
        for sentence in SemanticChunker._SENTENCE_SPLIT_RE.split(text)
        if sentence.strip()
    ]
    chunks: list[str] = []
    current = ""

    def emit_current() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for sentence in sentences:
        if len(sentence) > max_chars:
            emit_current()
            step = max(1, max_chars - overlap_chars)
            start = 0
            while start < len(sentence):
                piece = sentence[start:start + max_chars].strip()
                if piece:
                    chunks.append(piece)
                if start + max_chars >= len(sentence):
                    break
                start += step
            continue
        if len(current) + len(sentence) <= max_chars:
            current += sentence
            continue
        previous = current
        emit_current()
        overlap = previous[-overlap_chars:] if overlap_chars and previous else ""
        current = overlap + sentence if len(overlap) + len(sentence) <= max_chars else sentence
    emit_current()
    return chunks
