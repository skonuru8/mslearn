# Plan 4/8: Ingestion Pipeline (Celery + LangGraph + Trust Gate) — Implementation Plan

> **For the implementer (Cursor):** Work task-by-task in order, TDD (write the failing test, run it, implement, run again), commit per task with the given message. Run everything from the repo root with `.venv/bin/pytest` / `.venv/bin/ruff check .`. If a step's expected output differs from reality, STOP and note it in your summary rather than improvising. Steps use checkbox (`- [ ]`) syntax.

**Goal:** The background ingestion pipeline: sources → adapters → chunks → LangGraph extraction (schema-enforced claims with verbatim quotes) → trust gate (fuzzy quote match + embedding sanity) → retry → escalation → committed to Neo4j — orchestrated by Celery, resumable, with per-source failure monitoring and self-evolution-ready tunables.

**Architecture:** `mslearn/worker/` (Celery app, per-process context, tasks), `mslearn/pipeline/` (extraction contracts, trust gate, LangGraph graph, orchestrator), tunables + prompt overrides in OpsDB (audited — the eval loop in Plan 8 will write here; the pipeline only ever reads at call time). Redis stays broker-only: all durable job state lives in new OpsDB tables. Graph writes are **counted** (guarding the silent-no-op hazard found in the Plan-3 review).

**Tech Stack (added):** `celery[redis]>=5.4`, `langgraph>=0.2`, `rapidfuzz>=3.9`.

## Docs to read before starting (skim the named sections, not whole sites)

| Topic | URL | What you need from it |
|---|---|---|
| Celery tasks | https://docs.celeryq.dev/en/stable/userguide/tasks.html | `bind=True`, `autoretry_for`, `retry_backoff`, `acks_late` |
| Celery signals | https://docs.celeryq.dev/en/stable/userguide/signals.html#worker-process-init | per-process resource init (CRITICAL: SQLite/Neo4j clients must be built post-fork) |
| Celery routing | https://docs.celeryq.dev/en/stable/userguide/routing.html | `task_routes`, named queues |
| LangGraph low-level | https://langchain-ai.github.io/langgraph/concepts/low_level/ | `StateGraph`, TypedDict state, `add_conditional_edges`, `END`, `.compile()`, `.invoke()` |
| rapidfuzz | https://rapidfuzz.github.io/RapidFuzz/Usage/fuzz.html | `fuzz.partial_ratio` semantics (0–100) |
| Neo4j result counters | https://neo4j.com/docs/api/python-driver/current/api.html#neo4j.SummaryCounters | `contains_updates` for counted writes |
| Existing code | `mslearn/providers/router.py`, `mslearn/providers/ollama.py`, `mslearn/graph/store.py`, `mslearn/opsdb.py`, `mslearn/chunking.py`, `mslearn/adapters/registry.py` | The interfaces you are wiring together — read all six files fully |

## Global Constraints

- **No module-level OpsDB/GraphStore/ModelRouter singletons.** Workers construct them per process via the `worker_process_init` signal into a `PipelineContext`; tests inject fakes via `set_context()`. (SQLite connections must never cross a fork; recorded constraint from the Plan-1 review.)
- **Every graph write in the pipeline is verified**: use the new counted-write API; a write whose counters show no update raises `GraphWriteError` (mitigates Plan-3 review finding: `MATCH…MERGE` silently no-ops on missing ids).
- **Thresholds and prompts are never hardcoded at call sites.** All limits come from `get_tunable(db, key)` (audited table, defaults in `TUNABLE_DEFAULTS`); all prompts from `get_prompt(db, name)` (DB override key `prompt:<name>`, falls back to built-in). This is the self-evolution contract: Plan 8's eval loop writes; the pipeline reads.
- Redis is broker-only. Durable job state = OpsDB tables (`ingest_sources`, `chunk_jobs`). Kill a worker mid-run → `resume_pending()` re-enqueues without duplicating claims (graph upserts are idempotent by claim_id).
- Queues: `ingest` (extraction, local model), `judge` (escalations), `transcribe` (reserved). Escalated extraction runs under the `synthesis` role (strong backend) on the `judge` queue.
- Offline tests: Celery in `task_always_eager` mode with fakes; LangGraph tested with fake routers; no network, no live Neo4j needed (a `FakeGraphStore` covers pipeline tests; live-Neo4j integration test marked `neo4j`).
- Stances exactly: `recommends`, `warns_against`, `neutral`. Trust values: `trusted` (passed normally), `escalated` (passed after escalation), `rejected`.
- Prior suite (101 tests incl. neo4j-marked) stays green; ruff clean; commit per task.

---

### Task 1: Dependencies + tunables store + prompt registry

**Files:**
- Modify: `pyproject.toml` (add `"celery[redis]>=5.4"`, `"langgraph>=0.2"`, `"rapidfuzz>=3.9"`)
- Modify: `mslearn/opsdb.py` (tunables table + methods)
- Create: `mslearn/prompts.py`, `tests/test_tunables.py`, `tests/test_prompts.py`

**Interfaces:**
- Produces: `TUNABLE_DEFAULTS: dict[str, float]` with exactly `{"trust.quote_threshold": 90.0, "trust.embed_sim_threshold": 0.35, "extract.max_attempts": 2.0, "monitor.failure_rate_threshold": 0.5, "monitor.min_chunks": 10.0}`; `OpsDB.get_tunable(key) -> float` (DB value else default else KeyError); `OpsDB.set_tunable(key, value, reason)` (upsert + append-only audit rows in `tunable_audit`); `OpsDB.tunable_history(key) -> list[dict]` newest-first. `mslearn.prompts.get_prompt(db, name) -> str` (returns `db.get_setting(f"prompt:{name}")` override if set, else built-in from `PROMPTS`; KeyError if unknown name) and built-ins `PROMPTS = {"extraction": ..., "extraction_retry_suffix": ...}`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tunables.py
import pytest

from mslearn.opsdb import TUNABLE_DEFAULTS, OpsDB


def test_defaults_registry_exact():
    assert TUNABLE_DEFAULTS == {
        "trust.quote_threshold": 90.0,
        "trust.embed_sim_threshold": 0.35,
        "extract.max_attempts": 2.0,
        "monitor.failure_rate_threshold": 0.5,
        "monitor.min_chunks": 10.0,
    }


def test_get_falls_back_to_default(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    assert db.get_tunable("trust.quote_threshold") == 90.0
    with pytest.raises(KeyError):
        db.get_tunable("nope.unknown")


def test_set_overrides_and_audits(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    db.set_tunable("trust.quote_threshold", 85.0, reason="eval run 7 improved recall")
    db.set_tunable("trust.quote_threshold", 88.0, reason="eval run 9")
    assert db.get_tunable("trust.quote_threshold") == 88.0
    history = db.tunable_history("trust.quote_threshold")
    assert len(history) == 2
    assert history[0]["value"] == 88.0 and "run 9" in history[0]["reason"]
```

```python
# tests/test_prompts.py
import pytest

from mslearn.opsdb import OpsDB
from mslearn.prompts import PROMPTS, get_prompt


def test_builtin_extraction_prompt_mentions_verbatim_quote():
    assert "verbatim" in PROMPTS["extraction"].lower()


def test_db_override_wins(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    assert get_prompt(db, "extraction") == PROMPTS["extraction"]
    db.set_setting("prompt:extraction", "OVERRIDDEN")
    assert get_prompt(db, "extraction") == "OVERRIDDEN"
    with pytest.raises(KeyError):
        get_prompt(db, "unknown_prompt")
```

- [ ] **Step 2: Run to verify failure** — `ImportError: cannot import name 'TUNABLE_DEFAULTS'`

- [ ] **Step 3: Implement**

Append to `mslearn/opsdb.py` — extend `_SCHEMA` string with:

```sql
CREATE TABLE IF NOT EXISTS tunables (
    key TEXT PRIMARY KEY,
    value REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS tunable_audit (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    key TEXT NOT NULL,
    value REAL NOT NULL,
    reason TEXT NOT NULL
);
```

and module-level + methods (inside the existing lock discipline — every method body wrapped in `with self._lock:`):

```python
TUNABLE_DEFAULTS: dict[str, float] = {
    "trust.quote_threshold": 90.0,
    "trust.embed_sim_threshold": 0.35,
    "extract.max_attempts": 2.0,
    "monitor.failure_rate_threshold": 0.5,
    "monitor.min_chunks": 10.0,
}
```

```python
    def get_tunable(self, key: str) -> float:
        with self._lock:
            row = self.conn.execute(
                "SELECT value FROM tunables WHERE key = ?", (key,)
            ).fetchone()
        if row is not None:
            return float(row["value"])
        if key not in TUNABLE_DEFAULTS:
            raise KeyError(f"unknown tunable {key!r}")
        return TUNABLE_DEFAULTS[key]

    def set_tunable(self, key: str, value: float, reason: str) -> None:
        if key not in TUNABLE_DEFAULTS:
            raise KeyError(f"unknown tunable {key!r}")
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO tunables (key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, float(value)),
            )
            self.conn.execute(
                "INSERT INTO tunable_audit (ts, key, value, reason) VALUES (?, ?, ?, ?)",
                (time.time(), key, float(value), reason),
            )

    def tunable_history(self, key: str) -> list[dict]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT ts, key, value, reason FROM tunable_audit"
                " WHERE key = ? ORDER BY id DESC",
                (key,),
            ).fetchall()
        return [dict(r) for r in rows]
```

(If the existing lock usage pattern differs — e.g. lock inside `with self.conn:` — match the existing style in the file exactly.)

```python
# mslearn/prompts.py
from mslearn.opsdb import OpsDB

PROMPTS: dict[str, str] = {
    "extraction": (
        "You extract factual claims from one text chunk of a learning source.\n"
        "Return JSON only, matching the given schema.\n"
        "Rules:\n"
        "- Each claim is one self-contained factual or prescriptive statement.\n"
        "- 'quote' MUST be a verbatim substring copied character-for-character from the chunk"
        " that supports the claim. Never paraphrase inside 'quote'.\n"
        "- 'stance' is 'recommends' if the source advises doing it, 'warns_against' if it advises"
        " against it, else 'neutral'.\n"
        "- Extract at most 8 claims. Skip greetings, filler, and table-of-contents text.\n"
        "- If the chunk contains no claims, return {\"claims\": []}.\n"
    ),
    "extraction_retry_suffix": (
        "\nYour previous attempt failed validation: {reasons}.\n"
        "Copy 'quote' EXACTLY from the chunk text — character for character."
    ),
}


def get_prompt(db: OpsDB, name: str) -> str:
    if name not in PROMPTS:
        raise KeyError(f"unknown prompt {name!r}")
    override = db.get_setting(f"prompt:{name}")
    return override if override is not None else PROMPTS[name]
```

- [ ] **Step 4: Install + run**

Run: `.venv/bin/pip install -e ".[dev]" -q && .venv/bin/pytest tests/test_tunables.py tests/test_prompts.py -v && .venv/bin/ruff check .`
Expected: 5 PASSED; ruff clean

- [ ] **Step 5: Full suite (`.venv/bin/pytest -q`), commit**

```bash
git add pyproject.toml mslearn/opsdb.py mslearn/prompts.py tests/test_tunables.py tests/test_prompts.py
git commit -m "feat: audited tunables store and prompt registry with DB overrides (self-evolution contract)"
```

---

### Task 2: Ingest job state (OpsDB) + counted graph writes

**Files:**
- Modify: `mslearn/opsdb.py` (job tables + methods), `mslearn/graph/store.py` (counted writes + `get_chunk`)
- Create: `tests/test_ingest_state.py`
- Modify: `tests/test_graph_store.py` (counted-write integration test)

**Interfaces:**
- Produces (OpsDB): `register_source(source_id, ref, role, total_chunks)`; `set_source_status(source_id, status, error=None)` (status ∈ registered/running/paused/failed/done); `source_row(source_id) -> dict | None`; `register_chunk_jobs(source_id, chunk_ids)`; `mark_chunk(chunk_id, status, error=None)` (pending/running/done/failed/skipped_paused; done/failed also bump `done_chunks`/`failed_chunks` on the source row); `pending_chunks(source_id) -> list[str]`; `failure_stats(source_id) -> dict{"total", "failed"}`; `all_sources() -> list[dict]`.
- Produces (GraphStore): `run_write(...)` now RETURNS `neo4j.SummaryCounters`; new `run_write_checked(query, **params)` raising `GraphWriteError` (new exception in `mslearn/graph/store.py`) when `not counters.contains_updates`; `get_chunk(chunk_id) -> dict | None` (chunk_id, source_id, text, seq + flattened locator fields). Existing method signatures unchanged (their `None` returns become counters — no caller relies on the old return).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ingest_state.py
from mslearn.opsdb import OpsDB


def db(tmp_path):
    return OpsDB(tmp_path / "ops.db")


def test_source_and_chunk_lifecycle(tmp_path):
    d = db(tmp_path)
    d.register_source("s1", ref="/x.pdf", role="spine", total_chunks=3)
    d.register_chunk_jobs("s1", ["s1:0", "s1:1", "s1:2"])
    assert d.pending_chunks("s1") == ["s1:0", "s1:1", "s1:2"]
    d.mark_chunk("s1:0", "done")
    d.mark_chunk("s1:1", "failed", error="boom")
    assert d.pending_chunks("s1") == ["s1:2"]
    row = d.source_row("s1")
    assert row["done_chunks"] == 1 and row["failed_chunks"] == 1
    assert d.failure_stats("s1") == {"total": 3, "failed": 1}


def test_source_status_transitions(tmp_path):
    d = db(tmp_path)
    d.register_source("s1", ref="r", role="supplement", total_chunks=1)
    assert d.source_row("s1")["status"] == "registered"
    d.set_source_status("s1", "paused")
    assert d.source_row("s1")["status"] == "paused"
    d.set_source_status("s1", "failed", error="unparseable")
    assert d.source_row("s1")["error"] == "unparseable"


def test_register_idempotent(tmp_path):
    d = db(tmp_path)
    d.register_source("s1", ref="r", role="spine", total_chunks=2)
    d.register_source("s1", ref="r", role="spine", total_chunks=2)  # no crash, no dup
    d.register_chunk_jobs("s1", ["s1:0"])
    d.register_chunk_jobs("s1", ["s1:0"])
    assert d.pending_chunks("s1") == ["s1:0"]
    assert len(d.all_sources()) == 1
```

Append to `tests/test_graph_store.py`:

```python
def test_counted_write_detects_silent_noop(clean_graph):
    from mslearn.graph.store import GraphWriteError

    counters = clean_graph.run_write("CREATE (:Source {source_id: 'x'})")
    assert counters.contains_updates
    with pytest.raises(GraphWriteError):
        clean_graph.run_write_checked(
            "MATCH (c:Chunk {chunk_id: $cid}) SET c.text = 'y'", cid="missing"
        )


def test_get_chunk(clean_graph):
    from mslearn.chunking import chunk_source
    from tests.test_graph_ingest import embed_stub, make_doc

    doc = make_doc()
    chunks = chunk_source(doc)
    clean_graph.upsert_source(doc)
    clean_graph.upsert_chunks(chunks, embed_stub(chunks))
    row = clean_graph.get_chunk(chunks[0].chunk_id)
    assert row["text"] == chunks[0].text and row["kind"] == "page"
    assert clean_graph.get_chunk("missing") is None
```

- [ ] **Step 2: Run to verify failure** (offline part: AttributeError on register_source; neo4j part runs only with service)

- [ ] **Step 3: Implement**

OpsDB — extend `_SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS ingest_sources (
    source_id TEXT PRIMARY KEY,
    ref TEXT NOT NULL,
    role TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'registered',
    total_chunks INTEGER NOT NULL,
    done_chunks INTEGER NOT NULL DEFAULT 0,
    failed_chunks INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chunk_jobs (
    chunk_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
```

Methods (all under the existing lock discipline; `INSERT OR IGNORE` for idempotency):

```python
    def register_source(self, source_id, ref, role, total_chunks) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO ingest_sources"
                " (source_id, ref, role, total_chunks, ts) VALUES (?, ?, ?, ?, ?)",
                (source_id, ref, role, total_chunks, time.time()),
            )

    def set_source_status(self, source_id, status, error=None) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE ingest_sources SET status = ?, error = COALESCE(?, error)"
                " WHERE source_id = ?",
                (status, error, source_id),
            )

    def source_row(self, source_id) -> dict | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT * FROM ingest_sources WHERE source_id = ?", (source_id,)
            ).fetchone()
        return dict(row) if row else None

    def all_sources(self) -> list[dict]:
        with self._lock:
            rows = self.conn.execute("SELECT * FROM ingest_sources ORDER BY ts").fetchall()
        return [dict(r) for r in rows]

    def register_chunk_jobs(self, source_id, chunk_ids) -> None:
        with self._lock, self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO chunk_jobs (chunk_id, source_id) VALUES (?, ?)",
                [(cid, source_id) for cid in chunk_ids],
            )

    def mark_chunk(self, chunk_id, status, error=None) -> None:
        with self._lock, self.conn:
            row = self.conn.execute(
                "SELECT source_id, status FROM chunk_jobs WHERE chunk_id = ?", (chunk_id,)
            ).fetchone()
            self.conn.execute(
                "UPDATE chunk_jobs SET status = ?, error = ?,"
                " attempts = attempts + 1 WHERE chunk_id = ?",
                (status, error, chunk_id),
            )
            if row and status in ("done", "failed") and row["status"] not in ("done", "failed"):
                column = "done_chunks" if status == "done" else "failed_chunks"
                self.conn.execute(
                    f"UPDATE ingest_sources SET {column} = {column} + 1 WHERE source_id = ?",
                    (row["source_id"],),
                )

    def pending_chunks(self, source_id) -> list[str]:
        with self._lock:
            rows = self.conn.execute(
                "SELECT chunk_id FROM chunk_jobs WHERE source_id = ? AND status = 'pending'"
                " ORDER BY chunk_id",
                (source_id,),
            ).fetchall()
        return [r["chunk_id"] for r in rows]

    def failure_stats(self, source_id) -> dict:
        with self._lock:
            row = self.conn.execute(
                "SELECT count(*) AS total,"
                " sum(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed"
                " FROM chunk_jobs WHERE source_id = ?",
                (source_id,),
            ).fetchone()
        return {"total": row["total"], "failed": row["failed"] or 0}
```

GraphStore — add exception + change `run_write` to return counters + checked variant + `get_chunk`:

```python
class GraphWriteError(Exception):
    """A graph write matched nothing and silently changed nothing."""
```

```python
    def run_write(self, query: str, **params):
        with self._driver.session() as session:
            return session.execute_write(
                lambda tx: tx.run(query, **params).consume().counters
            )

    def run_write_checked(self, query: str, **params):
        counters = self.run_write(query, **params)
        if not counters.contains_updates:
            raise GraphWriteError(f"write changed nothing: {query[:120]}")
        return counters

    def get_chunk(self, chunk_id: str) -> dict | None:
        rows = self.run_read(
            "MATCH (c:Chunk {chunk_id: $chunk_id}) "
            "RETURN c.chunk_id AS chunk_id, c.source_id AS source_id, c.text AS text, "
            "c.seq AS seq, c.kind AS kind, c.page AS page, c.href AS href, c.url AS url, "
            "c.para_index AS para_index, c.start_s AS start_s, c.end_s AS end_s",
            chunk_id=chunk_id,
        )
        return rows[0] if rows else None
```

- [ ] **Step 4: Run offline tests + (if Neo4j up) integration; full suite; ruff**

- [ ] **Step 5: Commit**

```bash
git add mslearn/opsdb.py mslearn/graph/store.py tests/test_ingest_state.py tests/test_graph_store.py
git commit -m "feat: durable ingest job state; counted graph writes guard silent no-ops"
```

---

### Task 3: Extraction contracts (schema + parsing + claim ids)

**Files:**
- Create: `mslearn/pipeline/__init__.py` (empty), `mslearn/pipeline/contracts.py`, `tests/test_extraction_contracts.py`

**Interfaces:**
- Produces: `STANCES = ("recommends", "warns_against", "neutral")`; `EXTRACTION_SCHEMA: dict` (JSON schema for structured output — object with `claims` array of `{text, stance, quote}`, additionalProperties false); `ClaimDraft{text, stance, quote}` (pydantic BaseModel, stance validated against STANCES); `parse_extraction(parsed: object) -> list[ClaimDraft]` (validates the model's parsed JSON; raises `ExtractionParseError` on shape/stance violations); `derive_claim_id(chunk_id, text) -> str` (sha256 hex[:16], stable); `to_claim_record(draft, chunk_id, source_id, trust) -> ClaimRecord`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_extraction_contracts.py
import pytest

from mslearn.pipeline.contracts import (
    EXTRACTION_SCHEMA,
    STANCES,
    ClaimDraft,
    ExtractionParseError,
    derive_claim_id,
    parse_extraction,
    to_claim_record,
)


def test_schema_shape():
    props = EXTRACTION_SCHEMA["properties"]["claims"]["items"]["properties"]
    assert set(props) == {"text", "stance", "quote"}
    assert props["stance"]["enum"] == list(STANCES)
    assert EXTRACTION_SCHEMA["properties"]["claims"]["items"]["additionalProperties"] is False


def test_parse_valid():
    drafts = parse_extraction(
        {"claims": [{"text": "Caching helps.", "stance": "recommends", "quote": "cache it"}]}
    )
    assert drafts == [ClaimDraft(text="Caching helps.", stance="recommends", quote="cache it")]
    assert parse_extraction({"claims": []}) == []


def test_parse_rejects_bad_shapes():
    with pytest.raises(ExtractionParseError):
        parse_extraction({"nope": []})
    with pytest.raises(ExtractionParseError):
        parse_extraction({"claims": [{"text": "x", "stance": "loves_it", "quote": "q"}]})
    with pytest.raises(ExtractionParseError):
        parse_extraction(None)


def test_claim_id_stable_and_distinct():
    a = derive_claim_id("src:0", "Caching helps.")
    assert a == derive_claim_id("src:0", "Caching helps.")
    assert a != derive_claim_id("src:1", "Caching helps.")
    assert len(a) == 16


def test_to_claim_record():
    draft = ClaimDraft(text="t", stance="neutral", quote="q")
    rec = to_claim_record(draft, chunk_id="src:0", source_id="src", trust="trusted")
    assert rec.claim_id == derive_claim_id("src:0", "t")
    assert rec.chunk_id == "src:0" and rec.trust == "trusted" and rec.quote == "q"
```

- [ ] **Step 2: Run to verify failure** — ModuleNotFoundError

- [ ] **Step 3: Implement**

```python
# mslearn/pipeline/contracts.py
import hashlib

from pydantic import BaseModel, ValidationError, field_validator

from mslearn.graph.records import ClaimRecord

STANCES = ("recommends", "warns_against", "neutral")

EXTRACTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "stance": {"enum": list(STANCES)},
                    "quote": {"type": "string"},
                },
                "required": ["text", "stance", "quote"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}


class ExtractionParseError(Exception):
    """Model output did not match the extraction contract."""


class ClaimDraft(BaseModel):
    text: str
    stance: str
    quote: str

    @field_validator("stance")
    @classmethod
    def _stance_known(cls, value: str) -> str:
        if value not in STANCES:
            raise ValueError(f"unknown stance {value!r}")
        return value


class _ExtractionOutput(BaseModel):
    claims: list[ClaimDraft]


def parse_extraction(parsed: object) -> list[ClaimDraft]:
    try:
        return _ExtractionOutput.model_validate(parsed).claims
    except ValidationError as exc:
        raise ExtractionParseError(str(exc)[:500]) from exc


def derive_claim_id(chunk_id: str, text: str) -> str:
    return hashlib.sha256(f"{chunk_id}:{text}".encode()).hexdigest()[:16]


def to_claim_record(draft: ClaimDraft, *, chunk_id: str, source_id: str,
                    trust: str) -> ClaimRecord:
    return ClaimRecord(
        claim_id=derive_claim_id(chunk_id, draft.text),
        chunk_id=chunk_id, source_id=source_id,
        text=draft.text, stance=draft.stance, quote=draft.quote, trust=trust,
    )
```

- [ ] **Step 4: Run tests + ruff** — 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add mslearn/pipeline/ tests/test_extraction_contracts.py
git commit -m "feat: extraction contracts — schema, drafts, stable claim ids"
```

---

### Task 4: Trust gate

**Files:**
- Create: `mslearn/pipeline/trust.py`, `tests/test_trust_gate.py`

**Interfaces:**
- Consumes: `ClaimDraft` (Task 3); tunables (Task 1).
- Produces: `TrustVerdict{ok: bool, reasons: list[str], quote_score: float, embed_sim: float | None}` (frozen dataclass); `cosine(a, b) -> float` (pure python; 0.0 on zero vector); `check_claim(chunk_text, draft, *, quote_threshold, embed_sim_threshold, embedder=None) -> TrustVerdict`. Checks: (a) non-empty quote; (b) `rapidfuzz.fuzz.partial_ratio(quote, chunk_text) >= quote_threshold`; (c) when `embedder` given: `cosine(embed(text), embed(quote)) >= embed_sim_threshold`. `embedder: Callable[[list[str]], list[list[float]]]` (the router's `.embed`). All failures accumulate into `reasons` (no short-circuit — retry prompts cite everything).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_trust_gate.py
from mslearn.pipeline.contracts import ClaimDraft
from mslearn.pipeline.trust import TrustVerdict, check_claim, cosine

CHUNK = "Cache invalidation is one of the two hard problems in computer science."


def draft(quote, text="Cache invalidation is hard."):
    return ClaimDraft(text=text, stance="neutral", quote=quote)


def test_cosine():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0  # zero vector safe


def test_verbatim_quote_passes():
    verdict = check_claim(CHUNK, draft("Cache invalidation is one of the two hard problems"),
                          quote_threshold=90.0, embed_sim_threshold=0.35)
    assert verdict.ok and verdict.reasons == []
    assert verdict.quote_score >= 90.0 and verdict.embed_sim is None


def test_fabricated_quote_fails():
    verdict = check_claim(CHUNK, draft("Naming things is easy and fun for everyone"),
                          quote_threshold=90.0, embed_sim_threshold=0.35)
    assert not verdict.ok
    assert any("quote" in r for r in verdict.reasons)


def test_empty_quote_fails():
    verdict = check_claim(CHUNK, draft("   "), quote_threshold=90.0, embed_sim_threshold=0.35)
    assert not verdict.ok


def test_embedding_sanity_check():
    def far_embedder(texts):
        return [[1.0, 0.0] if "invalidation" in t.lower() else [0.0, 1.0] for t in texts]

    verdict = check_claim(
        CHUNK, draft("Cache invalidation is one of the two hard problems",
                     text="Bananas are yellow."),
        quote_threshold=90.0, embed_sim_threshold=0.35, embedder=far_embedder,
    )
    assert not verdict.ok and verdict.embed_sim == 0.0
    assert any("similarity" in r for r in verdict.reasons)


def test_verdict_frozen():
    import pytest

    verdict = TrustVerdict(ok=True, reasons=[], quote_score=100.0, embed_sim=None)
    with pytest.raises(AttributeError):
        verdict.ok = False
```

- [ ] **Step 2: Run to verify failure** — ModuleNotFoundError

- [ ] **Step 3: Implement**

```python
# mslearn/pipeline/trust.py
import math
from dataclasses import dataclass, field
from typing import Callable

from rapidfuzz import fuzz

from mslearn.pipeline.contracts import ClaimDraft

Embedder = Callable[[list[str]], list[list[float]]]


@dataclass(frozen=True)
class TrustVerdict:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    quote_score: float = 0.0
    embed_sim: float | None = None


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def check_claim(
    chunk_text: str,
    draft: ClaimDraft,
    *,
    quote_threshold: float,
    embed_sim_threshold: float,
    embedder: Embedder | None = None,
) -> TrustVerdict:
    reasons: list[str] = []
    quote = draft.quote.strip()

    score = 0.0
    if not quote:
        reasons.append("quote is empty")
    else:
        score = float(fuzz.partial_ratio(quote, chunk_text))
        if score < quote_threshold:
            reasons.append(
                f"quote not found in chunk (match {score:.0f} < {quote_threshold:.0f})"
            )

    sim: float | None = None
    if embedder is not None and quote:
        vec_text, vec_quote = embedder([draft.text, quote])
        sim = cosine(vec_text, vec_quote)
        if sim < embed_sim_threshold:
            reasons.append(
                f"claim/quote similarity {sim:.2f} < {embed_sim_threshold:.2f}"
            )

    return TrustVerdict(ok=not reasons, reasons=reasons, quote_score=score, embed_sim=sim)
```

- [ ] **Step 4: Run tests + ruff** — 6 PASSED

- [ ] **Step 5: Commit**

```bash
git add mslearn/pipeline/trust.py tests/test_trust_gate.py
git commit -m "feat: trust gate — fuzzy verbatim-quote check + embedding sanity, accumulated reasons"
```

---

### Task 5: LangGraph extraction graph

**Files:**
- Create: `mslearn/pipeline/extraction_graph.py`, `tests/test_extraction_graph.py`

**Interfaces:**
- Consumes: contracts (T3), trust gate (T4), prompts+tunables (T1), `ModelRouter` interface (`complete(role, ModelRequest)`, `embed(texts)`).
- Produces: `ExtractionState` (TypedDict: `chunk_id, chunk_text, attempt: int, escalated: bool, accepted: list[ClaimDraft], rejected: list[dict], reasons: list[str], error: str | None`); `build_extraction_graph(router, db)` → compiled LangGraph; `run_extraction(router, db, chunk_id, chunk_text) -> ExtractionState` convenience wrapper. Flow: `extract` (role `extraction`; escalated → role `synthesis`; prompt = `get_prompt(db,"extraction")` + chunk; retry attempts append `extraction_retry_suffix` with accumulated reasons) → `validate` (per-draft `check_claim` with tunable thresholds; embedder = `router.embed`) → conditional: all-ok or nothing-to-retry → END; any failures and attempts < `extract.max_attempts` → `extract` again; attempts exhausted and not yet escalated → escalate (reset attempt, `escalated=True`) → `extract`; escalated and still failing → END with failures in `rejected`. Accepted drafts accumulate across attempts (deduped by text); `ProviderError` inside extract sets `state["error"]` and ends.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_extraction_graph.py
from mslearn.opsdb import OpsDB
from mslearn.pipeline.extraction_graph import run_extraction
from mslearn.providers.base import ModelResponse, ProviderError

CHUNK = "Cache invalidation is one of the two hard problems in computer science."
GOOD = {"claims": [{"text": "Cache invalidation is hard.", "stance": "neutral",
                    "quote": "Cache invalidation is one of the two hard problems"}]}
BAD = {"claims": [{"text": "Bananas are yellow.", "stance": "neutral",
                   "quote": "this text does not appear anywhere at all"}]}


class ScriptedRouter:
    """Returns queued responses; records the roles used."""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def complete(self, role, request):
        self.calls.append(role)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return ModelResponse(text="", parsed=out, input_tokens=1, output_tokens=1,
                             latency_ms=1.0, provider="fake", model="m")

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


def db(tmp_path):
    return OpsDB(tmp_path / "ops.db")


def test_clean_pass_single_attempt(tmp_path):
    router = ScriptedRouter([GOOD])
    state = run_extraction(router, db(tmp_path), "c1", CHUNK)
    assert [d.text for d in state["accepted"]] == ["Cache invalidation is hard."]
    assert state["rejected"] == [] and state["error"] is None
    assert router.calls == ["extraction"]


def test_retry_then_pass(tmp_path):
    router = ScriptedRouter([BAD, GOOD])
    state = run_extraction(router, db(tmp_path), "c1", CHUNK)
    assert len(state["accepted"]) == 1
    assert router.calls == ["extraction", "extraction"]


def test_escalation_after_exhausted_retries(tmp_path):
    router = ScriptedRouter([BAD, BAD, GOOD])
    state = run_extraction(router, db(tmp_path), "c1", CHUNK)
    assert len(state["accepted"]) == 1 and state["escalated"] is True
    assert router.calls == ["extraction", "extraction", "synthesis"]


def test_escalated_failure_ends_with_rejects(tmp_path):
    router = ScriptedRouter([BAD, BAD, BAD, BAD])
    state = run_extraction(router, db(tmp_path), "c1", CHUNK)
    assert state["accepted"] == [] and len(state["rejected"]) == 1
    assert state["escalated"] is True


def test_provider_error_sets_error(tmp_path):
    router = ScriptedRouter([ProviderError("down")])
    state = run_extraction(router, db(tmp_path), "c1", CHUNK)
    assert state["error"] == "down" and state["accepted"] == []


def test_empty_claims_is_valid_end(tmp_path):
    router = ScriptedRouter([{"claims": []}])
    state = run_extraction(router, db(tmp_path), "c1", CHUNK)
    assert state["accepted"] == [] and state["rejected"] == [] and state["error"] is None
```

- [ ] **Step 2: Run to verify failure** — ModuleNotFoundError

- [ ] **Step 3: Implement**

```python
# mslearn/pipeline/extraction_graph.py
from typing import TypedDict

from langgraph.graph import END, StateGraph

from mslearn.opsdb import OpsDB
from mslearn.pipeline.contracts import (
    EXTRACTION_SCHEMA,
    ClaimDraft,
    ExtractionParseError,
    parse_extraction,
)
from mslearn.pipeline.trust import check_claim
from mslearn.prompts import get_prompt
from mslearn.providers.base import ModelMessage, ModelRequest, ProviderError


class ExtractionState(TypedDict):
    chunk_id: str
    chunk_text: str
    attempt: int
    escalated: bool
    drafts: list[ClaimDraft]
    accepted: list[ClaimDraft]
    rejected: list[dict]
    reasons: list[str]
    error: str | None


def build_extraction_graph(router, db: OpsDB):
    max_attempts = int(db.get_tunable("extract.max_attempts"))
    quote_threshold = db.get_tunable("trust.quote_threshold")
    embed_threshold = db.get_tunable("trust.embed_sim_threshold")
    base_prompt = get_prompt(db, "extraction")
    retry_suffix = get_prompt(db, "extraction_retry_suffix")

    def extract(state: ExtractionState) -> dict:
        prompt = f"{base_prompt}\n\nCHUNK:\n{state['chunk_text']}"
        if state["reasons"]:
            prompt += retry_suffix.format(reasons="; ".join(state["reasons"][-4:]))
        role = "synthesis" if state["escalated"] else "extraction"
        request = ModelRequest(
            messages=[ModelMessage(role="user", content=prompt)],
            json_schema=EXTRACTION_SCHEMA,
        )
        try:
            response = router.complete(role, request)
            drafts = parse_extraction(response.parsed)
        except ProviderError as exc:
            return {"error": str(exc)[:500], "drafts": []}
        except ExtractionParseError as exc:
            return {"drafts": [], "reasons": state["reasons"] + [f"parse: {exc}"],
                    "attempt": state["attempt"] + 1}
        return {"drafts": drafts, "attempt": state["attempt"] + 1}

    def validate(state: ExtractionState) -> dict:
        if state["error"] is not None:
            return {}
        accepted = list(state["accepted"])
        seen = {d.text for d in accepted}
        failing: list[dict] = []
        reasons: list[str] = []
        for draft in state["drafts"]:
            if draft.text in seen:
                continue
            verdict = check_claim(
                state["chunk_text"], draft,
                quote_threshold=quote_threshold,
                embed_sim_threshold=embed_threshold,
                embedder=router.embed,
            )
            if verdict.ok:
                accepted.append(draft)
                seen.add(draft.text)
            else:
                failing.append({"draft": draft.model_dump(), "reasons": verdict.reasons})
                reasons.extend(verdict.reasons)
        return {"accepted": accepted, "rejected": failing, "reasons": reasons}

    def route(state: ExtractionState) -> str:
        if state["error"] is not None or not state["rejected"]:
            return "done"
        if state["attempt"] < max_attempts:
            return "retry"
        if not state["escalated"]:
            return "escalate"
        return "done"

    def escalate(state: ExtractionState) -> dict:
        return {"escalated": True, "attempt": 0}

    builder = StateGraph(ExtractionState)
    builder.add_node("extract", extract)
    builder.add_node("validate", validate)
    builder.add_node("escalate", escalate)
    builder.set_entry_point("extract")
    builder.add_edge("extract", "validate")
    builder.add_conditional_edges(
        "validate", route, {"retry": "extract", "escalate": "escalate", "done": END}
    )
    builder.add_edge("escalate", "extract")
    return builder.compile()


def run_extraction(router, db: OpsDB, chunk_id: str, chunk_text: str) -> ExtractionState:
    graph = build_extraction_graph(router, db)
    initial: ExtractionState = {
        "chunk_id": chunk_id, "chunk_text": chunk_text, "attempt": 0,
        "escalated": False, "drafts": [], "accepted": [], "rejected": [],
        "reasons": [], "error": None,
    }
    return graph.invoke(initial)
```

Implementation note for the route logic: `rejected` holds only the CURRENT validate pass's failures (validate overwrites it each pass) — that is what routing keys on; accepted accumulates. This matches the tests exactly.

- [ ] **Step 4: Run tests + ruff** — 6 PASSED

- [ ] **Step 5: Full suite, commit**

```bash
git add mslearn/pipeline/extraction_graph.py tests/test_extraction_graph.py
git commit -m "feat: LangGraph extraction graph — extract/validate/retry/escalate with tunables"
```

---

### Task 6: Celery app, per-process context, extraction task

**Files:**
- Create: `mslearn/worker/__init__.py` (empty), `mslearn/worker/context.py`, `mslearn/worker/app.py`, `mslearn/worker/tasks.py`, `tests/test_worker_tasks.py`

**Interfaces:**
- Produces: `PipelineContext{settings, db: OpsDB, router: ModelRouter, graph: GraphStore}` (dataclass); `get_context()` (raises RuntimeError if unset), `set_context(ctx)`, `build_default_context() -> PipelineContext`; Celery `app` (broker = `settings.redis_url`, `task_acks_late=True`, `worker_prefetch_multiplier=1`, no result backend, routes `extract_chunk_task` → queue `ingest`); `worker_process_init` signal handler calling `set_context(build_default_context())`; `extract_chunk_task(chunk_id)` — flow below. Task 7's orchestrator enqueues it.
- Task flow: source paused? → `mark_chunk(skipped_paused)` return. `graph.get_chunk` → missing? mark failed. `run_extraction` → `error`? raise ProviderTransientError-alike path via Celery `autoretry_for=(ProviderTransientError,)` if transient else mark failed. Otherwise: embed accepted claim texts (`router.embed`), `graph.upsert_claim` each (trust = `escalated` if state escalated else `trusted`), record rejected count, `mark_chunk(done)`. After any failed mark: failure-rate monitor — `failure_stats` vs tunables → `set_source_status(paused)`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_worker_tasks.py
import pytest

from mslearn.opsdb import OpsDB
from mslearn.pipeline.contracts import derive_claim_id
from mslearn.worker import tasks as worker_tasks
from mslearn.worker.app import app
from mslearn.worker.context import PipelineContext, set_context
from tests.test_extraction_graph import BAD, GOOD, CHUNK, ScriptedRouter


class FakeGraph:
    def __init__(self, chunks):
        self.chunks = chunks
        self.claims = {}

    def get_chunk(self, chunk_id):
        return self.chunks.get(chunk_id)

    def upsert_claim(self, claim, embedding):
        self.claims[claim.claim_id] = (claim, embedding)


@pytest.fixture(autouse=True)
def eager_app():
    app.conf.task_always_eager = True
    yield
    app.conf.task_always_eager = False


@pytest.fixture()
def ctx(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    graph = FakeGraph({"s1:0": {"chunk_id": "s1:0", "source_id": "s1", "text": CHUNK}})
    db.register_source("s1", ref="r", role="spine", total_chunks=1)
    db.register_chunk_jobs("s1", ["s1:0"])

    def make(router):
        context = PipelineContext(settings=None, db=db, router=router, graph=graph)
        set_context(context)
        return context

    return make


def test_successful_chunk_commits_claims(ctx):
    context = ctx(ScriptedRouter([GOOD]))
    worker_tasks.extract_chunk_task.delay("s1:0").get()
    cid = derive_claim_id("s1:0", "Cache invalidation is hard.")
    claim, embedding = context.graph.claims[cid]
    assert claim.trust == "trusted" and claim.source_id == "s1"
    assert context.db.source_row("s1")["done_chunks"] == 1


def test_escalated_claims_marked(ctx):
    context = ctx(ScriptedRouter([BAD, BAD, GOOD]))
    worker_tasks.extract_chunk_task.delay("s1:0").get()
    (claim, _), = context.graph.claims.values()
    assert claim.trust == "escalated"


def test_paused_source_skips(ctx):
    context = ctx(ScriptedRouter([GOOD]))
    context.db.set_source_status("s1", "paused")
    worker_tasks.extract_chunk_task.delay("s1:0").get()
    assert context.graph.claims == {}
    assert context.db.pending_chunks("s1") == []  # marked skipped_paused, not pending


def test_missing_chunk_marks_failed(ctx):
    context = ctx(ScriptedRouter([GOOD]))
    context.db.register_chunk_jobs("s1", ["s1:9"])
    worker_tasks.extract_chunk_task.delay("s1:9").get()
    assert context.db.failure_stats("s1")["failed"] == 1


def test_failure_monitor_pauses_source(ctx, tmp_path):
    db = OpsDB(tmp_path / "ops2.db")
    graph = FakeGraph({})
    db.register_source("s2", ref="r", role="spine", total_chunks=12)
    chunk_ids = [f"s2:{i}" for i in range(12)]
    db.register_chunk_jobs("s2", chunk_ids)
    set_context(PipelineContext(settings=None, db=db, router=ScriptedRouter([]), graph=graph))
    for cid in chunk_ids[:10]:  # 10 failures (missing chunks), min_chunks=10, rate 10/12 > 0.5
        worker_tasks.extract_chunk_task.delay(cid).get()
    assert db.source_row("s2")["status"] == "paused"
```

- [ ] **Step 2: Run to verify failure** — ModuleNotFoundError

- [ ] **Step 3: Implement**

```python
# mslearn/worker/context.py
from dataclasses import dataclass

_context = None


@dataclass
class PipelineContext:
    settings: object
    db: object
    router: object
    graph: object


def set_context(context: PipelineContext) -> None:
    global _context
    _context = context


def get_context() -> PipelineContext:
    if _context is None:
        raise RuntimeError("pipeline context not initialised (worker_process_init not run?)")
    return _context


def build_default_context() -> PipelineContext:
    from mslearn.graph.store import GraphStore
    from mslearn.opsdb import OpsDB
    from mslearn.profiles import load_profiles
    from mslearn.providers.router import ModelRouter
    from mslearn.settings import get_settings

    settings = get_settings()
    db = OpsDB(settings.ops_db)
    router = ModelRouter(load_profiles(settings.profiles_path), db, settings)
    graph = GraphStore(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
    return PipelineContext(settings=settings, db=db, router=router, graph=graph)
```

```python
# mslearn/worker/app.py
from celery import Celery
from celery.signals import worker_process_init

from mslearn.settings import get_settings

app = Celery("mslearn", broker=get_settings().redis_url)
app.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_ignore_result=False,
    result_backend="cache+memory://",  # eager tests read results; prod workers don't rely on it
    broker_connection_retry_on_startup=True,
    task_routes={
        "mslearn.worker.tasks.extract_chunk_task": {"queue": "ingest"},
    },
)
app.autodiscover_tasks(["mslearn.worker"])


@worker_process_init.connect
def _init_worker_process(**_kwargs):
    from mslearn.worker.context import build_default_context, set_context

    set_context(build_default_context())
```

```python
# mslearn/worker/tasks.py
from mslearn.pipeline.contracts import to_claim_record
from mslearn.pipeline.extraction_graph import run_extraction
from mslearn.providers.base import ProviderTransientError
from mslearn.worker.app import app
from mslearn.worker.context import get_context


def _check_failure_monitor(db, source_id: str) -> None:
    stats = db.failure_stats(source_id)
    min_chunks = int(db.get_tunable("monitor.min_chunks"))
    threshold = db.get_tunable("monitor.failure_rate_threshold")
    if stats["total"] >= min_chunks and stats["failed"] / stats["total"] > threshold:
        db.set_source_status(source_id, "paused",
                             error=f"failure rate {stats['failed']}/{stats['total']}")


@app.task(
    bind=True,
    autoretry_for=(ProviderTransientError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def extract_chunk_task(self, chunk_id: str):
    ctx = get_context()
    source_id = chunk_id.rsplit(":", 1)[0]

    source = ctx.db.source_row(source_id)
    if source is not None and source["status"] == "paused":
        ctx.db.mark_chunk(chunk_id, "skipped_paused")
        return

    chunk = ctx.graph.get_chunk(chunk_id)
    if chunk is None:
        ctx.db.mark_chunk(chunk_id, "failed", error="chunk not found in graph")
        _check_failure_monitor(ctx.db, source_id)
        return

    state = run_extraction(ctx.router, ctx.db, chunk_id, chunk["text"])
    if state["error"] is not None:
        ctx.db.mark_chunk(chunk_id, "failed", error=state["error"])
        _check_failure_monitor(ctx.db, source_id)
        return

    trust = "escalated" if state["escalated"] else "trusted"
    accepted = state["accepted"]
    if accepted:
        embeddings = ctx.router.embed([d.text for d in accepted])
        for draft, embedding in zip(accepted, embeddings):
            record = to_claim_record(
                draft, chunk_id=chunk_id, source_id=chunk["source_id"], trust=trust
            )
            ctx.graph.upsert_claim(record, embedding)

    error = f"{len(state['rejected'])} claims rejected" if state["rejected"] else None
    ctx.db.mark_chunk(chunk_id, "done", error=error)
```

Note: `source_id` derived from `chunk_id.rsplit(":", 1)[0]` matches the `Chunk.chunk_id = f"{source_id}:{seq}"` contract from Plan 2. When the chunk exists, prefer `chunk["source_id"]` (used for the claim record).

- [ ] **Step 4: Run tests + ruff** — 5 PASSED (eager mode; no Redis needed)

- [ ] **Step 5: Full suite, commit**

```bash
git add mslearn/worker/ tests/test_worker_tasks.py
git commit -m "feat: Celery app with per-process context; extraction task with failure monitor"
```

---

### Task 7: Orchestrator, scheduling, resume, CLI

**Files:**
- Create: `mslearn/pipeline/orchestrator.py`, `mslearn/ingest_cli.py`, `tests/test_orchestrator.py`
- Modify: `README.md` (append Ingestion section), `Makefile` (add `worker` target)

**Interfaces:**
- Consumes: everything above + `load_source`/`chunk_source` (Plan 2).
- Produces: `ingest_source(ref, *, role="supplement", source_type=None, enqueue=True) -> str` (returns source_id; loads via adapter, chunks, embeds chunk texts via router, `graph.upsert_source`+`upsert_chunks`, `db.register_source`+`register_chunk_jobs`, sets status `running`, then `extract_chunk_task.delay(...)` per chunk when `enqueue`); `order_corpus(refs: list[tuple[str, str]]) -> list[tuple[str, str]]` (list of (ref, role): spine entries first, then supplements ordered by ascending file size when local paths — non-paths keep insertion order after files); `enqueue_corpus(refs) -> list[str]`; `resume_pending() -> int` (re-delays every pending chunk of every non-paused/non-done source; returns count). Adapter failures (any exception from `load_source`) → `db.register_source` best-effort + `set_source_status(failed, error=...)` + re-raise nothing (returns source_id or raises `IngestError`? — DECISION: mark failed and raise `IngestError` so the CLI reports it; corpus enqueuer catches per-source and continues).
- CLI: `python -m mslearn.ingest_cli <ref> [--role spine|supplement] [--type pdf|epub|blog|youtube|audio] [--local]` — `--local` runs tasks eagerly in-process (no Redis/worker needed) by setting `app.conf.task_always_eager = True` and building the default context; prints progress summary at the end.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_orchestrator.py
import pytest

from mslearn.opsdb import OpsDB
from mslearn.pipeline.orchestrator import IngestError, ingest_source, order_corpus, resume_pending
from mslearn.worker.context import PipelineContext, set_context
from tests.test_extraction_graph import ScriptedRouter


class RecordingGraph:
    def __init__(self):
        self.sources = []
        self.chunks = []

    def upsert_source(self, doc):
        self.sources.append(doc.source_id)

    def upsert_chunks(self, chunks, embeddings):
        assert len(chunks) == len(embeddings)
        self.chunks.extend(c.chunk_id for c in chunks)


class NoDelayTask:
    def __init__(self):
        self.delayed = []

    def delay(self, chunk_id):
        self.delayed.append(chunk_id)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = OpsDB(tmp_path / "ops.db")
    graph = RecordingGraph()
    router = ScriptedRouter([])
    set_context(PipelineContext(settings=None, db=db, router=router, graph=graph))
    fake_task = NoDelayTask()
    monkeypatch.setattr("mslearn.pipeline.orchestrator.extract_chunk_task", fake_task)
    return db, graph, fake_task


def test_ingest_source_registers_and_enqueues(env, tiny_pdf):
    db, graph, fake_task = env
    source_id = ingest_source(str(tiny_pdf), role="spine")
    assert graph.sources == [source_id]
    row = db.source_row(source_id)
    assert row["status"] == "running" and row["role"] == "spine"
    assert row["total_chunks"] == len(graph.chunks) == len(fake_task.delayed)


def test_ingest_failure_marks_failed(env, tmp_path):
    db, _, _ = env
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(IngestError):
        ingest_source(str(bad))
    sources = db.all_sources()
    assert len(sources) == 1 and sources[0]["status"] == "failed"


def test_order_corpus_spine_first_then_size(tmp_path):
    small = tmp_path / "small.pdf"
    small.write_bytes(b"x" * 10)
    big = tmp_path / "big.pdf"
    big.write_bytes(b"x" * 1000)
    refs = [(str(big), "supplement"), ("https://a.example/post", "supplement"),
            (str(small), "supplement"), (str(big), "spine")]
    ordered = order_corpus(refs)
    assert ordered[0] == (str(big), "spine")
    assert ordered[1] == (str(small), "supplement")
    assert ordered[2] == (str(big), "supplement")
    assert ordered[3][0].startswith("https://")


def test_resume_pending(env, tiny_pdf):
    db, _, fake_task = env
    ingest_source(str(tiny_pdf))
    fake_task.delayed.clear()
    count = resume_pending()
    assert count == len(db.pending_chunks(db.all_sources()[0]["source_id"]))
    assert len(fake_task.delayed) == count
```

- [ ] **Step 2: Run to verify failure** — ModuleNotFoundError

- [ ] **Step 3: Implement**

```python
# mslearn/pipeline/orchestrator.py
from pathlib import Path

from mslearn.adapters.registry import load_source
from mslearn.chunking import chunk_source
from mslearn.worker.context import get_context
from mslearn.worker.tasks import extract_chunk_task


class IngestError(Exception):
    """A source could not be ingested; recorded in ingest_sources."""


def ingest_source(ref: str, *, role: str = "supplement",
                  source_type: str | None = None, enqueue: bool = True) -> str:
    ctx = get_context()
    try:
        doc = load_source(ref, source_type=source_type, role=role)
    except Exception as exc:
        from mslearn.adapters.base import make_source_id

        source_id = make_source_id(ref)
        ctx.db.register_source(source_id, ref=ref, role=role, total_chunks=0)
        ctx.db.set_source_status(source_id, "failed", error=str(exc)[:500])
        raise IngestError(f"failed to load {ref!r}: {exc}") from exc

    chunks = chunk_source(doc)
    embeddings = ctx.router.embed([c.text for c in chunks]) if chunks else []
    ctx.graph.upsert_source(doc)
    ctx.graph.upsert_chunks(chunks, embeddings)
    ctx.db.register_source(doc.source_id, ref=ref, role=role, total_chunks=len(chunks))
    ctx.db.register_chunk_jobs(doc.source_id, [c.chunk_id for c in chunks])
    ctx.db.set_source_status(doc.source_id, "running")
    if enqueue:
        for chunk in chunks:
            extract_chunk_task.delay(chunk.chunk_id)
    return doc.source_id


def order_corpus(refs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    def size_key(ref: str) -> tuple[int, int]:
        path = Path(ref)
        if path.exists():
            return (0, path.stat().st_size)
        return (1, 0)  # non-file refs (URLs) after files, insertion-stable

    spines = [r for r in refs if r[1] == "spine"]
    supplements = sorted(
        (r for r in refs if r[1] != "spine"), key=lambda r: size_key(r[0])
    )
    return spines + supplements


def enqueue_corpus(refs: list[tuple[str, str]]) -> list[str]:
    source_ids = []
    for ref, role in order_corpus(refs):
        try:
            source_ids.append(ingest_source(ref, role=role))
        except IngestError:
            continue  # recorded as failed; never blocks the rest of the corpus
    return source_ids


def resume_pending() -> int:
    ctx = get_context()
    count = 0
    for source in ctx.db.all_sources():
        if source["status"] in ("paused", "done", "failed"):
            continue
        for chunk_id in ctx.db.pending_chunks(source["source_id"]):
            extract_chunk_task.delay(chunk_id)
            count += 1
    return count
```

```python
# mslearn/ingest_cli.py
"""Ingest a source: python -m mslearn.ingest_cli <ref> [--role spine] [--local]"""
import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a source into mslearn")
    parser.add_argument("ref")
    parser.add_argument("--role", choices=["spine", "supplement"], default="supplement")
    parser.add_argument("--type", dest="source_type", default=None)
    parser.add_argument("--local", action="store_true",
                        help="run extraction inline (no Redis/worker needed)")
    args = parser.parse_args()

    from mslearn.pipeline.orchestrator import ingest_source
    from mslearn.worker.app import app
    from mslearn.worker.context import build_default_context, set_context

    if args.local:
        app.conf.task_always_eager = True
    set_context(build_default_context())
    source_id = ingest_source(args.ref, role=args.role, source_type=args.source_type)

    ctx_db = __import__("mslearn.worker.context", fromlist=["get_context"]).get_context().db
    row = ctx_db.source_row(source_id)
    print(f"{source_id}: status={row['status']} chunks={row['total_chunks']}"
          f" done={row['done_chunks']} failed={row['failed_chunks']}")


if __name__ == "__main__":
    main()
```

(Implementation note: replace that `__import__` line with a clean `from mslearn.worker.context import get_context` at top and `get_context().db` — the snippet above is what NOT to leave in; write it cleanly.)

Makefile:

```makefile
worker:
	.venv/bin/celery -A mslearn.worker.app worker -Q ingest,judge --concurrency=2 -l info
```

README append:

```markdown
## Ingestion

`python -m mslearn.ingest_cli <ref> --role spine --local` ingests one source
inline (adapter → chunks → claim extraction → trust gate → Neo4j). Production
mode: `make services`, `make worker`, then enqueue without `--local`. Jobs are
durable in SQLite — `resume_pending()` re-enqueues after a crash; a source
whose chunks fail past the failure-rate tunable is paused, never retried
blindly. Thresholds and prompts are tunables (audited) — the eval loop adjusts
them; see the spec's self-evolution section.
```

- [ ] **Step 4: Run tests + full suite + ruff**

- [ ] **Step 5: Commit**

```bash
git add mslearn/pipeline/orchestrator.py mslearn/ingest_cli.py tests/test_orchestrator.py README.md Makefile
git commit -m "feat: ingestion orchestrator with spine-first scheduling, resume, CLI"
```

---

## Self-Review (performed at write time — error check)

- **Interface fidelity vs existing code:** `ModelRouter.complete(role, request)` / `.embed(texts)` match Plan 1 (`mslearn/providers/router.py`); `Chunk.chunk_id = f"{source_id}:{seq}"` (rsplit in tasks.py is safe because seq is the last `:`-segment); `GraphStore.upsert_claim(claim, embedding)`/`get_chunk` match Plan 3 + Task 2 additions; `ClaimRecord` fields match `mslearn/graph/records.py`. `tiny_pdf` fixture already exists in conftest (Plan 2).
- **Known LangGraph subtlety (checked):** state updates are partial dicts merged by key; `validate` REPLACES `rejected` each pass (routing depends on this) and REPLACES `reasons` with only the current pass's reasons — retry prompt uses last-4 reasons, acceptable. Escalate resets `attempt` to 0 so the escalated model gets `max_attempts` fresh tries; tests pin exact call sequences.
- **Celery eager caveat (checked):** eager mode + `autoretry_for` still executes retries synchronously; `test_provider_error...` uses non-transient `ProviderError` so no retry storm. `result_backend="cache+memory://"` exists solely so eager `.get()` works.
- **Fork-safety:** context built in `worker_process_init` (post-fork); no module-level DB/driver in worker modules (app.py only reads settings for the broker URL string).
- **Self-evolution wiring:** thresholds (Task 1 tunables) + prompts (DB overrides) read at graph-build time — a tunable change applies to the next task without restart; audit trail mandatory via `set_tunable(reason=...)`.
- **Silent-no-op mitigation:** counted writes exist (`run_write_checked`, Task 2); pipeline commit path uses idempotent `upsert_claim` after confirming the chunk exists via `get_chunk` — the MATCH target is therefore guaranteed; orchestrator writes sources before chunks before jobs.
- **Placeholder scan:** one deliberate anti-pattern flagged inline (ingest_cli `__import__` note tells the implementer to write the clean form).
```
