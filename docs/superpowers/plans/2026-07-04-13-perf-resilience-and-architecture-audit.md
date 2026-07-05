# Plan 13 — Performance, Hang Resilience, Architecture Conformance Audit

**Status:** ready for implementation
**Depends on:** `efbd1f5` (test-DB isolation)
**Incident evidence (live, 2026-07-04 morning):** synthesis of ONE 107-chunk video ran 78+ minutes. Timeline from `model_calls`: ~230 fast calls, then ONE request hung 4,601 s (survived the 600 s httpx read timeout — consistent with a machine sleep freezing the socket clock), errored `The read operation timed out`, Celery autoretried, run resumed and progressed. Meanwhile the UI showed "Building your course…" for an hour with `running_since: 1783171168` and no way to tell live from wedged. Additional latency data: synthesis-role calls with reasoning ON averaged 4.9 s, max 18.9 s per call; interactive role with reasoning OFF completes comparable calls in 1–3 s.

## Part A — Fixes (in order, one commit each)

### A1 — Reasoning off for all OpenRouter roles (ALREADY EDITED, uncommitted)
`profiles.yaml` working-tree edit adds `params: {reasoning: {enabled: false}}` to extraction/synthesis/evals (interactive already had it). Verify the edit, run suites, fold into the first commit. Rationale in the YAML comment. Worker restart note goes in the final report (profiles load per process).

### A2 — Stale "Building your course…" self-heal
`GET /api/corpus/synthesis/status` (corpus.py): when `synthesis:running_since` is set AND older than `2 * SYNTHESIS_RUNNING_TTL_S` (import from opsdb; = 60 min), treat as abandoned: clear the marker (set to ""), and return `running_since: null` plus `last_error: {ts: now, error: "course build was interrupted — press Build to restart"}` in the response ONLY when no fresher `last_run` exists (do not overwrite the DB's last_error). Also: `synthesize_task` must refresh `running_since` between its three phases (cluster → process → curriculum) so long-but-alive runs keep a fresh heartbeat and never trip the TTL.
Tests: fresh marker passes through; stale marker → cleared + synthetic last_error; heartbeat refresh between phases (monkeypatch phases, assert marker ts increases).

### A3 — Hung-request bound
`openrouter.py`: replace the single `timeout=600.0` with `httpx.Timeout(connect=15.0, read=600.0, write=60.0, pool=60.0)` so connect/pool exhaustion can't masquerade as a long read. Also add Celery `soft_time_limit=3600, time_limit=3900` to `synthesize_task` and `soft_time_limit=1800, time_limit=2100` to `chunk_source_task`/`extract_chunk_task` — a wedged task must die and release its slot instead of occupying it for hours; on `SoftTimeLimitExceeded`, write the source/synthesis error state before exiting (except-clause per task).
Tests: soft-limit handler writes error state (raise SoftTimeLimitExceeded from a monkeypatched phase).

### A4 — Synthesis progress in the UI (kills the "is it dead?" anxiety)
`synthesize_task` phases already know counts. Write `synthesis:progress` project setting at phase boundaries: `{"phase": "grouping|analyzing|ordering", "done": n, "total": m, "ts": ...}` — `process_dirty_concepts` should update it per concept (it loops; pass a small callback or write inside the loop via ctx.db). Status endpoint returns it; CurriculumView building card and CorpusView banner show "Analyzing topics… 12 of 29". Clear with the running marker.
Tests: endpoint shape; per-concept progress writes (fake ctx).

## Part B — Architecture conformance audit (report, no code)

Read `docs/superpowers/specs/2026-07-02-multi-source-learning-system-design.md` and produce `docs/superpowers/audits/2026-07-04-architecture-conformance.md` — a table: spec section → implemented where → status (on-track / deviated / missing) → evidence (file:line). Cover at minimum:
1. Adapters ×4 + locators; whisper fallback path (note: never live-tested).
2. Chunking token bounds + locator preservation.
3. Trust gate (quote fuzzy + embed sanity, tunables audited).
4. LangGraph extraction graph (extract→validate→retry→escalate).
5. Synthesis: spec said "checkpointed LangGraph synthesis graph" — implementation is plain functions + Celery. Document as an accepted deviation (plan 5) with rationale, or flag if unjustified.
6. Neo4j property graph + vector indexes + GraphML/JSON export portability rule ("every export run also dumps the graph").
7. mem0 learner-memory integrity rule (personalization-only; provenance eval gate exists?).
8. Celery queues: spec said 3 queues (ingest/judge/transcribe, whisper exclusive) — current: 2 (ingest/judge), transcription runs inside chunk_source_task on ingest. Flag memory-ceiling implication (18 GB M3) as deviation to either accept or fix later.
9. Profiles/hot-swap; model IDs config-only. Note the qwen→deepseek extraction switch (user-approved deviation).
10. Evals: golden sets (seeded? reviewed? — check evals/golden/*.jsonl row counts and review_status values), release gates vs spec numbers, self-evolution gating + audit trail + rollback.
11. Exports: markdown determinism, anki stable ids, graph dump.
12. Spec's 6-step verification: which steps have actually been run live; list what remains for "definition of done" (live release_check.sh, golden seeding + human review, judged eval run, worker kill/resume drill).

Honest status only — no grade inflation. Anything missing/deviated lands in a "Remaining for production-ready" checklist at the end.

## Part C — Token/latency diet quick wins (only if evidence supports, one commit)
- `concept_match`/`conflict_scan`/`concept_name` calls: check prompt sizes in synthesis.py — if full claim quotes are embedded where text alone suffices, trim (measure prompt tokens from model_calls input_tokens first; report before/after).
- Extraction `extract.max_tokens` 8192 was sized for reasoning-token overhead; with reasoning off, output-only budget of 4096 suffices — lower the tunable DEFAULT (existing DBs keep their stored value; note this).
- Do NOT touch trust gate, eval gates, or prompt semantics.

## Conventions
Cypher in store.py; tunables via registry; offline tests (graph tests only via `make graph-test` — they are destructive and env-gated); suites green per commit; conventional commits ending with the standard trailer.

## Verification
1. `make check` + `make ui-test` green; `make graph-test` green.
2. Status endpoint: stale marker self-heals; progress field present during a run.
3. Audit doc exists, every row has evidence.
4. Report: expected end-to-end time for one 107-chunk video with reasoning off (estimate from measured per-call latencies).
