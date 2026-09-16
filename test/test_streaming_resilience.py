"""流式故障注入：真实本地 HTTP 连接池、真实图、可控外部依赖。"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest
from openai import APITimeoutError

from agent.__main__ import run_interactive_stream, run_single_stream
from agent.graph import AgentGraph
from agent.streaming import EventType, make_event, parse_sse
from agent.streaming.context import _STREAM_ACTIVE, current_request_id, emit
from public_kb.embedding_service import _SafeEmbeddings


class EmbeddingHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if payload["input"] == ["hold"]:
            self.server.started.set()
            time.sleep(0.25)
        failed = payload["input"] == ["embedding_error"]
        body = json.dumps(
            {"error": {"message": "injected", "type": "server_error"}}
            if failed else {
                "object": "list",
                "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                "model": "test", "usage": {"prompt_tokens": 1, "total_tokens": 1},
            }
        ).encode()
        self.send_response(503 if failed else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
            self.wfile.flush()
        except OSError:
            pass

    def log_message(self, *args):
        pass


@pytest.fixture
def embedding_endpoint():
    server = ThreadingHTTPServer(("127.0.0.1", 0), EmbeddingHandler)
    server.started = threading.Event()
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_port}/v1", started=server.started)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_cli_reuses_real_embedding_pool_after_failure(monkeypatch, embedding_endpoint):
    client = httpx.AsyncClient(
        limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        timeout=0.2, trust_env=False,
    )
    embeddings = _SafeEmbeddings(
        model="test", api_key="test", base_url=embedding_endpoint.url,
        check_embedding_ctx_length=False, max_retries=0, timeout=0.2,
        http_async_client=client,
    )
    outcomes = []
    loops = []

    class HTTPAgent:
        async def astream(self, question, **kwargs):
            loops.append(asyncio.get_running_loop())
            try:
                await embeddings.aembed_query(question)
                outcomes.append("ok")
            except Exception as exc:
                outcomes.append(type(exc).__name__)
            if question == "last":
                await client.aclose()
            yield make_event(EventType.FINAL, "http", {"answer": "done"})

        def get_state(self, thread_id):
            return {"business_result": {"branch": "knowledge_qa"}}

    questions = iter(["first", "embedding_error", "after", "last", "quit"])
    monkeypatch.setattr("builtins.input", lambda _: next(questions))
    run_interactive_stream(HTTPAgent())
    assert outcomes == ["ok", "InternalServerError", "ok", "ok"]
    assert len(set(loops)) == 1


@pytest.mark.parametrize("failure", ["pool_timeout", "read_timeout", "cancel"])
def test_real_http_timeout_and_cancel_release_pool(embedding_endpoint, failure):
    async def scenario():
        timeout = httpx.Timeout(1, pool=0.04, read=0.06 if failure == "read_timeout" else 1)
        async with httpx.AsyncClient(
            timeout=timeout, trust_env=False,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        ) as client:
            embeddings = _SafeEmbeddings(
                model="test", api_key="test", base_url=embedding_endpoint.url,
                check_embedding_ctx_length=False, max_retries=0,
                http_async_client=client, timeout=timeout,
            )
            held = asyncio.create_task(embeddings.aembed_query("hold"))
            try:
                assert await asyncio.to_thread(embedding_endpoint.started.wait, 1)
                if failure == "pool_timeout":
                    with pytest.raises(APITimeoutError) as error:
                        await embeddings.aembed_query("queued")
                    assert isinstance(error.value.__cause__, httpx.PoolTimeout)
                if failure == "read_timeout":
                    with pytest.raises(APITimeoutError) as error:
                        await held
                    assert isinstance(error.value.__cause__, httpx.ReadTimeout)
                else:
                    held.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await held
                for _ in range(20):
                    assert await embeddings.aembed_query("recovered") == [0.1, 0.2]
            finally:
                held.cancel()
                await asyncio.gather(held, return_exceptions=True)

    asyncio.run(scenario())


class FaultRAG:
    def __init__(self):
        self.closed = 0

    async def astream(self, question):
        try:
            yield make_event(EventType.STAGE, "rag", {"stage": "retrieval_start"})
            if question == "timeout":
                raise APITimeoutError(request=httpx.Request("POST", "http://test/embeddings"))
            if question == "runtime":
                raise RuntimeError("Event loop is closed")
            if question == "hang":
                await asyncio.Event().wait()
            if question == "stream_hang":
                yield make_event(EventType.TOKEN, "rag", {"delta": "未完成正文"})
                await asyncio.Event().wait()
            if question == "cancel":
                raise asyncio.CancelledError()
            if question == "partial_error":
                yield make_event(EventType.TOKEN, "rag", {"delta": "未完成正文"})
                raise httpx.ReadError("injected mid-stream disconnect")
            if question == "truncated":
                yield make_event(EventType.TOKEN, "rag", {"delta": "未完成正文"})
                return
            answer = "知识库未找到足够依据" if question == "refusal" else f"答案:{question}"
            if question != "refusal":
                yield make_event(EventType.TOKEN, "rag", {"delta": answer})
            yield make_event(EventType.FINAL, "rag", {"result": {
                "answer": answer, "sources": [], "citations": [],
            }})
        finally:
            self.closed += 1


@pytest.fixture
def fault_graph(monkeypatch):
    # Fault injection targets legacy nodes; select that path explicitly.
    monkeypatch.setenv("AGENT_EXECUTION_MODE", "legacy")
    rag = FaultRAG()

    async def router(state):
        question = state["messages"][-1].content
        intent = "fallback" if question == "out_of_scope" else "knowledge_qa"
        emit(EventType.STAGE, {"stage": "router_done", "intent": intent})
        return {"router_intent": intent}

    monkeypatch.setattr("agent.router.build_router_node_async", lambda _: router)
    monkeypatch.setattr("agent.nodes.knowledge_qa._get_rag", lambda: rag)
    graph = AgentGraph(llm=object(), async_enabled=True, checkpointer_backend="memory")
    return graph, rag


async def collect(graph, question, thread_id="resilience", **kwargs):
    return [event async for event in graph.astream(question, thread_id=thread_id, **kwargs)]


def assert_envelope(events):
    terminals = {EventType.FINAL, EventType.ERROR, EventType.CANCELLED}
    assert events[0].type is EventType.META
    assert sum(event.type is EventType.META for event in events) == 1
    assert sum(event.type in terminals for event in events) == 1
    assert events[-1].type in terminals
    assert len({event.request_id for event in events}) == 1


@pytest.mark.parametrize("failure", ["timeout", "runtime", "partial_error", "truncated"])
def test_failure_is_not_sticky_and_has_one_terminal(fault_graph, failure):
    graph, rag = fault_graph

    async def scenario():
        failed = await collect(graph, failure)
        assert_envelope(failed)
        assert graph.get_state("resilience")["business_result"]["branch"] == "fallback"
        assert "尚未初始化" not in str(failed[-1].payload)
        out_of_scope = await collect(graph, "out_of_scope")
        assert_envelope(out_of_scope)
        assert "功能暂时不可用" not in out_of_scope[-1].payload["answer"]
        for turn in range(12):
            recovered = await collect(graph, f"normal-{turn}")
            assert_envelope(recovered)
            assert recovered[-1].payload["answer"] == f"答案:normal-{turn}"
            assert graph.get_state("resilience")["business_result"]["branch"] == "knowledge_qa"
        assert not _STREAM_ACTIVE.get()
        assert not current_request_id()

    asyncio.run(scenario())
    assert rag.closed == 13


def test_refusal_is_visible_without_duplicate_retrieval_stage(fault_graph, monkeypatch, capsys):
    graph, _ = fault_graph
    questions = iter(["refusal", "normal", "quit"])
    monkeypatch.setattr("builtins.input", lambda _: next(questions))
    run_interactive_stream(graph)
    output = capsys.readouterr().out
    assert output.count("知识库未找到足够依据") == 1
    assert output.count("答案:normal") == 1
    assert output.count("retrieval_start") == 2


def test_single_stream_does_not_print_answer_twice(fault_graph, capsys):
    graph, _ = fault_graph
    run_single_stream(graph, "normal")
    assert capsys.readouterr().out.count("答案:normal") == 1


def test_deadline_cancels_work_and_next_turn_recovers(fault_graph):
    graph, rag = fault_graph

    async def scenario():
        events = await asyncio.wait_for(collect(graph, "hang", deadline_s=0.05), 1)
        assert_envelope(events)
        assert events[-1].type is EventType.ERROR
        assert events[-1].payload["code"] == "deadline_exceeded"
        assert rag.closed == 1
        recovered = await collect(graph, "normal")
        assert recovered[-1].payload["answer"] == "答案:normal"

    asyncio.run(scenario())


def test_cancellation_propagates_and_next_turn_recovers(fault_graph):
    graph, rag = fault_graph

    async def scenario():
        task = asyncio.create_task(collect(graph, "hang"))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert rag.closed == 1
        events = await collect(graph, "normal")
        assert_envelope(events)
        assert events[-1].payload["answer"] == "答案:normal"

    asyncio.run(scenario())


def test_internal_cancellation_is_not_a_successful_answer(fault_graph):
    graph, _ = fault_graph

    async def scenario():
        events = await collect(graph, "cancel")
        assert_envelope(events)
        assert events[-1].type is EventType.ERROR
        recovered = await collect(graph, "normal")
        assert recovered[-1].payload["answer"] == "答案:normal"

    asyncio.run(scenario())


def test_concurrent_failure_isolation_200_requests(fault_graph):
    graph, _ = fault_graph

    async def scenario():
        limit = asyncio.Semaphore(16)

        async def session(index):
            async with limit:
                question = "timeout" if index % 5 == 0 else f"normal-{index}"
                events = await collect(graph, question, thread_id=f"load-{index}")
                assert_envelope(events)
                state = graph.get_state(f"load-{index}")["business_result"]
                assert state["branch"] == ("fallback" if question == "timeout" else "knowledge_qa")
                return events[0].request_id

        request_ids = await asyncio.gather(*(session(index) for index in range(200)))
        assert len(set(request_ids)) == 200

    asyncio.run(scenario())


def test_sse_heartbeat_delivers_bytes_and_closes_source(monkeypatch):
    pytest.importorskip("fastapi")
    import service.api as api
    from service.schemas import ChatRequest

    closed = []

    class SlowAgent:
        _settings = SimpleNamespace(stream_heartbeat_s=0.01)

        async def astream(self, *args, **kwargs):
            try:
                await asyncio.sleep(0.04)
                yield make_event(EventType.FINAL, "inner", {"answer": "done"})
            finally:
                closed.append(True)

    monkeypatch.setattr(api.app.state, "agent", SlowAgent(), raising=False)

    async def scenario():
        response = await api.chat_stream(ChatRequest(question="slow", thread_id="sse"))
        events = [parse_sse(chunk) async for chunk in response.body_iterator]
        assert EventType.HEARTBEAT in [event.type for event in events]
        assert events[-1].type is EventType.FINAL
        assert len({event.request_id for event in events}) == 1
        assert closed == [True]

    asyncio.run(scenario())


def test_sse_disconnect_closes_graph_and_next_request_recovers(fault_graph, monkeypatch):
    pytest.importorskip("fastapi")
    import service.api as api
    from service.schemas import ChatRequest

    graph, rag = fault_graph
    monkeypatch.setattr(api.app.state, "agent", graph, raising=False)

    async def scenario():
        baseline_tasks = asyncio.all_tasks()
        response = await api.chat_stream(ChatRequest(question="stream_hang", thread_id="disconnect"))
        async for chunk in response.body_iterator:
            if parse_sse(chunk).type is EventType.TOKEN:
                break
        await response.body_iterator.aclose()
        assert rag.closed == 1
        await asyncio.sleep(0)
        assert not (asyncio.all_tasks() - baseline_tasks)
        assert not _STREAM_ACTIVE.get()
        assert not current_request_id()
        response = await api.chat_stream(ChatRequest(question="normal", thread_id="disconnect"))
        events = [parse_sse(chunk) async for chunk in response.body_iterator]
        assert_envelope(events)
        assert events[-1].payload["answer"] == "答案:normal"
        assert graph.get_state("disconnect")["business_result"]["branch"] == "knowledge_qa"
        assert rag.closed == 2

    asyncio.run(scenario())


def test_rag_failed_initialization_is_retryable(monkeypatch):
    import agent.nodes.knowledge_qa as module
    import public_kb

    attempts = []

    class InitializingRAG:
        def ensure_loaded(self):
            attempts.append(self)
            if len(attempts) == 1:
                raise RuntimeError("temporary Milvus connection failure")

    monkeypatch.setattr(module, "_rag_engine", None)
    monkeypatch.setattr(public_kb, "PublicKnowledgeRAG", InitializingRAG)
    with pytest.raises(RuntimeError):
        module._get_rag()
    assert module._rag_engine is None
    recovered = module._get_rag()
    assert recovered is module._get_rag()
    assert len(attempts) == 2


def test_concurrent_rag_initialization_never_exposes_partial_instance(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import agent.nodes.knowledge_qa as module
    import public_kb

    constructed = []

    class InitializingRAG:
        ready = False

        def __init__(self):
            constructed.append(self)

        def ensure_loaded(self):
            time.sleep(0.02)
            self.ready = True

    def get_ready(_):
        instance = module._get_rag()
        assert instance.ready
        return instance

    monkeypatch.setattr(module, "_rag_engine", None)
    monkeypatch.setattr(public_kb, "PublicKnowledgeRAG", InitializingRAG)
    with ThreadPoolExecutor(max_workers=16) as executor:
        instances = list(executor.map(get_ready, range(64)))
    assert len(constructed) == 1
    assert all(instance is instances[0] for instance in instances)
