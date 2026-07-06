# Plan 14 — Finish the Audit's Code Items

**Status:** ready for implementation
**Depends on:** `7ea9b60` (Plan 13 complete)
**Source:** the "Remaining for production-ready" list in
`docs/superpowers/audits/2026-07-04-architecture-conformance.md`. This plan
covers ONLY the items an agent can actually finish in code + tests. The
operational items (run judged eval suite live, seed golden sets at spec scale
through human review, `release_check.sh` live run, worker kill/resume drill,
offline smoke-corpus pass, quiz→memory→bias live demo) are explicitly OUT OF
SCOPE — they need the live system, real corpus, and human review, and are
handed back to the user as a manual checklist.

## Task 1 — Wire the whisper transcriber into the worker (audit row 1b, the real bug)

Today `worker/tasks.py::chunk_source_task` calls
`load_source(ref, source_type=source_type, role=role)` with no `transcriber`.
Result: audio uploads hit `registry.py:48` `kwargs["transcriber"]` → `KeyError`;
caption-less YouTube hits `youtube.py:76-78` → `TranscriptUnavailable`. Both are
accepted by the API, both fail in the worker. A `Transcriber` (wraps a
`faster_whisper.WhisperModel`) is not serializable, so it CANNOT travel through
Celery task args — it must be constructed inside the worker process.

- Add a lazily-constructed shared transcriber to the worker's `PipelineContext`
  (`worker/context.py`): a `transcriber` field built by `build_default_context`
  via a factory that defers the heavy `faster_whisper` import until first use
  (mirror `FasterWhisperTranscriber._load`'s lazy pattern — construction of the
  `FasterWhisperTranscriber` object is cheap; only `.transcribe()` loads the
  model). Model name from settings (see Task 3), default `"small"`.
- `chunk_source_task`: pass `transcriber=ctx.transcriber` through to
  `load_source(...)` so it reaches `load_youtube`/`load_audio`.
- `load_source` already forwards `**kwargs`; `load_audio` requires the
  transcriber positionally (`registry.py:48`) — make `registry.load_source`
  pass `transcriber` through explicitly for both `audio` and `youtube`, and
  raise a clear `IngestError`-friendly message (not a bare `KeyError`) if a
  transcription-needing source is loaded without one (defensive; the CLI path
  `ingest_cli` may still lack one).
- `ingest_cli`: construct a transcriber there too (or accept a `--no-transcribe`
  that fails audio cleanly with a readable message).
- Tests (offline, NO real whisper): a fake transcriber returning canned
  `TranscriptSegment`s; assert an audio source and a caption-less-youtube source
  (fake `fetch_transcript` raising, fake `download_audio`) both produce chunks
  end-to-end through `chunk_source_task` with an eager Celery app; assert a clear
  error (not KeyError) when transcription is needed and none is available.

## Task 2 — Transcription resource ceiling (audit row 8 decision)

Decision: **accept whisper-on-ingest, bounded and documented** (a third
`transcribe` queue + per-argument routing is more machinery than a single-user
local app warrants; the spec's concern was the 18 GB M3 memory ceiling).

- Guard against two concurrent transcriptions colliding with Ollama on 18 GB:
  wrap the `.transcribe()` call site in a process-level `threading.Lock` (or a
  Celery-level guard) so that even with ingest `--concurrency=2`, only one
  whisper transcription runs at a time within a worker. Non-transcribing chunk
  work keeps its parallelism.
- Default whisper model `"small"` (already the default) — keep; note the
  int8/`"small"` memory footprint in a code comment.
- README: one line under the run section — "First audio/caption-less-video
  ingest downloads a Whisper model; transcription is serialized to protect
  memory." Update the audit doc row 8 / checklist item to "accepted + done".

## Task 3 — mem0 embedder id to config (audit row 7/10 minor)

`memory/mem0_impl.py:49` hardcodes `"nomic-embed-text"`, violating "model IDs
live in config, never code."
- Add an `embedding` model resolution: reuse the active profile's `embedding`
  role model (the router already knows it) or add a settings field
  `mem0_embed_model` defaulting to `nomic-embed-text`. Prefer reading the
  profile's embedding role so it stays consistent with the rest of the system.
- Test: mem0 config uses the configured id (construct with a stub profile,
  assert the id threads through — keep it offline; do not require a live mem0).

## Task 4 — Close the export-portability loophole (audit row 6)

Spec §3: "every export run also dumps the full knowledge graph." Today
`POST /api/exports {"kinds":["markdown"]}` can skip the graph dump.
- Make the graph dump (GraphML + JSON) ALWAYS included server-side regardless
  of requested `kinds` — the markdown/anki kinds stay opt-in, the graph dump is
  unconditional (it is cheap and is the portability guarantee). Adjust the
  response to always report the graph files.
- Test: request with `kinds:["markdown"]` still writes GraphML + JSON.

## Task 5 — Frontend eval report page (audit row 10 / spec §6 "UI report page")

The API endpoints exist (`server/routers/evals.py`) but nothing in
`frontend/src` calls them. Build a read-only **"Evals"** view under the Advanced
area (not the primary kid/grandparent flow):
- Calls `GET /api/evals/report` (or the existing report/history endpoints —
  read `evals.py` for exact routes) and renders per-component metric values vs
  their gate thresholds, pass/fail coloring, and the last run timestamp. Plain
  language where it faces the user ("Extraction accuracy: 0.00 — not yet run").
- Handle the "no runs yet" empty state cleanly (the gates have never been run
  live — the page must not look broken; say "No eval run yet. Run
  `python -m mslearn.evals.run` to populate this.").
- Add a nav link under Advanced; keep it out of the main flow.
- Tests: vitest render with a mocked report payload (populated + empty).

## Out of scope — hand back to the user (operational, needs live system / review)

Copy this list into the final report so the user sees exactly what remains and
why an agent can't do it:
- Seed golden sets at spec scale (~200 extraction / ~300 clustering / ~100
  tension) from the real corpus, each row human-reviewed.
- Run the judged eval suite against live OpenRouter and record a gate report.
- Run `scripts/release_check.sh` end-to-end live.
- Worker kill/resume drill + Neo4j duplicate-claim check.
- Offline-profile smoke-corpus pass (planted-conflict callout + open all three
  exports).
- Quiz-failure → memory-panel → biased-review live demonstration.

## Conventions
Cypher in `graph/store.py`; tunables/prompts via registry; offline tests with
fakes; graph tests only via `make graph-test` (destructive, env-gated). Suites
green per commit. Conventional commits ending with the standard trailer. Do not
push. Do not restart/kill the user's running processes.

## Verification
1. `make check` + `make ui-test` + `make ui-build` green; `make graph-test` green.
2. An audio source and a caption-less-youtube source both ingest end-to-end in
   an eager/offline test with a fake transcriber.
3. Export with `kinds:["markdown"]` still emits the graph dump.
4. Evals view renders populated + empty states.
5. Audit doc updated: rows 1b, 6, 7/10-embedder, 8, and the eval-UI item flipped
   to done; the operational items remain as the user's manual checklist.
