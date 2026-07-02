import json
import subprocess
import time

from mslearn.providers.base import (
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ProviderTransientError,
    parse_json_output,
)


class ClaudeCodeProvider(ModelProvider):
    """Headless Claude Code (`claude -p`), subscription-authenticated (non-bare mode)."""

    name = "claude_code"

    def __init__(self, binary: str = "claude", timeout: float = 600.0):
        self._binary = binary
        self._timeout = timeout

    def complete(self, model: str, request: ModelRequest) -> ModelResponse:
        system = "\n".join(m.content for m in request.messages if m.role == "system")
        prompt = "\n\n".join(m.content for m in request.messages if m.role != "system")
        if request.json_schema is not None:
            prompt += (
                "\n\nRespond with ONLY a JSON object matching this JSON schema"
                " (no prose, no code fences):\n"
                + json.dumps(request.json_schema)
            )
        cmd = [self._binary, "-p", "--output-format", "json"]
        if system:
            cmd += ["--append-system-prompt", system]
        if model and model != "default":
            cmd += ["--model", model]

        start = time.perf_counter()
        try:
            proc = subprocess.run(
                cmd, input=prompt, text=True, capture_output=True, timeout=self._timeout
            )
        except subprocess.TimeoutExpired as exc:
            raise ProviderTransientError(f"claude timed out after {self._timeout}s") from exc
        if proc.returncode != 0:
            raise ProviderTransientError(
                f"claude exited {proc.returncode}: {proc.stderr[:500]}"
            )
        data = json.loads(proc.stdout)
        text = data.get("result", "")
        parsed = parse_json_output(text) if request.json_schema is not None else None
        usage = data.get("usage") or {}
        return ModelResponse(
            text=text, parsed=parsed,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            latency_ms=(time.perf_counter() - start) * 1000,
            provider=self.name, model=model,
            cost_usd=data.get("total_cost_usd"),
        )
