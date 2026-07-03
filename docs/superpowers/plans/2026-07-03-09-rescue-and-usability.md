# Plan 09 — Ingestion Rescue, Honest Status, Projects, and a Human UI

**Status:** ready for implementation (Cursor)
**Depends on:** Plans 1–8 merged (`93a36ff` + fix wave)
**Author context:** produced from a live failure investigation on 2026-07-03 plus a whole-app review. Every Phase-1 item below was reproduced against the running system — these are not hypotheticals.

---

## 0. What actually broke (root-cause evidence — read first)

The user uploaded two PDFs. Outcome: source 1 = 27 chunks, 5 "done", 14 failed, auto-paused; source 2 = 1 chunk, failed, **yet status showed `done`**. Zero `Claim` nodes in Neo4j. "Run synthesis" appeared dead. Diagnosis:

| # | Root cause | Evidence |
|---|---|---|
| R1 | `qwen3.5:9b` is a **thinking model**. Ollama's `num_predict` budget covers thinking + answer. Our `ModelRequest.max_tokens` default is **2048** (`mslearn/providers/base.py:18`) → the model burns the whole budget thinking, `done_reason: "length"`, `message.content == ""` → `ProviderBadOutputError("invalid JSON from ollama: ''")`. | Reproduced with curl: `num_predict: 256` → content len 0, thinking len 1057, `done_reason: length`. With `num_predict: 4096` → valid JSON. 18 `model_calls` rows with exactly this error. |
| R2 | `think: false` is **not** a usable fix on ollama 0.31.1: it disables thinking but the `format` JSON-schema constraint stops being applied (prose comes back). Fix must be budget-based, not think-toggle. | Reproduced with curl. |
| R3 | Extraction latency 130–233 s **per chunk** locally (`model_calls.latency_ms`). A 27-chunk PDF ≈ 1.5 h. With `local=true` (the UI default!) this runs **inline inside the HTTP upload request** — browser spins on "Uploading…" forever, no progress. | `frontend/src/views/CorpusView.tsx:20` (`useState(true)`), `mslearn/server/routers/corpus.py:98-102`. |
| R4 | `try_complete_source` flips `running → done` when `done+failed >= total` — even when **every chunk failed**. Source 2 showed `done` with 0/1 success. | `mslearn/opsdb.py:243-255`. |
| R5 | `make serve` starts **only uvicorn**. No Celery worker → "Run synthesis" enqueues to Redis and nothing ever consumes it; non-local ingests sit pending forever. Nothing in the UI tells the user a worker is required or missing. | `Makefile` (`serve:` target), `corpus.py:146-149`. |
| R6 | Chunk failure reasons live in `chunk_jobs.error` / `ingest_sources.error` but the UI shows only `X/Y (N failed)` — the user cannot see *why* anything failed. | `CorpusView.tsx:210-213` renders counts only. |
| R7 | Neo4j driver spams `GqlStatusObject` warnings to stdout for every query touching a label/property that doesn't exist yet (empty graph). Harmless but terrifying; drowns real logs. | Server log from the session. |
| R8 | The 5 "done" chunks produced `{"claims": []}` (output_tokens 6–10). Likely the same truncation squeezing thinking quality; must re-verify after R1 fix with the same PDF before treating as a prompt problem. | `model_calls` rows: ok, output_tokens 6–10, latency 130 s+. |

---

## 1. Docs to read before touching code

| Topic | Where |
|---|---|
| Provider contract + error taxonomy | `mslearn/providers/base.py` (whole file, ~90 lines) |
| Ollama chat API: `format`, `think`, `done_reason`, `options.num_predict` | https://docs.ollama.com/api (chat endpoint) — verify against installed 0.31.x |
| Tunables + audit contract (self-evolution writes here) | `mslearn/opsdb.py` (`get_tunable`, `set_tunable`, `tunable_audit`), `mslearn/evals/evolve.py` header comment |
| Extraction LangGraph | `mslearn/pipeline/extraction_graph.py` |
| Ingest job lifecycle | `mslearn/pipeline/orchestrator.py`, `mslearn/worker/tasks.py`, `mslearn/opsdb.py:210-265` |
| GraphStore rule: ALL Cypher in store.py, counted writes | `mslearn/graph/store.py` module docstring |
| Neo4j Python driver notification filtering | https://neo4j.com/docs/python-manual/current/ — `notifications_min_severity` driver config |
| Frontend API client + SSE conventions | `frontend/src/api/client.ts` |
| Existing test patterns (fake providers, tmp OpsDB, TestClient) | `tests/test_server_corpus.py`, `tests/test_extraction_graph.py`, `tests/conftest.py` |

**Contract checks (unchanged from Plans 4–8):** all Cypher stays in `store.py`; every tunable/prompt read goes through `get_tunable`/`get_prompt` (self-evolution must be able to move it); every model call is logged; no facts from memory; tests offline by default.

---

## Phase 1 — Make ingestion actually work (P0, do first)

### 1.1 Fix the thinking-token truncation (R1, R2)
- `mslearn/providers/ollama.py`: parse `done_reason` from the response. If `json_schema` was requested and content is empty (or JSON parse fails) **and** `done_reason == "length"`, raise `ProviderBadOutputError(f"output truncated at num_predict={n}; model spent budget on thinking — raise max_tokens")`. Keep the existing error for the non-truncation case.
- New tunable `extract.max_tokens` (default `8192.0`) registered in the tunables seed/defaults; `extraction_graph.py` builds `ModelRequest(..., max_tokens=int(db.get_tunable("extract.max_tokens")))`. Do **not** change the global `ModelRequest` default.
- Do NOT use `think: false` (see R2). Leave a one-line comment in `ollama.py` explaining why.
- Tests: fake response with `done_reason: length` + empty content → truncation message; extraction request carries tunable value.

### 1.2 Honest source terminal status (R4)
- `opsdb.try_complete_source`: when all chunks are terminal, set status `failed` if `failed_chunks == total_chunks` (and write `error = "all N chunks failed"`), else `done`. Keep single-UPDATE atomicity (CASE expression). Return value semantics unchanged (fires synthesis only on the `done` path — synthesizing after a fully-failed source is pointless).
- Tests: all-failed → `failed` + no synthesis dispatch; mixed → `done`; race (two callers) still fires once.

### 1.3 Surface failure reasons (R6)
- New endpoint `GET /api/corpus/sources/{source_id}/failures` → `[{error, count, sample_chunk_ids(≤3)}]` grouped from `chunk_jobs` (new OpsDB method, under lock).
- `CorpusView`: failed count becomes a click-to-expand row showing grouped reasons in plain language, plus the source-level `error` (e.g. "paused: failure rate 14/27"). Add a **Retry failed** button → new endpoint `POST /api/corpus/sources/{id}/retry-failed` that resets `failed`/`skipped_paused` chunk jobs to `pending`, sets source `running`, re-enqueues (reuse `resume_pending` machinery).
- Tests: grouping endpoint; retry resets and re-enqueues.

### 1.4 Silence Neo4j notification spam (R7)
- `graph/store.py`: create driver with `notifications_min_severity="OFF"` (or `warning_filters` equivalent for the installed driver version — check `neo4j.__version__` support first; fall back to logging-level suppression of `neo4j.notifications` logger if the kwarg is unavailable).
- Test: none required (config); verify manually via `make serve` log.

### 1.5 Re-verify extraction quality after 1.1 (R8)
- Re-ingest `data/uploads/1783072656-L1.BigOh.short.pdf` with the fix. Expect: no `''` errors; multiple chunks yield claims. If most chunks still return `{"claims": []}`, tune the extraction prompt (registry override, not hardcode) — but only with evidence, and add the failing chunk as an extraction golden-set case.
- A chunk that legitimately yields zero claims and zero rejects stays `done` — but UI copy should show "no study content found" rather than nothing.

## Phase 2 — Honest run-mode + progress UX (P0)

### 2.1 One command to run everything (R5)
- `scripts/dev_up.sh`: starts docker services, waits for Redis/Neo4j health, launches Celery worker + uvicorn (trap INT → clean shutdown of both). `make run` target invokes it. README already documents the three-process requirement; keep in sync.

### 2.2 Background ingestion by default (R3)
- `CorpusView.tsx`: `local` default `false`; remove the "Run ingest locally (eager Celery)" checkbox from the primary UI (keep the capability API-side for tests/CLI).
- Upload endpoint already just enqueues when `local=false` — verify chunking+chunk-upsert time for a big PDF stays acceptable inside the request (<10 s for a 300-page book; if not, move chunking itself into a Celery task and return `202` with source row `status="chunking"`).

### 2.3 Upload + ingestion progress (user demand)
- File-transfer progress: switch `uploadSource` to `XMLHttpRequest` with `upload.onprogress` → percentage bar while the file uploads.
- Ingestion progress: while any source has status `running`/`chunking`, poll `GET /api/corpus/sources` every 3 s; render a real `<progress>` bar per source (`done+failed`/`total`) with plain-language label ("Reading… 12 of 27 sections · 2 problems"). Stop polling when all terminal.
- Tests: ui test for progress rendering from a mocked source row.

### 2.4 Worker health visibility + synthesis honesty (R5)
- New endpoint `GET /api/admin/health` → `{api: true, worker: bool, redis: bool, neo4j: bool}`; worker check = `celery_app.control.ping(timeout=1)` (guard: run in threadpool, not the event loop).
- UI header chip: green "Background worker running" / red "Worker offline — sources won't process, synthesis won't run" linking to README instructions.
- `POST /api/corpus/synthesize`: response includes `{enqueued: true, worker_online: bool}`; UI warns loudly when `worker_online=false`. Add `GET /api/corpus/synthesis/status` reading the `synthesis:last_run` setting so the UI can show "Last synthesis: 3 concepts updated, 2 min ago".
- Tests: health endpoint with worker absent; synthesize response shape.

## Phase 3 — Multiple independent projects (user demand)

Isolation model: one project = one corpus + its concepts/claims/quiz/chat/memory. Two PDFs about different subjects must never cluster together.

### 3.1 Data model
- OpsDB: `projects(project_id TEXT PK, name TEXT, created_ts REAL)` + `project_id` column (NOT NULL, default `'default'`) on `ingest_sources`, `chunk_jobs`, `quiz_results`; settings that are corpus-scoped (`corpus.domain_profile`, `synthesis:last_run`) become `project:{pid}:corpus.domain_profile` style keys via scoped helpers `get_project_setting/set_project_setting`. Migration: `ALTER TABLE ... ADD COLUMN` guarded by `PRAGMA table_info` check; create `default` project row on first open.
- GraphStore: `project_id` property on `Source/Chunk/Claim/Concept` nodes; **every** MATCH in `store.py` gains a `{project_id: $project_id}` filter (vector-index queries filter results post-KNN on `node.project_id`). `GraphStore` methods take `project_id` explicitly — no module-level current-project state (Celery forks).
- mem0: namespace per project (`user_id=f"learner:{project_id}"`).

### 3.2 API + workers
- `GET/POST /api/projects`, `DELETE /api/projects/{id}` (delete = graph nodes for that project + opsdb rows; confirm-dialog UX).
- Project selection via `X-Project-Id` header, resolved in `get_ctx` dependency; default `'default'`. All routers pass it down. Celery task signatures gain `project_id`.
- Chat session ids namespaced by project.

### 3.3 UI
- Project switcher in the top nav (dropdown + "New project"); persisted in `localStorage`; all api client calls attach the header. Every view already reloads on mount — verify curriculum/concept/chat/corpus refetch when the project changes (key the router tree on project id).
- Tests: two projects, sources isolated, curriculum isolated; opsdb migration idempotent; store filter test (`tests` neo4j-marked) — claim in project A never clusters/retrieves in project B.

## Phase 4 — UI a regular person can use (user demand)

Design constraint: usable by a school kid or a grandparent. No jargon anywhere in the primary flow. Follow accessibility basics (labels, focus, ≥16px body text, buttons ≥44px tap target).

### 4.1 One "Add learning material" card (replaces both forms)
- Tabs: **"From my computer"** (drag-and-drop zone + Browse) and **"From a link"** (single text box: "Paste a YouTube or article link"). Auto-detect type from the URL (`youtube.com`/`youtu.be` → youtube, else blog) — delete the source-type dropdown from the primary flow.
- Role picker becomes a plain question: "Is this your main book/course?" toggle (on → `spine`, off → `supplement`) with one-line explanation.
- Remove "eager Celery" wording entirely (done in 2.2).

### 4.2 Plain-language everything
- Status copy map: `running → "Reading…"`, `chunking → "Preparing…"`, `paused → "Paused — too many problems (see why)"`, `failed → "Couldn't read this (see why)"`, `done → "Ready to study"`.
- Domain profile control moves to a "Project settings" area, rephrased: "When sources disagree, treat this subject as: Facts & techniques / Opinions & interpretations".
- Error strings from the API pass through a translation map before display; raw detail behind a "show technical details" disclosure.
- Empty states: Curriculum page with no concepts → 3-step onboarding graphic ("1. Add a book or video 2. Wait for Reading to finish 3. Your course appears here") + button linking to Corpus. Chat with no corpus → same.

### 4.3 Navigation & readability
- Rename nav labels: Corpus → "My materials", Curriculum → "My course", Chat → "Ask questions", Quiz stays, Memory → "What the app knows about me", Admin → "Advanced".
- Increase base font size, spacing, and button sizes in `styles.css`; verify keyboard-only operation and visible focus rings.
- Keep all existing functionality reachable; nothing removed, only re-labelled/relocated.

## Phase 5 — Verification (definition of done for this plan)

1. `make run` → all three processes up; header chip green.
2. Upload `L1.BigOh.short.pdf`: transfer bar → "Reading… n of 27" live progress → terminal state with **zero** `invalid JSON from ollama: ''` failures; >0 claims in Neo4j (`MATCH (c:Claim) RETURN count(c)`).
3. Kill the worker, press Run synthesis → UI shows explicit "worker offline" warning (no silent no-op).
4. Create project B, upload a different PDF → curriculum/chat in A unchanged; switcher flips cleanly.
5. A fully-failing document (the appointment-confirmation PDF) ends `failed` with a readable reason and a Retry button — never `done`.
6. `make check` + `make ui-test` green; new tests included per phase.
7. README stays accurate (it was rewritten 2026-07-03 — update if commands change).

## Appendix A — Deep backend review findings (2026-07-03)

Fix alongside Phase 1 (small, surgical):

1. **`mslearn/server/routers/corpus.py:99` + `mslearn/server/app.py` (`_local_eager`)** — mutating global `celery_app.conf.task_always_eager` per-request is racy across concurrent requests (request B enqueues while A holds eager=True → B's tasks run inline too, or A restores False mid-B). Becomes moot when 2.2 removes UI-driven eager mode; keep `local` for tests only and document single-threaded assumption.
2. **`mslearn/worker/tasks.py:75-79`** — a chunk whose claims are ALL rejected marks the chunk `failed`, which feeds the failure-rate monitor and can pause the source even though the pipeline behaved correctly (model was hallucinating, gate did its job). Split counters: `failed` (infrastructure/model errors) vs `rejected` (gate). Monitor should trip on both but report them differently in the UI.
3. **`mslearn/pipeline/extraction_graph.py:63-70`** — on a parse-error retry path, `rejected` is set to a synthetic `{"draft": None}` entry; if attempts exhaust with a parse error, task marks chunk failed with reason `"parse: …"` — fine — but `validate` also drops previously-`accepted` claims' reasons; harmless today, verify with a two-claim test (one ok one bad, then retry) that accepted claims from attempt 1 aren't re-embedded/duplicated in attempt 2 (`seen` set only guards same-text).
4. **`mslearn/server/routers/corpus.py:94-95`** — `shutil.copyfileobj` on the request thread with no size cap: a multi-GB upload fills disk. Add max-size guard (e.g. 500 MB, 413 response).
5. **`mslearn/opsdb.py:190-196`** — `set_source_status(..., error=None)` COALESCEs, so a stale error string survives a later `running` transition (resume). Clear error on resume path explicitly.
6. *(verified OK — no action)* Route handlers are plain `def`, so FastAPI runs them (and the sync SSE generators) in its threadpool; provider calls do not block the event loop.

## Appendix B — Deep backend review findings (independent reviewer pass, 2026-07-03)

Severity-ordered. Fix B1–B4 with Phase 1; B5–B8 may ride any later phase.

1. **`mslearn/evals/evolve.py:114,170,174-178`** — `provenance.violations` is hardcoded to `0.0` for both baseline and shadow metrics before the gate check, so the provenance gate in self-evolution is vacuously true: a proposal that increases provenance violations still auto-applies. Compute the real judged provenance metric during evolve (or, if judged runs are too expensive per-proposal, refuse to auto-apply any proposal whose `targets_metric` is provenance-adjacent and mark the gate "not evaluated" instead of silently passing).
2. **`mslearn/evals/evolve.py:179-196`** — `create_evolution_run(..., accepted=False)` is written before the accept decision and never updated, so `GET /api/evals/evolve/history` reports `accepted=0` for every row including applied ones. Update the row (or insert after the decision).
3. **`mslearn/pipeline/quiz.py:90,98,135-145` + `mslearn/server/routers/study.py:68-86`** — pending quiz question is a single global slot keyed `quiz:pending:{concept_id}`: concurrent `GET /api/quiz/next` for one concept cross-clobber sessions, and `POST /api/quiz/answer` can be replayed forever against the cached question, inflating `quiz_stats`. Key the slot by `(session_id, concept_id)` and delete it after grading.
4. **`mslearn/pipeline/synthesis.py:100` + `mslearn/pipeline/exports.py:61,72`** — concept ids derive from `f"k-{min(anchor_id, *matched)}"`, which can change as new claims join a concept across synthesis runs; Anki guids key off concept_id → re-export produces duplicate cards instead of updates. Make concept id sticky once assigned (store and reuse; only mint for genuinely new clusters).
5. **`mslearn/evals/metrics.py:182-187`** — `_quote_match_rate` calls `check_claim` without `embedder=`, so `schema.quote_match_rate` skips the embedding-similarity axis that real extraction applies → inflated pass rate. Pass the embedder (or rename the metric to say quote-only).
6. **`mslearn/opsdb.py:217-232`** (`mark_chunk`) — terminal-state guard is read-then-write; the in-process lock doesn't cover multiple Celery worker processes, so a redelivered task can double-increment `done_chunks`/`failed_chunks`. Replace with one atomic `UPDATE ... WHERE status NOT IN ('done','failed')` + rowcount check.
7. **`mslearn/server/routers/chat.py:18`** (`_SESSIONS`) — process-local unbounded dict: leaks memory and breaks with `uvicorn --workers N`. Cap size (LRU) and document single-worker assumption, or move sessions to OpsDB.
8. **`mslearn/evals/runner.py:29-38`** — `component` filter is a no-op: `compute()` computes everything and `k in GATES` retains all gated metrics regardless. Either implement real filtering or drop the parameter.
9. **Failed chunks are unrecoverable** (`opsdb.py:234-241`): `pending_chunks()` only selects `status='pending'`; nothing resets `failed→pending`. Already covered by Phase 1.3's retry-failed endpoint — noted here so the implementer wires retry through the same machinery.

Reviewer verified OK (no action): qa/quiz/teaching/exports trust filtering on both retrieval paths; genanki id determinism (modulo B4); metrics greedy-match double-count guards + divide-by-zero guards; golden-set `approved/corrected` filtering; opsdb lock/commit discipline; no `async def` event-loop blocking; tunables/prompt registry compliance; Celery post-fork resource construction.

## Appendix C — Working-tree drift (fix before starting)

Uncommitted edits already sitting in the tree (pre-Plan-9 work in flight):
- `profiles.yaml:7` — default profile `extraction` role switched to `{provider: openrouter, model: "deepseek/deepseek-v4-flash"}`. Consistent with fixing R1 by routing extraction off-device; keep or revert deliberately. Note: Phase 1.1 (token-budget fix) is still required for the `offline`/`claude-code` profiles that keep Ollama extraction.
- `tests/test_router.py:52` — still asserts the old `ollama`/`qwen3.5:9b` extraction pair → **1 failing test** (`test_routes_by_active_profile_role`). Update the assertion to match `profiles.yaml` (line 55 was already updated).
- `docker-compose.yml` + `mslearn/settings.py` + `tests/test_compose.py` + `tests/test_settings.py` — Redis port 6379→6380, self-consistent.
- Baseline at review time: 229/230 backend tests pass, 16/16 ui tests pass, ruff clean.

---

*Execution order: Phase 1 → 2 (both P0) → 3 → 4 → 5. Commit per phase, run `make check` + `make ui-test` before each commit.*
