# Self-Improving Evals + Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Judge the guide the user actually sees, capture structured user feedback, feed both into the existing shadow-eval evolve loop as gated metrics, mine recurring failure patterns into the proposer, ratchet fixes via a growing golden set, and gate autonomy so tunables auto-apply while prompt rewrites wait for the user's approval.

**Architecture:** Reuse `evals/evolve.py`'s baseline→propose→shadow→gate→apply loop. Add guide-path judges + feedback aggregates as new metrics in `evals/metrics.py`, new `GATES`, a `note_feedback` table, feedback endpoints, a mining step feeding `evolve_propose`, a `guide` golden kind, and a `status` (pending/applied/rejected) column so prompt proposals queue for approval.

**Tech Stack:** Python (FastAPI, sqlite via `opsdb.py`, custom graph store), pytest; React + TypeScript + Vite, vitest.

**Depends on:** the Notes Redesign plan landing first (this judges the new guide and attaches feedback to the new note view).

## Global Constraints

- Reuse existing loop machinery (`evolve_once`, `OverlayOpsDB`, `compute_component_metrics`, `GATES`) — do not fork it.
- Feedback gates bind only once `total_rated >= MIN_FEEDBACK_SAMPLES` (default 10, a tunable); below that, report the metric but do not enforce (mirror the provenance "not evaluated" pattern).
- Tunable proposals passing shadow-eval auto-apply; prompt proposals passing shadow-eval go to `pending` and require explicit approval — never auto-applied.
- Every new model-call helper must degrade (return empty/neutral) on `ProviderBadOutputError`, never crash a run.
- Allowed feedback tags exactly: `too_shallow`, `repetitive`, `wrong`, `off_topic`.
- Backend tests: `pytest tests/<file> -v`. Frontend: `cd frontend && npx vitest run <file>`.

## Starting gate thresholds (locked here)

- `guide.grounding >= 0.98` (hard gate)
- `feedback.wrong_rate <= 0.05` (hard gate, sample-gated)
- `feedback.helpful_rate >= 0.70` (target + gate, sample-gated)
- `MIN_FEEDBACK_SAMPLES = 10`

---

### Task 1: `note_feedback` table + OpsDB methods

**Files:**
- Modify: `mslearn/opsdb.py` (schema DDL + methods)
- Test: `tests/test_opsdb_feedback.py`

**Interfaces:**
- Produces:
  - `db.add_note_feedback(project_id, concept_id, helpful, tags, comment, guide_hash) -> int`
  - `db.feedback_for_concept(concept_id, project_id) -> dict | None` (latest)
  - `db.feedback_aggregate(project_id) -> dict` with keys `total_rated, helpful, too_shallow, repetitive, wrong, off_topic`
  - `db.recent_negative_feedback(project_id, limit=20) -> list[dict]`

- [ ] **Step 1: Write failing test** `tests/test_opsdb_feedback.py`:

```python
from mslearn.opsdb import OpsDB


def test_feedback_insert_and_aggregate(tmp_path):
    db = OpsDB(tmp_path / "o.db")
    db.add_note_feedback("default", "k1", helpful=True, tags=["too_shallow"], comment="x", guide_hash="h1")
    db.add_note_feedback("default", "k2", helpful=False, tags=["wrong", "repetitive"], comment="", guide_hash="h2")
    agg = db.feedback_aggregate("default")
    assert agg["total_rated"] == 2
    assert agg["helpful"] == 1
    assert agg["too_shallow"] == 1
    assert agg["wrong"] == 1
    neg = db.recent_negative_feedback("default")
    assert any(r["concept_id"] == "k2" for r in neg)
    assert db.feedback_for_concept("k1", "default")["tags"] == ["too_shallow"]
```

- [ ] **Step 2: Run** `pytest tests/test_opsdb_feedback.py -v` → FAIL.

- [ ] **Step 3: Add DDL** to the schema string in `opsdb.py`:

```sql
CREATE TABLE IF NOT EXISTS note_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    concept_id TEXT NOT NULL,
    helpful INTEGER,
    tags TEXT NOT NULL DEFAULT '[]',
    comment TEXT NOT NULL DEFAULT '',
    guide_hash TEXT,
    ts INTEGER NOT NULL
);
```

Add methods (JSON-encode `tags`; aggregate with SQL counts; `recent_negative_feedback` = rows where `helpful=0` OR tags non-empty, newest first). Follow the existing `with self._lock: self.conn.execute(...)` pattern used elsewhere in `opsdb.py`.

- [ ] **Step 4: Run** `pytest tests/test_opsdb_feedback.py -v` → PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(opsdb): note_feedback table + aggregates"`

---

### Task 2: Feedback API endpoints

**Files:**
- Modify: `mslearn/server/routers/study.py`
- Test: `tests/test_study_feedback_api.py`

**Interfaces:**
- Consumes: `db.add_note_feedback`, `db.feedback_for_concept`.
- Produces:
  - `POST /api/study/concepts/{id}/feedback` body `{helpful?: bool, tags?: string[], comment?: string, guide_hash?: string}`
  - `GET /api/study/concepts/{id}/feedback` → latest or `{}`

- [ ] **Step 1: Write failing test** `tests/test_study_feedback_api.py` using the existing FastAPI TestClient fixture pattern (see other `tests/test_*_api.py`): POST valid feedback → 200; POST with an unknown tag → 422/400; GET returns the stored feedback.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** in `study.py`:

```python
ALLOWED_TAGS = {"too_shallow", "repetitive", "wrong", "off_topic"}

class FeedbackRequest(BaseModel):
    helpful: bool | None = None
    tags: list[str] = Field(default_factory=list)
    comment: str = ""
    guide_hash: str | None = None

@router.post("/concepts/{concept_id}/feedback")
def submit_feedback(concept_id: str, body: FeedbackRequest,
                    ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)):
    bad = [t for t in body.tags if t not in ALLOWED_TAGS]
    if bad:
        raise HTTPException(status_code=422, detail=f"unknown tags {bad}")
    ctx.db.add_note_feedback(project_id, concept_id, helpful=body.helpful,
                             tags=body.tags, comment=body.comment, guide_hash=body.guide_hash)
    return {"ok": True}

@router.get("/concepts/{concept_id}/feedback")
def get_feedback(concept_id: str, ctx=Depends(get_ctx), project_id: str = Depends(get_project_id)):
    return ctx.db.feedback_for_concept(concept_id, project_id) or {}
```

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(api): note feedback endpoints"`

---

### Task 3: NoteFeedback UI component

**Files:**
- Create: `frontend/src/components/NoteFeedback.tsx`
- Modify: `frontend/src/views/ConceptView.tsx` (render it under the guide), `frontend/src/api/types.ts`, `frontend/src/api/client.ts` (if a helper is needed)
- Test: `frontend/src/components/NoteFeedback.test.tsx`

**Interfaces:**
- Produces: `<NoteFeedback conceptId={id} />` — 👍/👎 buttons, four tag checkboxes (labels: "Too shallow"→`too_shallow`, "Repetitive"→`repetitive`, "Wrong"→`wrong`, "Off-topic"→`off_topic`), optional comment textarea, Save button posting to `/api/study/concepts/{id}/feedback`; prefills from GET; shows "Saved".

- [ ] **Step 1: Write failing test** `NoteFeedback.test.tsx`: render, click 👎, check "Too shallow", type a comment, click Save → assert a POST with body `{helpful:false, tags:["too_shallow"], comment:"...", ...}` (use the file's fetchMock util).

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `NoteFeedback.tsx` (functional component, local state for helpful/tags/comment/saved, `useEffect` to GET prefill). Add `NoteFeedbackRow` type to `types.ts` if useful. Render `<NoteFeedback conceptId={id} />` in `ConceptView.tsx` right after the `<InteractiveGuide .../>` block.

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5:** Add minimal CSS to `app.css` (`.note-feedback`, tag chips). Run `cd frontend && npx vitest run` → PASS; `npx tsc --noEmit` → clean.

- [ ] **Step 6: Commit** `git commit -am "feat(ui): NoteFeedback component on concept view"`

---

### Task 4: Guide-path judge (`judge_guide`) + `rubric_guide` prompt

**Files:**
- Modify: `mslearn/evals/judged.py` (new `judge_guide`, `guide_grounding_violations`)
- Modify: `mslearn/prompts.py` (new `rubric_guide`)
- Test: `tests/test_judge_guide.py`

**Interfaces:**
- Consumes: `generate_guide` (returns `(dict, bool)`), `ctx.graph.claims_in_concept`, `get_prompt(db, "rubric_guide")`.
- Produces:
  - `guide_grounding_violations(guide: dict, concept_claim_ids: set[str]) -> list[str]` — items citing ids outside the concept, or items with empty `claims`.
  - `judge_guide(ctx, n: int = 5) -> dict` keys `depth, non_redundancy, category_fit, grounding` (means in 0..1).

- [ ] **Step 1: Add `rubric_guide` prompt** to `PROMPTS`:

```python
    "rubric_guide": (
        "Score a study-guide JSON for one concept.\n"
        "Concept: {concept_name}\nSummary: {concept_summary}\nGuide JSON:\n{guide}\n"
        "Return JSON: {{\"depth_1_5\": n, \"redundancy_1_5\": n,"
        " \"category_fit_1_5\": n, \"grounding_1_5\": n}}.\n"
        "depth: sections explain (what/why/how/example) vs restate in one line.\n"
        "redundancy: HIGH score = little repetition of the summary or between sections.\n"
        "category_fit: concept fits a coherent category.\n"
        "grounding: every item ties to a real claim.\n"
    ),
```

- [ ] **Step 2: Write failing test** `tests/test_judge_guide.py`:

```python
from mslearn.evals.judged import guide_grounding_violations


def test_grounding_violations_flags_out_of_concept_and_empty():
    guide = {"tl_dr": {"text": "t", "claims": ["c1"]}, "sections": [
        {"id": "s1", "title": "S", "items": [
            {"kind": "claim", "text": "a", "claims": ["c1"]},
            {"kind": "claim", "text": "b", "claims": ["c9"]},
            {"kind": "claim", "text": "c", "claims": []},
        ]},
    ]}
    v = guide_grounding_violations(guide, {"c1"})
    assert any("c9" in x for x in v)
    assert any("empty" in x.lower() for x in v)
```

- [ ] **Step 3: Run** → FAIL.

- [ ] **Step 4: Implement** `guide_grounding_violations` (iterate sections→items; flag `claims` entries not in `concept_claim_ids`; flag items with no claims) and `judge_guide` (sample `ctx.graph.curriculum() or all_concepts()`, `generate_guide` each, call the `evals` router with `rubric_guide` + a strict schema like the existing `judge_teaching`, average the four axes / 5.0, and additionally fold in a structural grounding penalty from `guide_grounding_violations`). Mirror the structure of the existing `judge_teaching`.

- [ ] **Step 5: Run** `pytest tests/test_judge_guide.py -v` → PASS.

- [ ] **Step 6: Commit** `git commit -am "feat(evals): guide-path judge + grounding check"`

---

### Task 5: Feedback + guide metrics in `compute_component_metrics`; new gates

**Files:**
- Modify: `mslearn/evals/metrics.py` (`compute_component_metrics`, new `feedback_rates`, `guide_quality`)
- Modify: `mslearn/evals/gates.py` (`GATES`, sample-gating helper)
- Test: `tests/test_metrics_feedback.py`, `tests/test_gates.py`

**Interfaces:**
- Consumes: `db.feedback_aggregate`, `judge_guide`.
- Produces: metrics dict additionally carries `feedback.helpful_rate`, `feedback.shallow_rate`, `feedback.repetitive_rate`, `feedback.wrong_rate`, `feedback.offtopic_rate`, `feedback.total_rated`, `guide.depth`, `guide.non_redundancy`, `guide.category_fit`, `guide.grounding`.

- [ ] **Step 1: Write failing test** `tests/test_metrics_feedback.py`: seed feedback rows, assert `feedback_rates(ctx)` returns correct rates and `total_rated`.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `feedback_rates(ctx)` from `feedback_aggregate` (rate = count/total, guard div-by-zero → 0.0; include `feedback.total_rated`) and `guide_quality(ctx)` wrapping `judge_guide`. Add both into `compute_component_metrics`'s returned dict.

- [ ] **Step 4: Update `gates.py`:** add the four gates from the "Starting gate thresholds" section. Add a helper so `evolve` can tell which gates are **sample-gated** and skip enforcement when `feedback.total_rated < MIN_FEEDBACK_SAMPLES`:

```python
MIN_FEEDBACK_SAMPLES = 10
SAMPLE_GATED = {"feedback.helpful_rate", "feedback.wrong_rate"}

def gate_enforced(metric: str, metrics: dict) -> bool:
    if metric in SAMPLE_GATED:
        return metrics.get("feedback.total_rated", 0) >= MIN_FEEDBACK_SAMPLES
    return True
```

Add `test_gates.py` covering `gate_enforced` above/below the sample floor.

- [ ] **Step 5: Run** `pytest tests/test_metrics_feedback.py tests/test_gates.py -v` → PASS.

- [ ] **Step 6: Commit** `git commit -am "feat(evals): feedback + guide-quality metrics and gates"`

---

### Task 6: `evolution_runs.status` column (pending/applied/rejected)

**Files:**
- Modify: `mslearn/opsdb.py` (`_ensure_column` migration + methods)
- Test: `tests/test_opsdb_evolution_status.py`

**Interfaces:**
- Produces:
  - `db.create_evolution_run(..., status="applied"|"pending"|"rejected")` (add `status` param, default preserves current behavior)
  - `db.pending_evolution_runs() -> list[dict]`
  - `db.set_evolution_run_status(run_id, status)`

- [ ] **Step 1: Write failing test**: create a pending run, `pending_evolution_runs()` returns it; `set_evolution_run_status(id, "applied")` removes it from pending.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement:** in `_ensure_column` init path add `self._ensure_column("evolution_runs", "status", "TEXT NOT NULL DEFAULT 'applied'")`. Add the three methods. Keep the existing `accepted` flag working (status is additive).

- [ ] **Step 4: Run** → PASS. Also run `pytest tests/ -k evolution -v` to confirm existing evolution tests still pass.

- [ ] **Step 5: Commit** `git commit -am "feat(opsdb): evolution_runs.status for approval queue"`

---

### Task 7: Hybrid autonomy in `evolve_once`

**Files:**
- Modify: `mslearn/evals/evolve.py`
- Test: `tests/test_evolve.py` (extend existing)

**Interfaces:**
- Consumes: `gate_enforced`, `db.create_evolution_run(status=...)`.
- Produces: passing **tunable** proposals auto-apply (unchanged); passing **prompt** proposals create a `pending` run and are NOT applied.

- [ ] **Step 1: Write failing test** in `tests/test_evolve.py`: script a prompt proposal that passes gates; assert after `evolve_once` the prompt setting is unchanged and one pending run exists. Script a tunable proposal that passes; assert it is applied. Add a test that a sample-gated feedback gate does not block when `total_rated` is below the floor.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Edit `evolve_once`:** in the `if target_improved and gates_ok:` branch, split on `proposal.get("kind")`:
  - tunable → `set_tunable(...)`, `set_evolution_run_accepted(run_id, True)`, status stays `applied`, append to `accepted`.
  - prompt → do NOT `set_setting`; call `db.set_evolution_run_status(run_id, "pending")`; append to a new `pending` list in the return dict.
  - When computing `gates_ok`, skip any gate where `not gate_enforced(metric, baseline)`.
  - Return `{"accepted", "pending", "rejected", "baseline"}`.

- [ ] **Step 4: Run** `pytest tests/test_evolve.py -v` → PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(evolve): auto-apply tunables, queue prompt changes for approval"`

---

### Task 8: Approval endpoints + Evals UI

**Files:**
- Modify: `mslearn/server/routers/evals.py`
- Modify: `frontend/src/views/EvalsView.tsx`, `frontend/src/api/types.ts`
- Test: `tests/test_evals_pending_api.py`, `frontend/src/views/EvalsView.test.tsx`

**Interfaces:**
- Produces:
  - `GET /api/evals/pending` → pending runs (proposal, shadow before/after, why).
  - `POST /api/evals/pending/{run_id}/approve` → applies the prompt (`set_setting(f"prompt:{name}", new_prompt)` parsed from the stored proposal_json), status→`applied`.
  - `POST /api/evals/pending/{run_id}/reject` → status→`rejected`.

- [ ] **Step 1: Write failing API test** `tests/test_evals_pending_api.py`: seed a pending prompt run, GET lists it, approve applies the prompt setting + clears pending, reject clears without applying.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement endpoints** in `evals.py` (parse `proposal_json`, extract `key`→prompt name + `new_prompt`; approve calls `ctx.db.set_setting`; both call `set_evolution_run_status`).

- [ ] **Step 4: Run** → PASS.

- [ ] **Step 5: Frontend test** `EvalsView.test.tsx`: mock `/api/evals/pending` with one prompt proposal; assert it renders target metric, why, before→after, and Approve/Reject buttons that POST to the right URLs.

- [ ] **Step 6: Implement** a "Pending prompt changes" section in `EvalsView.tsx` (list pending, show metrics + prompt diff, Approve/Reject buttons). Run frontend tests + `tsc --noEmit` → PASS/clean.

- [ ] **Step 7: Commit** `git commit -am "feat(evals): approval queue endpoints + Evals UI"`

---

### Task 9: Pattern mining feeds the proposer

**Files:**
- Create: `mslearn/evals/patterns.py`
- Modify: `mslearn/prompts.py` (`patterns_summarize`; extend `evolve_propose` to accept `{patterns}`), `mslearn/evals/evolve.py` (call `mine_patterns`, pass into prompt)
- Test: `tests/test_patterns.py`

**Interfaces:**
- Consumes: `db.recent_negative_feedback`, `db.evolution_history`, `judge_guide` axes, `get_prompt(db, "patterns_summarize")`.
- Produces: `mine_patterns(ctx) -> list[dict]` items `{name, symptom, evidence, suggested_target_metric}`; `[]` on bad output.

- [ ] **Step 1: Write failing test** `tests/test_patterns.py`: with a scripted router returning a patterns payload, `mine_patterns` returns parsed list; with a raising/bad-output router it returns `[]` (no raise).

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `mine_patterns`: gather negative feedback + rejected evolution history, call the `evals` router with `patterns_summarize` (JSON schema `{patterns: array}`), wrap in try/except `ProviderBadOutputError` → `[]`. Add `patterns_summarize` prompt. In `evolve_propose` prompt, add a `Patterns:\n{patterns}` line; in `evolve_once` compute `patterns = mine_patterns(ctx)` and pass `patterns=json.dumps(...)` into `.format(...)`. Add `{patterns}` to the prompt's placeholders (and update `required_placeholders` expectations — since `evolve_propose` isn't in `TUNABLE_BOUNDS`/validated as a rewrite target, just ensure `.format` supplies it).

- [ ] **Step 4: Run** `pytest tests/test_patterns.py tests/test_evolve.py -v` → PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(evals): mine failure patterns into the proposer"`

---

### Task 10: Guide golden kind + ratchet from feedback

**Files:**
- Modify: `mslearn/evals/golden.py`, `mslearn/evals/seed.py`, `mslearn/evals/judged.py` (score against active `guide` fixtures)
- Modify: `mslearn/server/routers/evals.py` (`POST /api/evals/golden/guide/from-feedback`)
- Test: `tests/test_golden_guide.py`

**Interfaces:**
- Produces: a `guide` golden kind (fixture = frozen concept claims + failing axis + pass condition); `promote_feedback_to_golden(ctx, concept_id)`.

- [ ] **Step 1: Write failing test** `tests/test_golden_guide.py`: promote a negatively-rated concept → a `guide` golden fixture is created and loadable via `load_golden("guide", active_only=True)`.

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** the `guide` kind in `golden.py`/`seed.py` following the existing golden-kind pattern (dataclass row + loader + seed writer), a `promote_feedback_to_golden` helper snapshotting `claims_in_concept` + the concept's worst feedback tag, and wire `judge_guide` to also evaluate active `guide` fixtures. Add the `from-feedback` endpoint.

- [ ] **Step 4: Run** `pytest tests/test_golden_guide.py -v` → PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(evals): guide golden kind + ratchet flagged notes into fixtures"`

---

### Task 11: Full-suite verification

- [ ] **Step 1:** `pytest -q` → all pass (fix any evolution/eval tests that assumed the old auto-apply-prompt behavior or old return-dict shape).
- [ ] **Step 2:** `cd frontend && npx vitest run` → all pass; `npx tsc --noEmit` → clean.
- [ ] **Step 3: Commit** stragglers.

## Self-Review Notes

- B1 → Tasks 4,5. B2 → Tasks 1,2,3. B3 → Task 5. B4 → Task 9. B5 → Task 10. B6 → Tasks 6,7,8.
- Sample-gating (Global Constraint) → Task 5 `gate_enforced` + Task 7 test.
- Degrade-not-crash → Tasks 4,9 explicit `ProviderBadOutputError` handling + tests.
- Type consistency: `evolve_once` return dict gains `pending`; Task 8 UI consumes `/api/evals/pending` (DB-backed, not the return dict) — both derive from `evolution_runs.status='pending'`.
