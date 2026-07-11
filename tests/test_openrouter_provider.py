import json

import httpx
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
def test_ssl_error_is_transient_and_retryable():
    import ssl

    respx.post(URL).mock(side_effect=ssl.SSLError("[SSL: SSLV3_ALERT_BAD_RECORD_MAC] sslv3 alert bad record mac"))
    with pytest.raises(ProviderTransientError):
        OpenRouterProvider("sk-test").complete("m", req())


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
def test_complete_salvages_malformed_json():
    # deepseek-v4-flash occasionally emits JSON with unescaped inner quotes.
    # json-repair should recover a usable dict instead of us raising.
    body = {
        "choices": [
            {
                "message": {"content": '{"claims": [{"text": "a" "b", "kind": "claim"}]}'},
                "finish_reason": "stop",
            }
        ],
        "usage": {},
    }
    respx.post(URL).respond(json=body)
    resp = OpenRouterProvider("k").complete("m", req({"type": "object"}))
    assert isinstance(resp.parsed, dict)
    assert "claims" in resp.parsed


@respx.mock
def test_complete_still_raises_on_unsalvageable():
    body = {
        "choices": [
            {"message": {"content": "not json at all <<<"}, "finish_reason": "stop"}
        ],
        "usage": {},
    }
    respx.post(URL).respond(json=body)
    with pytest.raises(ProviderBadOutputError):
        OpenRouterProvider("k").complete("m", req({"type": "object"}))


@respx.mock
def test_truncated_json_error_carries_finish_reason():
    # The user's exact symptom: a reasoning model truncates mid-JSON
    # (finish_reason=length), producing degenerate content like
    # '{ "claims": … \t\n \t\n '. Without finish_reason in the message this
    # is indistinguishable from a model that just wrote garbage.
    body = {
        "choices": [{"message": {"content": '{ "claims": '}, "finish_reason": "length"}],
        "usage": {},
    }
    respx.post(URL).respond(json=body)
    with pytest.raises(ProviderBadOutputError, match="finish_reason='length'"):
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


def test_default_timeout_bounds_connect_and_pool_separately():
    # A single flat 600s timeout let connect/pool exhaustion masquerade as a
    # long model read (incident: a request "read" for 4,601s before the
    # connection layer noticed the socket was dead). Connect/pool must fail
    # fast; only the model's own response time keeps the generous budget.
    timeout = OpenRouterProvider("k")._client.timeout
    assert isinstance(timeout, httpx.Timeout)
    assert timeout.connect == 15.0
    assert timeout.read == 600.0
    assert timeout.write == 60.0
    assert timeout.pool == 60.0


@respx.mock
def test_image_message_becomes_content_array():
    route = respx.post(URL).respond(json=ok_body("a red square"))
    data_url = "data:image/png;base64,AAAABBBB"
    request = ModelRequest(
        messages=[ModelMessage(role="user", content="describe", images=[data_url])]
    )
    OpenRouterProvider("sk-test").complete("openai/gpt-4o-mini", request)
    sent = json.loads(route.calls[0].request.content)
    content = sent["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "describe"}
    assert content[1] == {"type": "image_url", "image_url": {"url": data_url}}


@respx.mock
def test_text_only_message_stays_plain_string():
    route = respx.post(URL).respond(json=ok_body("hi"))
    OpenRouterProvider("sk-test").complete("m", req())
    sent = json.loads(route.calls[0].request.content)
    assert sent["messages"][0]["content"] == "hi"
