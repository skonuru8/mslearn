import json

import pytest
import respx

from mslearn.providers.base import (
    ModelMessage,
    ModelRequest,
    ProviderBadOutputError,
    ProviderError,
    ProviderTransientError,
)
from mslearn.providers.openrouter import OpenRouterProvider

URL = "https://openrouter.ai/api/v1/chat/completions"


def req(schema=None):
    return ModelRequest(messages=[ModelMessage(role="user", content="hi")], json_schema=schema)


def ok_body(content, cost=0.0002):
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 9, "cost": cost},
    }


def test_missing_api_key_raises_clear_error():
    with pytest.raises(ProviderError, match="MSL_OPENROUTER_API_KEY"):
        OpenRouterProvider("")
    with pytest.raises(ProviderError, match="MSL_OPENROUTER_API_KEY"):
        OpenRouterProvider("   ")


@respx.mock
def test_complete_sends_auth_and_returns_cost():
    route = respx.post(URL).respond(json=ok_body("hello"))
    resp = OpenRouterProvider("sk-test").complete("deepseek/test-model", req())
    assert route.calls[0].request.headers["authorization"] == "Bearer sk-test"
    sent = json.loads(route.calls[0].request.content)
    assert sent["model"] == "deepseek/test-model"
    assert sent["usage"] == {"include": True}
    assert resp.text == "hello" and resp.cost_usd == 0.0002
    assert resp.input_tokens == 5 and resp.output_tokens == 9


@respx.mock
def test_complete_with_schema_sends_response_format_and_parses():
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    route = respx.post(URL).respond(json=ok_body('{"a": 2}'))
    resp = OpenRouterProvider("k").complete("m", req(schema))
    sent = json.loads(route.calls[0].request.content)
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["response_format"]["json_schema"]["schema"] == schema
    assert resp.parsed == {"a": 2}


@respx.mock
def test_bad_json_with_schema_raises():
    respx.post(URL).respond(json=ok_body("nope"))
    with pytest.raises(ProviderBadOutputError):
        OpenRouterProvider("k").complete("m", req({"type": "object"}))


@respx.mock
def test_429_is_transient():
    respx.post(URL).respond(status_code=429)
    with pytest.raises(ProviderTransientError):
        OpenRouterProvider("k").complete("m", req())


@respx.mock
def test_stream_parses_sse_deltas():
    sse = (
        'data: {"choices":[{"delta":{"content":"he"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post(URL).respond(content=sse, headers={"content-type": "text/event-stream"})
    assert list(OpenRouterProvider("k").stream("m", req())) == ["he", "llo"]


@respx.mock
def test_404_is_provider_error_not_transient():
    respx.post(URL).respond(status_code=404)
    with pytest.raises(ProviderError) as exc_info:
        OpenRouterProvider("k").complete("m", req())
    assert not isinstance(exc_info.value, ProviderTransientError)


@respx.mock
def test_stream_malformed_chunk_raises_bad_output():
    sse = "data: {not json}\n\n"
    respx.post(URL).respond(content=sse, headers={"content-type": "text/event-stream"})
    with pytest.raises(ProviderBadOutputError):
        list(OpenRouterProvider("k").stream("m", req()))


@respx.mock
def test_empty_choices_raises_bad_output():
    respx.post(URL).respond(json={"choices": [], "usage": {}})
    with pytest.raises(ProviderBadOutputError):
        OpenRouterProvider("k").complete("m", req())


@respx.mock
def test_null_content_with_finish_reason_length_raises_budget_hint():
    # Reasoning models (deepseek-v4-flash) can spend the entire completion
    # budget on hidden reasoning and return content: null. json.loads(None)
    # must never be reached — this must always surface as a clear
    # ProviderBadOutputError, not an uncaught TypeError / raw 500.
    body = {
        "choices": [{"message": {"content": None}, "finish_reason": "length"}],
        "usage": {},
    }
    respx.post(URL).respond(json=body)
    with pytest.raises(ProviderBadOutputError, match="reasoning"):
        OpenRouterProvider("k").complete("m", req())


@respx.mock
def test_null_content_without_length_finish_reason_still_raises():
    body = {
        "choices": [{"message": {"content": None}, "finish_reason": "stop"}],
        "usage": {},
    }
    respx.post(URL).respond(json=body)
    with pytest.raises(ProviderBadOutputError):
        OpenRouterProvider("k").complete("m", req())


@respx.mock
def test_empty_string_content_raises_bad_output_not_typeerror():
    body = {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}], "usage": {}}
    respx.post(URL).respond(json=body)
    with pytest.raises(ProviderBadOutputError):
        OpenRouterProvider("k").complete("m", req({"type": "object"}))


@respx.mock
def test_stream_with_only_reasoning_deltas_raises_bad_output():
    # A reasoning model can stream deltas that only ever populate
    # `reasoning`, never `content` — zero content frames, chat.py has
    # nothing to show, and the UI just looks hung. Must raise instead of
    # silently ending the stream.
    sse = (
        'data: {"choices":[{"delta":{"reasoning":"thinking..."}}]}\n\n'
        'data: {"choices":[{"delta":{"reasoning":"still thinking..."}}]}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post(URL).respond(content=sse, headers={"content-type": "text/event-stream"})
    with pytest.raises(ProviderBadOutputError, match="no content"):
        list(OpenRouterProvider("k").stream("m", req()))


@respx.mock
def test_stream_tolerates_trailing_usage_chunk():
    sse = (
        'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2,"cost":0.0001}}\n\n'
        "data: [DONE]\n\n"
    )
    respx.post(URL).respond(content=sse, headers={"content-type": "text/event-stream"})
    assert list(OpenRouterProvider("k").stream("m", req())) == ["hi"]


@respx.mock
def test_params_cannot_override_reserved_keys():
    route = respx.post(URL).respond(json=ok_body("x"))
    r = ModelRequest(messages=[ModelMessage(role="user", content="q")],
                     params={"model": "evil", "stream": True, "temperature": 0.3})
    OpenRouterProvider("k").complete("real-model", r)
    sent = json.loads(route.calls[0].request.content)
    assert sent["model"] == "real-model" and sent["stream"] is False
    assert sent["temperature"] == 0.3  # non-reserved params still pass through
