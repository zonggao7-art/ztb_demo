"""Structural guards for public_kb before and during directory consolidation."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import public_kb
import public_kb.chunker as legacy_chunker
import public_kb.ingestion.transforms.chunker as ingestion_chunker
import public_kb.ingestion.transforms.cleaner as ingestion_cleaner
import public_kb.text_cleaner as legacy_cleaner
from agent.nodes.knowledge_qa import node_knowledge_qa
from public_kb.embedding_service import (
    _SafeEmbeddings as legacy_safe_embeddings,
    create_embeddings as legacy_create_embeddings,
)
from public_kb.llm_factory import create_llm as legacy_create_llm
from public_kb.milvus_store import MilvusStoreManager as legacy_milvus_store_manager
from public_kb.mineru_parser import MinerUParser as legacy_mineru_parser
from public_kb.contracts import RetrievalDiagnostics, validate_ingestion_documents
from public_kb.csv_loader import (
    CsvLoader as legacy_csv_loader,
    save_chunks_to_markdown as legacy_save_chunks_to_markdown,
)
from public_kb.ingestion.sources.csv_loader import CsvLoader, save_chunks_to_markdown
from public_kb.ingestion.models import IngestionResult, SourceResult, StageResult
from public_kb.ingestion.pipeline import IngestionPipeline
from public_kb.qa_chain import (
    _SiliconFlowReranker,
    _dense_only_retrieve,
    build_qa_chain,
)
from public_kb.rag_engine import PublicKnowledgeRAG
from public_kb.retrieval.fallback import dense_only_retrieve
from public_kb.retrieval.retriever import HybridRetriever
from public_kb.citations import CitationValidator as legacy_citation_validator
from public_kb.generation.citations import CitationValidator
from public_kb.retrieval.reranker import Reranker, SiliconFlowReranker
from public_kb.services.embeddings import _SafeEmbeddings, create_embeddings
from public_kb.services.llm import create_llm
from public_kb.services.milvus_store import MilvusStoreManager
from public_kb.services.mineru_parser import MinerUParser


PUBLIC_KB_ROOT = Path(public_kb.__file__).resolve().parent


def test_stable_public_entries_import_without_initializing_services():
    assert public_kb.PublicKnowledgeRAG is PublicKnowledgeRAG
    assert callable(build_qa_chain)
    assert callable(node_knowledge_qa)
    assert not inspect.iscoroutinefunction(PublicKnowledgeRAG.query)
    assert not inspect.iscoroutinefunction(HybridRetriever.retrieve)


def test_qa_chain_facade_forwards_to_split_retrieval_pipeline():
    assert _SiliconFlowReranker is SiliconFlowReranker
    assert _dense_only_retrieve is dense_only_retrieve
    assert callable(build_qa_chain)


def test_ingestion_pipeline_boundaries_import_and_keep_legacy_aliases():
    assert IngestionPipeline is not None
    assert SourceResult is not None
    assert StageResult is not None
    assert IngestionResult is not None
    assert validate_ingestion_documents is not None
    assert ingestion_chunker.SemanticChunker is legacy_chunker.SemanticChunker
    assert ingestion_cleaner.TextCleaner is legacy_cleaner.TextCleaner
    assert CsvLoader is legacy_csv_loader
    assert save_chunks_to_markdown is legacy_save_chunks_to_markdown


def test_shared_contracts_and_chunk_identity_remain_at_package_root():
    from public_kb.chunk_ids import compute_chunk_uid
    from public_kb.config import Settings
    from public_kb.contracts import RetrievalMode, RerankerStatus

    assert callable(compute_chunk_uid)
    assert Settings is not None
    assert RetrievalMode.HYBRID_RRF is not None
    assert RerankerStatus.FAILED is not None
    assert RetrievalDiagnostics is not None


def test_shared_services_are_available_at_consolidated_paths():
    assert _SafeEmbeddings is not None
    assert callable(create_embeddings)
    assert callable(create_llm)
    assert MilvusStoreManager is not None
    assert MinerUParser is not None


def test_shared_services_preserve_legacy_import_aliases():
    assert legacy_safe_embeddings is _SafeEmbeddings
    assert legacy_create_embeddings is create_embeddings
    assert legacy_create_llm is create_llm
    assert legacy_milvus_store_manager is MilvusStoreManager
    assert legacy_mineru_parser is MinerUParser


def test_generation_citations_preserves_legacy_import_alias():
    assert CitationValidator is legacy_citation_validator


def test_retrieval_reranker_contract_and_client_are_consolidated():
    assert Reranker is not None
    assert SiliconFlowReranker is not None
    assert not (PUBLIC_KB_ROOT / "retrieval" / "reranker" / "protocol.py").exists()
    assert not (PUBLIC_KB_ROOT / "retrieval" / "reranker" / "siliconflow.py").exists()


def test_generation_and_retrieval_do_not_import_legacy_citation_reranker_paths():
    scanned_paths = [
        PUBLIC_KB_ROOT / "rag_engine.py",
        PUBLIC_KB_ROOT / "generation",
        PUBLIC_KB_ROOT / "ingestion",
        PUBLIC_KB_ROOT / "retrieval",
        PUBLIC_KB_ROOT / "services",
    ]
    python_paths = []
    for path in scanned_paths:
        if path.is_file():
            python_paths.append(path)
        elif path.is_dir():
            python_paths.extend(path.rglob("*.py"))

    for path in python_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            if node.level == 0:
                assert module != "public_kb.citations", str(path)
                assert not module.startswith("public_kb.citations."), str(path)
                assert module != "public_kb.retrieval.reranker.protocol", str(path)
                assert module != "public_kb.retrieval.reranker.siliconflow", str(path)
            if node.level > 0:
                assert module not in {"reranker.protocol", "reranker.siliconflow"}, str(path)
                if path.parent == PUBLIC_KB_ROOT / "generation":
                    assert not (node.level >= 2 and module == "citations"), str(path)


def test_internal_pipelines_do_not_import_legacy_service_paths():
    scanned_paths = [
        PUBLIC_KB_ROOT / "rag_engine.py",
        PUBLIC_KB_ROOT / "generation",
        PUBLIC_KB_ROOT / "ingestion",
        PUBLIC_KB_ROOT / "retrieval",
        PUBLIC_KB_ROOT / "services",
    ]
    deprecated_names = {"embedding_service", "llm_factory", "milvus_store", "mineru_parser"}
    python_paths = []
    for path in scanned_paths:
        if path.is_file():
            python_paths.append(path)
        elif path.is_dir():
            python_paths.extend(path.rglob("*.py"))

    for path in python_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module not in deprecated_names, f"{path}: {node.module}"
                assert not any(alias.name in deprecated_names for alias in node.names), str(path)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in deprecated_names, f"{path}: {alias.name}"
                    assert not alias.name.startswith("public_kb.embedding_service"), str(path)
                    assert not alias.name.startswith("public_kb.llm_factory"), str(path)
                    assert not alias.name.startswith("public_kb.milvus_store"), str(path)
                    assert not alias.name.startswith("public_kb.mineru_parser"), str(path)


def test_ingestion_does_not_import_legacy_package_roots():
    legacy_absolute_names = {"chunker", "text_cleaner", "csv_loader"}
    scanned_paths = [
        PUBLIC_KB_ROOT / "rag_engine.py",
        PUBLIC_KB_ROOT / "generation",
        PUBLIC_KB_ROOT / "ingestion",
        PUBLIC_KB_ROOT / "retrieval",
        PUBLIC_KB_ROOT / "services",
    ]
    python_paths = []
    for path in scanned_paths:
        if path.is_file():
            python_paths.append(path)
        elif path.is_dir():
            python_paths.extend(path.rglob("*.py"))

    assert not (PUBLIC_KB_ROOT / "ingestion" / "transforms" / "chunk_ids.py").exists()

    for path in python_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            module = node.module or ""
            if node.level == 0:
                assert module not in legacy_absolute_names, f"{path}: {module}"
                assert not module.startswith("public_kb.chunker"), str(path)
                assert not module.startswith("public_kb.text_cleaner"), str(path)
                assert not module.startswith("public_kb.csv_loader"), str(path)
            if node.level == 2 and module == "csv_loader":
                assert False, f"{path}: from ..csv_loader imports the legacy package root"
            if node.level >= 3 and module in legacy_absolute_names:
                assert False, f"{path}: from ...{module} imports the legacy package root"


def test_agent_knowledge_entry_does_not_import_public_kb_internals():
    entry_path = Path(node_knowledge_qa.__module__.replace(".", "/"))
    entry_path = Path(*entry_path.parts).with_suffix(".py")
    if not entry_path.is_absolute():
        entry_path = PUBLIC_KB_ROOT.parent / entry_path

    tree = ast.parse(entry_path.read_text(encoding="utf-8"))
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    forbidden_prefixes = (
        "public_kb.qa_chain",
        "public_kb.rag_engine",
        "public_kb.retrieval",
        "public_kb.generation",
        "public_kb.ingestion",
    )
    assert not any(
        module == prefix or module.startswith(prefix + ".")
        for module in imported_modules
        for prefix in forbidden_prefixes
    )
