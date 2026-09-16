"""DeepSeek request compatibility through the real SDK and mock HTTP transport."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from agent.router import RouterDecision, ROUTER_TOOLS, build_router_node, build_router_node_async
from agent.nodes.fallback import node_fallback
from public_kb.llm_factory import BudgetedChatOpenAI


def response_for(request, calls):
    payload = json.loads(request.content)
    calls.append(payload)
    if payload.get("thinking") != {"type": "disabled"} or "response_format" in payload:
        return httpx.Response(400, json={"error": {
            "message": "Thinking mode does not support this tool_choice", "type": "invalid_request_error",
        }})
    name = payload["tools"][0]["function"]["name"]
    return httpx.Response(200, json={
        "id": "offline", "object": "chat.completion", "created": 0, "model": payload["model"],
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "tool_calls": [{
                "id": "call_test", "type": "function", "function": {
                    "name": name, "arguments": json.dumps({"intent": "knowledge_qa", "reason": "法规问题"}),
                },
            }],
        }}],
    })


@pytest.mark.parametrize("async_mode", [False, True])
@pytest.mark.parametrize("method", [None, "function_calling", "json_schema"])
def test_structured_requests_disable_thinking_and_validate(method, async_mode):
    calls = []
    transport = httpx.MockTransport(lambda request: response_for(request, calls))

    async def run():
        with httpx.Client(transport=transport) as sync_client:
            async with httpx.AsyncClient(transport=transport) as async_client:
                original_body = {"thinking": {"type": "enabled"}, "custom_option": "preserved"}
                model = BudgetedChatOpenAI(
                    model="deepseek-v4-flash-vision-exp", api_key="offline", base_url="https://api.deepseek.com/v1",
                    http_client=sync_client, http_async_client=async_client, max_retries=0,
                    extra_body=original_body,
                )
                structured = model.with_structured_output(RouterDecision, **({"method": method} if method else {}))
                for _ in range(3):
                    result = await structured.ainvoke("招标方式有哪些？") if async_mode else structured.invoke("招标方式有哪些？")
                    assert isinstance(result, RouterDecision)
                    assert result.intent == "knowledge_qa"
                assert model.extra_body == original_body
                assert original_body["thinking"]["type"] == "enabled"
                assert model._get_request_payload("plain answer")["extra_body"] == original_body
    asyncio.run(run())
    assert len(calls) == 3
    assert all(payload["custom_option"] == "preserved" for payload in calls)
    assert all(payload["tool_choice"] != "auto" for payload in calls)


@pytest.mark.parametrize("tool_choice", ["required", "auto", "route_knowledge_qa"])
def test_tools_keep_selection_policy_without_mutating_shared_model(tool_choice):
    model = BudgetedChatOpenAI(model="test", api_key="offline", base_url="https://api.deepseek.com")
    bound = model.bind_tools(ROUTER_TOOLS, tool_choice=tool_choice, extra_body={"custom_option": 1})
    assert bound.kwargs["extra_body"] == {"custom_option": 1, "thinking": {"type": "disabled"}}
    assert bound.kwargs["tool_choice"] == (
        {"type": "function", "function": {"name": tool_choice}}
        if tool_choice == "route_knowledge_qa" else tool_choice
    )
    assert model.extra_body is None


def test_invalid_router_enum_still_fails_validation():
    def handler(request):
        body = response_for(request, []).json()
        body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = json.dumps({
            "intent": "delete_database", "reason": "invalid",
        })
        return httpx.Response(200, json=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = BudgetedChatOpenAI(
            model="test", api_key="offline", base_url="https://api.deepseek.com", http_client=client,
        )
        with pytest.raises(ValidationError):
            model.with_structured_output(RouterDecision).invoke("test")


def test_explicit_json_mode_remains_json_mode():
    model = BudgetedChatOpenAI(model="test", api_key="offline", base_url="https://api.deepseek.com")
    structured = model.with_structured_output(RouterDecision, method="json_mode")
    assert structured.first.kwargs["response_format"] == {"type": "json_object"}
    assert structured.first.bound.extra_body == {"thinking": {"type": "disabled"}}
    assert model.extra_body is None


@pytest.mark.parametrize("endpoint", ["https://openrouter.ai/api/v1", "https://api.openai.com/v1", "https://api.deepseek.com.untrusted.invalid"])
def test_other_providers_keep_original_defaults(endpoint):
    model = BudgetedChatOpenAI(model="deepseek/test", api_key="offline", base_url=endpoint)
    structured = model.with_structured_output(RouterDecision)
    assert structured.first.kwargs["response_format"] is RouterDecision
    assert "extra_body" not in model.bind_tools(ROUTER_TOOLS, tool_choice="required").kwargs


@pytest.mark.parametrize("async_mode", [False, True])
def test_router_failure_is_not_misreported_as_unclear_question(async_mode):
    model = MagicMock()
    structured = model.with_structured_output.return_value
    structured.invoke.side_effect = RuntimeError("response_format not supported")
    structured.ainvoke = AsyncMock(side_effect=RuntimeError("response_format not supported"))
    model.bind_tools.return_value.invoke.side_effect = RuntimeError("secret provider body")
    model.bind_tools.return_value.ainvoke = AsyncMock(side_effect=RuntimeError("secret provider body"))
    state = {"messages": [HumanMessage(content="招标方式有哪些？")]}
    router = build_router_node_async(model) if async_mode else build_router_node(model)
    failed = asyncio.run(router(state)) if async_mode else router(state)
    answer = node_fallback(failed)["business_result"]["answer"]
    assert "模型" in answer and "不太确定" not in answer
    assert "secret" not in answer
    structured.invoke.side_effect = None
    structured.invoke.return_value = RouterDecision(intent="knowledge_qa", reason="法规问题")
    structured.ainvoke = AsyncMock(return_value=RouterDecision(intent="knowledge_qa", reason="法规问题"))
    recovered = asyncio.run(router(state)) if async_mode else router(state)
    assert recovered["router_intent"] == "knowledge_qa"
