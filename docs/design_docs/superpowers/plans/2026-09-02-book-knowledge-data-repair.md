# 三本书知识库数据修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 从 `DATA/raw_data` 中三本书的 MinerU `*_content_list.json` 确定性重建无目录/前置页污染、结构正确、页码可追溯、标识唯一的知识库切块及交接产物。

**Architecture:** 新增纯函数规范化层，将 MinerU 块转换为携带页码的结构块；增强切片器，使其按父章节打包短单元并保留问答/法条完整性；新增独立质量门禁和本地重建 CLI。Milvus 只接受门禁通过的产物，但本轮不自动删除或重建现有集合。

**Tech Stack:** Python 3、`langchain_core.documents.Document`、pytest、标准库 `argparse/json/pathlib/re/hashlib/statistics`

**Spec:** `docs/知识库切分质量根因排查与数据修复方案_20260902.md`

## Global Constraints

- 三本书均以 `*_content_list.json` 为唯一修复源，禁止使用污染的 assembled Markdown/JSONL。
- `max_chars=2000`、`overlap_chars=100`；短单元打包目标至少 200 字，不拆散问答和法条首句。
- 正文不得出现 `<!-- page: N -->`；页码仅写 `page_start/page_end` 元数据且覆盖率必须为 100%。
- `chunk_uid` 必须为 `ck-<32hex>`，三书联合 0 冲突且同参数重跑完全一致。
- `setuptools` 必须 `<70`；不改 embedding/reranker 身份配置和检索零降级策略。
- 不覆盖源 `content_list.json`、原 Markdown/PDF，也不自动清空或重建 Milvus。

---

### Task 1: 规范化 MinerU 块并恢复三类书籍结构

**Files:**
- Create: `public_kb/normalize.py`
- Create: `test/test_book_normalization.py`

**Interfaces:**
- Produces: `SourceBlock(kind: str, text: str, page_idx: int, level: int | None, content_type: str, source_ordinal: int)`
- Produces: `BookProfile(key: str, body_start_page: int, doc_name: str)`
- Produces: `normalize_content_list(items: list[dict], profile: BookProfile) -> list[SourceBlock]`
- Produces: `render_normalized_markdown(blocks: list[SourceBlock]) -> str`

- [ ] **Step 1: Write failing fixture tests for strict heading recovery**

```python
def test_book2_joins_split_chapter_and_question_with_answer():
    items = [
        {"type": "text", "text": "第一章", "page_idx": 6},
        {"type": "text", "text": "政府采购", "page_idx": 6, "text_level": 1},
        {"type": "text", "text": "1.什么是采购？", "page_idx": 7, "text_level": 1},
        {"type": "text", "text": "答：采购是取得资源的活动。", "page_idx": 7},
    ]
    blocks = normalize_content_list(items, BOOK_PROFILES["book2"])
    assert [(b.kind, b.level, b.text) for b in blocks] == [
        ("heading", 1, "第一章 政府采购"),
        ("heading", 3, "1.什么是采购？"),
        ("text", None, "答：采购是取得资源的活动。"),
    ]
```

- [ ] **Step 2: Run tests and confirm RED because `public_kb.normalize` is absent**

Run: `python -m pytest test/test_book_normalization.py -v`

Expected: collection error `ModuleNotFoundError: No module named 'public_kb.normalize'`.

- [ ] **Step 3: Implement profile detection, front-matter cutoff, strict numbered headings, split chapter/section joins, question recognition, article title/body split, and table caption/footnote extraction**

```python
blocks = normalize_content_list(items, BOOK_PROFILES["book3"])
# 第四十一条【条旨】正文 → heading("第四十一条【条旨】") + text("正文")
# table → caption/footnote text with content_type="table_image" and page_idx retained
```

- [ ] **Step 4: Run unit tests and real-source structural smoke checks**

Run: `python -m pytest test/test_book_normalization.py -v`

Expected: all tests pass; book1 begins at page 15, book2 contains 10 joined chapter headings, and book3 directory pages do not enter normalized output.

### Task 2: 增强清洗和切片，保留页码与语义单元

**Files:**
- Modify: `public_kb/text_cleaner.py`
- Modify: `public_kb/chunker.py`
- Create: `test/test_book_chunker.py`

**Interfaces:**
- Consumes: `SourceBlock` from Task 1
- Produces: `SemanticChunker.chunk_blocks(blocks: list[SourceBlock], doc_name: str, *, qa_mode: bool = False) -> list[Document]`
- Preserves: `SemanticChunker.chunk(markdown_text: str, doc_name: str) -> list[Document]`

- [ ] **Step 1: Write failing tests for HTML comment removal, short-unit packing, stable monotonic chunk indexes, breadcrumb prefix, QA binding, and page ranges**

```python
def test_chunk_blocks_packs_short_qa_without_splitting_answer():
    docs = SemanticChunker(max_chars=2000, overlap_chars=100, min_chars=200).chunk_blocks(
        fixture_blocks, "book2", qa_mode=True
    )
    assert all("问题" not in d.metadata["chapter"] or "答：" in d.page_content for d in docs)
    assert [d.metadata["chunk_index"] for d in docs] == list(range(len(docs)))
    assert all(d.metadata["page_start"] <= d.metadata["page_end"] for d in docs)
```

- [ ] **Step 2: Run tests and confirm RED on missing behavior**

Run: `python -m pytest test/test_book_chunker.py -v`

Expected: failures for retained HTML comments, absent `chunk_blocks`, reset indexes, and missing page metadata.

- [ ] **Step 3: Implement minimal clean/chunk behavior**

Implement HTML-comment stripping before existing cleaner rules; build heading stack only from normalized heading blocks; turn each heading body into an atomic semantic unit; pack adjacent units with the same parent context toward `max_chars`; split only oversized units at sentence boundaries; prepend `【chapter】`; assign global deterministic indexes; compute `structural_id`, `page_start`, `page_end`, `content_type`, and `chunk_uid` after final text is known.

- [ ] **Step 4: Run focused and citation regression tests**

Run: `python -m pytest test/test_book_chunker.py test/test_citation_tracing.py -v`

Expected: all focused tests pass and existing citation UID behavior remains compatible.

### Task 3: 建立入库前硬门禁与确定性校验

**Files:**
- Create: `public_kb/handoff_validate.py`
- Create: `test/test_handoff_validate.py`

**Interfaces:**
- Produces: `ValidationIssue(code: str, message: str, count: int)`
- Produces: `ValidationReport(ok: bool, metrics: dict[str, object], issues: list[ValidationIssue])`
- Produces: `validate_documents(documents: Sequence[Document], *, expected_chapters: int | None = None, qa_mode: bool = False) -> ValidationReport`
- Produces: `validate_determinism(first: Sequence[Document], second: Sequence[Document]) -> ValidationIssue | None`

- [ ] **Step 1: Write failing tests for every hard gate**

```python
def test_validator_rejects_comment_short_ratio_missing_pages_and_duplicate_uid():
    report = validate_documents(bad_docs)
    assert report.ok is False
    assert {i.code for i in report.issues} >= {
        "html_comment", "short_chunk_ratio", "page_coverage", "chunk_uid_duplicate"
    }
```

- [ ] **Step 2: Run and confirm RED because validator module is absent**

Run: `python -m pytest test/test_handoff_validate.py -v`

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Implement quantitative metrics and fail-closed validation**

Calculate comment/noise count, `<100` ratio, median length, unique chapters, breadcrumb coverage, page coverage, structural-ID uniqueness, UID format/uniqueness, forbidden front-matter markers, and repeat-run UID equality. Return a serializable report; do not silently downgrade failures.

- [ ] **Step 4: Run validator tests**

Run: `python -m pytest test/test_handoff_validate.py -v`

Expected: valid fixtures pass; each independently mutated invalid fixture fails with its specific code.

### Task 4: 实现本地重切、交接导出与显式入库 CLI

**Files:**
- Create: `public_kb/book_pipeline.py`
- Modify: `public_kb/__main__.py`
- Create: `test/test_book_pipeline.py`

**Interfaces:**
- Consumes: three content-list paths or a `DATA/raw_data` root
- Produces: per-book `normalized.md`, `normalized.blocks.jsonl`, `documents.jsonl`, `quality_report.json`, and combined `manifest.json`
- Produces CLI: `python -m public_kb --prepare-handoff --data-dir DATA/raw_data --output-dir DATA/repaired_knowledge`
- Produces CLI: `python -m public_kb --ingest-jsonl <documents.jsonl>`
- Produces CLI: `python -m public_kb --ingest-markdown <normalized.md> --metadata-jsonl <normalized.blocks.jsonl>`

- [ ] **Step 1: Write failing round-trip and fail-closed CLI tests using temporary directories**

```python
def test_prepare_handoff_is_deterministic_and_writes_only_after_validation(tmp_path):
    first = prepare_handoff(fixture_root, tmp_path / "a")
    second = prepare_handoff(fixture_root, tmp_path / "b")
    assert first["chunk_uids"] == second["chunk_uids"]
    assert (tmp_path / "a" / "manifest.json").exists()
```

- [ ] **Step 2: Run and confirm RED**

Run: `python -m pytest test/test_book_pipeline.py -v`

Expected: missing module/functions and CLI flags.

- [ ] **Step 3: Implement atomic export and explicit ingestion**

Discover exactly one source per profile, rebuild twice in memory for determinism, validate each book and the combined UID set, write to a temporary sibling directory, then rename into place. JSONL import must parse all rows and validate before invoking `MilvusStoreManager.initialize_collection`; Markdown import must require the page-metadata sidecar.

- [ ] **Step 4: Run pipeline and CLI tests**

Run: `python -m pytest test/test_book_pipeline.py test/test_book_normalization.py test/test_book_chunker.py test/test_handoff_validate.py -v`

Expected: all pass; invalid fixtures leave no partial output directory.

### Task 5: 重建三本书本地数据并产出质量报告

**Files:**
- Create (ignored local data): `DATA/repaired_knowledge/**`
- Create: `docs/知识库三本书重切分修复报告_20260902.md`

**Interfaces:**
- Consumes: production `DATA/raw_data/*/auto/*_content_list.json`
- Produces: validated local handoff bundle and human-readable before/after report

- [ ] **Step 1: Run real-data rebuild**

Run: `python -m public_kb --prepare-handoff --data-dir DATA/raw_data --output-dir DATA/repaired_knowledge`

Expected: three books discovered; no existing source file modified; command exits 0 only if per-book and combined gates pass.

- [ ] **Step 2: Run the exact command a second time into a separate directory**

Run: `python -m public_kb --prepare-handoff --data-dir DATA/raw_data --output-dir DATA/repaired_knowledge_repeat`

Expected: manifest UID digest and ordered UID list match the first run exactly.

- [ ] **Step 3: Inspect deterministic samples**

Inspect first/median/last chunks plus 30 evenly spaced chunks per book, emphasizing book1 breadcrumbs, book2 complete `问题+答：`, book3 complete `第X条【条旨】+首句`, and page boundaries. Record defects instead of weakening gates.

- [ ] **Step 4: Write the repair report**

Record input paths/hashes, chunk counts, median and short ratios, comment/noise count, chapter/breadcrumb/page coverage, structural/UID collision count, repeat-run digest, sampled defects, and the explicit statement that Milvus was not modified.

### Task 6: 全量回归与完成核验

**Files:**
- Modify only if required by verified regressions: files introduced above

**Interfaces:**
- Verifies all project behavior and plan acceptance criteria

- [ ] **Step 1: Run focused data-pipeline tests**

Run: `python -m pytest test/test_book_normalization.py test/test_book_chunker.py test/test_handoff_validate.py test/test_book_pipeline.py -v`

- [ ] **Step 2: Run the full offline test suite**

Run: `python -m pytest test/ -v`

- [ ] **Step 3: Re-read manifest and quality reports against all P3 gates**

Confirm 0 HTML-comment chunks, 0 pure-noise chunks, `<100` ratio `<=2%`, median `>=800`, breadcrumb coverage `>=80%`, page coverage `100%`, structural and UID collisions `0`, and repeat-run UID digest equality.

- [ ] **Step 4: Review the final diff without disturbing pre-existing changes**

Run: `git status --short` and `git diff -- public_kb/chunker.py public_kb/text_cleaner.py public_kb/normalize.py public_kb/handoff_validate.py public_kb/book_pipeline.py public_kb/__main__.py test/test_book_normalization.py test/test_book_chunker.py test/test_handoff_validate.py test/test_book_pipeline.py docs/知识库三本书重切分修复报告_20260902.md`

Expected: only scoped files are new/changed; existing user edits in config, embedding, Milvus, QA, and unrelated tests remain untouched.
