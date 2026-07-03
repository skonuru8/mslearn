# Plan 10 — Reasoning-Model Budget + Runtime Fixes (A1–A8)

**Status:** ready for implementation
**Depends on:** Plan 09 complete (`eb1b647`)
**Baseline:** 264 backend tests, 27 ui tests, ruff clean, ui-build green.

## Root cause (verified in code 2026-07-03)

`deepseek/deepseek-v4-flash` (default profile: synthesis/interactive/evals, and now extraction) is a **reasoning model**. Every non-extraction `ModelRequest` still uses default `max_tokens=2048` (`mslearn/providers/base.py:18`). The model spends the completion budget on reasoning → OpenRouter returns `choices[0].message.content: null` → `mslearn/providers/openrouter.py:74` assigns `text=None`, then line 80 `json.loads(None)` raises **`TypeError`** (only `JSONDecodeError` is caught) → escapes the `ProviderBadOutputError` 502 handler → raw 500 / worker traceback. Same class of bug as Plan 09 R1, fixed there only for the Ollama extraction path.

One cause, four symptoms: synthesize_task traceback, quiz `/next` 500, teach hang-then-fail ("My course" click), chat producing zero frames.

## Tasks (order matters; A1+A2 unblock everything else)

### A1 — openrouter.py null-content guard (PRIMARY)
`complete()`: if `content` is `None` or `""`, raise `ProviderBadOutputError` including `choices[0].finish_reason` — when `finish_reason == "length"`, message must say "completion budget spent on reasoning — raise max_tokens". Wrap the `json.loads` in a guard that can never let `TypeError` escape. Mirror the Plan 09 ollama fix wording.
Tests: fake response with `content: null, finish_reason: "length"` → ProviderBadOutputError; `content: null` without schema also raises (never return `text=None`).

### A2 — per-role max-token budgets
Role-level `params: {max_tokens: N}` in `profiles.yaml` (router `_merged` role-param merge already exists — verify it applies to `max_tokens`, else extend). Set 8192 for synthesis/interactive/evals roles in the `openrouter` profile. Keep `extract.max_tokens` tunable as-is. If per-callsite tunables are preferred instead, seed `synth.max_tokens`/`chat.max_tokens`/`quiz.max_tokens`/`teach.max_tokens` in `TUNABLE_DEFAULTS` and wire via `get_tunable` in `synthesis.py`, `quiz.py`, `teaching.py`, `chat.py`, `evolve.py`. Pick ONE mechanism, document in plan-completion note.
Tests: request built by each callsite carries the configured budget.

### A3 — teach generation UX
`ConceptView.tsx` GET `/teach` is a synchronous multi-minute LLM call inside the request. After A1/A2 it succeeds but still slow: add UI state "Writing your lesson… (first time can take a minute or two)" instead of generic spinner; add 'teach cached' fast path indicator. Moving generation to worker + polling is OPTIONAL — only if straightforward; do not destabilize.

### A4 — chat zero-frame streams
`openrouter.py stream()`: track whether any content delta was yielded; on `[DONE]`/end with zero content yielded, raise `ProviderBadOutputError("stream ended with no content (reasoning budget exhausted?)")` so chat.py's existing mid-stream error frame fires and the UI shows a real error instead of silence.
Tests: fake SSE with only reasoning deltas → error frame in `/api/chat` response.

### A5 — persist chat history
Chat turns currently in process-local `_SESSIONS` (LRU-capped) — history dies on restart. Persist turns to OpsDB: new table `chat_turns(project_id, session_id, ts, question, answer)` under lock; `GET /api/chat/sessions/{id}` reads from DB; `_SESSIONS` becomes read-through cache or is deleted. Scope by project (Plan 09 Phase 3 conventions — `X-Project-Id` dep).
Tests: turn appended → survives new OpsDB handle; project isolation.

### A6 — memory tab friendliness + mem0 init
`MemoryView` 503 copy → plain language: "Personal memory is off. The app can still teach and quiz you — it just won't personalize." + "show technical details" disclosure with the real reason. Check startup log `learner memory disabled: <reason>` and fix the actual mem0 init failure if it's config-shape (document what was wrong).

### A7 — lazy provider construction (startup-crash regression)
`ModelRouter.__init__` eagerly constructs all providers (`router.py:25-27`); `OpenRouterProvider.__init__` raises on empty key → app cannot start keyless even on `offline` profile. Make provider construction lazy (build on first use per provider name, cache), or defer the key check to first `complete/stream/embed`. Keyless + offline profile must fully boot and work.
Tests: router with empty openrouter key + offline profile → extraction/embedding calls work, no raise at construction.

### A8 — log noise
`scripts/dev_up.sh`: celery `-l warning`, uvicorn `--log-level warning` (access log off). Task failures must land in `ingest_sources.error` / failures endpoint (verify Plan 09 1.3 covers worker exceptions, not only chunk errors). Keep one concise startup banner per process so users know it's alive.

## Conventions (unchanged)
All Cypher in `graph/store.py`; tunables/prompts via `get_tunable`/`get_prompt`; model calls logged; offline tests with fake providers; `make check` + `make ui-test` green per commit; conventional commit messages.

## Verification
1. Keyless machine, offline profile: app boots, ingest works.
2. With key: upload smoke PDF → synthesis completes without traceback; quiz `/next` returns question; concept teach renders; chat streams answer or shows explicit error — no silent hang anywhere.
3. Restart API → chat history still present.
4. `make check` + `make ui-test` green.
