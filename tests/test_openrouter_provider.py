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
