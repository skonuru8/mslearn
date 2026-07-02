# Plan 1/8: Foundation & Model Providers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repo scaffold, infrastructure services (Redis + Neo4j via docker-compose), the operational SQLite store, the named-profile config system, and the complete `ModelProvider` layer (Ollama, OpenRouter, Claude Code) with per-call logging — everything later plans build on.

**Architecture:** Python package `mslearn/` (maps to the spec's `core/`; later plans add `mslearn/server`, `mslearn/worker`, `mslearn/graphs`, `mslearn/adapters`). Providers implement one ABC; a `ModelRouter` resolves pipeline roles → provider+model via the active profile (persisted in SQLite) and logs every call. All tests run offline (respx-mocked HTTP, monkeypatched subprocess).

**Tech Stack:** Python ≥3.12, pydantic v2 + pydantic-settings, httpx, PyYAML, pytest + respx + ruff. Services: `redis:7-alpine`, `neo4j:5-community` (APOC enabled).

## Global Constraints

- Python `>=3.12`; pydantic v2 API only (`model_validate`, `model_config`)
- All durable state lives in SQLite or Neo4j — Redis is broker-only, never a datastore
- Model IDs appear **only** in `profiles.yaml`, never in Python code
- Every model call (success or failure) is logged to the `model_calls` table
- All tests pass with no network and no running services: HTTP mocked with `respx`, subprocess monkeypatched
- Env vars are prefixed `MSL_` (e.g. `MSL_OPENROUTER_API_KEY`); secrets never committed
- Provider errors use the taxonomy: `ProviderTransientError` (retryable), `ProviderBadOutputError` (schema/JSON failure), `ProviderError` (other)

---

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `mslearn/__init__.py`, `tests/__init__.py`, `tests/test_scaffold.py`

**Interfaces:**
- Produces: installable package `mslearn`, `pytest` + `ruff` wiring all later tasks use.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scaffold.py
import mslearn


def test_package_importable_and_versioned():
    assert mslearn.__version__ == "1.0.0"
```

- [ ] **Step 2: Create the scaffold files**

```toml
# pyproject.toml
[project]
name = "mslearn"
version = "1.0.0"
description = "Personal multi-source learning system"
requires-python = ">=3.12"
dependencies = [
    "httpx>=0.27",
    "pydantic>=2.7",
    "pydantic-settings>=2.2",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "respx>=0.21", "ruff>=0.4"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["mslearn"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["evals: release-gate eval suite (needs live backends)"]

[tool.ruff]
line-length = 100
```

```python
# mslearn/__init__.py
__version__ = "1.0.0"
```

```
# .gitignore
__pycache__/
*.pyc
.venv/
data/
.env
node_modules/
dist/
.pytest_cache/
.ruff_cache/
```

`tests/__init__.py` is an empty file.

- [ ] **Step 3: Install and run the test**

Run: `python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" && .venv/bin/pytest tests/test_scaffold.py -v`
Expected: `test_package_importable_and_versioned PASSED`

- [ ] **Step 4: Run ruff**

Run: `.venv/bin/ruff check .`
Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore mslearn/ tests/
git commit -m "feat: project scaffold with pytest and ruff wiring"
```

---

### Task 2: Infrastructure services (docker-compose)

**Files:**
- Create: `docker-compose.yml`, `Makefile`, `tests/test_compose.py`

**Interfaces:**
- Produces: `make services` / `make services-down`; Redis on `localhost:6379`, Neo4j bolt on `localhost:7687` + browser on `localhost:7474` (auth `neo4j` / env `NEO4J_PASSWORD`, default `learnsys`). Plans 3–6 connect to these.

- [ ] **Step 1: Write the failing test** (validates the compose file's contract without Docker)

```python
# tests/test_compose.py
from pathlib import Path

import yaml


def compose():
    return yaml.safe_load(Path("docker-compose.yml").read_text())


def test_redis_service_defined():
    svc = compose()["services"]["redis"]
    assert svc["image"].startswith("redis:7")
    assert "6379:6379" in svc["ports"]


def test_neo4j_service_defined_with_apoc():
    svc = compose()["services"]["neo4j"]
    assert svc["image"].startswith("neo4j:5")
    assert "7687:7687" in svc["ports"] and "7474:7474" in svc["ports"]
    env = svc["environment"]
    assert env["NEO4J_PLUGINS"] == '["apoc"]'
    assert "NEO4J_AUTH" in env


def test_neo4j_data_is_persisted_in_volume():
    svc = compose()["services"]["neo4j"]
    assert any("/data" in v for v in svc["volumes"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_compose.py -v`
Expected: FAIL with `FileNotFoundError: docker-compose.yml`

- [ ] **Step 3: Write the compose file and Makefile**

```yaml
# docker-compose.yml
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    # Broker only — no persistence volume on purpose; durable state lives in SQLite/Neo4j.

  neo4j:
    image: neo4j:5-community
    ports:
      - "7474:7474"
      - "7687:7687"
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD:-learnsys}
      NEO4J_PLUGINS: '["apoc"]'
      NEO4J_dbms_security_procedures_unrestricted: apoc.*
    volumes:
      - neo4j_data:/data

volumes:
  neo4j_data:
```

```makefile
# Makefile
.PHONY: services services-down test check

services:
	docker compose up -d

services-down:
	docker compose down

test:
	.venv/bin/pytest

check:
	.venv/bin/ruff check .
	.venv/bin/pytest
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_compose.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Manual service verification (one-time, requires Docker)**

Run: `make services && sleep 20 && docker compose ps`
Expected: both services `Up`. Then `docker compose exec redis redis-cli ping` → `PONG`.

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml Makefile tests/test_compose.py
git commit -m "feat: docker-compose infra (redis broker, neo4j+apoc) with contract tests"
```

---

### Task 3: Settings

**Files:**
- Create: `mslearn/settings.py`, `.env.example`, `tests/test_settings.py`

**Interfaces:**
- Produces: `Settings` (pydantic-settings, env prefix `MSL_`) with fields `data_dir: Path`, `profiles_path: Path`, `redis_url: str`, `neo4j_uri: str`, `neo4j_user: str`, `neo4j_password: str`, `openrouter_api_key: str`, `ollama_base_url: str`, `claude_binary: str`; property `ops_db: Path`. Factory `get_settings() -> Settings`. Used by every later task.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_settings.py
from pathlib import Path

from mslearn.settings import Settings


def test_defaults():
    s = Settings(_env_file=None)
    assert s.data_dir == Path("data")
    assert s.ops_db == Path("data") / "ops.db"
    assert s.redis_url == "redis://localhost:6379/0"
    assert s.neo4j_uri == "bolt://localhost:7687"
    assert s.ollama_base_url == "http://localhost:11434"


def test_env_override(monkeypatch):
    monkeypatch.setenv("MSL_OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("MSL_DATA_DIR", "/tmp/msl")
    s = Settings(_env_file=None)
    assert s.openrouter_api_key == "sk-test"
    assert s.ops_db == Path("/tmp/msl/ops.db")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mslearn.settings'`

- [ ] **Step 3: Write the implementation**

```python
# mslearn/settings.py
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="MSL_", extra="ignore")

    data_dir: Path = Path("data")
    profiles_path: Path = Path("profiles.yaml")
    redis_url: str = "redis://localhost:6379/0"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "learnsys"
    openrouter_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"
    claude_binary: str = "claude"

    @property
    def ops_db(self) -> Path:
        return self.data_dir / "ops.db"


def get_settings() -> Settings:
    return Settings()
```

```
# .env.example
MSL_OPENROUTER_API_KEY=
MSL_NEO4J_PASSWORD=learnsys
# NEO4J_PASSWORD is read by docker-compose; keep it in sync with MSL_NEO4J_PASSWORD
NEO4J_PASSWORD=learnsys
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_settings.py -v`
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add mslearn/settings.py .env.example tests/test_settings.py
git commit -m "feat: pydantic-settings config with MSL_ env prefix"
```

---

### Task 4: Operational SQLite store

**Files:**
- Create: `mslearn/opsdb.py`, `tests/test_opsdb.py`

**Interfaces:**
- Produces: `OpsDB(path)` with `log_model_call(*, role, provider, model, input_tokens=None, output_tokens=None, latency_ms=None, cost_usd=None, outcome="ok", error=None)`, `recent_calls(limit=50) -> list[dict]`, `get_setting(key, default=None) -> str | None`, `set_setting(key, value)`. WAL mode. Consumed by the profile system (Task 5) and the router (Task 10); later plans add eval/quiz tables here.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_opsdb.py
from mslearn.opsdb import OpsDB


def test_log_and_read_model_call(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    db.log_model_call(
        role="extraction", provider="ollama", model="m", input_tokens=10,
        output_tokens=20, latency_ms=123.4, outcome="ok",
    )
    db.log_model_call(role="synthesis", provider="openrouter", model="m2",
                      outcome="error", error="boom")
    calls = db.recent_calls()
    assert len(calls) == 2
    assert calls[0]["role"] == "synthesis" and calls[0]["error"] == "boom"  # newest first
    assert calls[1]["output_tokens"] == 20


def test_settings_kv_roundtrip_and_upsert(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    assert db.get_setting("active_profile") is None
    assert db.get_setting("active_profile", "openrouter") == "openrouter"
    db.set_setting("active_profile", "offline")
    db.set_setting("active_profile", "claude-code")
    assert db.get_setting("active_profile") == "claude-code"


def test_creates_parent_dirs(tmp_path):
    OpsDB(tmp_path / "nested" / "dir" / "ops.db")  # must not raise
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_opsdb.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mslearn.opsdb'`

- [ ] **Step 3: Write the implementation**

```python
# mslearn/opsdb.py
import sqlite3
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_calls (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    role TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER,
    output_tokens INTEGER,
    latency_ms REAL,
    cost_usd REAL,
    outcome TEXT NOT NULL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class OpsDB:
    def __init__(self, path: Path | str):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)

    def log_model_call(
        self, *, role: str, provider: str, model: str,
        input_tokens: int | None = None, output_tokens: int | None = None,
        latency_ms: float | None = None, cost_usd: float | None = None,
        outcome: str = "ok", error: str | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO model_calls (ts, role, provider, model, input_tokens,"
                " output_tokens, latency_ms, cost_usd, outcome, error)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (time.time(), role, provider, model, input_tokens,
                 output_tokens, latency_ms, cost_usd, outcome, error),
            )

    def recent_calls(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM model_calls ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_opsdb.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add mslearn/opsdb.py tests/test_opsdb.py
git commit -m "feat: operational SQLite store (model_calls log, settings KV, WAL)"
```

---

### Task 5: Profiles — config file, loader, active-profile persistence

**Files:**
- Create: `profiles.yaml`, `mslearn/profiles.py`, `tests/test_profiles.py`

**Interfaces:**
- Consumes: `OpsDB.get_setting` / `set_setting` (Task 4).
- Produces: `ROLES = ("extraction", "synthesis", "interactive", "evals", "embedding")`; pydantic models `RoleConfig{provider: str, model: str, params: dict}`, `Profile{roles: dict[str, RoleConfig]}`, `ProfilesConfig{default_profile: str, profiles: dict[str, Profile]}`; `load_profiles(path) -> ProfilesConfig` (validates every profile covers all ROLES); `get_active_profile_name(db, cfg) -> str`; `set_active_profile_name(db, cfg, name)` (raises `ValueError` on unknown name). The router (Task 10) and the server's profile-toggle endpoint (Plan 6) use these.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_profiles.py
import pytest

from mslearn.opsdb import OpsDB
from mslearn.profiles import (
    ROLES,
    get_active_profile_name,
    load_profiles,
    set_active_profile_name,
)


def test_shipped_profiles_yaml_is_valid_and_complete():
    cfg = load_profiles("profiles.yaml")
    assert cfg.default_profile == "openrouter"
    assert set(cfg.profiles) == {"openrouter", "claude-code", "offline"}
    for profile in cfg.profiles.values():
        assert set(profile.roles) == set(ROLES)
    # offline must not depend on any remote provider
    assert all(rc.provider == "ollama" for rc in cfg.profiles["offline"].roles.values())


def test_missing_role_rejected(tmp_path):
    bad = tmp_path / "p.yaml"
    bad.write_text(
        "default_profile: x\n"
        "profiles:\n"
        "  x:\n"
        "    roles:\n"
        "      extraction: {provider: ollama, model: m}\n"
    )
    with pytest.raises(ValueError, match="missing roles"):
        load_profiles(bad)


def test_active_profile_defaults_and_switches(tmp_path):
    cfg = load_profiles("profiles.yaml")
    db = OpsDB(tmp_path / "ops.db")
    assert get_active_profile_name(db, cfg) == "openrouter"
    set_active_profile_name(db, cfg, "offline")
    assert get_active_profile_name(db, cfg) == "offline"
    with pytest.raises(ValueError, match="unknown profile"):
        set_active_profile_name(db, cfg, "nope")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_profiles.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mslearn.profiles'`

- [ ] **Step 3: Write the config file and implementation**

```yaml
# profiles.yaml
# Model IDs live HERE, never in code. Bump versions by editing this file.
default_profile: openrouter

profiles:
  openrouter:
    roles:
      extraction:  {provider: ollama,     model: "qwen3.5:9b"}
      synthesis:   {provider: openrouter, model: "deepseek/deepseek-r1"}
      interactive: {provider: openrouter, model: "deepseek/deepseek-chat"}
      evals:       {provider: openrouter, model: "deepseek/deepseek-r1"}
      embedding:   {provider: ollama,     model: "nomic-embed-text"}

  claude-code:
    roles:
      extraction:  {provider: ollama,      model: "qwen3.5:9b"}
      synthesis:   {provider: claude_code, model: "default"}
      interactive: {provider: claude_code, model: "default"}
      evals:       {provider: claude_code, model: "default"}
      embedding:   {provider: ollama,      model: "nomic-embed-text"}

  offline:
    roles:
      extraction:  {provider: ollama, model: "qwen3.5:9b"}
      synthesis:   {provider: ollama, model: "qwen3.5:9b"}
      interactive: {provider: ollama, model: "qwen3.5:9b"}
      evals:       {provider: ollama, model: "qwen3.5:9b"}
      embedding:   {provider: ollama, model: "nomic-embed-text"}
```

```python
# mslearn/profiles.py
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from mslearn.opsdb import OpsDB

ROLES = ("extraction", "synthesis", "interactive", "evals", "embedding")
_ACTIVE_KEY = "active_profile"


class RoleConfig(BaseModel):
    provider: str
    model: str
    params: dict = Field(default_factory=dict)


class Profile(BaseModel):
    roles: dict[str, RoleConfig]


class ProfilesConfig(BaseModel):
    default_profile: str
    profiles: dict[str, Profile]


def load_profiles(path: Path | str) -> ProfilesConfig:
    data = yaml.safe_load(Path(path).read_text())
    cfg = ProfilesConfig.model_validate(data)
    for name, profile in cfg.profiles.items():
        missing = set(ROLES) - set(profile.roles)
        if missing:
            raise ValueError(f"profile {name!r} missing roles: {sorted(missing)}")
    if cfg.default_profile not in cfg.profiles:
        raise ValueError(f"unknown profile {cfg.default_profile!r} as default_profile")
    return cfg


def get_active_profile_name(db: OpsDB, cfg: ProfilesConfig) -> str:
    name = db.get_setting(_ACTIVE_KEY, cfg.default_profile)
    return name if name in cfg.profiles else cfg.default_profile


def set_active_profile_name(db: OpsDB, cfg: ProfilesConfig, name: str) -> None:
    if name not in cfg.profiles:
        raise ValueError(f"unknown profile {name!r}")
    db.set_setting(_ACTIVE_KEY, name)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_profiles.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add profiles.yaml mslearn/profiles.py tests/test_profiles.py
git commit -m "feat: named backend profiles with persisted active-profile switching"
```

---

### Task 6: Provider base — types, errors, ABC, tolerant JSON parsing

**Files:**
- Create: `mslearn/providers/__init__.py`, `mslearn/providers/base.py`, `tests/test_provider_base.py`

**Interfaces:**
- Produces (used by Tasks 7–10 and every later plan):
  - `ModelMessage{role: str, content: str}` (dataclass)
  - `ModelRequest{messages: list[ModelMessage], json_schema: dict | None = None, max_tokens: int = 2048, params: dict = {}}`
  - `ModelResponse{text: str, parsed: Any | None, input_tokens: int | None, output_tokens: int | None, latency_ms: float, provider: str, model: str, cost_usd: float | None = None}`
  - Exceptions: `ProviderError`, `ProviderTransientError(ProviderError)`, `ProviderBadOutputError(ProviderError)`
  - `ModelProvider` ABC: abstract `complete(model: str, request: ModelRequest) -> ModelResponse`; default `stream(model, request) -> Iterator[str]` (yields `complete().text` once); default `embed(model, texts) -> list[list[float]]` raises `NotImplementedError`
  - `parse_json_output(text: str) -> Any` — accepts raw JSON or ```json-fenced JSON, raises `ProviderBadOutputError` otherwise

- [ ] **Step 1: Write the failing test**

```python
# tests/test_provider_base.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_provider_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mslearn.providers'`

- [ ] **Step 3: Write the implementation**

`mslearn/providers/__init__.py` is an empty file.

```python
# mslearn/providers/base.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_provider_base.py -v`
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add mslearn/providers/ tests/test_provider_base.py
git commit -m "feat: ModelProvider ABC, request/response types, error taxonomy"
```

---

### Task 7: OllamaProvider (complete, stream, embed)

**Files:**
- Create: `mslearn/providers/ollama.py`, `tests/test_ollama_provider.py`

**Interfaces:**
- Consumes: everything from `mslearn.providers.base` (Task 6).
- Produces: `OllamaProvider(base_url: str, timeout: float = 300.0)` implementing `complete` (schema-enforced via Ollama `format`), `stream` (NDJSON chunks), `embed` (`/api/embed`). Extraction (Plan 4) and offline interactive (Plan 6) use it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ollama_provider.py
import json

import httpx
import pytest
import respx

from mslearn.providers.base import (
    ModelMessage,
    ModelRequest,
    ProviderBadOutputError,
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_ollama_provider.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mslearn.providers.ollama'`

- [ ] **Step 3: Write the implementation**

```python
# mslearn/providers/ollama.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ollama_provider.py -v`
Expected: 6 PASSED

- [ ] **Step 5: Commit**

```bash
git add mslearn/providers/ollama.py tests/test_ollama_provider.py
git commit -m "feat: Ollama provider (structured complete, NDJSON stream, embeddings)"
```

---

### Task 8: OpenRouterProvider (complete with cost, SSE stream)

**Files:**
- Create: `mslearn/providers/openrouter.py`, `tests/test_openrouter_provider.py`

**Interfaces:**
- Consumes: `mslearn.providers.base` (Task 6).
- Produces: `OpenRouterProvider(api_key: str, timeout: float = 300.0)` — `complete` (OpenAI-compatible `/chat/completions`, `response_format: json_schema` when a schema is given, `usage.include` for cost) and `stream` (SSE). Synthesis/interactive/evals roles (Plans 5, 6, 8) use it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_openrouter_provider.py
import json

import pytest
import respx

from mslearn.providers.base import (
    ModelMessage,
    ModelRequest,
    ProviderBadOutputError,
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_openrouter_provider.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mslearn.providers.openrouter'`

- [ ] **Step 3: Write the implementation**

```python
# mslearn/providers/openrouter.py
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
        data = self._post(self._body(model, request, stream=False)).json()
        text = data["choices"][0]["message"]["content"]
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
        resp = self._post(self._body(model, request, stream=True))
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            payload = line[len("data: "):]
            if payload.strip() == "[DONE]":
                break
            chunk = json.loads(payload)
            delta = chunk["choices"][0].get("delta", {}).get("content")
            if delta:
                yield delta
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_openrouter_provider.py -v`
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add mslearn/providers/openrouter.py tests/test_openrouter_provider.py
git commit -m "feat: OpenRouter provider (json_schema output, SSE streaming, cost capture)"
```

---

### Task 9: ClaudeCodeProvider (headless `claude -p` subprocess)

**Files:**
- Create: `mslearn/providers/claude_code.py`, `tests/test_claude_code_provider.py`

**Interfaces:**
- Consumes: `mslearn.providers.base` (Task 6), incl. `parse_json_output`.
- Produces: `ClaudeCodeProvider(binary: str = "claude", timeout: float = 600.0)` — `complete` runs `claude -p --output-format json` (prompt on stdin, system messages via `--append-system-prompt`); when a `json_schema` is requested it is appended to the prompt as an instruction and the result parsed with `parse_json_output`. Streaming uses the base-class fallback (single yield). Only exercised when the `claude-code` profile is active.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_claude_code_provider.py
import json
import subprocess

import pytest

from mslearn.providers.base import (
    ModelMessage,
    ModelRequest,
    ProviderBadOutputError,
    ProviderTransientError,
)
from mslearn.providers.claude_code import ClaudeCodeProvider


def fake_run(result_text, returncode=0, usage=None):
    def _run(cmd, **kwargs):
        fake_run.last_cmd, fake_run.last_input = cmd, kwargs.get("input")
        out = json.dumps({"result": result_text, "usage": usage or {}})
        return subprocess.CompletedProcess(cmd, returncode, stdout=out, stderr="err")
    return _run


def req(schema=None, system=None):
    msgs = ([ModelMessage(role="system", content=system)] if system else [])
    msgs.append(ModelMessage(role="user", content="hi"))
    return ModelRequest(messages=msgs, json_schema=schema)


def test_complete_invokes_headless_json_mode(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        fake_run("hello", usage={"input_tokens": 4, "output_tokens": 6}))
    resp = ClaudeCodeProvider().complete("default", req(system="be brief"))
    cmd = fake_run.last_cmd
    assert cmd[:2] == ["claude", "-p"]
    assert "--output-format" in cmd and "json" in cmd
    assert "--append-system-prompt" in cmd and "be brief" in cmd
    assert fake_run.last_input == "hi"
    assert resp.text == "hello" and resp.input_tokens == 4 and resp.output_tokens == 6
    assert resp.provider == "claude_code"


def test_schema_instruction_appended_and_parsed(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run('{"a": 3}'))
    schema = {"type": "object", "properties": {"a": {"type": "integer"}}}
    resp = ClaudeCodeProvider().complete("default", req(schema=schema))
    assert "JSON" in fake_run.last_input and '"integer"' in fake_run.last_input
    assert resp.parsed == {"a": 3}


def test_bad_json_raises(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run("not json"))
    with pytest.raises(ProviderBadOutputError):
        ClaudeCodeProvider().complete("default", req(schema={"type": "object"}))


def test_nonzero_exit_is_transient(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run("", returncode=1))
    with pytest.raises(ProviderTransientError):
        ClaudeCodeProvider().complete("default", req())


def test_explicit_model_flag(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake_run("ok"))
    ClaudeCodeProvider().complete("opus", req())
    assert "--model" in fake_run.last_cmd and "opus" in fake_run.last_cmd
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_claude_code_provider.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mslearn.providers.claude_code'`

- [ ] **Step 3: Write the implementation**

```python
# mslearn/providers/claude_code.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_claude_code_provider.py -v`
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add mslearn/providers/claude_code.py tests/test_claude_code_provider.py
git commit -m "feat: Claude Code headless provider (subscription non-bare mode)"
```

---

### Task 10: ModelRouter — role resolution + call logging

**Files:**
- Create: `mslearn/providers/router.py`, `tests/test_router.py`

**Interfaces:**
- Consumes: `ProfilesConfig`/`get_active_profile_name` (Task 5), `OpsDB.log_model_call` (Task 4), all three providers (Tasks 7–9), `Settings` (Task 3).
- Produces (the single entry point every later plan calls for model work):
  - `ModelRouter(cfg: ProfilesConfig, db: OpsDB, settings: Settings, providers: dict[str, ModelProvider] | None = None)` — the `providers` override is the seam tests and fakes use
  - `complete(role: str, request: ModelRequest) -> ModelResponse` — resolves active profile → role → provider+model, merges `RoleConfig.params` into the request, logs outcome
  - `stream(role: str, request: ModelRequest) -> Iterator[str]` — logs once after the stream is drained (or fails)
  - `embed(texts: list[str]) -> list[list[float]]` — always uses the `embedding` role

- [ ] **Step 1: Write the failing test**

```python
# tests/test_router.py
import pytest

from mslearn.opsdb import OpsDB
from mslearn.profiles import load_profiles, set_active_profile_name
from mslearn.providers.base import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ProviderError,
)
from mslearn.providers.router import ModelRouter
from mslearn.settings import Settings


class FakeProvider(ModelProvider):
    def __init__(self, name):
        self.name = name
        self.calls = []

    def complete(self, model, request):
        self.calls.append((model, request))
        return ModelResponse(text=f"{self.name}:{model}", parsed=None, input_tokens=1,
                             output_tokens=2, latency_ms=1.0, provider=self.name, model=model)

    def embed(self, model, texts):
        return [[0.0] * 3 for _ in texts]


class ExplodingProvider(FakeProvider):
    def complete(self, model, request):
        raise ProviderError("kaboom")


@pytest.fixture
def env(tmp_path):
    cfg = load_profiles("profiles.yaml")
    db = OpsDB(tmp_path / "ops.db")
    fakes = {"ollama": FakeProvider("ollama"), "openrouter": FakeProvider("openrouter"),
             "claude_code": FakeProvider("claude_code")}
    router = ModelRouter(cfg, db, Settings(_env_file=None), providers=fakes)
    return cfg, db, fakes, router


def request():
    return ModelRequest(messages=[ModelMessage(role="user", content="q")])


def test_routes_by_active_profile_role(env):
    cfg, db, fakes, router = env
    resp = router.complete("extraction", request())
    assert resp.provider == "ollama" and resp.model == "qwen3.5:9b"
    resp = router.complete("synthesis", request())
    assert resp.provider == "openrouter" and resp.model == "deepseek/deepseek-r1"


def test_profile_switch_changes_routing(env):
    cfg, db, fakes, router = env
    set_active_profile_name(db, cfg, "offline")
    resp = router.complete("synthesis", request())
    assert resp.provider == "ollama"


def test_success_and_failure_are_logged(env):
    cfg, db, fakes, router = env
    router.complete("interactive", request())
    fakes["openrouter"] = ExplodingProvider("openrouter")
    router._providers["openrouter"] = fakes["openrouter"]
    with pytest.raises(ProviderError):
        router.complete("interactive", request())
    calls = db.recent_calls()
    assert calls[0]["outcome"] == "error" and "kaboom" in calls[0]["error"]
    assert calls[1]["outcome"] == "ok" and calls[1]["role"] == "interactive"


def test_embed_uses_embedding_role(env):
    cfg, db, fakes, router = env
    vecs = router.embed(["x", "y"])
    assert len(vecs) == 2 and len(vecs[0]) == 3


def test_role_params_merged_into_request(env):
    cfg, db, fakes, router = env
    cfg.profiles["openrouter"].roles["synthesis"].params = {"temperature": 0.2}
    router.complete("synthesis", request())
    _, sent = fakes["openrouter"].calls[-1]
    assert sent.params["temperature"] == 0.2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_router.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mslearn.providers.router'`

- [ ] **Step 3: Write the implementation**

```python
# mslearn/providers/router.py
from dataclasses import replace
from typing import Iterator

from mslearn.opsdb import OpsDB
from mslearn.profiles import ProfilesConfig, RoleConfig, get_active_profile_name
from mslearn.providers.base import ModelProvider, ModelRequest, ModelResponse, ProviderError
from mslearn.providers.claude_code import ClaudeCodeProvider
from mslearn.providers.ollama import OllamaProvider
from mslearn.providers.openrouter import OpenRouterProvider
from mslearn.settings import Settings


class ModelRouter:
    def __init__(
        self,
        cfg: ProfilesConfig,
        db: OpsDB,
        settings: Settings,
        providers: dict[str, ModelProvider] | None = None,
    ):
        self._cfg = cfg
        self._db = db
        self._providers: dict[str, ModelProvider] = providers or {
            "ollama": OllamaProvider(settings.ollama_base_url),
            "openrouter": OpenRouterProvider(settings.openrouter_api_key),
            "claude_code": ClaudeCodeProvider(settings.claude_binary),
        }

    def _resolve(self, role: str) -> tuple[ModelProvider, RoleConfig]:
        profile = self._cfg.profiles[get_active_profile_name(self._db, self._cfg)]
        role_cfg = profile.roles[role]
        return self._providers[role_cfg.provider], role_cfg

    def _merged(self, request: ModelRequest, role_cfg: RoleConfig) -> ModelRequest:
        if not role_cfg.params:
            return request
        return replace(request, params={**role_cfg.params, **request.params})

    def complete(self, role: str, request: ModelRequest) -> ModelResponse:
        provider, role_cfg = self._resolve(role)
        try:
            resp = provider.complete(role_cfg.model, self._merged(request, role_cfg))
        except ProviderError as exc:
            self._db.log_model_call(role=role, provider=provider.name, model=role_cfg.model,
                                    outcome="error", error=str(exc)[:500])
            raise
        self._db.log_model_call(
            role=role, provider=provider.name, model=role_cfg.model,
            input_tokens=resp.input_tokens, output_tokens=resp.output_tokens,
            latency_ms=resp.latency_ms, cost_usd=resp.cost_usd, outcome="ok",
        )
        return resp

    def stream(self, role: str, request: ModelRequest) -> Iterator[str]:
        provider, role_cfg = self._resolve(role)
        try:
            yield from provider.stream(role_cfg.model, self._merged(request, role_cfg))
        except ProviderError as exc:
            self._db.log_model_call(role=role, provider=provider.name, model=role_cfg.model,
                                    outcome="error", error=str(exc)[:500])
            raise
        self._db.log_model_call(role=role, provider=provider.name, model=role_cfg.model,
                                outcome="ok")

    def embed(self, texts: list[str]) -> list[list[float]]:
        provider, role_cfg = self._resolve("embedding")
        return provider.embed(role_cfg.model, texts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_router.py -v`
Expected: 5 PASSED

- [ ] **Step 5: Run the full suite + lint**

Run: `.venv/bin/ruff check . && .venv/bin/pytest`
Expected: ruff clean; all tests PASS

- [ ] **Step 6: Commit**

```bash
git add mslearn/providers/router.py tests/test_router.py
git commit -m "feat: ModelRouter with profile-based role routing and call logging"
```

---

### Task 11: README + live smoke script

**Files:**
- Create: `README.md`, `scripts/smoke_providers.py`

**Interfaces:**
- Consumes: everything above.
- Produces: documented quickstart; `scripts/smoke_providers.py` — the only file in this plan that touches live services (run manually, never in CI).

- [ ] **Step 1: Write the smoke script**

```python
# scripts/smoke_providers.py
"""Manual smoke test against live services. Not run in CI.

Usage: .venv/bin/python scripts/smoke_providers.py [role ...]
Defaults to: extraction embedding (local-only). Add e.g. `interactive`
to hit OpenRouter (needs MSL_OPENROUTER_API_KEY in .env).
"""
import sys

from mslearn.opsdb import OpsDB
from mslearn.profiles import load_profiles
from mslearn.providers.base import ModelMessage, ModelRequest
from mslearn.providers.router import ModelRouter
from mslearn.settings import get_settings

roles = sys.argv[1:] or ["extraction", "embedding"]
settings = get_settings()
router = ModelRouter(load_profiles(settings.profiles_path), OpsDB(settings.ops_db), settings)

for role in roles:
    if role == "embedding":
        vecs = router.embed(["hello world"])
        print(f"embedding: OK dim={len(vecs[0])}")
        continue
    resp = router.complete(
        role,
        ModelRequest(
            messages=[ModelMessage(role="user", content="Reply with the single word: pong")],
            max_tokens=16,
        ),
    )
    print(f"{role}: {resp.provider}/{resp.model} -> {resp.text!r} "
          f"({resp.latency_ms:.0f} ms, cost={resp.cost_usd})")
print("Logged calls:", len(OpsDB(settings.ops_db).recent_calls()))
```

- [ ] **Step 2: Write the README**

```markdown
# mslearn — Personal Multi-Source Learning System

Turns your books, blogs, YouTube playlists, and podcasts into a trust-gated
concept graph with cross-source conflict classification, teaching, quizzes,
and portable Markdown/Anki exports.

Spec: `docs/superpowers/specs/2026-07-02-multi-source-learning-system-design.md`

## Prerequisites
- Python 3.12+, Docker, [Ollama](https://ollama.com) with models pulled:
  `ollama pull qwen3.5:9b && ollama pull nomic-embed-text`
- An OpenRouter API key (default profile) and/or Claude Code installed

## Setup
    python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
    cp .env.example .env   # fill in MSL_OPENROUTER_API_KEY
    make services          # starts Redis + Neo4j (browser: http://localhost:7474)

## Verify
    make check                                    # lint + offline test suite
    .venv/bin/python scripts/smoke_providers.py   # live local-provider smoke
    .venv/bin/python scripts/smoke_providers.py interactive   # + OpenRouter

## Backend profiles
Model routing lives in `profiles.yaml` (profiles: `openrouter` default,
`claude-code`, `offline`). Model IDs are config-only — edit the YAML to bump.
```

- [ ] **Step 3: Verify offline suite still green**

Run: `.venv/bin/ruff check . && .venv/bin/pytest`
Expected: ruff clean; all tests PASS (smoke script is not collected by pytest)

- [ ] **Step 4: Manual live smoke (requires Ollama running with models pulled)**

Run: `.venv/bin/python scripts/smoke_providers.py`
Expected: `extraction: ollama/qwen3.5:9b -> 'pong' (...)` and `embedding: OK dim=768`

- [ ] **Step 5: Commit**

```bash
git add README.md scripts/smoke_providers.py
git commit -m "docs: README quickstart + live provider smoke script"
```

---

## Self-Review (performed at write time)

- **Spec coverage (Plan 1 scope):** system-shape services ✓ (Task 2), profiles + hot-swap persistence ✓ (Task 5; the UI toggle endpoint arrives with the server in Plan 6, backed by `set_active_profile_name`), `ModelProvider` interface + 3 implementations ✓ (Tasks 6–9), call logging ✓ (Tasks 4, 10), "model IDs config-only" ✓ (profiles.yaml + tests), offline CI ✓ (respx/monkeypatch throughout).
- **Placeholder scan:** none — every step has complete code and exact commands.
- **Type consistency:** `ModelRequest`/`ModelResponse`/error names identical across Tasks 6–10; `recent_calls` (Task 4) is what Task 10's tests use; `ROLES` includes `embedding` used by `router.embed`.
```
