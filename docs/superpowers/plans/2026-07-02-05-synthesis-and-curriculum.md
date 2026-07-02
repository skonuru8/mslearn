# Plan 5/8: Synthesis & Curriculum — Implementation Plan

> **For the implementer (Cursor):** Work task-by-task in order, TDD (failing test → implement → green), commit per task with the given message. Repo root, `.venv/bin/pytest` / `.venv/bin/ruff check .`. If expected output differs from reality, STOP and note it in your summary rather than improvising. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Cross-source synthesis on the judge backend: cluster trusted claims into concepts (embedding-blocked candidates + judge verdicts), detect and classify intra-concept conflicts (4-way taxonomy, domain-profile steered), name concepts, build the spine-ordered curriculum with `DEPENDS_ON` topological sort — incrementally, via dirty marking. Plus the two failure-accounting fixes carried from the Plan-4 review.

**Architecture:** `mslearn/pipeline/synthesis.py` (clustering / conflicts / curriculum as plain functions over `PipelineContext`), `synthesize_task` on the **judge** queue, triggered automatically when a source finishes ingesting. All judge calls: role `synthesis`, schema-enforced JSON, prompts via `get_prompt`, limits via tunables (self-evolution contract). Offline tests use `InMemoryGraphStore` (tests/fakes.py) + the existing `ScriptedRouter`.

## Docs to read before starting

| Topic | Where | What you need |
|---|---|---|
| Existing pipeline | `mslearn/worker/tasks.py`, `mslearn/pipeline/extraction_graph.py`, `mslearn/worker/context.py` | task patterns, context, role switching |
| Graph store | `mslearn/graph/store.py` (FULL read) | every query you'll extend; counted writes; the directed `CONFLICTS_WITH` gotcha documented in docstrings |
| Router | `mslearn/providers/router.py` | `complete(role, request)`, `embed(texts)` |
| Prompts/tunables | `mslearn/prompts.py`, `mslearn/opsdb.py` | override + audit mechanics |
| Celery task_routes | https://docs.celeryq.dev/en/stable/userguide/routing.html | routing `synthesize_task` → `judge` queue |
| Neo4j aggregation | https://neo4j.com/docs/cypher-manual/current/functions/aggregating/ | `min()`/`collect()` for spine ordering |

## Global Constraints

- Judge calls: role `synthesis` only; every prompt via `get_prompt(db, name)`; every limit via `get_tunable(db, key)`; JSON schema on every judge request; model outputs validated — unknown claim/concept ids in a judge response are DROPPED with a logged reason, never written
- `CONFLICTS_WITH` pairs are normalized (sorted claim-id order) before writing — fixes the Plan-3 directed-edge duplicate hazard
- Vector-search hits exclude the `embedding` property unless explicitly requested (payload discipline)
- Synthesis is incremental: only unassigned claims get clustered; only dirty concepts get conflict/naming passes; curriculum rebuild is cheap and full
- Trust filter: only `trusted` and `escalated` claims enter synthesis; `rejected` never
- Offline tests via `tests/fakes.py::InMemoryGraphStore` + `ScriptedRouter`; one neo4j-marked end-to-end test
- Prior suite (139 tests) stays green; ruff clean; commit per task

---

### Task 1: Plan-4 hardening (carried review findings)

**Files:**
- Modify: `mslearn/worker/tasks.py`
- Modify: `tests/test_worker_tasks.py`

Two accounting bugs from the Plan-4 review:

**(a) Persistent parse failure ends as `done`.** When extraction exhausts retries+escalation with `accepted == []` and `rejected != []`, the chunk is marked `done` with an error note — the failure monitor never sees it. Fix in `extract_chunk_task`: after `run_extraction`, if `state["accepted"] == [] and state["rejected"]`, mark the chunk `failed` (error = first rejected reasons, truncated) and run `_check_failure_monitor`; only mark `done` when there were accepted claims OR the chunk legitimately produced zero claims (`accepted == [] and rejected == []`).

**(b) Transient exhaustion leaves the chunk `pending` forever.** When `ProviderTransientError` escapes after Celery's `max_retries`, no `mark_chunk` ever runs; `resume_pending()` re-enqueues it endlessly. Fix: implement `on_failure` on the task (Celery `Task.on_failure(self, exc, task_id, args, kwargs, einfo)` — use `bind=True` class-style or the `@app.task(base=...)` pattern; simplest: define `def _on_failure(self, exc, task_id, args, kwargs, einfo)` via `on_failure` parameter of the decorator or a custom Task subclass) that marks the chunk (args[0]) `failed` with `error=f"retries exhausted: {exc}"` and runs the failure monitor. Guard: context may be unavailable in on_failure edge cases — wrap in try/except and log.

- [ ] **Step 1: Write failing tests** (append to `tests/test_worker_tasks.py`)

```python
def test_persistent_parse_failure_marks_failed(ctx):
    # BAD four times: retry, escalate, still failing -> rejected, no accepted
    context = ctx(ScriptedRouter([BAD, BAD, BAD, BAD]))
    worker_tasks.extract_chunk_task.delay("s1:0").get()
    assert context.db.failure_stats("s1")["failed"] == 1
    assert context.graph.claims == {}


def test_transient_exhaustion_marks_failed(ctx):
    from mslearn.providers.base import ProviderTransientError

    context = ctx(ScriptedRouter([ProviderTransientError("net down")] * 10))
    try:
        worker_tasks.extract_chunk_task.delay("s1:0").get()
    except ProviderTransientError:
        pass  # eager mode propagates after retries
    assert context.db.failure_stats("s1")["failed"] == 1
```

(Eager-mode note: with `task_always_eager`, retries run synchronously and the final exception propagates from `.get()` — hence the try/except. If `on_failure` is not invoked in eager mode in the installed Celery version, mark the chunk in an `except ProviderTransientError` inside the task body when `self.request.retries >= self.max_retries` before re-raising — implement whichever mechanism the test proves works, and document the choice in your summary.)

- [ ] **Step 2: Run to verify failure** — first test: chunk counted done not failed; second: failed count 0

- [ ] **Step 3: Implement** per above (choose the mechanism the tests validate)

- [ ] **Step 4: Full suite + ruff** — all green

- [ ] **Step 5: Commit**

```bash
git add mslearn/worker/tasks.py tests/test_worker_tasks.py
git commit -m "fix: failure accounting — parse-dead chunks and transient exhaustion mark failed"
```

---

### Task 2: GraphStore synthesis queries + conflict-pair normalization

**Files:**
- Modify: `mslearn/graph/store.py`, `tests/test_graph_concepts.py`, `tests/test_graph_claims.py`
- Create: `tests/test_graph_synthesis_queries.py`

**Interfaces (Plans 5–6 import):**
- CHANGED: `add_conflict(claim_a, claim_b, classification, rationale)` now normalizes: `a, b = sorted((claim_a, claim_b))` before MERGE — reverse-order call updates the same edge (add regression test: add (x,y) then (y,x) → `conflicts_in_concept` returns ONE row)
- CHANGED: `vector_search_claims(embedding, k=10, include_embedding=False)` / `vector_search_chunks(...)` — strip `embedding` key from hits unless True (update the existing Plan-3 test accordingly: it may currently rely on hits containing embedding — verify and adjust that test's assertions minimally)
- NEW: `unassigned_trusted_claims() -> list[dict]` — Claims with `trust IN ['trusted','escalated']` and no `IN_CONCEPT` edge; returns claim_id, text, stance, source_id, embedding (embedding needed for candidate search); ordered by claim_id
- NEW: `concept_id_of_claim(claim_id) -> str | None`
- NEW: `set_concept_meta(concept_id, name=None, summary=None, order_index=None)` — only non-None fields SET (counted write)
- NEW: `all_concepts() -> list[dict]` (concept_id, name, summary, order_index, dirty)
- NEW: `spine_concept_order() -> list[dict]` (concept_id, first_seq = min spine-chunk seq over the concept's claims): `MATCH (cl:Claim)-[:IN_CONCEPT]->(k:Concept), (cl)-[:EXTRACTED_FROM]->(ch:Chunk)<-[:HAS_CHUNK]-(s:Source {role:'spine'}) RETURN k.concept_id AS concept_id, min(ch.seq) AS first_seq ORDER BY first_seq`
- NEW: `curriculum() -> list[dict]` — concepts with non-null order_index ordered ascending, fields concept_id/name/summary/order_index

- [ ] **Step 1: failing tests** — `tests/test_graph_synthesis_queries.py` (pytestmark neo4j), seeding via the existing helpers (`make_doc`/`embed_stub`/`claim`/`unit_vec` imports as in prior graph tests):

```python
import pytest

from mslearn.graph.records import ConceptRecord
from mslearn.chunking import chunk_source
from tests.test_graph_claims import claim, unit_vec
from tests.test_graph_ingest import embed_stub, make_doc

pytestmark = pytest.mark.neo4j


def seed(clean_graph, n_claims=3):
    doc = make_doc()
    chunks = chunk_source(doc)
    clean_graph.upsert_source(doc)
    clean_graph.upsert_chunks(chunks, embed_stub(chunks))
    for i in range(n_claims):
        clean_graph.upsert_claim(claim(f"cl{i}", chunks[0].chunk_id, f"claim {i}"), unit_vec(i))
    return clean_graph


def test_unassigned_trusted_claims_and_assignment(clean_graph):
    store = seed(clean_graph)
    store.set_claim_trust("cl2", "rejected")
    rows = store.unassigned_trusted_claims()
    assert [r["claim_id"] for r in rows] == ["cl0", "cl1"]
    assert len(rows[0]["embedding"]) == 768
    store.upsert_concept(ConceptRecord(concept_id="k1", name=""))
    store.assign_claim("cl0", "k1")
    assert [r["claim_id"] for r in store.unassigned_trusted_claims()] == ["cl1"]
    assert store.concept_id_of_claim("cl0") == "k1"
    assert store.concept_id_of_claim("cl1") is None


def test_conflict_pair_normalized(clean_graph):
    store = seed(clean_graph)
    store.upsert_concept(ConceptRecord(concept_id="k1", name=""))
    store.assign_claim("cl0", "k1")
    store.assign_claim("cl1", "k1")
    store.add_conflict("cl1", "cl0", "outdated", "first")
    store.add_conflict("cl0", "cl1", "outdated", "second")  # reverse order: same edge
    conflicts = store.conflicts_in_concept("k1")
    assert len(conflicts) == 1
    assert conflicts[0]["rationale"] == "second"


def test_concept_meta_and_curriculum(clean_graph):
    store = seed(clean_graph)
    store.upsert_concept(ConceptRecord(concept_id="k1", name=""))
    store.upsert_concept(ConceptRecord(concept_id="k2", name=""))
    store.set_concept_meta("k1", name="Caching", summary="s", order_index=1)
    store.set_concept_meta("k2", name="Invalidation", order_index=0)
    cur = store.curriculum()
    assert [c["concept_id"] for c in cur] == ["k2", "k1"]
    assert cur[1]["name"] == "Caching"


def test_spine_concept_order(clean_graph):
    store = seed(clean_graph)
    store.upsert_concept(ConceptRecord(concept_id="k1", name=""))
    store.assign_claim("cl0", "k1")
    rows = store.spine_concept_order()
    assert rows and rows[0]["concept_id"] == "k1" and rows[0]["first_seq"] == 0


def test_vector_hits_exclude_embedding_by_default(clean_graph):
    store = seed(clean_graph)
    hits = store.vector_search_claims(unit_vec(0), k=2)
    assert hits and all("embedding" not in h for h in hits)
    hits_with = store.vector_search_claims(unit_vec(0), k=2, include_embedding=True)
    assert "embedding" in hits_with[0]
```

- [ ] **Step 2: verify failure** (Neo4j up)

- [ ] **Step 3: Implement** — all Cypher in store.py, following existing patterns; `add_conflict` gains `a, b = sorted((claim_a, claim_b))` at the top (keep the validate_classification call); vector `_vector_search` strips the key post-query when not requested.

- [ ] **Step 4: Run neo4j suite + full offline suite + ruff**

- [ ] **Step 5: Commit**

```bash
git add mslearn/graph/store.py tests/
git commit -m "feat: synthesis queries, normalized conflict pairs, lean vector hits"
```

---

### Task 3: Synthesis prompts, tunables, domain profile

**Files:**
- Modify: `mslearn/prompts.py`, `mslearn/opsdb.py` (TUNABLE_DEFAULTS), `tests/test_prompts.py`, `tests/test_tunables.py`

**Interfaces:**
- `TUNABLE_DEFAULTS` gains exactly: `"synth.candidate_k": 8.0`, `"synth.similarity_floor": 0.75` (update the exact-dict test)
- `PROMPTS` gains four entries (each mentioning its JSON contract): `concept_match` (anchor claim + numbered candidates with ids → `{"matches": [claim_id,...]}` of candidates expressing the SAME underlying concept/practice), `conflict_scan` (concept's claims with ids/stances + `{domain_guidance}` placeholder → `{"conflicts": [{"claim_a","claim_b","classification","rationale"}]}` with classification restricted to the 4 taxonomy values), `concept_name` (claims → `{"name": <3-6 words>, "summary": <2 sentences>}`), `concept_deps` (numbered concept names+ids → `{"edges": [{"from_concept","to_concept"}]}` where from depends on to, prerequisites only, no cycles)
- New helper in `mslearn/prompts.py`: `domain_guidance(profile: str) -> str` — `"technical"` → prefer `context_dependent` when claims hold under different conditions; `"interpretive"` → prefer `genuine_debate`, preserve competing framings; unknown profile raises KeyError. And `get_domain_profile(db) -> str` = `db.get_setting("corpus.domain_profile", "technical")`.

- [ ] Steps: failing tests (prompt keys exist + placeholder present + tunables exact-dict updated + domain_guidance branches) → implement → suite+ruff → commit

```bash
git add mslearn/prompts.py mslearn/opsdb.py tests/test_prompts.py tests/test_tunables.py
git commit -m "feat: synthesis prompts, candidate tunables, domain profile guidance"
```

---

### Task 4: In-memory graph fake + clustering service

**Files:**
- Create: `tests/fakes.py`, `mslearn/pipeline/synthesis.py` (clustering part), `tests/test_clustering.py`

**Interfaces:**
- `tests/fakes.py::InMemoryGraphStore` — dict-backed implementation of exactly the store subset synthesis uses: `unassigned_trusted_claims`, `concept_id_of_claim`, `upsert_concept`, `assign_claim`, `mark_concept_dirty`, `dirty_concepts`, `claims_in_concept`, `add_conflict` (sorted-pair, overwrite same edge), `conflicts_in_concept`, `set_concept_meta`, `all_concepts`, `spine_concept_order` (constructor takes optional `{concept_id: first_seq}` after assignment — computed from a `spine_seq` map keyed by claim_id passed at construction), `add_depends_on`, `concept_dependencies`, `curriculum`, `vector_search_claims(embedding, k, include_embedding=False)` (cosine over stored claim embeddings, descending, same dict shape with `score`). Claims added via test helper `add_claim(claim_id, text, stance, source_id, embedding, trust="trusted", spine_seq=None)`.
- `mslearn/pipeline/synthesis.py::cluster_new_claims(ctx) -> set[str]` — for each unassigned trusted claim: vector-search top `synth.candidate_k`+1, drop self, filter `score >= synth.similarity_floor`; no candidates → new singleton concept `k-{claim_id}` (name "" for now), assign, mark dirty; else judge `concept_match` (role synthesis, schema) → matched ids intersected with the candidate ids offered (hallucinated ids dropped); if any matched claim already has a concept → assign anchor to that concept (first by candidate rank); else create `k-{min(anchor_id, *matched_unassigned_ids)}`, assign anchor AND matched unassigned claims; mark every touched concept dirty. Returns dirty concept ids.

- [ ] **Step 1: failing tests** — `tests/test_clustering.py` with `InMemoryGraphStore` + `ScriptedRouter` (import from tests.test_extraction_graph), covering: singleton path (no candidates above floor → own concept); join-existing path (anchor matches claim already in concept → same concept, no new concept); new-cluster path (two unassigned claims merge into one `k-` concept); hallucinated id in judge response dropped (respond with bogus claim_id → treated as no match); trust filter (rejected claim never clustered). Assert exact judge role sequence (`["synthesis", ...]`) and dirty sets.

- [ ] **Step 2: verify failure** → **Step 3: implement** → **Step 4: suite+ruff** → **Step 5: commit**

```bash
git add tests/fakes.py mslearn/pipeline/synthesis.py tests/test_clustering.py
git commit -m "feat: embedding-blocked claim clustering with judge verdicts (incremental, hallucination-guarded)"
```

---

### Task 5: Conflict detection + concept naming (dirty pass)

**Files:**
- Modify: `mslearn/pipeline/synthesis.py`
- Create: `tests/test_conflict_pass.py`

**Interfaces:**
- `process_dirty_concepts(ctx) -> int` (returns processed count). Per dirty concept: claims = `claims_in_concept`; if ≥2 claims → judge `conflict_scan` with `domain_guidance(get_domain_profile(db))` filled in; validate each returned conflict: both ids present in the concept's claims, classification in taxonomy (else drop, count in a returned/logged summary); `add_conflict` each. Then judge `concept_name` → `set_concept_meta(name=..., summary=...)`. Finally `mark_concept_dirty(concept_id, False)`. Single-claim concepts skip conflict scan but still get named.

- [ ] **Step 1: failing tests** — scripted: concept with recommends/warns_against pair → judge returns one conflict `context_dependent` + naming call → assert edge written with rationale, name set, dirty cleared, role calls == ["synthesis", "synthesis"]; invalid classification from judge → dropped, no edge, name still set; singleton concept → only naming call.

- [ ] Steps 2–5 as usual; commit:

```bash
git add mslearn/pipeline/synthesis.py tests/test_conflict_pass.py
git commit -m "feat: domain-steered conflict classification and concept naming over dirty concepts"
```

---

### Task 6: Curriculum builder

**Files:**
- Modify: `mslearn/pipeline/synthesis.py`
- Create: `tests/test_curriculum.py`

**Interfaces:**
- `build_curriculum(ctx) -> list[str]` (ordered concept ids). Steps: `spine_concept_order()` → if ≥2 spine concepts, judge `concept_deps` over them (ids+names) → validate ids, drop self-edges and edges creating a cycle (check before adding: run Kahn incrementally or reject edge if it would close a cycle — implement `_acyclic_add(edges, new_edge)` helper with DFS reachability); `add_depends_on` valid edges. Order: Kahn's topological sort over spine concepts where an edge `from DEPENDS_ON to` forces `to` before `from`; ties broken by `first_seq` ascending. Non-spine concepts appended after, ordered by name then concept_id. Write `order_index` via `set_concept_meta`; return the ordered ids.

- [ ] **Step 1: failing tests** — scripted deps: 3 spine concepts, judge returns k3→k1 dependency → order respects both topo (k1 before k3) and first_seq ties; judge proposes cycle (k1→k2 and k2→k1) → second edge dropped, order still total; supplement-only concept lands after spine concepts; idempotent rerun (same order_index).

- [ ] Steps 2–5; commit:

```bash
git add mslearn/pipeline/synthesis.py tests/test_curriculum.py
git commit -m "feat: spine-anchored curriculum with judge dependencies, cycle-guarded topo order"
```

---

### Task 7: Synthesis task, trigger hook, CLI, README

**Files:**
- Modify: `mslearn/worker/app.py` (route `synthesize_task` → `judge`), `mslearn/worker/tasks.py` (task + trigger), `README.md`
- Create: `mslearn/synth_cli.py`, `tests/test_synthesis_task.py`

**Interfaces:**
- `synthesize_task()` (Celery, judge queue): `cluster_new_claims` → `process_dirty_concepts` → `build_curriculum`; logs a one-line summary via `db.set_setting("synthesis:last_run", ...)` (timestamp + counts JSON string)
- Trigger: in `extract_chunk_task`, after a `done`/`failed` mark, when `source_row` shows `done_chunks + failed_chunks >= total_chunks` and status == running → `set_source_status(source_id, "done")` + `synthesize_task.delay()` (source completion = synthesis kick; multiple sources → multiple runs, each incremental & idempotent)
- `python -m mslearn.synth_cli [--local]` — mirrors ingest_cli: `--local` eager + default context, prints concept count + curriculum length
- One neo4j-marked integration test (`tests/test_synthesis_task.py::test_end_to_end_synthesis` — seed two similar claims + one opposed via real GraphStore, ScriptedRouter for judge calls, run the three synthesis functions directly against live Neo4j, assert concept/conflict/curriculum in the graph). Offline test: trigger fires exactly once per source completion (eager app, FakeGraph from worker tests extended minimally or monkeypatched `synthesize_task.delay`).

- [ ] Steps 1–5 as usual; README append (Synthesis section: what runs when, domain profile setting, tunables list); commit:

```bash
git add mslearn/worker/ mslearn/synth_cli.py tests/test_synthesis_task.py README.md
git commit -m "feat: synthesis task on judge queue, auto-trigger on source completion, CLI"
```

---

## Self-Review (performed at write time — error check)

- **Carried findings closed:** Plan-4 Important #1/#2 → Task 1 (with eager-mode mechanism caveat left to test-proven choice); Plan-3 directed-conflict hazard → Task 2 normalization + regression test; embedding payload → Task 2 default-strip (existing Plan-3 test updated, not weakened).
- **Judge-output safety:** every judge response validated against offered ids/taxonomy; hallucinated ids dropped by contract (tests pin it). Escalation-queue note from review: synthesis runs on judge queue via routing; extraction's escalated calls remain in-task role switches (documented, acceptable — queue isolates concurrency, role picks the model).
- **Incremental semantics:** cluster only unassigned; dirty concepts reprocessed; conflict edge overwrite-on-same-pair makes reruns idempotent; curriculum rebuild idempotent (order_index overwrite).
- **Interfaces verified against real code:** `ScriptedRouter` reuse (role recording), `PipelineContext` shape, store method names extend Plan-3 file conventions, `pytestmark = pytest.mark.neo4j` pattern matches existing graph tests.
- **Ambiguity resolved by decision:** singleton claims form singleton concepts (curriculum/teaching need every claim reachable); non-spine concepts ordered after spine (name-sorted); `synthesize_task` triggered per source completion, safe to run concurrently-ish because each phase is idempotent, though Celery judge-queue concurrency 1 recommended in README.
```
