"""公共知识库建库与检索契约的纯离线测试。

不读取真实 Milvus 配置，不连接数据库，可直接运行：
    python -m unittest test.test_kb_contracts -v
"""

from __future__ import annotations

import unittest

from langchain_core.documents import Document

from public_kb.contracts import (
    ConfigurationContractError,
    IngestionContractError,
    MilvusCollectionContract,
    RerankerStatus,
    RetrievalContractError,
    RetrievalDiagnostics,
    RetrievalMode,
    validate_embedding_batch,
    validate_ingestion_documents,
    validate_qa_result,
    validate_question,
)


def _document(**metadata: object) -> Document:
    base = {
        "doc_name": "中华人民共和国招标投标法",
        "chapter": "第三章 投标",
        "chunk_index": 0,
    }
    base.update(metadata)
    return Document(page_content="第三条 招标投标活动应当遵循公开原则。", metadata=base)


class MilvusCollectionContractTests(unittest.TestCase):
    def test_default_contract_is_valid(self) -> None:
        MilvusCollectionContract().validate()

    def test_field_names_must_be_distinct(self) -> None:
        contract = MilvusCollectionContract(sparse_field="vector")
        with self.assertRaises(ConfigurationContractError):
            contract.validate()

    def test_server_bm25_metric_cannot_use_ip(self) -> None:
        contract = MilvusCollectionContract(sparse_metric="IP")
        with self.assertRaises(ConfigurationContractError):
            contract.validate()


class IngestionContractTests(unittest.TestCase):
    def test_valid_documents_return_a_list_copy(self) -> None:
        source = (_document(),)
        validated = validate_ingestion_documents(source)
        self.assertIsInstance(validated, list)
        self.assertEqual(validated, list(source))

    def test_empty_documents_are_rejected(self) -> None:
        with self.assertRaises(IngestionContractError):
            validate_ingestion_documents([])

    def test_empty_text_is_rejected(self) -> None:
        document = _document()
        document.page_content = "   "
        with self.assertRaises(IngestionContractError):
            validate_ingestion_documents([document])

    def test_required_metadata_is_enforced(self) -> None:
        document = Document(
            page_content="有效正文",
            metadata={"doc_name": "法规", "chapter": "第一章"},
        )
        with self.assertRaises(IngestionContractError):
            validate_ingestion_documents([document])

    def test_chunk_index_must_be_non_negative_integer(self) -> None:
        with self.assertRaises(IngestionContractError):
            validate_ingestion_documents([_document(chunk_index=-1)])

    def test_embedding_count_and_dimension_are_validated(self) -> None:
        documents = [_document(), _document(chunk_index=1)]
        validate_embedding_batch(documents, [[0.1] * 3, [0.2] * 3], 3)
        with self.assertRaises(IngestionContractError):
            validate_embedding_batch(documents, [[0.1] * 3], 3)
        with self.assertRaises(IngestionContractError):
            validate_embedding_batch(documents, [[0.1] * 2, [0.2] * 3], 3)


class RetrievalContractTests(unittest.TestCase):
    def test_question_is_trimmed(self) -> None:
        self.assertEqual(validate_question("  招标方式有哪些？  "), "招标方式有哪些？")

    def test_empty_or_non_string_question_is_rejected(self) -> None:
        with self.assertRaises(RetrievalContractError):
            validate_question("   ")
        with self.assertRaises(RetrievalContractError):
            validate_question(None)  # type: ignore[arg-type]

    def test_diagnostics_are_json_compatible(self) -> None:
        diagnostics = RetrievalDiagnostics(
            retrieval_mode=RetrievalMode.HYBRID_RERANK,
            dense_count=30,
            sparse_count=30,
            fusion_count=20,
            reranker_status=RerankerStatus.SUCCESS,
            threshold=0.45,
        )
        self.assertEqual(
            diagnostics.to_dict(),
            {
                "retrieval_mode": "hybrid_rerank",
                "dense_count": 30,
                "sparse_count": 30,
                "fusion_count": 20,
                "reranker_status": "success",
                "threshold": 0.45,
                "fallback_reason": None,
            },
        )

    def test_invalid_diagnostics_are_rejected(self) -> None:
        with self.assertRaises(RetrievalContractError):
            RetrievalDiagnostics(
                retrieval_mode=RetrievalMode.DENSE_NATIVE,
                dense_count=-1,
            )
        with self.assertRaises(RetrievalContractError):
            RetrievalDiagnostics(
                retrieval_mode=RetrievalMode.HYBRID_RERANK,
                threshold=1.1,
            )

    def test_existing_qa_contract_accepts_optional_diagnostics(self) -> None:
        result = {
            "answer": "回答",
            "sources": [],
            "citations": [],
            "citation_validation": {"all_passed": True},
            "retrieval_diagnostics": RetrievalDiagnostics(
                retrieval_mode=RetrievalMode.REFUSAL,
            ).to_dict(),
        }
        validate_qa_result(result)

    def test_missing_existing_qa_field_is_rejected(self) -> None:
        with self.assertRaises(RetrievalContractError):
            validate_qa_result({
                "answer": "回答",
                "sources": [],
                "citations": [],
            })


if __name__ == "__main__":
    unittest.main()
