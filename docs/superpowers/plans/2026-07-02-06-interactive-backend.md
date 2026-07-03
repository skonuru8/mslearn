# Plan 6/8: Interactive Backend, Memory, Exports — Implementation Plan

> **For the implementer (Cursor):** TDD per task, commit per task, repo root, `.venv/bin/pytest` / `.venv/bin/ruff check .`. STOP and report if reality diverges from a step.
>
> **Contract check before starting** — every symbol below must import cleanly; if any fails, STOP and report:
> ```
> .venv/bin/python -c "
> from mslearn.worker.context import PipelineContext, get_context, set_context, build_default_context
> from mslearn.pipeline.synthesis import cluster_new_claims, process_dirty_concepts, build_curriculum
> from mslearn.pipeline.orchestrator import ingest_source, enqueue_corpus, resume_pending
> from mslearn.graph.store import GraphStore, GraphWriteError
> from mslearn.graph.export import write_graphml, write_json
> from mslearn.prompts import get_prompt, PROMPTS, get_domain_profile
> from mslearn.opsdb import OpsDB, TUNABLE_DEFAULTS
> from mslearn.providers.router import ModelRouter
> print('contract ok')"
> ```

**Goal:** The localhost web backend: FastAPI serving corpus management, curriculum, teaching (with required tension callouts + citations), quiz with graded free-text answers, streaming Q&A chat with framework attribution, learner memory (mem0 behind an interface, integrity-ruled), profile/tunables admin, spend panel, and deterministic exports (Markdown, Anki `.apkg`, GraphML/JSON).

**Architecture:** `mslearn/server/` — `app.py` (FastAPI factory + lifespan builds one `PipelineContext` and calls `set_context`), `deps.py` (DI: `get_ctx()`), routers `corpus.py`, `study.py` (curriculum/teach/quiz), `chat.py` (SSE), `memory.py`, `admin.py`, `exports.py`. Learner memory behind `mslearn/memory/base.py::LearnerMemory` protocol — `Mem0Memory` impl (lazy import) + `InMemoryLearnerMemory` for tests. Teaching content generated on demand, cached on the Concept node, invalidated by flags. All tests offline: FastAPI `TestClient`, fakes injected by overriding `set_context`.

**Tech Stack (added):** `fastapi>=0.111`, `uvicorn>=0.30`, `genanki>=0.13`, `mem0ai>=0.1` (lazy).

## Docs to read before starting

| Topic | URL / file | Need |
|---|---|---|
| FastAPI lifespan + DI | https://fastapi.tiangolo.com/advanced/events/ + /tutorial/dependencies/ | app factory, overrides in tests |
| StreamingResponse SSE | https://fastapi.tiangolo.com/advanced/custom-response/#streamingresponse | `media_type="text/event-stream"`, `data: ...\n\n` framing |
| genanki | https://github.com/kerrickstaley/genanki README | Model/Note/Deck/Package, stable model/deck ids |
| mem0 | https://docs.mem0.ai/open-source/quickstart + /open-source/graph_memory | `Memory.from_config`, `add/search/get_all/delete`, Neo4j graph config, Ollama embedder + OpenAI-compatible LLM config |
| Existing code | `mslearn/graph/store.py`, `mslearn/pipeline/synthesis.py`, `mslearn/worker/*`, `mslearn/providers/router.py` | everything you wire |

## Global Constraints

- **Memory integrity rule (spec §3b):** `LearnerMemory` is consulted ONLY to personalize delivery (teach depth, quiz selection, chat tone/level). No factual sentence in any generated output may originate from memory — facts come from trust-gated claims with citations. Teaching/answer prompts must instruct: "Use ONLY the provided claims for facts; cite as [claim:<id>]."
- Every generated factual output carries citations `[claim:<claim_id>]`; the teach/chat prompt supplies claims WITH ids and locator info; outputs are stored/streamed verbatim
- Concepts with conflicts: tension callout REQUIRED in teach output (prompt instructs; deterministic post-check: if `conflicts_in_concept` non-empty, generated markdown must contain a `## Where sources disagree` section — regenerate once if missing, then error)
- Interactive model calls: role `interactive` (streaming chat) or `synthesis` (teach generation, quiz grading — judgment work); prompts via `get_prompt` (new entries registered), never inline strings
- All durable state: SQLite (quiz_results, settings) / Neo4j (teach cache on Concept props) / mem0's Neo4j namespace. Exports under `data/exports/`
- Server never constructs per-request resources; one context at startup (OpsDB lock + thread-safe driver make this sound)
- Tests offline: TestClient + `InMemoryGraphStore` (extend tests/fakes.py as needed) + `InMemoryLearnerMemory` + `ScriptedRouter`; mem0 NEVER imported in tests (lazy import gate test like faster-whisper's)
- Prior suite (164) green; ruff clean; commit per task

---

### Task 1: Server skeleton + context wiring + admin router

**Files:** `mslearn/server/__init__.py` (empty), `mslearn/server/app.py`, `mslearn/server/deps.py`, `mslearn/server/routers/__init__.py`, `mslearn/server/routers/admin.py`, `tests/test_server_admin.py`; modify `pyproject.toml` (fastapi, uvicorn, genanki, mem0ai), `Makefile` (`serve:` target `.venv/bin/uvicorn mslearn.server.app:create_app --factory --port 8000`).

**Interfaces:** `create_app(context: PipelineContext | None = None) -> FastAPI` (None → `build_default_context()` in lifespan; tests pass fakes); `deps.get_ctx()` returns the app-bound context. Admin endpoints:
- `GET /api/admin/profiles` → `{"active": name, "available": [names]}` (uses `load_profiles` + `get_active_profile_name`)
- `POST /api/admin/profiles/{name}` → switches via `set_active_profile_name` (404 unknown)
- `GET /api/admin/tunables` → list of `{key, value, default}`; `POST /api/admin/tunables/{key}` body `{value, reason}` → `set_tunable` (422 unknown key)
- `GET /api/admin/tunables/{key}/history` → `tunable_history`
- `GET /api/admin/spend?limit=100` → `recent_calls` + aggregate `{total_cost_usd, total_calls, by_role: {...}}`
- `GET /api/health` → `{"status": "ok"}`

**Tests:** TestClient with fake context (OpsDB on tmp_path is real; router/graph fakes): health; profile list/switch/404; tunable get includes defaults, set+history round-trip, unknown 422; spend aggregates roles from logged fake calls.

**Commit:** `feat: FastAPI skeleton with profile/tunables/spend admin API`

---

### Task 2: Corpus router

**Files:** `mslearn/server/routers/corpus.py`, `tests/test_server_corpus.py`

**Interfaces:**
- `POST /api/corpus/sources` body `{ref, role, source_type?, local?}` → calls `ingest_source` (`enqueue=True`; when `local` true set `app.conf.task_always_eager` for the call — document that production uses workers); returns `{source_id}`; `IngestError` → 422 with reason
- `GET /api/corpus/sources` → `all_sources()` rows (progress fields included)
- `POST /api/corpus/sources/{source_id}/pause` / `/resume` → `set_source_status` paused / running + `resume_pending()` on resume; 404 unknown
- `POST /api/corpus/settings/domain-profile` body `{profile}` (technical|interpretive → `set_setting("corpus.domain_profile", ...)`; 422 otherwise); `GET` returns current
- `POST /api/corpus/synthesize` → `synthesize_task.delay()`; returns `{"enqueued": true}` (manual kick)

**Tests:** ingest happy path registers + returns id (monkeypatch orchestrator's task delay as in test_orchestrator.py); ingest failure 422 + source marked failed; pause/resume flips status and resume re-delays pending; domain profile set/get + validation.

**Commit:** `feat: corpus API — ingest, progress, pause/resume, domain profile`

---

### Task 3: Learner memory interface (+ mem0 impl, lazy)

**Files:** `mslearn/memory/__init__.py` (empty), `mslearn/memory/base.py`, `mslearn/memory/mem0_impl.py`, `mslearn/memory/fake.py` is NOT needed (put `InMemoryLearnerMemory` in `tests/fakes.py`), `tests/test_memory.py`; modify `mslearn/worker/context.py` (`PipelineContext` gains `memory: object | None = None`; `build_default_context` builds `Mem0Memory` lazily — wrap in try/except ImportError → None with a logged warning so the system runs without mem0 installed).

**Interfaces (`mslearn/memory/base.py`):**

```python
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MemoryItem:
    memory_id: str
    text: str
    category: str  # "struggle" | "preference" | "interaction"
    created_at: float


class LearnerMemory(Protocol):
    def add(self, text: str, category: str) -> str: ...
    def search(self, query: str, k: int = 5) -> list[MemoryItem]: ...
    def all(self) -> list[MemoryItem]: ...
    def delete(self, memory_id: str) -> None: ...
```

`Mem0Memory(settings)` — lazy `from mem0 import Memory` inside `__init__`/`_client()`; config: Neo4j graph store (settings uri/user/password), embedder = Ollama (`nomic-embed-text`, base url from settings), LLM = OpenAI-compatible pointing at OpenRouter (api key + `deepseek/deepseek-chat` — read model from active profile's `interactive` role via load_profiles, NOT hardcoded; if that plumbing is awkward, read `db.get_setting("memory.llm_model", <interactive role model>)`). Single user_id `"learner"`. Map mem0 result dicts → `MemoryItem` (category from metadata, default "interaction").
`InMemoryLearnerMemory` (tests/fakes.py): list-backed, naive substring search.

**Tests:** protocol conformance of the fake; lazy-import test (`import mslearn.memory.mem0_impl` → `"mem0" not in sys.modules`); context builds with memory=None when mem0 missing (monkeypatch import to raise).

**Commit:** `feat: LearnerMemory interface with lazy mem0 impl and test fake`

---

### Task 4: Study router — curriculum + teaching (cached, tension-checked)

**Files:** `mslearn/server/routers/study.py`, `mslearn/pipeline/teaching.py`, additions to `mslearn/prompts.py` (`teach_concept`, `quiz_question`, `quiz_grade`), `tests/test_teaching.py`, `tests/test_server_study.py`; store additions in `mslearn/graph/store.py`: `get_concept(concept_id) -> dict | None` (meta + teach_md + teach_at), `set_concept_teaching(concept_id, teach_md)`, `citations_for_claims(claim_ids) -> list[dict]` (claim → chunk locator fields for source-linked citations).

**Interfaces:**
- `PROMPTS["teach_concept"]`: inputs = concept name/summary, claims list `[claim:<id>] (<stance>, source <source_id>) <text> — quote: "<quote>"`, conflicts list with classifications/rationales, `{memory_hints}` (personalization only), `{domain_guidance}`. Output contract in prompt: markdown with sections `## Explanation`, `## Worked example`, `## Common misconception`, and — REQUIRED whenever conflicts provided — `## Where sources disagree` presenting each side with `[claim:<id>]` citations; every factual sentence cites; memory hints must never introduce facts.
- `teaching.generate_teaching(ctx, concept_id, force=False) -> str`: cached `teach_md` returned unless force/dirty; builds prompt (claims via `claims_in_concept` + quotes/citations, conflicts via `conflicts_in_concept`, memory hints via `ctx.memory.search(concept name)` when memory present — hint texts prefixed "PERSONALIZATION ONLY:"), role `synthesis`, non-schema (markdown) call; **tension post-check**: conflicts exist and `## Where sources disagree` missing → one regeneration with an appended corrective instruction → still missing → raise `TeachingError`; cache via `set_concept_teaching`.
- Endpoints: `GET /api/study/curriculum` (store.curriculum()); `GET /api/study/concepts/{id}` (meta + claims + conflicts + citations); `GET /api/study/concepts/{id}/teach?force=` → `{markdown}`; `POST /api/study/claims/{claim_id}/flag` body `{reason}` → `set_claim_trust(claim_id,"rejected")` + concept marked dirty + teach cache cleared (`set_concept_teaching(id, "")`) + `synthesize_task.delay()`; 404s throughout.

**Tests (offline, fakes + ScriptedRouter):** teaching happy path caches (second call: zero router calls); force regenerates; tension post-check triggers exactly one retry then errors (scripted: markdown without section, then with); memory hints included in prompt when memory present but output verification not needed (assert prompt contains "PERSONALIZATION ONLY"); flag endpoint rejects claim, dirties concept, clears cache, enqueues synthesis (monkeypatched delay); curriculum endpoint shape.

**Commit:** `feat: teaching generation with required tension callouts, caching, flag-to-regenerate`

---

### Task 5: Quiz router

**Files:** `mslearn/server/routers/study.py` (extend), `mslearn/pipeline/quiz.py`, `mslearn/opsdb.py` (quiz_results table + `record_quiz_result` + `quiz_stats(concept_id?)`), `tests/test_quiz.py`

**Interfaces:**
- `PROMPTS["quiz_question"]` (concept + claims w/ ids → JSON `{"question","expected_points":[...]}`, reasoning-not-recall instruction) and `PROMPTS["quiz_grade"]` (question, expected_points, user answer → JSON `{"correct": bool, "score_0_100": int, "explanation": str}` — explanation must cite claims)
- `quiz.next_concept(ctx) -> str | None`: prefer concepts with recent failures from `quiz_stats` (memory-informed: also `memory.search("struggles")` names — advisory re-ordering only), else curriculum order first un-quizzed
- Endpoints: `GET /api/quiz/next` → `{concept_id, question}` (schema call role synthesis; question cached transiently in opsdb settings under `quiz:pending:<concept_id>`); `POST /api/quiz/answer` body `{concept_id, answer}` → grade (schema call), `record_quiz_result(concept_id, correct, score)`, memory add on failure (`category="struggle"`, text "struggled with <concept name>: <expected point missed>") — never store the user's answer text as fact; returns grade JSON
- `GET /api/quiz/stats` → per-concept aggregates

**Tests:** next-question generation + pending caching; grading records result and, on failure, adds ONE memory item with category struggle; stats aggregation; schema-invalid judge output → 502 with taxonomy error surfaced (ProviderBadOutputError passthrough handler).

**Commit:** `feat: quiz flow — reasoning questions, judged grading, struggle memory`

---

### Task 6: Chat router (streaming Q&A with attribution)

**Files:** `mslearn/server/routers/chat.py`, `mslearn/pipeline/qa.py`, `mslearn/prompts.py` (`qa_answer`), `tests/test_qa.py`, `tests/test_server_chat.py`

**Interfaces:**
- `qa.retrieve(ctx, question, k=8) -> dict`: embed question (`router.embed`), `vector_search_claims` (trusted/escalated only — filter) + `vector_search_chunks`, gather conflicts among retrieved claims' concepts; returns `{"claims": [...], "chunks": [...], "conflicts": [...]}`
- `PROMPTS["qa_answer"]`: context blocks with `[claim:<id>]`; instruction: answer ONLY from provided material with citations; when provided conflicts show frameworks disagreeing, attribute each position to its source explicitly ("Source X holds..., while Source Y...") — never blend into one voice; if material insufficient say so; `{memory_hints}` personalization-only.
- `POST /api/chat` body `{question, session_id}` → SSE stream (`text/event-stream`): frames `data: {"delta": "..."}\n\n` from `router.stream("interactive", request)` where request messages = system prompt + transient session history (in-process dict, last 10 turns) + retrieval context + question; final frame `data: {"done": true, "citations": [claim_ids present in answer]}\n\n`; after stream: append turn to session history; `memory.add` interaction summary (question topic only, category "interaction")
- `GET /api/chat/sessions/{id}` → transient history (or empty)

**Tests:** retrieval filters rejected claims; SSE endpooint with ScriptedRouter streaming fake (give ScriptedRouter a `stream` method in tests/fakes.py or subclass — yields chunks) → TestClient collects frames, asserts delta framing + done frame + citation extraction (`[claim:x]` regex from streamed text); conflict attribution instruction present in prompt when conflicts retrieved; history trimmed to 10.

**Commit:** `feat: streaming Q&A with trust-filtered retrieval and framework attribution`

---

### Task 7: Memory router + exports + static serving

**Files:** `mslearn/server/routers/memory.py`, `mslearn/server/routers/exports.py`, `mslearn/pipeline/exports.py`, `tests/test_exports.py`, `tests/test_server_memory.py`; `app.py` mounts `frontend/dist` as static when it exists (`StaticFiles(directory=..., html=True)` at `/`); README section.

**Interfaces:**
- Memory endpoints: `GET /api/memory` (all items), `DELETE /api/memory/{memory_id}`; 503 when context.memory is None (mem0 unavailable)
- `exports.export_markdown(ctx, out_dir) -> list[Path]`: one file per curriculum concept — name, summary, teach_md if cached (else claims+quotes rendered deterministically — NO model calls), citations footnoted with locators; plus `_index.md`
- `exports.export_anki(ctx, out_path) -> Path`: genanki — stable ids (`deck_id = 1607392319`, `model_id = 1607392320` constants), one basic card per concept (front = "Explain: <name>" back = summary + key claims) + one per conflict (front = "Where do sources disagree on <name>?" back = positions w/ citations); write `.apkg`
- `exports.export_graph(ctx, out_dir)`: `export_all()` → `write_graphml` + `write_json`
- `POST /api/exports` body `{kinds: ["markdown","anki","graph"]}` → runs selected, returns file paths; files under `data/exports/<timestamp>/`
- Static: `create_app` mounts dist when present (API routes take precedence — mount last)

**Tests:** markdown deterministic (no router calls — assert ScriptedRouter unused), files contain citations + conflict sections for conflicted concepts; anki file exists + genanki package readable (open zip, check `collection.anki2` present); graph export produces both files (fake export_all data); memory endpoints list/delete + 503 path; static mount smoke (dist absent → API still serves).

**Commit:** `feat: memory panel API, deterministic Markdown/Anki/graph exports, static serving`

---

## Self-Review (write-time error check)

- Memory integrity enforced three ways: prompt contract (facts only from claims), hint prefix "PERSONALIZATION ONLY", quiz stores struggle summaries not answers-as-facts; Plan 8 adds the provenance eval on top.
- Tension callout is required + post-checked deterministically (section heading), matching spec §4 "required part of the explanation".
- mem0 fully isolated behind `LearnerMemory`: lazy import, None-tolerant context, fake for tests — mem0 API drift cannot break the suite.
- Roles: interactive = streaming chat only; synthesis = teach/quiz-grade/question (judgment). Matches spec §5 role table.
- Static mount after routers avoids shadowing `/api/*`.
- Session history transient by design (spec: no conversational memory becomes knowledge).
```
