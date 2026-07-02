import json
import time
from typing import Iterator

import httpx

from mslearn.providers.base import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ProviderBadOutputError,
    ProviderError,
    ProviderTransientError,
)

_BASE = "https://openrouter.ai/api/v1"


def _json(resp: httpx.Response) -> dict:
    try:
        return resp.json()
    except json.JSONDecodeError as exc:
        raise ProviderBadOutputError(f"invalid JSON from openrouter: {resp.text[:200]!r}") from exc


class OpenRouterProvider(ModelProvider):
    name = "openrouter"

    def __init__(self, api_key: str, timeout: float = 300.0):
        self._client = httpx.Client(
            base_url=_BASE,
            timeout=timeout,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def _body(self, model: str, request: ModelRequest, stream: bool) -> dict:
        body = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "max_tokens": request.max_tokens,
            "stream": stream,
            "usage": {"include": True},
            **request.params,
        }
        if request.json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "strict": True, "schema": request.json_schema},
            }
        return body

    def _post(self, body: dict) -> httpx.Response:
        try:
            resp = self._client.post("/chat/completions", json=body)
            resp.raise_for_status()
            return resp
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 or exc.response.status_code == 429:
                raise ProviderTransientError(str(exc)) from exc
            raise ProviderError(str(exc)) from exc
        except httpx.TransportError as exc:
            raise ProviderTransientError(str(exc)) from exc

    def complete(self, model: str, request: ModelRequest) -> ModelResponse:
        start = time.perf_counter()
        data = _json(self._post(self._body(model, request, stream=False)))
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderBadOutputError(f"unexpected openrouter response shape: {str(data)[:200]}") from exc
        parsed = None
        if request.json_schema is not None:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ProviderBadOutputError(
                    f"invalid JSON from openrouter: {text[:200]!r}"
                ) from exc
        usage = data.get("usage") or {}
        return ModelResponse(
            text=text, parsed=parsed,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            latency_ms=(time.perf_counter() - start) * 1000,
            provider=self.name, model=model,
            cost_usd=usage.get("cost"),
        )

    def stream(self, model: str, request: ModelRequest) -> Iterator[str]:
        body = self._body(model, request, stream=True)
        try:
            with self._client.stream("POST", "/chat/completions", json=body) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[len("data: "):]
                    if payload.strip() == "[DONE]":
                        break
                    try:
                        chunk = json.loads(payload)
                        delta = chunk["choices"][0].get("delta", {}).get("content")
                    except (json.JSONDecodeError, KeyError, IndexError) as exc:
                        raise ProviderBadOutputError(f"malformed SSE chunk: {payload[:200]!r}") from exc
                    if delta:
                        yield delta
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 or exc.response.status_code == 429:
                raise ProviderTransientError(str(exc)) from exc
            raise ProviderError(str(exc)) from exc
        except httpx.TransportError as exc:
            raise ProviderTransientError(str(exc)) from exc
