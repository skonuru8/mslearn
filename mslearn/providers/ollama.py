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


class OllamaProvider(ModelProvider):
    name = "ollama"

    def __init__(self, base_url: str, timeout: float = 300.0):
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def _body(self, model: str, request: ModelRequest, stream: bool) -> dict:
        body = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
            "stream": stream,
            "options": {"num_predict": request.max_tokens, **request.params},
        }
        if request.json_schema is not None:
            body["format"] = request.json_schema
        return body

    def _post(self, path: str, body: dict) -> httpx.Response:
        try:
            resp = self._client.post(path, json=body)
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
        data = self._post("/api/chat", self._body(model, request, stream=False)).json()
        text = data["message"]["content"]
        parsed = None
        if request.json_schema is not None:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ProviderBadOutputError(f"invalid JSON from ollama: {text[:200]!r}") from exc
        return ModelResponse(
            text=text, parsed=parsed,
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
            latency_ms=(time.perf_counter() - start) * 1000,
            provider=self.name, model=model,
        )

    def stream(self, model: str, request: ModelRequest) -> Iterator[str]:
        resp = self._post("/api/chat", self._body(model, request, stream=True))
        for line in resp.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            content = chunk.get("message", {}).get("content", "")
            if content:
                yield content

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        data = self._post("/api/embed", {"model": model, "input": texts}).json()
        return data["embeddings"]
