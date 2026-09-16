"""固定回复边界、重复投递和敏感日志检查；不访问网络或读取真实凭证。"""

import json
import logging
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from scripts.feishu_connection_probe import FixedReplyHandler, SafeStatusHandler, TEST_REPLY


def make_event(chat_type="p2p", message_type="text", sender_type="user", message_id="om_test"):
    return NS(event=NS(message=NS(chat_type=chat_type, message_type=message_type,
                                message_id=message_id, content="PRIVATE_MESSAGE"),
                       sender=NS(sender_type=sender_type)))


def make_client():
    client = Mock()
    client.im.v1.message.reply.return_value = NS(success=lambda: True, code=0)
    return client


def test_reply_targets_original_message_and_skips_duplicate(capsys):
    client = make_client()
    handler = FixedReplyHandler(client)
    handler(make_event())
    handler(make_event())
    client.im.v1.message.reply.assert_called_once()
    request = client.im.v1.message.reply.call_args.args[0]
    assert request.message_id == "om_test"
    assert json.loads(request.request_body.content) == {"text": TEST_REPLY}
    other_client = make_client()
    FixedReplyHandler(other_client)(make_event())
    assert other_client.im.v1.message.reply.call_args.args[0].request_body.uuid == request.request_body.uuid
    output = capsys.readouterr().out
    assert "[REPLY_SENT]" in output and "[DUPLICATE_SKIPPED]" in output
    assert "PRIVATE_MESSAGE" not in output and "om_test" not in output


@pytest.mark.parametrize("kwargs", [
    {"chat_type": "group"}, {"sender_type": "app"}, {"sender_type": None},
    {"message_type": "image"}, {"message_id": None},
])
def test_out_of_scope_messages_never_send(kwargs):
    client = make_client()
    FixedReplyHandler(client)(make_event(**kwargs))
    client.im.v1.message.reply.assert_not_called()


@pytest.mark.parametrize("failure", ["api", "exception"])
def test_failed_reply_not_recorded_as_success(failure, capsys):
    client = make_client()
    reply = client.im.v1.message.reply
    if failure == "api":
        reply.return_value = NS(success=lambda: False, code=99991672, msg="PRIVATE_ERROR")
    else:
        reply.side_effect = TimeoutError("PRIVATE_ERROR")
    handler = FixedReplyHandler(client)
    handler(make_event())
    output = capsys.readouterr().out
    assert "[REPLY_FAILED]" in output
    assert "[REPLY_SENT]" not in output and "PRIVATE_ERROR" not in output
    reply.side_effect = None
    reply.return_value = NS(success=lambda: True, code=0)
    handler(make_event())
    assert reply.call_count == 2
    assert "[REPLY_SENT]" in capsys.readouterr().out


def test_sdk_sensitive_logs_are_not_forwarded(capsys):
    handler = SafeStatusHandler()
    for level, message in [(logging.INFO, "connected to wss://example.invalid?ticket=PRIVATE"),
                           (logging.ERROR, "API error: PRIVATE")]:
        handler.emit(logging.LogRecord("Lark", level, "", 0, message, (), None))
    output = capsys.readouterr().out
    assert "[CONNECTED]" in output and "[SDK_WARNING]" in output
    assert "PRIVATE" not in output and "wss://" not in output
