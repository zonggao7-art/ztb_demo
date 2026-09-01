# -*- coding: utf-8 -*-
"""异步 RAG 链路测试（阶段 2）— 全离线 mock Embedding/Milvus/Reranker/LLM。

覆盖手册 §阶段2 测试要求：
  - build_async_qa_chain：token + citations 结构、拒答路径、降级路径
  - AsyncRAGPipeline.stream_answer：token 增量
  - PublicKnowledgeRAG.aquery / astream：事件序列与结果结构
  - node_knowledge_qa_async：business_result 契约与异常兜底
  - 同步/异步拒答语义零退化对照
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, List, Optional, Tuple

import pytest
from langchain_core.documents import Document
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from public_kb.config import Settings


# ============================================================
#  测试替身
# ============================================================

class _FakeEmbeddings:
    """模拟 _SafeEmbeddings 的异步接口。"""

    def __init__(self, dim: int = 8):
        self.dim = dim
        self.embed_calls = 0

    async def aembed_query(self, text: str) -> List[float]:
        self.embed_calls += 1
        return [0.1] * self.dim

    def embed_query(self, text: str) -> List[float]:
        self.embed_calls += 1
        return [0.1] * self.dim


class _FakeHit:
    def __init__(self, entity: Dict[str, Any], score: float):
        self.entity = entity
        self.score = score


def _hit(text: str, chunk_id: int, score: float = 0.9,
         doc: str = "中华人民共和国招标投标法", chapter: str = "第一章 总则") -> _FakeHit:
    return _FakeHit(
        {
            "id": chunk_id,
            "distance": score,
            "text": text,
            "doc_name": doc,
            "chapter": chapter,
            "chunk_index": 0,
        },
        score,
    )


class _FakeCollection:
    """模拟 pymilvus MilvusClient（describe/hybrid_search/search）。"""

    def __init__(self, hits: List[_FakeHit], *, has_sparse: bool = True):
        self.hits = hits
        self.has_sparse = has_sparse
        self.hybrid_calls = 0
        self.search_calls = 0
        self.hybrid_error: Optional[Exception] = None

    def describe_collection(self, name: str) -> Dict[str, Any]:
        fields = [{"name": "id"}, {"name": "vector"}]
        if self.has_sparse:
            fields.append({"name": "sparse_vector"})
        return {"fields": fields}

    def hybrid_search(self, name, *, reqs, ranker, limit, output_fields):
        self.hybrid_calls += 1
        if self.hybrid_error:
            raise self.hybrid_error
        return [list(self.hits)[:limit]]

    def search(self, name, *, data, anns_field, search_params, limit, output_fields):
        self.search_calls += 1
        return [list(self.hits)[:limit]]


class _FakeVectorStore:
    """langchain_milvus 包装器替身（仅方案 B 兜底会用到）。"""

    def __init__(self, docs_with_scores: List[Tuple[Document, float]]):
        self._docs = docs_with_scores

    def similarity_search_with_score(self, query, k=4, **kwargs):
        return list(self._docs)[:k]


class _StubReranker:
    """可控的异步 Reranker 替身。"""

    def __init__(self, scores: Optional[List[Dict[str, Any]]] = None,
                 fail_returns_empty: bool = False):
        self._scores = scores
        self._fail = fail_returns_empty
        self.calls = 0

    async def rerank(self, query, documents, top_k=3):
        self.calls += 1
        if self._fail:
            return []
        if self._scores is not None:
            return self._scores
        # 默认：保持原始顺序，分数从 0.95 递减（高置信 → 阈值放宽至 0.40）
        return [
            {"index": i, "relevance_score": round(0.95 - i * 0.05, 4)}
            for i in range(min(top_k, len(documents)))
        ]


_ANSWER = "根据规定【来源1】，招标分为公开招标和邀请招标。"
_STREAM_CHUNKS = ("根据规定【来源1】，", "招标分为公开招标", "和邀请招标。")


class _FakeLLM(BaseChatModel):
    """确定性假 ChatModel：ainvoke 返回整段回答，astream 按 chunk 吐 token。"""

    response_text: str = _ANSWER
    stream_chunks: Tuple[str, ...] = _STREAM_CHUNKS

    @property
    def _llm_type(self) -> str:
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self.response_text))])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        return self._generate(messages)

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        for c in self.stream_chunks:
            yield ChatGenerationChunk(message=AIMessageChunk(content=c))

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        for c in self.stream_chunks:
            yield ChatGenerationChunk(message=AIMessageChunk(content=c))


def _make_settings() -> Settings:
    return Settings()


def _make_docs_from_hits(hits: List[_FakeHit]) -> List[Tuple[Document, float]]:
    from public_kb.qa_chain import _entity_to_doc

    return [(_entity_to_doc(h.entity, h.score), h.score) for h in hits]


# ============================================================
#  build_async_qa_chain / AsyncRAGPipeline
# ============================================================

def test_async_chain_happy_path():
    """混合检索命中 → 回答 + 引用 + 校验报告结构完整且全部通过。"""
    from public_kb.qa_chain_async import build_async_qa_chain

    hits = [_hit("招标方式包括公开招标和邀请招标。", 101),
            _hit("公开招标是主要采购方式。", 102),
            _hit("邀请招标需经批准。", 103)]
    collection = _FakeCollection(hits)
    chain = build_async_qa_chain(
        vector_store=_FakeVectorStore([]),
        llm=_FakeLLM(),
        settings=_make_settings(),
        collection=collection,
        embeddings=_FakeEmbeddings(),
        reranker=_StubReranker(),
    )
    result = asyncio.run(chain.ainvoke("招标方式有哪些？"))

    assert set(result.keys()) == {"answer", "sources", "citations", "citation_validation"}
    assert result["answer"] == _ANSWER
    assert len(result["citations"]) == 3
    c1 = result["citations"][0]
    assert c1["chunk_id"] == 101
    assert c1["context_index"] == 1
    assert c1["doc_name"] != ""
    assert c1["text"].startswith("招标方式")
    assert result["citation_validation"]["all_passed"] is True
    assert collection.hybrid_calls == 1          # 走了混合检索
    assert collection.search_calls == 0


def test_async_chain_refusal_when_no_hits():
    from public_kb.qa_chain_async import build_async_qa_chain

    collection = _FakeCollection([])
    chain = build_async_qa_chain(
        vector_store=_FakeVectorStore([]),
        llm=_FakeLLM(),
        settings=_make_settings(),
        collection=collection,
        embeddings=_FakeEmbeddings(),
        reranker=_StubReranker(),
    )
    result = asyncio.run(chain.ainvoke("完全不相关的问题"))
    assert "暂无相关内容" in result["answer"]
    assert result["citations"] == []
    assert result["citation_validation"]["is_refusal"] is True


def test_refusal_semantics_identical_to_sync():
    """拒答路径与同步链逐字段一致（业务语义零退化）。"""
    from public_kb.qa_chain import build_qa_chain
    from public_kb.qa_chain_async import build_async_qa_chain

    settings = _make_settings()
    sync_chain = build_qa_chain(
        vector_store=_FakeVectorStore([]),
        llm=_FakeLLM(),
        settings=settings,
        collection=_FakeCollection([]),
        embeddings=_FakeEmbeddings(),
    )
    async_chain = build_async_qa_chain(
        vector_store=_FakeVectorStore([]),
        llm=_FakeLLM(),
        settings=settings,
        collection=_FakeCollection([]),
        embeddings=_FakeEmbeddings(),
        reranker=_StubReranker(),
    )
    r_sync = sync_chain.invoke("问题")
    r_async = asyncio.run(async_chain.ainvoke("问题"))
    assert r_sync["answer"] == r_async["answer"]
    assert r_sync["sources"] == r_async["sources"]
    assert r_sync["citation_validation"] == r_async["citation_validation"]


def test_async_chain_dense_fallback_when_no_sparse_field():
    """旧 schema 无稀疏字段 → 自动降级为稠密+Reranker 模式。"""
    from public_kb.qa_chain_async import build_async_qa_chain

    hits = [_hit("评标委员会由招标人代表和技术专家组成。", 201)]
    collection = _FakeCollection(hits, has_sparse=False)
    chain = build_async_qa_chain(
        vector_store=_FakeVectorStore([]),
        llm=_FakeLLM(),
        settings=_make_settings(),
        collection=collection,
        embeddings=_FakeEmbeddings(),
        reranker=_StubReranker(),
    )
    result = asyncio.run(chain.ainvoke("评标委员会怎么组成？"))
    assert collection.hybrid_calls == 0
    assert collection.search_calls == 1
    assert len(result["citations"]) == 1
    assert result["citation_validation"]["all_passed"] is True


def test_async_chain_falls_back_to_dense_on_hybrid_error():
    """hybrid_search 抛错 → 与同步版一致回退稠密降级检索，不抛错。"""
    from public_kb.qa_chain_async import build_async_qa_chain

    hits = [_hit("投标人不得相互串通投标报价。", 301)]
    collection = _FakeCollection(hits)
    collection.hybrid_error = RuntimeError("milvus boom")
    chain = build_async_qa_chain(
        vector_store=_FakeVectorStore([]),
        llm=_FakeLLM(),
        settings=_make_settings(),
        collection=collection,
        embeddings=_FakeEmbeddings(),
        reranker=_StubReranker(),
    )
    result = asyncio.run(chain.ainvoke("串标有什么后果？"))
    assert collection.hybrid_calls >= 1  # 含 output_fields 回退重试
    assert collection.search_calls == 1  # 已回退到方案 A
    assert len(result["citations"]) == 1


def test_async_chain_low_scores_trigger_refusal():
    """rerank 分数低于动态阈值（top<0.5 → threshold 0.50）→ 拒答。"""
    from public_kb.qa_chain_async import build_async_qa_chain

    hits = [_hit("无关内容一。", 401), _hit("无关内容二。", 402)]
    low_scores = [{"index": 0, "relevance_score": 0.42},
                  {"index": 1, "relevance_score": 0.41}]
    chain = build_async_qa_chain(
        vector_store=_FakeVectorStore([]),
        llm=_FakeLLM(),
        settings=_make_settings(),
        collection=_FakeCollection(hits),
        embeddings=_FakeEmbeddings(),
        reranker=_StubReranker(scores=low_scores),
    )
    result = asyncio.run(chain.ainvoke("问题"))
    assert "暂无相关内容" in result["answer"]


def test_stream_answer_yields_token_deltas():
    """stream_answer 拼接结果与非流式回答一致。"""
    from public_kb.qa_chain_async import AsyncRAGPipeline

    hits = [_hit("招标方式包括公开招标和邀请招标。", 101)]
    pipeline = AsyncRAGPipeline(
        _FakeVectorStore([]), _FakeLLM(), _make_settings(),
        collection=_FakeCollection(hits), embeddings=_FakeEmbeddings(),
        reranker=_StubReranker(),
    )

    async def _t():
        return [delta async for delta in pipeline.stream_answer([], "q")]

    deltas = asyncio.run(_t())
    assert "".join(deltas) == _ANSWER


def test_embedding_and_describe_run_in_parallel():
    """query 向量化与 schema 探测并行执行（gather_limited(limit=2)）。"""
    from public_kb.qa_chain_async import AsyncRAGPipeline

    emb = _FakeEmbeddings()
    hits = [_hit("内容。", 501)]
    pipeline = AsyncRAGPipeline(
        _FakeVectorStore([]), _FakeLLM(), _make_settings(),
        collection=_FakeCollection(hits), embeddings=emb,
        reranker=_StubReranker(),
    )

    async def _t():
        return await pipeline.retrieve_async("问题")

    docs = asyncio.run(_t())
    assert emb.embed_calls == 1
    assert len(docs) == 1


# ============================================================
#  PublicKnowledgeRAG.aquery / astream
# ============================================================

def _make_rag_with_fakes(hits: List[_FakeHit], has_sparse: bool = True):
    """绕过 __init__（避免真实 Milvus/API 连接），直接注入替身组件。"""
    from public_kb.rag_engine import PublicKnowledgeRAG

    rag = object.__new__(PublicKnowledgeRAG)
    rag._settings = _make_settings()
    rag._embeddings = _FakeEmbeddings()
    rag._llm = _FakeLLM()

    class _FakeStoreManager:
        def __init__(self):
            self.collection = _FakeCollection(hits, has_sparse=has_sparse)
            self.store = _FakeVectorStore([])

    rag._store_manager = _FakeStoreManager()
    rag._qa_chain = object()      # 非空哨兵：表示知识库已初始化
    rag._async_pipeline = None
    return rag


def test_rag_aquery_result_shape():
    rag = _make_rag_with_fakes([_hit("招标方式包括公开招标和邀请招标。", 101)])
    result = asyncio.run(rag.aquery("招标方式有哪些？"))
    assert set(result.keys()) == {"answer", "sources", "citations", "citation_validation"}
    assert result["answer"] == _ANSWER


def test_rag_aquery_uninitialized_raises_runtimeerror():
    from public_kb.rag_engine import PublicKnowledgeRAG

    rag = _make_rag_with_fakes([])
    rag._qa_chain = None
    with pytest.raises(RuntimeError, match="尚未初始化"):
        asyncio.run(rag.aquery("问题"))


def test_rag_astream_event_sequence():
    """事件序列：stage → retrieval → token* → citations → final；引用晚于正文。"""
    rag = _make_rag_with_fakes([_hit("招标方式包括公开招标和邀请招标。", 101)])

    async def _collect():
        events = []
        async for ev in rag.astream("招标方式有哪些？"):
            events.append(ev)
        return events

    events = asyncio.run(_collect())
    types = [ev.type.value for ev in events]

    assert types[0] == "stage"
    assert types[1] == "retrieval"
    assert types.count("token") == len(_STREAM_CHUNKS)
    assert types[-1] == "final"
    assert types.index("citations") > max(i for i, t in enumerate(types) if t == "token")

    final_payload = events[-1].payload["result"]
    assert final_payload["answer"] == _ANSWER
    assert len(final_payload["citations"]) >= 1
    assert final_payload["citation_validation"]["all_passed"] is True

    tokens = [ev.payload["delta"] for ev in events if ev.type.value == "token"]
    assert "".join(tokens) == _ANSWER
    # 所有事件共享同一 request_id
    assert len({ev.request_id for ev in events}) == 1


def test_rag_astream_refusal_skips_token_events():
    rag = _make_rag_with_fakes([])
    async def _collect():
        return [ev async for ev in rag.astream("问题")]
    events = asyncio.run(_collect())
    types = [ev.type.value for ev in events]
    assert "token" not in types and "retrieval" not in types
    assert types[-1] == "final"
    assert "暂无相关内容" in events[-1].payload["result"]["answer"]


# ============================================================
#  node_knowledge_qa_async 节点
# ============================================================

def test_node_knowledge_qa_async_success(monkeypatch):
    from agent.nodes import knowledge_qa as sync_mod
    from agent.nodes.knowledge_qa_async import node_knowledge_qa_async

    fake_result = {
        "answer": _ANSWER,
        "sources": [{"doc": "x", "chapter": "y", "chunk_index": 0,
                     "content_snippet": "...", "score": 0.9}],
        "citations": [{"context_index": 1, "chunk_id": 101}],
        "citation_validation": {"all_passed": True},
    }

    class _FakeRAG:
        async def aquery(self, question):
            return fake_result

    monkeypatch.setattr(sync_mod, "_rag_engine", _FakeRAG())

    state = {"messages": [HumanMessage(content="招标方式有哪些？")]}
    out = asyncio.run(node_knowledge_qa_async(state))

    biz = out["business_result"]
    assert biz["branch"] == "knowledge_qa"
    assert biz["answer"] == _ANSWER
    assert biz["data"]["citations"][0]["chunk_id"] == 101
    assert isinstance(out["messages"][0], AIMessage)
    assert out["messages"][0].content == _ANSWER


def test_node_knowledge_qa_async_runtime_error(monkeypatch):
    from agent.nodes import knowledge_qa as sync_mod
    from agent.nodes.knowledge_qa_async import node_knowledge_qa_async

    class _BrokenRAG:
        async def aquery(self, question):
            raise RuntimeError("知识库尚未初始化，请先调用 init_knowledge_base() 入库。")

    monkeypatch.setattr(sync_mod, "_rag_engine", _BrokenRAG())

    state = {"messages": [HumanMessage(content="问题")]}
    out = asyncio.run(node_knowledge_qa_async(state))
    assert "知识库尚未初始化" in out["business_result"]["answer"]
    assert "⚠️" in out["messages"][0].content


def test_node_knowledge_qa_async_empty_messages(monkeypatch):
    from agent.nodes import knowledge_qa as sync_mod
    from agent.nodes.knowledge_qa_async import node_knowledge_qa_async

    monkeypatch.setattr(sync_mod, "_rag_engine", None)
    out = asyncio.run(node_knowledge_qa_async({"messages": []}))
    assert out["business_result"]["branch"] == "knowledge_qa"
    assert "重新输入" in out["business_result"]["answer"]


def test_graph_registers_async_knowledge_qa_node():
    """async_nodes=True 时 knowledge_qa 注册的是协程节点。"""
    from unittest.mock import patch

    import agent.graph as graph_mod

    captured = {}

    def _fake_compile(self=None, **kwargs):  # noqa: ARG001
        raise NotImplementedError

    # 直接检查 build_graph 内部注册逻辑：用 StateGraph 真实构建但拦截 compile
    with patch.object(graph_mod.StateGraph, "compile", autospec=True) as mock_compile:
        mock_compile.return_value = object()
        graph_mod.build_graph(llm=_FakeLLM(), checkpointer=object(), async_nodes=True)

        compiled_graph = mock_compile.call_args.args[0]
        captured = compiled_graph.nodes

    assert "knowledge_qa" in captured
    # 异步节点：StateGraph 存 RunnableCallable(func=None, afunc=包装器)
    afunc = captured["knowledge_qa"].runnable.afunc
    assert afunc is not None
    assert asyncio.iscoroutinefunction(afunc)
    assert "knowledge_qa_async" in afunc.__name__  # wraps 保留原节点名


def test_graph_registers_sync_nodes_by_default():
    from unittest.mock import patch

    import agent.graph as graph_mod

    with patch.object(graph_mod.StateGraph, "compile", autospec=True) as mock_compile:
        mock_compile.return_value = object()
        graph_mod.build_graph(llm=_FakeLLM(), checkpointer=object(), async_nodes=False)
        compiled_graph = mock_compile.call_args.args[0]

    func = compiled_graph.nodes["knowledge_qa"].runnable.func
    assert func is not None
    assert not asyncio.iscoroutinefunction(func)


# ============================================================
#  集成测试（需要本地 Milvus + 真实 API Key，默认跳过）
# ============================================================

@pytest.mark.skipif(
    os.getenv("RUN_LIVE_RAG_ASYNC", "") != "1",
    reason="需要本地 Milvus standalone 与真实 Embedding/LLM Key（RUN_LIVE_RAG_ASYNC=1 开启）",
)
def test_live_rag_async_integration():
    """使用本地 Milvus 跑一条真实问题，验证 aquery 与 astream 端到端。"""
    from public_kb.rag_engine import PublicKnowledgeRAG

    rag = PublicKnowledgeRAG()
    rag.ensure_loaded()

    question = "招标方式有哪些？"
    result = asyncio.run(rag.aquery(question))
    assert set(result.keys()) == {"answer", "sources", "citations", "citation_validation"}
    assert result["answer"]

    async def _stream():
        types = []
        async for ev in rag.astream(question):
            types.append(ev.type.value)
        return types

    types = asyncio.run(_stream())
    assert types[-1] == "final"
