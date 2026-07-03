# Plan 11 — REST Diet, Terminal Signal, Background Ingest

**Status:** ready for implementation
**Depends on:** Plan 10 complete (`577ec11`)
**Source:** read-only audit 2026-07-03 (polling map, endpoint costs, logging inventory — findings embedded below with file:line evidence).

Baseline steady state today: 6 calls/min per open tab (health @15s + spend @30s), ~50 kB/min, polling continues when tab backgrounded. Target: 2 small calls/min visible, 0 hidden.

## Task 1 — Spend chip stops shipping 100 rows (biggest byte win)

`GET /api/admin/spend?limit=100` returns 100 full `model_calls` rows (~23.7 kB) every 30 s; `AdminBar.tsx:108` renders only `total_cost_usd` + `total_calls`; `recent_calls` is referenced nowhere in `frontend/src` (verified).

- New `OpsDB.spend_totals()` → `SELECT COUNT(*) AS total_calls, COALESCE(SUM(cost_usd),0) AS total_cost_usd FROM model_calls` (under `self._lock`).
- Keep `/api/admin/spend` (full rows) for on-demand detail only — never on a timer.
- Tests: totals method; endpoint shape.

## Task 2 — One consolidated status poll, visibility-gated

- New `GET /api/status` in `admin.py`: `{worker, redis, neo4j, spend: {total_cost_usd, total_calls}, synthesis: {last_run, last_error}}`. Reuse `worker_online()`, `_redis_online`, `_neo4j_online`, `spend_totals()`, project synthesis settings (project via `X-Project-Id` dep for the synthesis part).
- `AdminBar.tsx:43-44`: delete both timers; one ~30 s timer calling `/api/status`, paused when `document.visibilityState !== "visible"` (visibilitychange listener; immediate refresh on return to visible). Chip + spend numbers + synthesis banner all feed from it. CorpusView may reuse the synthesis part or keep its on-mount fetch — do not add a second timer.
- `CorpusView.tsx:129-139` 3 s active-source poll: keep (already scoped to running/chunking sources) but gate on tab visibility too.
- Note: `/api/admin/health` handler blocks a threadpool thread up to ~3 s (celery ping 1 s + redis ping 1 s + neo4j verify). Fine at 1 call/30 s; do not increase frequency. Keep endpoint for compatibility, AdminBar stops calling it.
- Tests: /api/status shape (worker offline case); AdminBar test update (existing `AdminBar.test.tsx` asserts chip behavior — adapt to consolidated fetch).

## Task 3 — Terse terminal signal ("mslearn" logger)

No `basicConfig`/`dictConfig` anywhere in `mslearn/`; dev_up.sh runs celery `-l warning` + uvicorn `--log-level warning`, so `logger.info` is swallowed today. Provider errors are never logged (only written to `model_calls`) — that is why errors appear in the UI but not the terminal.

- New `mslearn/logging_setup.py`: `configure_event_log()` → logger `"mslearn"`, own `StreamHandler(sys.stderr)`, terse one-line formatter (`%H:%M:%S level message`), `setLevel(INFO)`, `propagate=False`, idempotent (don't stack handlers). Call from `server/app.py::create_app` and `worker/app.py` module init.
- Event sites (all one-liners, ≤160 chars):
  1. `orchestrator.py::ingest_source` — `source registered id=… ref=… chunks=N`.
  2. `worker/tasks.py::_finalize_chunk` — single choke point: INFO on `done`, WARNING on `failed`/`rejected` with truncated error.
  3. `worker/tasks.py::_check_failure_monitor` — WARNING `source … paused: failure rate …` when it flips status.
  4. `worker/tasks.py::synthesize_task` — INFO success line with processed/curriculum counts (payload already computed).
  5. `providers/router.py` complete/stream/embed error blocks (`router.py:56-63, 77-85, 98-105`) — WARNING `provider error role=… model=…: <first 120 chars>` next to the existing `log_model_call`.
  6. `server/app.py::provider_bad_output_handler` — WARNING before returning 502.
- dev_up.sh flags unchanged.
- Tests: caplog-based — chunk failure logs one warning line; provider error logs role+model.

## Task 4 — Truncated-JSON errors carry finish_reason + get a retry

`openrouter.py::complete`: the empty-content branch includes `finish_reason`; the `json.loads` failure branch (the user's exact symptom — degenerate `'{ "claims": … \t\n \t\n '`) does not, though `choice` is in scope.

- Include `finish_reason` in that error message.
- In `pipeline/extraction_graph.py::extract`: catch `ProviderBadOutputError` separately from other `ProviderError` — treat like a parse failure (append reason, increment attempt, retry within existing `extract.max_attempts` budget, then escalate path as normal) instead of failing the chunk permanently on one truncation. Preserve current behavior for non-BadOutput `ProviderError` (terminal error state).
- Tests: fake provider raising BadOutput once then GOOD → chunk succeeds on attempt 2; finish_reason appears in message.

## Task 5 — Background ingest (`chunk_source_task`) + "Preparing…" becomes real

`ingest_source` runs adapter load (YouTube no-caption fallback = yt_dlp download + whisper transcription — minutes), chunking, embedding of every chunk, and Neo4j upserts inline in the HTTP POST (`orchestrator.py:14-47`, called from `corpus.py` create/upload). The `"chunking"` status is rendered in the UI (`userMessages.ts`, `CorpusView.tsx:20-22`) but never written by any backend path (grep: zero hits in `mslearn/`).

- Split `ingest_source`: synchronous part = `make_source_id` + `register_source(total_chunks=0)` + `set_source_status("chunking")`, return source_id immediately. New Celery task `chunk_source_task(project_id, source_id, ref, role, source_type)` on the `ingest` queue does load→chunk→embed→graph upserts→`register_chunk_jobs`→update total_chunks→status `running`→enqueue `extract_chunk_task`s. Adapter failure inside the task → status `failed` with reason (same as today's sync failure path).
- Idempotency/races (from audit):
  - Task starts with `if source_row(...)["status"] != "chunking": return` guard (mirrors the paused-skip in `extract_chunk_task`).
  - `register_source` is INSERT OR IGNORE (safe); `register_chunk_jobs` is INSERT OR IGNORE (safe); `upsert_chunks` is MERGE (safe) — a retried task cannot duplicate.
  - Delete-while-chunking: DELETE endpoint should also work for `chunking` sources; task's status guard prevents post-delete resurrection (row gone → guard returns).
  - `autoretry_for=(ProviderTransientError,)` + backoff (embedding call can blip), max_retries 3, on exhaustion → status `failed`.
- `--local`/eager paths (CLI, tests) must keep working: eager mode runs the task inline — verify `ingest_cli` and existing tests still pass; keep a `local=true` inline option where tests rely on it.
- The `total_chunks` for a `chunking` source is 0 — progress UI already shows "Preparing…" for that status; verify no divide-by-zero (`progressFraction` guards `total_chunks <= 0` already).
- Tests: create-source returns immediately with status `chunking`; eager run flips to `running` with jobs registered; adapter failure → `failed`+reason; status guard makes second run a no-op.

## Task 6 — UI build staleness guard

User saw missing Remove button because `frontend/dist` predated the commit. In `scripts/dev_up.sh`, before starting uvicorn: if `frontend/dist` is missing or any file in `frontend/src` is newer than the dist bundle → run `npm --prefix frontend run build` (echo one line saying why). Keep it fast when fresh.

## Task 7 — Reasoning-off experiment for interactive (empirical, optional)

OpenRouter documents `"reasoning": {"enabled": false}`; support is model-specific and unverified for `deepseek/deepseek-v4-flash`. `RoleConfig.params` already merges into requests (`router.py::_merged`), so this is a `profiles.yaml`-only change.
- If a live key is available: one live call to v4-flash with `params: {reasoning: {enabled: false}}`; if accepted and content is immediate (no reasoning tokens in usage), switch the `interactive` role to v4-flash with that param and drop deepseek-chat. If rejected/ignored → keep deepseek-chat, document the result in this plan file.
- Never block the rest of the plan on this.

**Result (empirical, live-verified 2026-07-03, `MSL_OPENROUTER_API_KEY` present in `.env`):** the param is honored.

Two probe calls to `deepseek/deepseek-v4-flash` via a throwaway scratchpad script (not committed), prompt: *"A user says: 'I think the capital of France is Lyon.' Briefly correct them."*

| | without param | `reasoning: {enabled: false}` |
|---|---|---|
| `content` | correct, non-null | correct, non-null |
| `finish_reason` | `stop` | `stop` |
| `completion_tokens` | 50 | 11 |
| `reasoning_tokens` | 34 | 0 |
| `cost` (USD) | 1.708e-05 | 4.312e-06 (~4x cheaper) |

No reasoning tokens, immediate correct content, no rejection/ignoring of the param. `profiles.yaml`'s `openrouter` profile `interactive` role now uses `deepseek/deepseek-v4-flash` with `params: {reasoning: {enabled: false}}`, dropping `deepseek/deepseek-chat`. `tests/test_router.py::test_provider_error_logs_role_and_model` updated to assert the new model id.

## Task 8 (lower priority) — Curriculum N+1

`CurriculumView.tsx:23-30` fires one `GET /api/study/concepts/{id}` per concept just for conflict badge counts. Add `conflict_count` to the `/api/study/curriculum` rows (one Cypher aggregate in `store.py`), drop the fan-out.

## Conventions
All Cypher in `graph/store.py`; tunables/prompts via `get_tunable`/`get_prompt`; offline tests with fake providers; `make check` + `make ui-test` (+ `ui-build` when frontend touched) green per commit; conventional commits.

## Verification
1. Idle tab network: only `/api/status` every ~30 s (<1 kB); nothing when tab hidden.
2. Add YouTube link → POST returns in <2 s, row shows "Preparing…", then live "Reading…" progress.
3. Terminal shows one-line events: source registered / chunk failures with reason / source done / synthesis result / provider errors — while celery+uvicorn stay quiet.
4. Truncated-JSON extraction failure retries once and succeeds (test), error text includes finish_reason.
5. `make run` after a frontend change rebuilds dist automatically.
6. Full suites green.
