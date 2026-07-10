# Fast Local Embedding + Synthesis Throughput Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make ingestion and synthesis fast **while keeping all embeddings local** (no online embedding models) — by eliminating a redundant double-embed, uncorking local Ollama concurrency, and parallelizing the serial synthesis pass. No behavior/output change (Path A).

**Architecture:** Embeddings are the highest-volume calls (every chunk + every claim) and stay on local Ollama `nomic-embed-text` (768-dim). Today they are slow for three fixable reasons: (1) every claim text is embedded **twice** (trust gate + storage), (2) local Ollama serves only `OLLAMA_NUM_PARALLEL` requests (default 1) so the 8-wide `extract` pool collapses to a ~1–2-wide gate, and (3) synthesis is a serial loop of ~2 blocking model calls per concept. This plan removes the redundant embed, raises local concurrency, and thread-pools synthesis. Embedding stays local; **no online model, no new vendor, no vector-dimension migration.**

**Tech Stack:** Python 3.12 / FastAPI / Celery / Neo4j (native vector index, 768-dim, unchanged) / SQLite OpsDB / local Ollama `nomic-embed-text`. Optional in-process `fastembed` (ONNX) as a server-free local alternative.

## Global Constraints

- **All embeddings stay local. No online embedding models.** `nomic-embed-text` remains the embedding model on every profile. This plan does NOT add Voyage/OpenAI/Gemini or any remote embedder.
- **No dimension migration.** Vector width stays 768 (nomic-embed). Neo4j vector indexes are untouched. (Only the optional `fastembed` upgrade must keep the same 768-dim model, `nomic-embed-text-v1.5`, so indexes still don't change.)
- **Path A only.** Same output as today (same claims, concepts, guide) — perf only. Path B (progressive availability / raw-chunk RAG) is OUT, recorded as a README TODO (Task 4.1).
- **Trust gate untouched.** Every claim still carries a verbatim quote checked by rapidfuzz + embedding cosine. Only *when/how many times* we embed changes, never the gate logic.
- **Provider abstraction is the only embedding integration point.** All embeddings flow through `ModelRouter.embed(texts) -> provider.embed(model, texts)` (`mslearn/providers/router.py:116`).
- TDD: failing test first for every code change. Frequent commits. `make check` green before each commit. Baseline: 387 passed, 24 skipped.

**Decision baked in (correct at approval if wrong):** the default embed-speed mechanism is **tune local Ollama** (Task 2.x, zero new code deps). The **in-process `fastembed`** path (Phase 3) is OPTIONAL — include it only if the user wants server-free local embedding or Ollama tuning proves insufficient.

---

## File Structure

- `mslearn/pipeline/extraction_graph.py` + `mslearn/worker/tasks.py` — thread trust-gate claim vectors through so accepted claims are not re-embedded (kills the double-embed).
- `mslearn/pipeline/synthesis.py` — thread-pool `process_dirty_concepts`.
- `mslearn/opsdb.py` — add `synth.concurrency` tunable.
- `mslearn/settings.py` — update the extract-concurrency comment; raise default.
- `Makefile` / `scripts/dev_up.sh` — raise `judge` + `extract` worker concurrency; document `OLLAMA_NUM_PARALLEL` / `OLLAMA_KEEP_ALIVE`.
- `mslearn/server/routers` (health) — optional startup warning when extract concurrency likely exceeds Ollama parallelism.
- `README.md` / `.env.example` — document local-Ollama tuning + Path B TODO.
- (Optional Phase 3) `mslearn/providers/fastembed_local.py` — in-process ONNX embedder; register in router; `pyproject.toml` dep.
- Tests: `tests/test_extract_no_double_embed.py`, `tests/test_synthesis_parallel.py`, `tests/test_opsdb_tunables.py` (extend), and (optional) `tests/test_fastembed_provider.py`.

---

## Phase 1 — Eliminate the redundant double-embed (biggest local win, pure code)

Each chunk embeds claim texts **twice**: once in the trust gate (`extraction_graph.py:105`, embeds `texts + quotes`) and again at storage (`tasks.py:295`, embeds `[d.text for d in accepted]`). Accepted claims are a subset of the drafts already embedded. Reuse those vectors → halve local embed calls with zero infra change.

### Task 1.1: Thread trust-gate claim vectors through to storage

**Files:**
- Modify: `mslearn/pipeline/extraction_graph.py` (validate node already computes `embeddings = router.embed(texts + quotes)` at line 105 — expose the claim-text half keyed by text)
- Modify: `mslearn/worker/tasks.py:~285-300` (use passed-through vectors for accepted claims; only embed the normally-empty remainder)
- Test: `tests/test_extract_no_double_embed.py` (create)

**Interfaces:**
- Consumes: validate node's `embeddings` — the first `len(texts)` vectors are the claim-text embeddings, in draft order.
- Produces: extraction result carries `claim_embeddings: dict[str, list[float]]` (claim text → vector). `extract_chunk_task` stores accepted claims from that map; calls `router.embed` again only for accepted texts missing from it (defensive, normally none).

- [ ] **Step 1: Write the failing test** — a fake router that records every text passed to `embed()`; build a chunk with 2 draft claims whose quotes match the chunk (both pass the gate); run `extract_chunk_task`; assert each accepted claim text is embedded **exactly once** across the whole task.

```python
# tests/test_extract_no_double_embed.py
# Model on tests/test_worker_tasks.py: reuse its fake PipelineContext + a
# router whose embed() appends texts to a list. After extract_chunk_task runs,
# assert accepted claim texts appear once in the embed log, not twice.
def test_accepted_claims_not_reembedded(...):
    ...
    assert embed_log.count(accepted_text) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_extract_no_double_embed.py -v`
Expected: FAIL (accepted text embedded twice).

- [ ] **Step 3: Implement passthrough** — in the validate node build `claim_embeddings = {t: v for t, v in zip(texts, embeddings[:len(texts)])}` and attach to graph state / returned result. In `tasks.py`, replace storage-time `router.embed([d.text for d in accepted])` with a lookup into that map; embed only accepted texts absent from it.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_extract_no_double_embed.py tests/test_worker_tasks.py -v`
Expected: PASS (new test green; existing extraction tests still green).

- [ ] **Step 5: Commit**

```bash
git add mslearn/pipeline/extraction_graph.py mslearn/worker/tasks.py tests/test_extract_no_double_embed.py
git commit -m "perf(extract): reuse trust-gate vectors, drop redundant claim re-embed"
```

---

## Phase 2 — Uncork local Ollama concurrency + parallelize synthesis

### Task 2.1: `synth.concurrency` tunable

**Files:**
- Modify: `mslearn/opsdb.py` (seed tunable `synth.concurrency`, default 8)
- Test: `tests/test_opsdb_tunables.py` (extend, or create)

- [ ] **Step 1: Write failing test** asserting `db.get_tunable("synth.concurrency") == 8` on a fresh OpsDB.
- [ ] **Step 2: Run — FAIL** (unknown tunable).
- [ ] **Step 3:** Add `synth.concurrency` (8.0) to the tunable seed table in `opsdb.py` (mirror existing `extract.max_claims` / `guide.max_tokens` seeds).
- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Commit** `feat(opsdb): add synth.concurrency tunable (default 8)`.

### Task 2.2: Thread-pool the per-concept synthesis loop

**Files:**
- Modify: `mslearn/pipeline/synthesis.py:170-266` (`process_dirty_concepts`)
- Test: `tests/test_synthesis_parallel.py` (create)

**Interfaces:**
- Consumes: `db.get_tunable("synth.concurrency")`.
- Produces: same return (`len(dirty_ids)`), same graph writes, same progress updates — per-concept work runs on `ThreadPoolExecutor(max_workers=concurrency)`.

Behavior-preservation notes (must hold):
- Extract each concept's body (conflict-scan call + `add_conflict` writes, name call + `set_concept_meta`, `mark_concept_dirty(False)`) into `_process_one_concept(ctx, concept_id, ...)`. No shared mutable state except the graph (thread-safe: per-call Neo4j sessions).
- `drops` counter: accumulate via returned values or a `threading.Lock`, not a bare shared int. Preserve the existing per-drop warnings and the final `process_dirty_concepts: dropped N` aggregate.
- Progress (`synthesis:progress` `done/total`) must climb monotonically to `total`: Lock-guard a `done += 1` inside the worker and write progress from there.

- [ ] **Step 1: Write the failing test** — 4 dirty concepts; fake router sleeps ~50ms per `complete` and records max in-flight concurrency via a Lock+counter; set `synth.concurrency=4`; assert (a) all 4 concepts named + conflict-scanned (same writes as serial) and (b) observed max concurrency ≥ 2 (parallelism happened).

```python
# tests/test_synthesis_parallel.py
def test_process_dirty_runs_concurrently(...):
    process_dirty_concepts(ctx, project_id="default")
    assert fake_router.max_in_flight >= 2
    assert set(named_concepts) == set(dirty_ids)
```

- [ ] **Step 2: Run — FAIL** (serial → `max_in_flight == 1`).
- [ ] **Step 3:** Refactor `process_dirty_concepts` to submit `_process_one_concept` per dirty id to a `ThreadPoolExecutor` (width `int(db.get_tunable("synth.concurrency"))`), keeping progress + drop aggregation as above.
- [ ] **Step 4: Run — PASS**, then `.venv/bin/pytest tests/test_synthesis_task.py -v` (end-to-end synthesis task still green).
- [ ] **Step 5: Commit** `perf(synthesis): thread-pool per-concept processing`.

### Task 2.3: Raise worker concurrency + document Ollama tuning

**Files:**
- Modify: `Makefile:57,60` (`worker-extract`, `worker-judge`) and `scripts/dev_up.sh:81,85`
- Modify: `mslearn/settings.py:26-28` (extract-concurrency comment)
- Modify: `README.md` + `.env.example` (Ollama tuning block)

Facts for the implementer:
- `OLLAMA_NUM_PARALLEL` is an **Ollama server** env var, set when Ollama starts — the app cannot set it. Document that the user must launch Ollama with it (e.g. `OLLAMA_NUM_PARALLEL=8 ollama serve`, or `launchctl setenv OLLAMA_NUM_PARALLEL 8` for the macOS desktop app). Also recommend `OLLAMA_KEEP_ALIVE=30m` so `nomic-embed` isn't cold-reloaded between waves.
- The `judge` synthesis task is single-flight per project (`try_mark_synthesis_queued`, `tasks.py:189`), so raising judge worker concurrency is safe (win is cross-project; each task already parallelizes internally after Task 2.2).

- [ ] **Step 1:** Set `worker-judge` → `--concurrency=2` and `worker-extract` default `MSL_EXTRACT_CONCURRENCY` → 8 in both `Makefile` and `scripts/dev_up.sh`.
- [ ] **Step 2:** Rewrite the `settings.py` comment: extraction concurrency should match `OLLAMA_NUM_PARALLEL` (both ~8) now that the double-embed is gone; the local embed is the shared resource, so keep them balanced.
- [ ] **Step 3:** Add a README **"Local embedding throughput"** block: launch Ollama with `OLLAMA_NUM_PARALLEL=8` + `OLLAMA_KEEP_ALIVE=30m`; set `MSL_EXTRACT_CONCURRENCY=8`. Explain the two must stay balanced (extraction fires one local embed per chunk).
- [ ] **Step 4:** `make check` green (routing invariant test unaffected).
- [ ] **Step 5: Commit** `perf(worker): raise extract/judge concurrency; document Ollama parallelism`.

### Task 2.4 (optional): Startup warning when extraction will out-run Ollama

**Files:**
- Modify: the admin health path (`mslearn/server/routers/*` health; find via `grep -rn "admin/health\|worker_online" mslearn/server`)

- [ ] Add a best-effort check: if `MSL_EXTRACT_CONCURRENCY` > detected/assumed Ollama parallelism, surface a UI warning chip ("extraction concurrency exceeds Ollama parallelism — embeddings will bottleneck"). Degrade silently if Ollama can't be probed. Test with a fake settings/health stub. Commit `feat(health): warn when extract concurrency out-runs Ollama`.

---

## Phase 3 (OPTIONAL) — In-process local embedder (server-free, no Ollama round-trip)

Include only if the user wants local embedding without tuning an external Ollama server, or if Phase 2 tuning is insufficient. Uses `fastembed` (ONNX, CPU/Metal) with `nomic-embed-text-v1.5` → **same 768-dim, no migration**. Runs inside the worker process, batched, no HTTP.

### Task 3.1: FastembedProvider

**Files:**
- Create: `mslearn/providers/fastembed_local.py`
- Modify: `mslearn/providers/router.py` (register `"fastembed"` factory)
- Modify: `pyproject.toml` (add `fastembed>=0.3`)
- Modify: `profiles.yaml` (optional: point an `embedding` role at `{provider: fastembed, model: "nomic-ai/nomic-embed-text-v1.5"}`)
- Test: `tests/test_fastembed_provider.py`

**Interfaces:**
- Produces: `FastembedProvider()` with `name="fastembed"`, `embed(model, texts) -> list[list[float]]` (768-dim), `complete(...)` raises `NotImplementedError`. Model loaded lazily on first `embed`, cached on the instance.

- [ ] **Step 1: Write failing test** — assert `embed("nomic-ai/nomic-embed-text-v1.5", ["hello","world"])` returns 2 vectors of length 768. (Marks the test to skip if `fastembed` isn't installed, like other optional-dep tests.)
- [ ] **Step 2: Run — FAIL** (module missing).
- [ ] **Step 3: Implement** — wrap `fastembed.TextEmbedding(model_name=...)`, lazy-init, `list(model.embed(texts))` → `[v.tolist() for v in ...]`; `complete` raises.
- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Commit** `feat(providers): optional in-process fastembed embedder (768-dim, local)`.

### Task 3.2: Wire + document (only if adopting)
- [ ] Register the factory in `router.py` (mirror Task pattern), add `fastembed` dep to `pyproject.toml`, optionally switch the `openrouter` profile's `embedding` role to fastembed, `.venv/bin/pip install "fastembed>=0.3"`, `make check`. Commit `feat(profiles): use in-process fastembed for local embeddings`.

---

## Phase 4 — Docs / verification / Path B TODO

### Task 4.1: Record Path B as a README roadmap TODO

**Files:** Modify `README.md`.

- [ ] Add:

```markdown
## Roadmap — Path B: instant availability (not yet built)

A source is usable today once its claim graph is built. A future change could
make sources queryable from embeddings alone (defer reasoning to query time,
NotebookLM-style):
- Serve Q&A/search over raw chunk embeddings the moment ingestion embeds a
  source, before extraction finishes — answers marked "from source
  (unverified)" vs "from verified claims".
- Populate the interactive guide progressively as claims/concepts land, with a
  live "building study guide (n/m)" state.
- Lazy/on-demand extraction for supplements (extract only when first opened).
Deferred; Path A (fast local pipeline) does not change behavior.
```

- [ ] Commit `docs: record Path B (instant availability) as roadmap TODO`.

### Task 4.2: Full suite + smoke
- [ ] `make check` — all green (baseline 387 + new tests).
- [ ] Manual (user-run, needs live Ollama + Neo4j): launch Ollama with `OLLAMA_NUM_PARALLEL=8 OLLAMA_KEEP_ALIVE=30m`, `MSL_EXTRACT_CONCURRENCY=8`, `make run`, upload the js cheatsheet + a HEIC photo; confirm chunk progress completes in seconds (not 2–3 min) and synthesis far faster, guide identical.
- [ ] Commit any doc tweaks: `docs: local throughput runbook`.

---

## Self-Review notes

- **Spec coverage:** local-only fast embedding via (1) kill double-embed (Phase 1), (2) Ollama concurrency + balanced extract concurrency (Phase 2.3/2.4), optional (3) in-process fastembed (Phase 3); synthesis parallelized (Phase 2.1/2.2); Path B as TODO (Phase 4.1). No online embedder, no dimension migration — matches "local but quick, no online models."
- **Dropped from the earlier remote draft:** Voyage provider, `voyage_api_key`/`embedding_dim` settings, GraphStore dim wiring, `reset_vector_indexes` migration — all unnecessary now that embedding stays local at 768-dim.
- **Dropped incremental-synthesis item:** verified synthesis is already incremental (dirty-marking only touches affected concepts); the 117-dirty run was a one-time first build. The win is parallelization, not dirty-marking.
- **Behavior unchanged:** every task asserts same outputs; only perf/config changes.
- **Open decision for approval:** include optional Phase 3 (fastembed) now, or ship Phase 1–2 first and add fastembed only if Ollama tuning isn't enough.
