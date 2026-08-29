"""qa_chain 混合检索优化的纯离线测试。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import requests

from langchain_core.documents import Document
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from public_kb.config import Settings
from public_kb.contracts import RerankerStatus
from public_kb.generation.chain import build_chain as build_qa_chain
from public_kb.retrieval.fallback import dense_only_retrieve as _dense_only_retrieve
from public_kb.retrieval.reranker import SiliconFlowReranker as _SiliconFlowReranker
from public_kb.retrieval.retriever import HybridRetrievalError


class FakeEmbeddings:
    def __init__(self) -> None:
        self.query_calls = 0

    def embed_query(self, text: str) -> list[float]:
        self.query_calls += 1
        return [0.1, 0.2, 0.3]


class FakeVectorStore:
    def similarity_search_with_score(self, question: str, k: int):
        return []


class FakeHit:
    def __init__(self, hit_id: int, text: str, score: float) -> None:
        self.score = score
        self.entity = {
            "id": hit_id,
            "entity": {
                "text": text,
                "doc_name": "招标投标法",
                "chapter": "第一章",
                "chunk_index": hit_id,
            },
        }


class FakeCollection:
    def __init__(self, *, has_sparse: bool = True, fail_hybrid: bool = False) -> None:
        self.has_sparse = has_sparse
        self.fail_hybrid = fail_hybrid
        self.describe_calls = 0
        self.hybrid_reqs = None
        self.search_calls = 0

    def describe_collection(self, name: str) -> dict:
        self.describe_calls += 1
        fields = [{"name": "id"}, {"name": "text"}, {"name": "vector"}]
        if self.has_sparse:
            fields.append({"name": "sparse_vector"})
        return {"fields": fields}

    def hybrid_search(self, name: str, **kwargs):
        if self.fail_hybrid:
            raise RuntimeError("hybrid unavailable")
        self.hybrid_reqs = kwargs["reqs"]
        return [[
            FakeHit(1, "公开招标是招标方式之一。", 0.032),
            FakeHit(2, "邀请招标适用于特定情形。", 0.030),
        ]]

    def search(self, name: str, **kwargs):
        self.search_calls += 1
        return [[FakeHit(1, "公开招标是招标方式之一。", 0.91)]]


class SuccessfulReranker:
    last_status = RerankerStatus.SUCCESS

    def rerank(self, query: str, documents: list[str], top_k: int):
        return [{"index": 0, "relevance_score": 0.88}]


class FailedReranker:
    last_status = RerankerStatus.FAILED

    def rerank(self, query: str, documents: list[str], top_k: int):
        return []


def _settings(**overrides: object) -> Settings:
    values = {
        "collection_name": "public_kb_hybrid_poc_contract",
        "embedding_dim": 3,
        "retrieval_top_k": 2,
        "similarity_threshold": 0.45,
    }
    values.update(overrides)
    return Settings(**values)


class RerankerTests(unittest.TestCase):
    def test_failure_returns_no_fake_scores(self) -> None:
        class FailingHttp:
            @staticmethod
            def post(*args, **kwargs):
                raise TimeoutError("timeout")

        reranker = _SiliconFlowReranker(
            "model", "key", "https://example.invalid/v1",
            http_client=FailingHttp, max_retries=0,
        )

        self.assertEqual(reranker.rerank("问题", ["文档"], 1), [])
        self.assertIs(reranker.last_status, RerankerStatus.FAILED)

    def test_transient_failure_is_retried_without_sleeping_in_tests(self) -> None:
        class RetryableHttp:
            def __init__(self) -> None:
                self.calls = 0

            def post(self, *args, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise TimeoutError("transient")

                class Response:
                    @staticmethod
                    def raise_for_status():
                        return None

                    @staticmethod
                    def json():
                        return {"results": [{"index": 0, "relevance_score": 0.9}]}

                return Response()

        http_client = RetryableHttp()
        sleeps: list[float] = []
        reranker = _SiliconFlowReranker(
            "model", "key", "https://example.invalid/v1",
            http_client=http_client, max_retries=2,
            retry_backoff_seconds=0.25,
            sleep_fn=sleeps.append,
        )

        results = reranker.rerank("问题", ["文档"], 1)

        self.assertEqual(http_client.calls, 2)
        self.assertEqual(sleeps, [0.25])
        self.assertEqual(results[0]["relevance_score"], 0.9)
        self.assertIs(reranker.last_status, RerankerStatus.SUCCESS)

    def test_client_error_is_not_retried(self) -> None:
        class ClientErrorHttp:
            def __init__(self) -> None:
                self.calls = 0

            def post(self, *args, **kwargs):
                self.calls += 1
                response = SimpleNamespace(status_code=400)
                raise requests.HTTPError("bad request", response=response)

        http_client = ClientErrorHttp()
        reranker = _SiliconFlowReranker(
            "model", "key", "https://example.invalid/v1",
            http_client=http_client, max_retries=2, sleep_fn=lambda seconds: None,
        )

        self.assertEqual(reranker.rerank("问题", ["文档"], 1), [])
        self.assertEqual(http_client.calls, 1)
        self.assertIs(reranker.last_status, RerankerStatus.FAILED)


class DenseFallbackTests(unittest.TestCase):
    def test_precomputed_vector_avoids_second_embedding_call(self) -> None:
        embeddings = FakeEmbeddings()
        collection = FakeCollection()

        results = _dense_only_retrieve(
            "问题",
            FakeVectorStore(),
            _settings(),
            collection,
            embeddings,
            dense_vec=[0.1, 0.2, 0.3],
        )

        self.assertEqual(embeddings.query_calls, 0)
        self.assertEqual(len(results), 1)


class QaChainOfflineTests(unittest.TestCase):
    def _invoke(self, collection: FakeCollection, embeddings: FakeEmbeddings, reranker):
        llm = FakeListChatModel(responses=["公开招标是法定招标方式之一【来源1】"])
        chain = build_qa_chain(
            vector_store=FakeVectorStore(),
            llm=llm,
            settings=_settings(),
            collection=collection,
            embeddings=embeddings,
            reranker_class=lambda *args, **kwargs: reranker,
        )
        return chain.invoke("招标方式有哪些？")

    def test_hybrid_rerank_reports_real_mode_and_bm25_request(self) -> None:
        collection = FakeCollection()
        result = self._invoke(collection, FakeEmbeddings(), SuccessfulReranker())

        self.assertEqual(result["retrieval_diagnostics"]["retrieval_mode"], "hybrid_rerank")
        self.assertEqual(result["retrieval_diagnostics"]["reranker_status"], "success")
        self.assertEqual(result["sources"][0]["score"], 0.88)
        self.assertEqual(len(collection.hybrid_reqs), 2)
        sparse_request = collection.hybrid_reqs[1]
        self.assertEqual(sparse_request.anns_field, "sparse_vector")
        self.assertEqual(sparse_request.param["metric_type"], "BM25")

    def test_reranker_failure_keeps_rrf_order_without_fake_half_score(self) -> None:
        result = self._invoke(FakeCollection(), FakeEmbeddings(), FailedReranker())

        diagnostics = result["retrieval_diagnostics"]
        self.assertEqual(diagnostics["retrieval_mode"], "hybrid_rrf")
        self.assertEqual(diagnostics["reranker_status"], "failed")
        self.assertEqual(diagnostics["fallback_reason"], "reranker_failed")
        self.assertNotEqual(result["sources"][0]["score"], 0.5)

    def test_missing_sparse_reuses_query_vector_for_dense_fallback(self) -> None:
        embeddings = FakeEmbeddings()
        result = self._invoke(FakeCollection(has_sparse=False), embeddings, FailedReranker())

        self.assertEqual(embeddings.query_calls, 1)
        self.assertEqual(result["retrieval_diagnostics"]["retrieval_mode"], "dense_native")
        self.assertEqual(
            result["retrieval_diagnostics"]["fallback_reason"],
            "sparse_vector_field_missing",
        )

    def test_strict_mode_exposes_hybrid_failure(self) -> None:
        llm = FakeListChatModel(responses=["不会执行"])
        chain = build_qa_chain(
            vector_store=FakeVectorStore(),
            llm=llm,
            settings=_settings(strict_hybrid_validation=True),
            collection=FakeCollection(fail_hybrid=True),
            embeddings=FakeEmbeddings(),
            reranker_class=_SiliconFlowReranker,
        )

        with self.assertRaises(HybridRetrievalError):
            chain.invoke("招标方式有哪些？")

    def test_schema_capability_is_cached_per_chain(self) -> None:
        collection = FakeCollection()
        llm = FakeListChatModel(responses=["回答【来源1】", "回答【来源1】"])
        chain = build_qa_chain(
            vector_store=FakeVectorStore(),
            llm=llm,
            settings=_settings(),
            collection=collection,
            embeddings=FakeEmbeddings(),
            reranker_class=lambda *args, **kwargs: SuccessfulReranker(),
        )
        chain.invoke("问题一")
        chain.invoke("问题二")

        self.assertEqual(collection.describe_calls, 1)


if __name__ == "__main__":
    unittest.main()
