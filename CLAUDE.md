# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

**招投标智能助手** (Bidding Intelligent Assistant) — an AI Agent for the government procurement & bidding domain. It combines structured MySQL queries, Milvus hybrid RAG over authoritative legal PDFs, and DeepSeek LLM reasoning. Four core capabilities: professional knowledge Q&A, price/bid inquiry, general chat, and document Q&A (placeholder).

## Commands

```bash
# Install dependencies (setuptools must be <70 for pymilvus 2.4.x)
pip install -r requirements.txt

# Single Q&A
python -m agent --question "招标方式有哪些？"

# Interactive session (clear = reset history, quit/exit = stop)
python -m agent --interactive

# Verbose/debug mode
python -m agent --question "..." --verbose

# Initialize the public knowledge base from PDFs (one-time, ~10-20 min)
python -m public_kb --init --pdf-dir raw_pdfs

# Standalone knowledge base Q&A test
python -m public_kb --interactive

# Start infrastructure (from project root)
docker compose -f milvus/docker-compose.yml up -d      # Milvus + etcd + MinIO + Attu
docker compose -f docker/mysql/docker-compose.yml up -d # local MySQL (optional)

# Run tests (pytest-compatible; some also run standalone via __main__)
python -m pytest test/ -v

# Diagnostic tools
python test/explain_sql.py --db ztb_clean --sql "<SQL>"   # EXPLAIN a query
python test/profile_node_price.py                         # profile price_inquiry node end-to-end
python test/db_explorer.py --overview                      # DB table overview
python test/create_fulltext_indexes.py --dry-run           # check FULLTEXT index coverage
```

## Architecture

The system has two independent packages connected by a thin contract:

### `agent/` — LangGraph StateGraph skeleton

- **`graph.py`**: Builds and compiles the StateGraph. All business nodes are wrapped in `_with_fallback()` — any unhandled exception returns a friendly degradation message instead of crashing. The `AgentGraph` class is the public entry point.
- **`state.py`**: Single `AgentState(TypedDict)` with three fields: `messages` (Annotated list with `add_messages` reducer for ID dedup), `router_intent` (str enum), `business_result` (generic dict). All branches share this same state — new branches do NOT add fields.
- **`router.py`**: LLM-based intent classifier. Tries `with_structured_output(RouterDecision)` first; falls back to Tool Calling if the API doesn't support it. Carries the last 3 conversation turns for context-aware routing. All failures → `fallback`.
- **`nodes/`**: Business nodes, each following the signature `(AgentState) → dict`. `price_inquiry/` is a package (split 2026-08-15): `node.py` (entry + guards + guidance), `queries.py` (table-specific query paths), `recall.py` (multi-stage retrieval chain: Milvus semantic → FULLTEXT → LIKE → full scan with descending weights), `sql_builders.py`, `intent.py` (unified intent parsing), `semantic.py` (Milvus semantic collection bootstrap/recall), `enum_norm.py`, `db.py` (connection pool), `schema.py`, `models.py`. The package `__init__.py` re-exports all symbols, so `from agent.nodes.price_inquiry import ...` is unchanged for external callers.
- **`checkpointer.py`**: Factory that returns `MemorySaver` (default, ephemeral). Supports `sqlite`/`postgres`/`redis` backends via the same interface — one-line change, zero business code impact.

### `public_kb/` — RAG engine

- **`rag_engine.py`**: `PublicKnowledgeRAG` class — the public entry point. Methods: `init_knowledge_base(pdf_dir)`, `query(question)`, `clear_kb()`, `add_pdf(path)`. Uses lazy singleton pattern; the knowledge base is read-only after initialization.
- Pipeline: PDF → `MinerUParser` (MinerU API → Markdown) → `TextCleaner` → `SemanticChunker` (heading-aware, 2000 chars/chunk, 100 char overlap) → `create_embeddings()` (BGE-m3, 1024 dims) → `MilvusStoreManager` → `build_qa_chain()` (LCEL hybrid retrieval chain).
- **`config.py`**: `Settings` dataclass — single source of truth for all parameters. Loads from `.env` automatically. All modules share this config.
- Hybrid retrieval: dense (BGE-m3) + sparse → RRF fusion (k=60) → BGE-reranker-v2-m3 Cross-Encoder rerank → top-K (5). Adaptive similarity threshold with 0.45 floor.

### Data sources

- **MySQL `ztb_clean`**: Cleaned structured data (company info, penalties, bid projects, products). Module-level connection pool with reuse (no fixed cap). Uses FULLTEXT indexes with ngram parser (ngram_token_size=2) for Chinese text search.
- **Milvus**: Two collections — `public_kb` (law/regulation chunks) and `mysql_price_semantic` (structured data semantic recall). IVF_FLAT index, COSINE metric, nlist=128, nprobe=32.

### Node interface contract

Every business node follows this signature (defined in `agent/nodes/`):

```python
def node_xxx(state: AgentState) -> dict:
    """Returns {"business_result": {"branch": str, "answer": str, "data": ...},
                "messages": [AIMessage(content=str)]}"""
```

When adding a new branch: (1) create `agent/nodes/new_branch.py`, (2) add the Literal to `RouterIntent` in `router.py`, (3) add a routing tool, (4) register in `graph.py`. State definition and other nodes remain untouched.

## Key constraints

- `setuptools` **must be <70**. pymilvus 2.4.x depends on the removed `pkg_resources` module.
- `public_kb` is **read-only** after initialization; only batch import or admin `clear_kb()`.
- All SQL queries prioritize indexable exact matches (`=`, `>=`, `<=`) and FULLTEXT over `LIKE '%...%'`.
- Router LLM uses `temperature=0` for deterministic classification.
- The project `.env` contains real API keys — never commit it.

## Tests

The `test/` directory contains both diagnostic scripts and pytest-compatible tests:

**Diagnostic/profiling scripts:**
- `test/db_explorer.py` — database overview (table counts, sizes)
- `test/explain_sql.py` — run EXPLAIN on a query to detect full table scans
- `test/profile_node_price.py` — benchmark price_inquiry node end-to-end
- `test/create_fulltext_indexes.py` — auto-create or dry-run FULLTEXT indexes
- `test/_diag_common.py` — shared MySQL connection helper for the active diag scripts

**Archived one-off scripts**（历史数据准备/迁移工具，冻结保留）:
- `archive/` — migrate_milvus_cloud.py, rebuild_and_verify.py
- `scripts/archive/` — run_evaluation.py, generate_report.py, csv_to_mysql.py
- `test/legacy/` — _step* 迁移步骤、scan_tables / inspect_price_dbs / export_samples 等数据诊断工具

**Pytest-compatible tests** (test internal functions with mocks, no live DB/LLM needed):
- `test/test_recall_optimization.py` — recall stage logic tests
- `test/test_sub_route.py` — sub-route classification tests
- `test/test_bug_repairs.py` — regression tests for fixed bugs
- `test/test_p0_*.py` — P0 bug fix verification tests
- `test/test_citation_tracing.py` — citation tracing tests (chunk_uid, standardized citations, validation rules R1-R7)

**Knowledge-base citation evaluation** (requires live Milvus + LLM):
- `python scripts/run_knowledge_citation_eval.py` — full citation traceability eval over `testset_knowledge.jsonl` (~106 legal questions), verifies per-result citation completeness and Milvus back-check association; writes `test_report/knowledge_citation_results.jsonl` + summary report

Run all with: `python -m pytest test/ -v`

## Reference docs

`docs/project_overview.md` is the comprehensive reference (v2.0, ~1100 lines). It covers architecture, setup, deployment, troubleshooting, technical debt register, and architecture decision records. Consult it for detailed explanations before modifying core flows.
