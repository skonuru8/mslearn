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

    # Reasoning models on big synthesis prompts can legitimately take >5 min;
    # a 300s read timeout was killing whole synthesis runs mid-flight.
    def __init__(self, api_key: str, timeout: float = 600.0):
        key = (api_key or "").strip()
        if not key:
            raise ProviderError(
                "OpenRouter API key missing — set MSL_OPENROUTER_API_KEY in .env "
                "(cp .env.example .env) or switch to the offline profile in Advanced"
            )
        self._client = httpx.Client(
            base_url=_BASE,
            timeout=timeout,
            headers={"Authorization": f"Bearer {key}"},
        )

    def _body(self, model: str, request: ModelRequest, stream: bool) -> dict:
        body = {
            **request.params,
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "max_tokens": request.max_tokens,
            "stream": stream,
            "usage": {"include": True},
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
            choice = data["choices"][0]
            text = choice["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderBadOutputError(f"unexpected openrouter response shape: {str(data)[:200]}") from exc
        if not text:
            # Reasoning models (e.g. deepseek-v4-flash) can spend the entire
            # completion budget on hidden reasoning tokens, leaving
            # choices[0].message.content == null. json.loads(None) raises an
            # uncaught TypeError if we let it through — always fail with a
            # clear ProviderBadOutputError instead. Mirrors the ollama
            # done_reason guard in providers/ollama.py.
            finish_reason = choice.get("finish_reason")
            if finish_reason == "length":
                raise ProviderBadOutputError(
                    "openrouter returned empty content (finish_reason=length);"
                    " the model spent its completion budget on reasoning —"
                    " raise max_tokens"
                )
            raise ProviderBadOutputError(
                f"openrouter returned empty content (finish_reason={finish_reason!r})"
            )
        parsed = None
        if request.json_schema is not None:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                finish_reason = choice.get("finish_reason")
                raise ProviderBadOutputError(
                    f"invalid JSON from openrouter (finish_reason={finish_reason!r}):"
                    f" {text[:200]!r}"
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
        yielded = False
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
                        if not chunk.get("choices"):
                            continue
                        delta = chunk["choices"][0].get("delta", {}).get("content")
                    except (json.JSONDecodeError, KeyError, IndexError) as exc:
                        raise ProviderBadOutputError(f"malformed SSE chunk: {payload[:200]!r}") from exc
                    if delta:
                        yielded = True
                        yield delta
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500 or exc.response.status_code == 429:
                raise ProviderTransientError(str(exc)) from exc
            raise ProviderError(str(exc)) from exc
        except httpx.TransportError as exc:
            raise ProviderTransientError(str(exc)) from exc
        if not yielded:
            # A stream that only carried reasoning deltas (no `content`) ends
            # silently otherwise — chat.py has nothing to turn into an error
            # frame and the UI just looks hung. Fail loudly instead.
            raise ProviderBadOutputError(
                "stream ended with no content (reasoning budget exhausted?)"
            )
