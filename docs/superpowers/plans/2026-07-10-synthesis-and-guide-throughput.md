# Synthesis + Guide Throughput Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove every artificial one-at-a-time model-call loop in the synthesis→guide path: parallelize claim clustering, widen synthesis concurrency, batch a Neo4j write, batch concept-match calls (eval-gated), and pre-warm all concept guides in a background parallel pass.

**Architecture:** `synthesize_task` runs three phases (cluster → process_dirty → curriculum). Clustering is serialized (1 model call/claim) and process_dirty is parallel but at concurrency 8. We parallelize clustering (calls parallel via thread pool, assignment serial to preserve behavior), raise `synth.concurrency` to 24, batch the curriculum order write, optionally batch multiple anchors per concept-match call (behind a tunable + clustering-F1 eval gate), and add a new `warm_guides_task` that generates all guides in parallel after synthesis finishes so every `teach` open is a cache hit.

**Tech Stack:** Python 3.12 / Celery / Neo4j (native vector index) / SQLite OpsDB / OpenRouter deepseek-v4-flash / `concurrent.futures.ThreadPoolExecutor`.

## Global Constraints

- **Behavior-preserving except D.** Tasks A/B/C/E must produce identical outputs (same claim→concept assignments, same conflicts, same curriculum order, same guide content) — only timing changes. D (batched concept-match) is the ONLY change that can alter clustering results and carries a hard eval gate.
- **D eval gate:** with batched matching enabled, the clustering golden-set metric must still meet **clustering F1 ≥ 0.80** (project release gate). If it regresses, set `synth.match_batch = 1` (disables batching; A's parallelism remains). This gate is part of D's definition of done.
- **Parallelism correctness:** `GraphStore` is thread-safe (opens a Neo4j session per call); `OpsDB` is thread-safe (internal lock, `check_same_thread=False`). Do not add locks around graph/db calls except a single progress counter. The `concept_match` decision is order-independent, so it is safe to precompute in parallel; claim→concept ASSIGNMENT has ordering effects and MUST stay serial.
- **Concurrency is a tunable, not a guarantee.** Real speedup is capped by OpenRouter's per-account rate limit. 429s are already handled as `ProviderTransientError` (retry w/ backoff). Keep `synth.concurrency` DB-tunable so it can be dialed to the empirical ceiling.
- **Every Celery task must route to a consumed queue** (`prepare`/`extract`/`judge`) — guarded by `test_all_tasks_routed_to_consumed_queues`.
- TDD: failing test first. Frequent commits. `make check` green before each commit. Current baseline: **390 passed, 24 skipped**.

**Values (verbatim):** `synth.concurrency` 8→**24**; `synth.match_batch` default **8** (set in Task 4; introduced disabled-equivalent earlier); guide warm queue = **judge**; guides progress key = **`guides:progress`** with shape `{"phase","done","total","ts"}`.

---

## File Structure

- `mslearn/opsdb.py` — tunables (`synth.concurrency`→24, add `synth.match_batch`); guides single-flight guard (`try_mark_guides_queued`/`clear_guides_queued`); registry + drift-guard test.
- `mslearn/graph/store.py` — `set_concept_orders(orders, project_id)` (one `UNWIND` write).
- `mslearn/pipeline/synthesis.py` — parallelize `cluster_new_claims`; use `set_concept_orders` in `build_curriculum`.
- `mslearn/prompts.py` — batched `concept_match_batch` prompt (D).
- `mslearn/pipeline/synthesis.py` (D) — batched matcher path behind `synth.match_batch`.
- `mslearn/worker/tasks.py` — `warm_guides_task`; enqueue at end of `synthesize_task`.
- `mslearn/worker/app.py` — route `warm_guides_task` → `judge`.
- Tests: `tests/test_clustering.py` (extend: parallel same-assignment), `tests/test_synthesis_task.py` (guide-warm enqueue), new `tests/test_set_concept_orders.py`, `tests/test_warm_guides.py`, `tests/test_concept_match_batch.py`, `tests/test_tunables.py` (registry).

---

## Task 1: Tunables + guides single-flight guard

**Files:**
- Modify: `mslearn/opsdb.py` (`TUNABLE_DEFAULTS` ~line 135; add guard methods near `try_mark_synthesis_queued` ~line 306)
- Modify: `tests/test_tunables.py` (exact-registry drift guard)
- Test: `tests/test_opsdb_tunables.py` (extend), `tests/test_guides_queued.py` (create)

**Interfaces:**
- Produces: `db.get_tunable("synth.concurrency") == 24`, `db.get_tunable("synth.match_batch") == 8`, `db.try_mark_guides_queued(project_id) -> bool`, `db.clear_guides_queued(project_id) -> None`.

- [ ] **Step 1: Failing tests**

```python
# tests/test_opsdb_tunables.py (add)
def test_synth_concurrency_and_match_batch(tmp_path):
    from mslearn.opsdb import OpsDB
    db = OpsDB(tmp_path / "x.db")
    assert db.get_tunable("synth.concurrency") == 24.0
    assert db.get_tunable("synth.match_batch") == 8.0

# tests/test_guides_queued.py (new)
def test_guides_queued_single_flight(tmp_path):
    from mslearn.opsdb import OpsDB
    db = OpsDB(tmp_path / "x.db")
    assert db.try_mark_guides_queued("p") is True     # first claims it
    assert db.try_mark_guides_queued("p") is False    # second deduped
    db.clear_guides_queued("p")
    assert db.try_mark_guides_queued("p") is True      # freed after clear
```

- [ ] **Step 2: Run — FAIL** (`.venv/bin/pytest tests/test_opsdb_tunables.py tests/test_guides_queued.py -v`).

- [ ] **Step 3: Implement.** In `TUNABLE_DEFAULTS`: change `synth.concurrency` value to `24.0` (it was added as 8.0 in the prior branch — if absent, add it) and add `"synth.match_batch": 8.0,` with a comment ("anchors per batched concept_match call; 1 disables batching"). Add guides guards mirroring `try_mark_synthesis_queued`/`clear_synthesis_queued` but keyed on `"guides:queued"` with a TTL constant `GUIDES_QUEUED_TTL_S = 3600` (no running marker needed — the queued marker + `generate_guide` idempotency suffice). Update the exact-registry dict in `tests/test_tunables.py::test_defaults_registry_exact` to include both keys/values.

- [ ] **Step 4: Run — PASS** (both new tests + `tests/test_tunables.py`).

- [ ] **Step 5: Commit** `feat(opsdb): synth.concurrency=24, synth.match_batch, guides single-flight guard`.

---

## Task 2: Batch curriculum order writes (C)

**Files:**
- Modify: `mslearn/graph/store.py` (add `set_concept_orders`, near `set_concept_meta` ~line 528; mirror the existing `UNWIND $rows` pattern at line ~156)
- Modify: `mslearn/pipeline/synthesis.py` (`build_curriculum`, the `for idx, concept_id in enumerate(ordered): graph.set_concept_meta(...)` loop ~line 386)
- Test: `tests/test_set_concept_orders.py` (create; Cypher-shape unit test without live Neo4j)

**Interfaces:**
- Produces: `GraphStore.set_concept_orders(orders: list[tuple[str, int]], *, project_id="default") -> None` — writes all order indexes in one `UNWIND`.

- [ ] **Step 1: Failing test** (shape test bypassing the driver, like existing store unit tests)

```python
# tests/test_set_concept_orders.py
from mslearn.graph.store import GraphStore

def test_set_concept_orders_single_unwind(monkeypatch):
    store = GraphStore.__new__(GraphStore)
    calls = []
    monkeypatch.setattr(store, "run_write", lambda q, **kw: calls.append((q, kw)))
    store.set_concept_orders([("a", 0), ("b", 1)], project_id="p")
    assert len(calls) == 1                      # ONE write, not N
    q, kw = calls[0]
    assert "UNWIND" in q
    assert kw["rows"] == [{"concept_id": "a", "order_index": 0},
                          {"concept_id": "b", "order_index": 1}]

def test_set_concept_orders_empty_noop(monkeypatch):
    store = GraphStore.__new__(GraphStore)
    calls = []
    monkeypatch.setattr(store, "run_write", lambda q, **kw: calls.append(q))
    store.set_concept_orders([], project_id="p")
    assert calls == []
```

- [ ] **Step 2: Run — FAIL** (method missing).

- [ ] **Step 3: Implement**

```python
# mslearn/graph/store.py
def set_concept_orders(self, orders, *, project_id: str = "default") -> None:
    if not orders:
        return
    rows = [{"concept_id": cid, "order_index": int(idx)} for cid, idx in orders]
    self.run_write(
        "UNWIND $rows AS row "
        "MATCH (k:Concept {concept_id: row.concept_id, project_id: $project_id}) "
        "SET k.order_index = row.order_index",
        rows=rows, project_id=project_id,
    )
```

In `build_curriculum`, replace the per-concept loop:
```python
    graph.set_concept_orders([(cid, idx) for idx, cid in enumerate(ordered)], project_id=project_id)
```

- [ ] **Step 4: Run — PASS**; also `.venv/bin/pytest tests/test_synthesis_task.py -v` (curriculum still ordered).

- [ ] **Step 5: Commit** `perf(graph): batch concept order writes into one UNWIND`.

---

## Task 3: Parallelize `cluster_new_claims` (A) — calls parallel, assignment serial

**Files:**
- Modify: `mslearn/pipeline/synthesis.py` (`cluster_new_claims`, ~lines 23-110)
- Test: `tests/test_clustering.py` (extend)

**Interfaces:**
- Consumes: `db.get_tunable("synth.concurrency")`.
- Produces: same `cluster_new_claims(ctx, project_id) -> set[str]` (dirty concept ids), identical assignments to today.

Design (MUST preserve behavior):
- **Phase A (parallel, read-only, no assignment):** snapshot `anchors = list(graph.unassigned_trusted_claims(project_id))`. For each anchor, in a `ThreadPoolExecutor(int(db.get_tunable("synth.concurrency")))` worker: compute candidates exactly as today (`vector_search_claims` + the same filter on score/`similarity_floor`/trust), and if candidates exist call the EXISTING per-anchor `concept_match` (reuse `concept_match_claim_ids(ctx, anchor, candidates)` — do NOT change the prompt in this task). Collect `results: dict[anchor_id, tuple[candidates, matches]]`. Anchors with no candidates → recorded with empty matches (no model call).
- **Phase B (serial, deterministic, no model calls):** iterate `anchors` in their original order; for each, skip if `graph.concept_id_of_claim(anchor_id)` is now set (unchanged guard); otherwise apply the EXISTING mint/reuse/assign logic using that anchor's precomputed `(candidates, matches)` and live `concept_id_of_claim` reads. Preserve the "dropped match: claim X not in candidate set" per-item warnings (emit them in Phase A where the match set is validated) and the `cluster_new_claims: dropped N` aggregate.

Key correctness note for the implementer: the ONLY thing moved into parallel is the read-only candidate computation + `concept_match` model call, whose result depends only on `(anchor, candidates)` and is independent of assignment order. All `assign_claim`/`_mint_or_reuse_concept`/`mark_concept_dirty` calls stay in serial Phase B, run in the same anchor order as today, reading live assignment state — so the resulting assignments are identical.

- [ ] **Step 1: Failing test** — a fake context with a router whose `complete` sleeps ~40ms and tracks max in-flight; ≥4 unassigned claims with candidates; set `synth.concurrency=4`. Assert (a) the resulting claim→concept assignments EQUAL those from the current serial implementation (compute an expected mapping, or assert the specific expected clusters for a hand-built fixture), and (b) `max_in_flight >= 2`. Reuse `tests/test_clustering.py` fixtures/fakes.

```python
# tests/test_clustering.py (add)
def test_cluster_new_claims_parallel_same_assignments(...):
    # hand-built: claims c1,c2 same concept; c3 alone. router returns matches
    # accordingly. Run cluster_new_claims; assert c1&c2 share a concept id,
    # c3 separate — identical to serial expectation — and max_in_flight >= 2.
    ...
```

- [ ] **Step 2: Run — FAIL** (serial → `max_in_flight == 1`).

- [ ] **Step 3: Implement** the Phase A / Phase B split as above.

- [ ] **Step 4: Run — PASS**; then `.venv/bin/pytest tests/test_clustering.py tests/test_synthesis_task.py -v` (all clustering + synthesis behavior green).

- [ ] **Step 5: Commit** `perf(synthesis): parallelize cluster_new_claims (matches parallel, assignment serial)`.

---

## Task 4: Batched concept-match (D) — behind `synth.match_batch`, eval-gated

**Files:**
- Modify: `mslearn/prompts.py` (add `concept_match_batch` prompt)
- Modify: `mslearn/pipeline/synthesis.py` (Phase A: when `synth.match_batch > 1`, group anchors into batches and issue one batched call per batch)
- Test: `tests/test_concept_match_batch.py` (create), extend `tests/test_clustering.py`

**Interfaces:**
- Consumes: `db.get_tunable("synth.match_batch")`.
- Produces: a batched matcher that, given up to `match_batch` anchors each with their own candidates, returns `dict[anchor_id, list[matched_id]]` via ONE model call; each anchor's matches validated against ITS OWN candidate set.

Design:
- New prompt `concept_match_batch`: presents each anchor with a delimited, numbered candidate list, and requires output `{"results": [{"anchor": "<id>", "matches": ["<candidate_id>", ...]}, ...]}`. Rules identical to `concept_match` (only candidate ids from THAT anchor's list; `[]` if none).
- JSON schema keys results by anchor. Parse: for each anchor, keep only ids present in that anchor's candidate set; emit the same "dropped match" warning for strays.
- In Phase A: if `match_batch <= 1`, keep per-anchor calls (Task 3 path). If `> 1`, chunk anchors (that have candidates) into groups of `match_batch`; submit each GROUP to the thread pool as one batched call. Anchors without candidates skip the model entirely (as in Task 3).
- Behdavior control: `match_batch=1` is exactly Task 3 (per-anchor). Default is 8 (this task sets it via Task 1's tunable already at 8).

- [ ] **Step 1: Failing test** — `test_concept_match_batch.py`: a fake router returning a batched `results` payload for 2 anchors with different candidate sets; assert the parser returns the correct per-anchor matches and drops a stray id (with warning). Then in `tests/test_clustering.py` add `test_cluster_batched_same_clusters_as_unbatched`: run clustering with `match_batch=1` and with `match_batch=4` on the SAME fixture + a router that answers consistently; assert identical final clusters (proves batching doesn't change grouping for a consistent judge).

- [ ] **Step 2: Run — FAIL**.

- [ ] **Step 3: Implement** the batched prompt, schema, parser, and Phase A batching path.

- [ ] **Step 4: Run — PASS**; `make check`.

- [ ] **Step 5: EVAL GATE (required).** Run the clustering eval and record the F1. Find the runner: `grep -rn "cluster" mslearn/evals/metrics.py mslearn/evals/runner.py mslearn/evals/gates.py`. Run the clustering metric (e.g. `.venv/bin/pytest -m evals -k cluster` or the documented eval entrypoint) and confirm **F1 ≥ 0.80** with `match_batch=8`. Record the number in the commit body. If it is `< 0.80`, set the `synth.match_batch` default back to `1` in `opsdb.py` (batching off; Task 3 parallelism stays) and note the eval result in the commit — do NOT ship batching that fails the gate.

- [ ] **Step 6: Commit** `perf(synthesis): batched concept_match behind synth.match_batch (F1=<value>)`.

---

## Task 5: Background guide warming (E)

**Files:**
- Modify: `mslearn/worker/tasks.py` (add `warm_guides_task`; enqueue at end of `synthesize_task` after `build_curriculum`)
- Modify: `mslearn/worker/app.py` (route `warm_guides_task` → `judge`)
- Test: `tests/test_warm_guides.py` (create), extend `tests/test_synthesis_task.py`

**Interfaces:**
- Consumes: `generate_guide(ctx, concept_id, force=False, project_id)` (idempotent — returns cached for non-dirty), `db.try_mark_guides_queued`/`clear_guides_queued`, `db.get_tunable("synth.concurrency")`.
- Produces: Celery task `warm_guides_task(project_id: str = "default")` that generates all concept guides in parallel; `synthesize_task` enqueues it once at the end.

Design:
- `warm_guides_task`: `clear_guides_queued(project_id)` at start; list all concepts (`graph.all_concepts`); run `generate_guide(ctx, cid, force=False, project_id)` for each over `ThreadPoolExecutor(int(db.get_tunable("synth.concurrency")))`. Wrap EACH concept's call in try/except: log and continue on failure (best-effort; one bad guide must not fail the pass). Write `guides:progress` (`{"phase":"warming","done","total","ts"}`) under a lock-guarded counter, same pattern as `process_dirty_concepts`.
- In `synthesize_task`, after `ordered = build_curriculum(...)` succeeds: `if ctx.db.try_mark_guides_queued(project_id): warm_guides_task.delay(project_id)`. Place it so a synthesis failure does NOT enqueue warming. `synthesize_task` returns normally right after (warming runs as a separate task — synthesis is already marked done).
- Route in `app.py` `task_routes`: `"mslearn.worker.tasks.warm_guides_task": {"queue": "judge"}`.
- Thread-safety: verify `SqliteMemory` used by `generate_guide` → `_format_memory_hints` is safe under concurrent reads; it already degrades to "(none)" on exception, so wrap remains, but confirm no shared-cursor corruption. If `SqliteMemory` shares one connection without a lock, add a lock or use `check_same_thread=False` consistent with `OpsDB` — but only if a real hazard exists (check `mslearn/memory/sqlite_memory.py` first).

- [ ] **Step 1: Failing tests** — `test_warm_guides.py`:
  - `test_warm_guides_generates_all`: fake ctx with 3 concepts, a `generate_guide` spy; assert all 3 get generated and `guides:progress` reaches done==total.
  - `test_warm_guides_best_effort`: make one concept's `generate_guide` raise; assert the other two still complete and the task does not raise.
  - `test_warm_guides_parallel`: generate_guide sleeps + tracks max in-flight; assert `>= 2` at concurrency 4.
  - In `test_synthesis_task.py`: assert `synthesize_task` enqueues `warm_guides_task` exactly once on success (patch `warm_guides_task.delay`, assert called once with the project id), and does NOT enqueue on a synthesis exception.

- [ ] **Step 2: Run — FAIL**.

- [ ] **Step 3: Implement** `warm_guides_task`, the enqueue, and the route.

- [ ] **Step 4: Run — PASS**; `make check` (the routing invariant test must still pass with the new task routed to `judge`).

- [ ] **Step 5: Commit** `feat(guide): background parallel warm_guides_task after synthesis`.

---

## Task 6: Docs + verification

**Files:** Modify `README.md`.

- [ ] **Step 1:** Document in the synthesis/worker section: synthesis now parallelizes clustering + concept processing (`synth.concurrency`, default 24; dial to your OpenRouter rate limit), and after synthesis a background `warm_guides_task` pre-generates all concept guides so opening a topic is instant. Note `synth.match_batch` (default 8; set 1 to disable anchor batching).
- [ ] **Step 2:** `make check` — all green (baseline 390 + new tests).
- [ ] **Step 3:** Manual smoke (user-run): re-upload the js cheatsheet; confirm synthesis wall-clock drops and `guides:progress` climbs after synthesis; opening concepts is instant.
- [ ] **Step 4: Commit** `docs: synthesis parallelism + background guide warming`.

---

## Self-Review

- **Spec coverage:** A=Task 3, B=Task 1 (concurrency 24), C=Task 2, D=Task 4 (+eval gate), E=Task 5. Tunables/guards=Task 1. Docs=Task 6. All spec sections mapped.
- **Behavior preservation:** Tasks 2/3/5 assert identical outputs; Task 4 is the only behavior-affecting change and has the F1≥0.80 gate with a `match_batch=1` kill-switch.
- **Type consistency:** `set_concept_orders(orders: list[tuple[str,int]])`, `try_mark_guides_queued(project_id)->bool`, `warm_guides_task(project_id)`, `guides:progress` shape — used consistently across tasks.
- **Concurrency:** all parallel sections use `synth.concurrency`; assignment/progress correctly kept serial/lock-guarded.
- **Placeholder scan:** eval entrypoint in Task 4 Step 5 is discovered via a concrete grep (the repo's eval runner name isn't hard-coded in this plan because it must be confirmed live) — the step names the exact grep and the exact gate value (0.80), so it is actionable, not vague.
