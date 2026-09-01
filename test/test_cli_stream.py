from __future__ import annotations

import pytest

from agent.__main__ import _render_stream_event
from agent.streaming import EventType, make_event


def test_stage_and_tokens_render_incrementally():
    stage = make_event(EventType.STAGE, "cli", {"stage": "retrieval_start"})
    token = make_event(EventType.TOKEN, "cli", {"delta": "答案"})
    rendered = _render_stream_event(stage) + _render_stream_event(token)
    assert "🔍" in rendered and "答案" in rendered
