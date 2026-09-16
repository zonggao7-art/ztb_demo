"""飞书桥接的发布边界、会话隔离、重复投递与失败语义。"""

import json
from threading import Event
from types import SimpleNamespace as NS
from unittest.mock import Mock

import httpx
import pytest

from scripts.feishu_agent_bridge import (
    AgentReplyHandler, AgentResponseError, FAILURE_REPLY, format_final, query_agent,
)


def final_payload(answer="最终回答", status="complete", sources=None):
    return {"answer": answer, "business_result": {"answer": answer, "execution_status": status,
            "data": {"citations": sources or []}}}


def frame(kind, payload, request_id="req-1"):
    data = {"type": kind, "payload": payload, "request_id": request_id, "ts": 1}
    return f"id: {request_id}\r\nevent: {kind}\r\ndata: {json.dumps(data, ensure_ascii=False)}\r\n\r\n"


class FragmentedStream(httpx.SyncByteStream):
    def __init__(self, data):
        self.data = data.encode("utf-8")

    def __iter__(self):
        # 特意拆开中文 UTF-8 字符和 CRLF 分隔符。
        for i in range(0, len(self.data), 7):
            yield self.data[i:i + 7]


def transport_for(data):
    return httpx.MockTransport(lambda request: httpx.Response(
        200, headers={"content-type": "text/event-stream"}, stream=FragmentedStream(data)))


def test_http_uses_same_endpoint_and_only_final():
    seen = []

    def backend(request):
        seen.append(request)
        data = (frame("token", {"text": "未审核草稿"}) + frame("heartbeat", {})
                + frame("final", final_payload("已审核最终答案")))
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=FragmentedStream(data))

    text = query_agent("问题", "feishu:test", transport=httpx.MockTransport(backend))
    assert "已审核最终答案" in text and "未审核草稿" not in text
    assert str(seen[0].url) == "http://127.0.0.1:8000/chat/stream"
    assert json.loads(seen[0].content) == {"question": "问题", "thread_id": "feishu:test", "deadline_s": 90}


@pytest.mark.parametrize("data", [
    frame("token", {"text": "只有草稿"}),
    frame("error", {"message": "PRIVATE_ERROR"}),
    frame("cancelled", {}),
    frame("meta", {}) + frame("final", final_payload(), "other-request"),
    frame("final", {"answer": "未核验正文"}),
    frame("final", final_payload()).replace("event: final", "event: token"),
])
def test_incomplete_or_mismatched_stream_is_not_an_answer(data):
    with pytest.raises(AgentResponseError):
        query_agent("问题", "feishu:test", transport=transport_for(data))


@pytest.mark.parametrize("status,label", [
    ("clarify", "需要补充信息"), ("partial", "部分结果"),
    ("unsupported", "暂不支持"), ("insufficient_evidence", "证据不足"),
])
def test_business_status_preserved(status, label):
    text = format_final(final_payload(status=status))
    assert text.startswith(label) and "任务已完成" not in text


def test_citation_index_and_long_answer():
    sources = [{"context_index": 3, "doc_name": "示例法规", "chapter": "第一条"}]
    assert "【来源3】示例法规 · 第一条" in format_final(final_payload("正文【来源3】", sources=sources))
    text = format_final(final_payload("长" * 10000))
    assert "暂无法完整显示" in text and "长长长" not in text
    with pytest.raises(AgentResponseError):
        format_final(final_payload(sources=sources + sources))


def message(message_id="om_1", chat_id="oc_private", **overrides):
    values = dict(message_id=message_id, chat_id=chat_id, chat_type="p2p", message_type="text",
                  content=json.dumps({"text": "查询项目编号示例的中标详情"}))
    values.update(overrides)
    return NS(event=NS(message=NS(**values), sender=NS(sender_type="user")))


def drain(handler):
    handler.pool.submit(lambda: None).result(timeout=5)


def test_callback_returns_before_model_and_deduplicates_inflight():
    release, started = Event(), Event()

    def query(*args):
        started.set()
        assert release.wait(5)
        return "最终回答"

    send, report = Mock(return_value=True), Mock()
    handler = AgentReplyHandler(send, report, query=query)
    try:
        handler(message())
        assert started.wait(2)
        handler(message())
        send.assert_not_called()
        release.set()
        drain(handler)
        handler(message())
        drain(handler)
        send.assert_called_once_with("om_1", "最终回答")
    finally:
        release.set()
        handler.close()


def test_send_retry_reuses_answer_and_single_turn_ids_are_isolated():
    send = Mock(side_effect=[False, True, True])
    query = Mock(return_value="最终回答")
    handler = AgentReplyHandler(send, Mock(), query=query)
    try:
        handler(message())
        drain(handler)
        handler(message())
        drain(handler)
        assert query.call_count == 1 and send.call_count == 2
        handler(message("om_2"))
        drain(handler)
        first_id, second_id = (call.args[1] for call in query.call_args_list)
        assert first_id != second_id and first_id.startswith("feishu:")
        assert "oc_private" not in first_id
    finally:
        handler.close()


def test_backend_failure_is_truthful_and_private():
    send, report = Mock(return_value=True), Mock()
    handler = AgentReplyHandler(send, report, query=Mock(side_effect=TimeoutError("PRIVATE_DETAIL")))
    try:
        handler(message())
        drain(handler)
        send.assert_called_once_with("om_1", FAILURE_REPLY)
        assert "PRIVATE_DETAIL" not in str(report.call_args_list)
    finally:
        handler.close()


@pytest.mark.parametrize("content", ["bad-json", '[]', '{"text":""}', json.dumps({"text": "长" * 6000})],
                         ids=["invalid-json", "not-object", "empty", "over-limit"])
def test_invalid_or_long_question_does_not_call_agent(content):
    send, query = Mock(return_value=True), Mock()
    handler = AgentReplyHandler(send, Mock(), query=query)
    try:
        handler(message(content=content))
        drain(handler)
        query.assert_not_called()
        send.assert_called_once()
    finally:
        handler.close()


def test_group_messages_never_call_or_reply():
    send, query = Mock(), Mock()
    handler = AgentReplyHandler(send, Mock(), query=query)
    try:
        handler(message(chat_type="group"))
        drain(handler)
        query.assert_not_called()
        send.assert_not_called()
    finally:
        handler.close()


def test_queue_full_does_not_acknowledge_unaccepted_message():
    release = Event()
    handler = AgentReplyHandler(Mock(return_value=True), Mock(),
                                query=lambda *args: (release.wait(5) and "回答"))
    try:
        for i in range(8):
            handler(message(f"om_{i}"))
        with pytest.raises(RuntimeError, match="feishu_queue_full"):
            handler(message("om_overflow"))
        assert "om_overflow" not in handler.active
    finally:
        release.set()
        handler.close()
