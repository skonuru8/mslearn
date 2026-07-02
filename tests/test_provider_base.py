import pytest

from mslearn.providers.base import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ProviderBadOutputError,
    parse_json_output,
)


def test_parse_json_output_raw():
    assert parse_json_output('{"a": 1}') == {"a": 1}


def test_parse_json_output_fenced():
    text = 'Here you go:\n```json\n{"a": [1, 2]}\n```\nDone.'
    assert parse_json_output(text) == {"a": [1, 2]}


def test_parse_json_output_garbage_raises():
    with pytest.raises(ProviderBadOutputError):
        parse_json_output("not json at all")


class FakeProvider(ModelProvider):
    name = "fake"

    def complete(self, model, request):
        return ModelResponse(text="hello", parsed=None, input_tokens=1, output_tokens=1,
                             latency_ms=0.1, provider=self.name, model=model)


def test_default_stream_falls_back_to_complete():
    p = FakeProvider()
    req = ModelRequest(messages=[ModelMessage(role="user", content="hi")])
    assert list(p.stream("m", req)) == ["hello"]


def test_default_embed_not_supported():
    with pytest.raises(NotImplementedError):
        FakeProvider().embed("m", ["x"])
