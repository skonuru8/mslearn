# Architecture Conformance Audit — 2026-07-04

Spec: `docs/superpowers/specs/2026-07-02-multi-source-learning-system-design.md`
Audited at: commit `79b6a40` (post plan-13 A1–A4). Every row verified against
the working tree; line numbers refer to that commit. Statuses are honest:
**on-track** (matches spec), **deviated** (differs; noted whether accepted),
**partial** (some of the spec item exists), **missing**.

## Conformance table

| # | Spec section | Implemented where | Status | Evidence (file:line) |
|---|---|---|---|---|
| 1a | Adapters ×4 with locators (§2) | `mslearn/adapters/` | on-track | PDF via PyMuPDF, page locators: `adapters/pdf.py:8,23`. EPUB via ebooklib, href locators: `adapters/epub.py:21,37`. Blog via trafilatura, url+para locators: `adapters/blog.py:19-27`. YouTube captions via youtube-transcript-api, time locators: `adapters/youtube.py:43-74`. Audio via faster-whisper: `adapters/audio.py:7-18`, `transcribe.py:17-34`. |
| 1b | Whisper fallback path (§2: caption-less yt-dlp→Whisper; audio faster-whisper) | `adapters/youtube.py`, `adapters/registry.py`, `worker/tasks.py`, `worker/context.py` | **fixed (plan 14)** | The wiring is now closed: `PipelineContext` carries a lazily-constructed shared `transcriber` built once per worker process (`worker/context.py`, cheap — the faster_whisper import + model load defers to first `.transcribe()`), threaded through `chunk_source_task` → `load_source(..., transcriber=ctx.transcriber)` → `load_audio`/`load_youtube`. `registry.load_source` passes it explicitly and raises a readable error instead of a bare `KeyError` when audio is ingested without one. Offline tests (`tests/test_ingest_transcriber.py`) drive an audio source and a caption-less-YouTube source end-to-end through `chunk_source_task` with a fake transcriber, and assert the clear error path. Memory-bounding of the transcription itself is row 8. |
| 2 | Chunking ~200–500 tokens, locators preserved (§2) | `mslearn/chunking.py` | on-track (minor note) | `CHUNK_TARGET_TOKENS = 500` upper bound with oversized-paragraph splitting: `chunking.py:6,32,43-52`; locator carried per emitted chunk: `chunking.py:54-79`. No explicit 200-token lower bound — small trailing buffers emit as-is. Minor, not a correctness issue. |
| 3 | Trust gate: quote fuzzy match + embed sanity, tunables audited (§2) | `mslearn/pipeline/trust.py`, `mslearn/opsdb.py` | on-track | `check_claim` at `pipeline/trust.py:26`: rapidfuzz `partial_ratio` vs threshold (`trust.py:41-44`), claim↔quote cosine vs threshold (`trust.py:51-53`). Thresholds are tunables with defaults `trust.quote_threshold=90`, `trust.embed_sim_threshold=0.35` (`opsdb.py:118-119`), resolved at call time and audited on write (`opsdb.py:407-425`, `tunable_audit` table `opsdb.py:28`). |
| 4 | LangGraph extraction graph extract→validate→retry→escalate (§2) | `mslearn/pipeline/extraction_graph.py` | on-track | `StateGraph` with nodes extract/validate/escalate and conditional edges `{"retry": "extract", "escalate": "escalate", "done": END}`: `extraction_graph.py:115-124`; escalation switches role to `synthesis` (judge backend): `extraction_graph.py:48,112-113`; retry re-emphasizes failure reasons in the prompt: `extraction_graph.py:47`. Attempt budget from tunable `extract.max_attempts` (`opsdb.py:120`). |
| 5 | Synthesis as checkpointed LangGraph graph (§3) | `mslearn/pipeline/synthesis.py`, `worker/tasks.py` | **deviated (accepted, plan 5)** | Implemented as plain functions (`cluster_new_claims` `synthesis.py:23`, `process_dirty_concepts` `synthesis.py:170`, `build_curriculum` `synthesis.py:269`) driven by `synthesize_task` on the judge queue (`worker/tasks.py:326`, routed at `worker/app.py:23`). Plan 5 documented this shape explicitly (`docs/superpowers/plans/2026-07-02-05-synthesis-and-curriculum.md:7`). Rationale holds: the run is incremental and idempotent (dirty-concept marking `synthesis.py:47-104`), so re-running after a crash reconverges without LangGraph checkpoints; Celery autoretry + the plan-13 heartbeat/progress markers (`worker/tasks.py:343-352`) cover the resumability/observability intent. No checkpoint store exists, so a killed run redoes its current phase from the top — acceptable at this corpus scale. Tension taxonomy matches spec: `graph/records.py:3-8` {context_dependent, outdated, genuine_debate, evidence_mismatch}; domain-profile steering: `synthesis.py:176-177`. |
| 6 | Neo4j property graph + vector indexes + export portability rule (§3) | `mslearn/graph/store.py`, `graph/export.py`, `pipeline/exports.py` | on-track (one caveat) | Labels/rels as specced: `EXTRACTED_FROM` `store.py:205`, `IN_CONCEPT` `store.py:358`, `DEPENDS_ON` `store.py:371`, `CONFLICTS_WITH {classification, rationale}` `store.py:386-392`. Native vector indexes on claims+chunks: `store.py:39-42,95,288-334`. GraphML+JSON dump: `graph/export.py:8-16`, `pipeline/exports.py:80-88`. Portability caveat closed (plan 14): the export endpoint now emits the GraphML+JSON dump unconditionally regardless of requested `kinds` (`server/routers/exports.py`), so `{"kinds": ["markdown"]}` still dumps the graph — a caller can no longer opt out. Covered by `tests/test_exports.py::test_exports_endpoint_always_dumps_graph_even_for_markdown_only`. |
| 7 | mem0 learner-memory integrity rule (§3b) | `mslearn/memory/mem0_impl.py`, `server/routers/chat.py`, `pipeline/quiz.py`, `server/routers/memory.py` | partial | mem0 on the same Neo4j: `memory/mem0_impl.py:38-45`. Personalization-only enforced by prompt framing ("PERSONALIZATION ONLY" prefix on every hint, `server/routers/chat.py:138`) and by construction — facts are only retrieved from the graph (`chat.py:83` retrieval vs memory hints are separate prompt sections). Quiz failures recorded to memory: `pipeline/quiz.py:138,197-202`. Memory panel inspect/delete: `server/routers/memory.py:12,21`, `frontend/src/views/MemoryView.tsx`. Provenance eval exists and is real when run non-offline: `evals/judged.py:119-134`, wired into the runner (`evals/runner.py:32`) and into self-evolution gating (`evals/evolve.py:126`). Gaps: offline runs default `provenance.violations` to 0.0 (`runner.py:34`) so the gate is vacuous without live backends, and it has never been run live. Minor resolved (plan 14): the mem0 embedder model is no longer hardcoded — it resolves from the active profile's `embedding` role with a `memory.embed_model` opsdb override (`mem0_impl.py`), restoring "model IDs live in config, never code". |
| 8 | Celery queues: 3 (ingest/judge/transcribe, whisper exclusive) (§1) | `worker/app.py`, `Makefile`, `scripts/dev_up.sh`, `mslearn/transcribe.py` | **deviated (accepted + done, plan 14)** | 2 queues, not 3: routes at `worker/app.py:20-24` (chunk/extract→ingest, synthesize→judge); workers `-Q ingest --concurrency=2` and `-Q judge --concurrency=1` (`Makefile:47-51`, `scripts/dev_up.sh:70-74`). Decision: accept whisper-on-ingest rather than add a third `transcribe` queue (more machinery than a single-user local app warrants). The 18 GB memory concern the spec's exclusivity was protecting is met by serializing transcription instead: `SerializingTranscriber` (`transcribe.py`) wraps the whisper transcriber in a process lock + advisory file lock so even with `--concurrency=2` only one transcription runs at a time per machine; non-transcribing chunk work keeps its parallelism. Model stays `"small"` + int8 (~0.5 GB resident). Judge concurrency is 1 vs spec's ~8 — deliberate post-spec choice (plan 12: synthesis must not starve ingest; a single serial synthesis run is the dedup model). |
| 9 | Profiles/hot-swap; model IDs config-only (§5) | `profiles.yaml`, `mslearn/profiles.py`, `server/routers/admin.py` | on-track (noted deviations) | Three profiles with role→provider+model+params maps: `profiles.yaml` (openrouter/claude-code/offline). Hot-swap endpoints `GET/POST /api/admin/profiles`: `admin.py:101-112`; UI toggle: `AdminBar.tsx:121-129`. `ModelProvider` interface with 3 implementations: `providers/base.py:63`, `ollama.py:24`, `openrouter.py:26`, `claude_code.py:15` (non-bare `claude -p`, `claude_code.py:16`). Every call logged to SQLite: `providers/router.py:60-69` → spend chip in UI (`AdminBar.tsx:133`). Deviations: extraction on the default profile is `deepseek/deepseek-v4-flash` via OpenRouter, not local qwen (user-approved switch; qwen3.5:9b remains in offline/claude-code profiles); reasoning disabled on all four openrouter roles (plan 13 A1, incident-driven); mem0 embedder hardcode noted in row 7. |
| 10 | Evals: golden sets, gates, self-evolution (§6) | `mslearn/evals/`, `evals/golden/` | **partial** | Release gate numbers match the spec exactly: `evals/gates.py:3-9` (extraction ≥0.90/≥0.85, grounding false-accept ≤0.02, clustering F1 ≥0.80, tension ≥0.75, schema ≥0.99). Deterministic metrics implemented: `evals/metrics.py:17-206`. Self-evolution is genuinely eval-gated with audit trail and rollback: `evals/evolve.py:115` (proposal → golden-set comparison → apply only on improvement), provenance-adjacent guard `evolve.py:17,126`, audit rows `opsdb.py:418`, rollback `evolve.py:248-253` + endpoint `admin.py:148-153`, tunable history endpoint `admin.py:141-144`. Seeding + review workflow exists at the API level: `evals/seed.py:38-156` (writes `review="pending"`), review endpoints `server/routers/evals.py:28-63`, loader honors review status (`evals/golden.py:13,87`). **Gaps:** the shipped golden sets are 6 handwritten rows per component (`evals/golden/*.jsonl`, verified 6/6/6/6) vs spec's ~200 extraction / ~300 clustering / ~100 tension — placeholder scale; all rows were committed directly as `"review": "approved"` (commit `e59eee7`) without passing through the human-review workflow the spec requires; no frontend review view (the eval **report** page now exists — plan 14 added a read-only Evals view under Advanced calling `GET /api/evals/report`, `frontend/src/views/EvalsView.tsx`; the golden-review view is still API-only); the `evals` pytest marker is declared (`pyproject.toml:41`) but no test carries it; no judged eval run has ever executed against live backends. |
| 11 | Exports: markdown determinism, anki stable ids, graph dump (§4) | `mslearn/pipeline/exports.py` | on-track | Markdown regenerates without model calls (no router usage in the module; content from graph reads only) with sorted, stable ordering: `exports.py:95,137`. Anki guids are stable per concept/claim seed: `exports.py:225-229` (`genanki.guid_for("mslearn", guid_seed)`); concept ids are sticky by design to protect them (`pipeline/synthesis.py:344-353`). Graph dump: row 6. |
| 12 | Spec verification steps 1–6 (§Verification) | — | **partial** | Step-by-step below. |

## Verification steps: what has actually been run

1. **`make run` boots services + API + workers** — live-verified by daily use
   (`scripts/dev_up.sh`, the running system this audit was written next to). PASS.
2. **Smoke corpus on `offline` profile end-to-end** — `scripts/make_smoke_corpus.py`
   and fixtures exist; no record that the full offline-profile pass (claims →
   curriculum → planted-conflict callout → all three exports opened) was executed
   as specified. NOT EVIDENCED.
3. **OpenRouter profile: streaming Q&A, quiz grading, memory bias** — partially
   live-verified: interactive calls exist in `model_calls` (3 ok) and chat streams
   (`server/routers/chat.py:43-56`); the quiz-failure→memory-panel→review-bias
   loop has not been demonstrated live. PARTIAL.
4. **`pytest` green without network** — PASS, continuously (308 passed, 22 skipped
   offline at this commit; graph integration via `make graph-test` against a
   disposable container).
5. **Eval run meets all release gates incl. memory-provenance** — NOT RUN. Golden
   sets are placeholder-scale, judged evals have never run live, and the offline
   runner stubs provenance to 0.0.
6. **Kill worker mid-ingestion → resume without duplicate claims** — no deliberate
   drill recorded. Circumstantial evidence the machinery works: idempotent
   MERGE-based upserts (`store.py:158,202`), atomic chunk-state transitions
   (`opsdb.py:490-519`), `resume_pending` (`orchestrator.py:72`), and the 2026-07-04
   incident where a synthesis run errored mid-flight, autoretried, and resumed.
   PARTIAL — the specific duplicate-claim check in Neo4j after a kill has not
   been performed.

## Remaining for production-ready

- [x] Wire a whisper transcriber into the worker ingest path (`chunk_source_task` →
      `load_source`) so audio uploads and caption-less YouTube stop failing —
      the UI accepts both today and both break in the worker (row 1b). **Done,
      plan 14** (shared transcriber on `PipelineContext`, offline-tested).
- [x] Decide the transcription queue question (row 8): **accepted**
      whisper-on-ingest with a bounded memory budget — transcription is
      serialized machine-wide via `SerializingTranscriber` (process + file
      lock), model kept at `"small"`/int8, documented in the README run
      section. No third `transcribe` queue.
- [ ] Seed golden sets at spec scale (~200 extraction chunks across all 4 source
      types, ~300 clustering pairs, ~100 tension pairs) from the real corpus and
      pass every row through human review (`review: pending → approved/corrected`)
      instead of committing pre-approved rows.
- [ ] Run the judged eval suite against live backends and record a release-gate
      report; until then no release gate has ever actually been evaluated,
      including the memory-provenance gate.
- [ ] Run `scripts/release_check.sh` end-to-end on a live environment
      (services up → suites → neo4j marks → eval runner) and record the result.
- [ ] Perform the worker kill/resume drill (spec verification step 6) and check
      for duplicate claims in Neo4j afterwards.
- [ ] Execute the offline-profile smoke-corpus pass (spec verification step 2)
      including the planted-disagreement conflict callout and opening all three
      export formats.
- [ ] Demonstrate the quiz-failure → memory panel → biased-review loop live
      (spec verification step 3, second half).
- [x] Build the frontend eval report page (spec §6 "UI report page"). **Done,
      plan 14** — read-only Evals view under Advanced (`EvalsView.tsx`) renders
      per-component metric vs gate with pass/fail and a clean empty state. (The
      golden-set *review* view remains API-only and is a separate future item.)
- [x] Move the mem0 embedder model id (`nomic-embed-text`, `mem0_impl.py:49`)
      into `profiles.yaml`/settings to restore "model IDs live in config, never
      code". **Done, plan 14** — resolves from the active profile's `embedding`
      role with a `memory.embed_model` opsdb override.
- [x] Close the export-portability loophole. **Done, plan 14** — every export
      request now dumps the GraphML+JSON graph server-side regardless of the
      requested `kinds`; the opt-out is gone.
