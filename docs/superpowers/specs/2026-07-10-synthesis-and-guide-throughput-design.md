# Synthesis + Guide Throughput — Eliminate All Artificial Serialization

**Date:** 2026-07-10
**Status:** design for approval

## Problem

A single ~44-chunk image upload produced ~107 concepts and synthesis took ~8 minutes even after the first throughput pass. Root cause, traced end-to-end through `synthesize_task`:

1. **`cluster_new_claims` is fully serial** — one `concept_match` model call per unassigned claim in a plain `for` loop (`synthesis.py:35`). ~107 claims ≈ ~3 min. This was missed in the prior pass (only `process_dirty_concepts` was parallelized).
2. **`process_dirty_concepts` is parallel but under-fed** — `synth.concurrency=8` is far too low for remote OpenRouter I/O (each deepseek-flash call ~8–20s). ~4.5 min.
3. **`build_curriculum` writes order indexes serially** — N one-row Neo4j `set_concept_meta` writes (`synthesis.py:386`). Minor (~1–2s) but real.
4. **First open of every concept blocks on an LLM call** — `teach` → `generate_guide` runs one ~8192-token `interactive` generation per concept on first open (`guide_gen.py:46`), cached afterward. Every fresh topic = a multi-second wait.

Verified NOT bottlenecks: httpx pool (100 max connections), extraction (now ~40s/44 chunks), prepare's batched embed, model-call logging. Inherent/unavoidable: one vision call per image, Whisper transcription, and total model-call count scaling with corpus size.

**Goal:** remove every *artificial* serialization so total time = `model_work / concurrency`, bounded only by OpenRouter rate limits and per-call latency — never by a one-at-a-time loop. Behavior/output unchanged (same concepts, assignments, curriculum, guides).

## Design

Five changes (A–E). A/B/C/E are behavior-preserving parallelization; D changes clustering call structure and carries an eval gate.

### A. Parallelize `cluster_new_claims` (calls parallel / assignment serial)

The per-claim `concept_match` LLM call decides which candidate claims are semantically the same concept as the anchor — this decision depends only on `(anchor, its vector candidates)`, both computable up front, and is independent of assignment order. Only the *assignment* (mint/reuse/assign a concept id) has ordering effects.

Split into two phases:
- **Phase A (parallel, read-only):** snapshot `unassigned_trusted_claims`; for each anchor compute candidates (`vector_search_claims` + filter) and run the `concept_match` call. Produce `matches_by_anchor: dict[anchor_id, list[matched_claim_id]]`. Runs on a `ThreadPoolExecutor(synth.concurrency)`. Anchors whose candidate set is empty need no call (recorded as no-match directly).
- **Phase B (serial, fast, no model):** iterate anchors in a deterministic order; apply the existing mint/reuse/assign logic using the precomputed matches + live `concept_id_of_claim` state. Identical assignment result to today.

Preserve the existing "dropped match: claim X not in candidate set" warnings and the `cluster_new_claims: dropped N` aggregate. Return value (`set[str]` of dirty concept ids) unchanged.

### B. Raise `synth.concurrency` 8 → 24

Remote I/O; widening is near-free. Used by both Phase A of clustering and `process_dirty_concepts`. Single tunable, single source of truth.

### C. Batch `build_curriculum` order writes

Replace the `for idx, concept_id: set_concept_meta(order_index=idx)` loop with one `GraphStore.set_concept_orders(pairs, project_id)` that writes all order indexes in a single `UNWIND` Cypher. One round-trip instead of N.

### D. Batch multiple anchors per `concept_match` call (reduce call *count*)

Where A parallelizes calls, D reduces how many there are. Group up to `synth.match_batch` (default 8) anchors — each with its own candidate list — into ONE `concept_match` call that returns matches per anchor: `{anchor_id: [matched_ids]}`. Batches run in parallel (Phase A). Cuts ~107 calls to ~14.

**Constraints:**
- Each anchor's returned matches are validated against THAT anchor's own candidate set (same drop-warning on stray ids).
- The prompt must clearly delimit per-anchor candidate lists and require per-anchor keyed output; the JSON schema keys results by anchor id.
- **Eval gate (required):** the clustering golden set must still meet the release gate (clustering F1 ≥ 0.80 per the project spec) with batching on. If it regresses, reduce `synth.match_batch` or revert D — A+B already deliver most of the win. This gate is part of D's definition of done.

### E. Background parallel guide warming

New Celery task `warm_guides_task(project_id)`:
- Enqueued at the end of `synthesize_task`, AFTER `build_curriculum`, behind a single-flight guard (`try_mark_guides_queued` / `clear_guides_queued`, mirroring the synthesis-queued guard). `synthesize_task` then returns — synthesis is marked done and concepts are browsable immediately.
- Iterates all concepts for the project and runs `generate_guide(ctx, concept_id, force=False)` over a `ThreadPoolExecutor(synth.concurrency)`.
- **Idempotent:** `generate_guide` already returns the cached guide for non-dirty concepts, so re-runs only generate missing/dirty guides.
- **Best-effort:** a failure generating one concept's guide is logged and skipped — it must never fail the whole pass or the synthesis flow.
- Writes `guides:progress` (`{phase, done, total, ts}`, same shape as `synthesis:progress`) so the UI can show "building guides n/m".
- Routed to the `judge` queue (the task fans out internally via the thread pool, so one queue slot suffices; a dedicated `guides` queue is a possible future refinement, not required now).

Result: after synthesis, guides warm in the background in parallel; every `teach` open is instant (cache hit).

**Thread-safety note (verify during implementation):** `generate_guide` calls `GraphStore` (thread-safe, per-call sessions), `router.complete` (thread-safe), `set_concept_teaching`/`mark_concept_dirty` (per-concept, isolated), and `_format_memory_hints` → `memory.search`. Confirm `SqliteMemory` is safe under concurrent reads; note that `_format_memory_hints` already wraps the search in try/except and degrades to "(none)" on any failure, so a memory hiccup cannot corrupt a guide — but the memory DB itself must not be corrupted by concurrent access.

## Components / files

- `mslearn/opsdb.py` — `synth.concurrency` 8→24; add `synth.match_batch` (8); add guides single-flight helpers (`try_mark_guides_queued`, `clear_guides_queued`) mirroring synthesis; register both in `TUNABLE_DEFAULTS` (and update the exact-registry drift-guard test).
- `mslearn/pipeline/synthesis.py` — parallelize `cluster_new_claims` (A), batched concept_match (D); no change to `process_dirty_concepts` beyond reading the raised tunable (B).
- `mslearn/prompts.py` — a batched `concept_match` prompt variant (D) keyed per anchor.
- `mslearn/graph/store.py` — `set_concept_orders(pairs, project_id)` UNWIND write (C).
- `mslearn/worker/tasks.py` — `warm_guides_task`; enqueue after `build_curriculum` in `synthesize_task` (E).
- `mslearn/worker/app.py` — route `warm_guides_task` to a consumed queue (`judge`); covered by `test_all_tasks_routed_to_consumed_queues`.
- Frontend (optional, small): surface `guides:progress` if a status endpoint exposes it — can be a follow-up; not required for the speedup.
- Tests: parallel clustering (calls parallel, same assignments), batched-match parse/validation, `set_concept_orders`, `warm_guides_task` (idempotent, best-effort, parallel), tunable registry.

## Non-goals

- Path B (query-time RAG / progressive availability) — remains a separate roadmap item.
- fastembed in-process embedder — remains a roadmap item.
- Reducing the *inherent* per-corpus model-call count below "one decision per claim/concept" (batching in D reduces calls but the work is inherent).

## Expected outcome

Synthesis ~8 min → ~1–2 min (A+B+C; D pushes clustering toward seconds). Teach opens instant after synthesis (E). No remaining one-at-a-time loop over model calls anywhere in the ingest→synthesis→guide path.

## Verification

- Unit/integration: all new tests green; `make check` stays green.
- Eval gate (D): clustering F1 ≥ 0.80 on the golden set with batching on.
- Manual: re-upload the js cheatsheet; synthesis completes in ~1–2 min; opening concepts is instant; `guides:progress` climbs to total in the background.
