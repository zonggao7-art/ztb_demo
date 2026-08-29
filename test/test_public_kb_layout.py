"""Structural guards for the consolidated public_kb package."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import public_kb
from agent.nodes.knowledge_qa import node_knowledge_qa
from public_kb.contracts import (
    RetrievalDiagnostics,
    RetrievalMode,
    RerankerStatus,
    validate_ingestion_documents,
)
from public_kb.chunk_ids import compute_chunk_uid
from public_kb.config import Settings
from public_kb.generation.citations import CitationValidator
from public_kb.generation.chain import build_chain
from public_kb.ingestion.models import IngestionResult, SourceResult, StageResult
from public_kb.ingestion.pipeline import IngestionPipeline
from public_kb.ingestion.sources.csv_loader import CsvLoader, save_chunks_to_markdown
from public_kb.ingestion.transforms.chunker import SemanticChunker
from public_kb.ingestion.transforms.cleaner import TextCleaner
from public_kb.qa_chain import (
    HybridRetrievalError as StableHybridRetrievalError,
    _dense_only_retrieve as stable_dense_only_retrieve,
    _SiliconFlowReranker as stable_siliconflow_reranker,
    build_qa_chain,
)
from public_kb.rag_engine import PublicKnowledgeRAG
from public_kb.retrieval.fallback import dense_only_retrieve
from public_kb.retrieval.reranker import Reranker, SiliconFlowReranker
from public_kb.retrieval.retriever import HybridRetrievalError, HybridRetriever
from public_kb.services.embeddings import _SafeEmbeddings, create_embeddings
from public_kb.services.llm import create_llm
from public_kb.services.milvus_store import MilvusStoreManager
from public_kb.services.mineru_parser import MinerUParser


PUBLIC_KB_ROOT = Path(public_kb.__file__).resolve().parent
LEGACY_MODULES = {
    "public_kb.embedding_service",
    "public_kb.llm_factory",
    "public_kb.milvus_store",
    "public_kb.mineru_parser",
    "public_kb.chunker",
    "public_kb.text_cleaner",
    "public_kb.csv_loader",
    "public_kb.citations",
    "public_kb.process_csv",
}
LEGACY_FILES = {
    "embedding_service.py",
    "llm_factory.py",
    "milvus_store.py",
    "mineru_parser.py",
    "chunker.py",
    "text_cleaner.py",
    "csv_loader.py",
    "citations.py",
    "process_csv.py",
}


def test_stable_public_entries_import_without_initializing_services():
    assert public_kb.PublicKnowledgeRAG is PublicKnowledgeRAG
    assert callable(build_chain)
    assert callable(node_knowledge_qa)
    assert not inspect.iscoroutinefunction(PublicKnowledgeRAG.query)
    assert not inspect.iscoroutinefunction(HybridRetriever.retrieve)


def test_consolidated_pipeline_boundaries_import():
    assert IngestionPipeline is not None
    assert SourceResult is not None
    assert StageResult is not None
    assert IngestionResult is not None
    assert validate_ingestion_documents is not None
    assert SemanticChunker is not None
    assert TextCleaner is not None
    assert CsvLoader is not None
    assert save_chunks_to_markdown is not None


def test_shared_contracts_and_chunk_identity_remain_at_package_root():
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


def test_generation_and_retrieval_boundaries_are_consolidated():
    assert CitationValidator is not None
    assert Reranker is not None
    assert SiliconFlowReranker is not None
    assert dense_only_retrieve is not None
    assert not (PUBLIC_KB_ROOT / "retrieval" / "reranker" / "protocol.py").exists()
    assert not (PUBLIC_KB_ROOT / "retrieval" / "reranker" / "siliconflow.py").exists()


def test_qa_chain_is_a_thin_stable_entrypoint():
    parameters = list(inspect.signature(build_qa_chain).parameters)
    assert parameters == ["vector_store", "llm", "settings", "collection", "embeddings"]
    assert stable_siliconflow_reranker is SiliconFlowReranker
    assert stable_dense_only_retrieve is dense_only_retrieve
    assert StableHybridRetrievalError is HybridRetrievalError


def test_legacy_compatibility_files_are_removed():
    for file_name in LEGACY_FILES:
        assert not (PUBLIC_KB_ROOT / file_name).exists(), file_name


def _iter_python_paths():
    yield from PUBLIC_KB_ROOT.rglob("*.py")
    yield from (PUBLIC_KB_ROOT.parent / "agent").rglob("*.py")
    yield from (PUBLIC_KB_ROOT.parent / "scripts").glob("poc_*.py")


def test_code_has_no_legacy_public_kb_import_paths():
    python_paths = list(_iter_python_paths())
    assert python_paths

    for path in python_paths:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                module_names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                module_names = [node.module or ""]
            else:
                continue
            assert not any(module in LEGACY_MODULES for module in module_names), path


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
