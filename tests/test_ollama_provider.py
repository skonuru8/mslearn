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
from mslearn.providers.ollama import OllamaProvider

BASE = "http://localhost:11434"


def req(schema=None):
    return ModelRequest(messages=[ModelMessage(role="user", content="hi")], json_schema=schema)


@respx.mock
def test_complete_plain_text():
    respx.post(f"{BASE}/api/chat").respond(json={
        "message": {"content": "hello"}, "prompt_eval_count": 7, "eval_count": 3,
    })
    resp = OllamaProvider(BASE).complete("qwen-test", req())
    assert resp.text == "hello"
    assert resp.input_tokens == 7 and resp.output_tokens == 3
    assert resp.provider == "ollama" and resp.model == "qwen-test"


@respx.mock
def test_complete_with_schema_sends_format_and_parses():
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    route = respx.post(f"{BASE}/api/chat").respond(json={
        "message": {"content": '{"a": 1}'}, "prompt_eval_count": 1, "eval_count": 1,
    })
    resp = OllamaProvider(BASE).complete("m", req(schema))
    sent = json.loads(route.calls[0].request.content)
    assert sent["format"] == schema
    assert resp.parsed == {"a": 1}


@respx.mock
def test_bad_json_with_schema_raises_bad_output():
    respx.post(f"{BASE}/api/chat").respond(json={"message": {"content": "oops"}})
    with pytest.raises(ProviderBadOutputError):
        OllamaProvider(BASE).complete("m", req({"type": "object"}))


@respx.mock
def test_5xx_and_network_are_transient():
    respx.post(f"{BASE}/api/chat").respond(status_code=500)
    with pytest.raises(ProviderTransientError):
        OllamaProvider(BASE).complete("m", req())
    respx.post(f"{BASE}/api/chat").mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(ProviderTransientError):
        OllamaProvider(BASE).complete("m", req())


@respx.mock
def test_stream_yields_chunks():
    lines = [
        json.dumps({"message": {"content": "he"}, "done": False}),
        json.dumps({"message": {"content": "llo"}, "done": True}),
    ]
    respx.post(f"{BASE}/api/chat").respond(content="\n".join(lines))
    assert list(OllamaProvider(BASE).stream("m", req())) == ["he", "llo"]


@respx.mock
def test_embed():
    respx.post(f"{BASE}/api/embed").respond(json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]})
    vecs = OllamaProvider(BASE).embed("emb-model", ["a", "b"])
    assert vecs == [[0.1, 0.2], [0.3, 0.4]]


@respx.mock
def test_429_is_transient():
    respx.post(f"{BASE}/api/chat").respond(status_code=429)
    with pytest.raises(ProviderTransientError):
        OllamaProvider(BASE).complete("m", req())


@respx.mock
def test_404_is_provider_error_not_transient():
    respx.post(f"{BASE}/api/chat").respond(status_code=404)
    with pytest.raises(ProviderError) as exc_info:
        OllamaProvider(BASE).complete("m", req())
    assert not isinstance(exc_info.value, ProviderTransientError)
