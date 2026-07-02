import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class ModelMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class ModelRequest:
    messages: list[ModelMessage]
    json_schema: dict | None = None
    max_tokens: int = 2048
    params: dict = field(default_factory=dict)


@dataclass
class ModelResponse:
    text: str
    parsed: Any | None
    input_tokens: int | None
    output_tokens: int | None
    latency_ms: float
    provider: str
    model: str
    cost_usd: float | None = None


class ProviderError(Exception):
    """Base class for provider failures."""


class ProviderTransientError(ProviderError):
    """Retryable: network failure, timeout, 429, 5xx."""


class ProviderBadOutputError(ProviderError):
    """Model produced output that violates the requested contract (bad JSON/schema)."""


_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json_output(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = _FENCE_RE.search(text)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    raise ProviderBadOutputError(f"output is not valid JSON: {text[:200]!r}")


class ModelProvider(ABC):
    name: str

    @abstractmethod
    def complete(self, model: str, request: ModelRequest) -> ModelResponse: ...

    def stream(self, model: str, request: ModelRequest) -> Iterator[str]:
        yield self.complete(model, request).text

    def embed(self, model: str, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError(f"{self.name} does not support embeddings")
