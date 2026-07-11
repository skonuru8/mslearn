# Self-Improving Evals + User Feedback

Date: 2026-07-11
Status: Approved (design decisions locked with user)
Sequence: **Subsystem B** — depends on Subsystem A (notes redesign) landing first, because B judges the *new* guide and attaches feedback to the *new* note view.

## Goal

Make the system measurably better every run by learning from (a) its own judged output and (b) the user's feedback, with the user kept in the loop on risky changes. This is **not** magic self-improvement: it is the existing shadow-eval + gate loop, extended to judge what the user actually sees, fed by structured human feedback, ratcheted by a growing regression set.

## What already exists (reuse, do not rebuild)

- **`evolve_once` (`evals/evolve.py`)**: baseline metrics → LLM proposes a prompt/tunable change (`evolve_propose`) → shadow-eval on an `OverlayOpsDB` copy → gates (target must improve, nothing else may regress) → auto-apply + log to `evolution_runs`.
- **Component metrics (`evals/metrics.py`)** and **`GATES` (`evals/gates.py`)** for extraction, grounding, clustering, tension, schema.
- **LLM-as-judge (`evals/judged.py`)**: clarity/grounding/provenance — but on the legacy `generate_teaching` markdown path.
- **Golden fixtures (`evals/golden.py`, `seed.py`)**: extraction/grounding/clustering/tension.
- **Endpoints (`server/routers/evals.py`)**: `/api/evals/run`, `/evolve`, `/evolve/history`, `/report`, `/history`, golden CRUD.

## The five gaps (this is the work)

### B1. Judge the guide the user actually sees

Add guide-path judges in `evals/judged.py` that run on `generate_guide` output (JSON), not `generate_teaching` (markdown). New `judge_guide(ctx, n)` samples curriculum concepts, generates each guide, and scores with a new `rubric_guide` prompt returning:

- `depth_1_5` — does each section explain (what/why/how/example) vs. restate the claim in one line?
- `redundancy_1_5` (lower = more redundant) — does the lede/sections merely repeat the concept summary / each other?
- `category_fit_1_5` — does the concept sit under a coherent category (uses the concept's `category`)?
- `grounding_1_5` — every item tied to a real in-concept claim id (cross-checked structurally against `claims_in_concept`, not just the judge's opinion).

Structural grounding check reuses the `provenance_citations` idea but on guide JSON `item.claims` instead of `[claim:id]` markdown.

Wire aggregates into `compute_component_metrics`:
`guide.depth`, `guide.non_redundancy`, `guide.category_fit`, `guide.grounding` (means, normalized 0–1).

### B2. Let the user give feedback (structured tags + note)

**Data.** New table in `opsdb.py`:
```
CREATE TABLE note_feedback (
  id INTEGER PK, project_id TEXT, concept_id TEXT,
  helpful INTEGER,              -- 1 / 0 / NULL
  tags TEXT,                    -- JSON array subset of: too_shallow, repetitive, wrong, off_topic
  comment TEXT,                 -- optional free text
  guide_hash TEXT,              -- hash of the guide JSON rated, so feedback binds to a version
  ts INTEGER
)
```
`OpsDB` gains `add_note_feedback(...)`, `feedback_for_concept(concept_id)`, `feedback_aggregate(project_id)` (counts + rates), `recent_negative_feedback(limit)`.

**API.** In `server/routers/study.py`:
- `POST /api/study/concepts/{id}/feedback` body `{ helpful?: bool, tags?: string[], comment?: string, guide_hash?: string }` → validates tags against the allowed set → `add_note_feedback`.
- `GET /api/study/concepts/{id}/feedback` → latest feedback for prefill.

**UI.** New `NoteFeedback` component rendered at the bottom of `ConceptView`'s note: 👍/👎, four tag checkboxes (Too shallow / Repetitive / Wrong / Off-topic), optional comment, Save. Note-level only (not per-section). Shows a saved confirmation and lets the user update.

### B3. Wire feedback into the loop

`evals/metrics.py` gains feedback-derived metrics from `feedback_aggregate`:
- `feedback.helpful_rate` (👍 / total rated)
- `feedback.shallow_rate`, `feedback.repetitive_rate`, `feedback.wrong_rate`, `feedback.offtopic_rate`

Add to `GATES` (`evals/gates.py`):
- `feedback.helpful_rate >= <threshold>` (target; also a gate once enough samples)
- `feedback.wrong_rate <= <small>` (hard gate — wrongness must never regress)
- `guide.grounding >= <high>` (hard gate)

Guard against small-sample noise: feedback gates only bind once `total_rated >= MIN_FEEDBACK_SAMPLES` (a tunable); below that they are reported but not enforced (mirrors how the provenance gate handles "not evaluated").

### B4. Learn patterns from itself

New `evals/patterns.py`: `mine_patterns(ctx)` that clusters recurring signal into named failure patterns:
- Aggregates low guide-judge scores (which rubric axis, on which concepts) + `recent_negative_feedback` tags/comments + rejected `evolution_history`.
- One model call (`patterns_summarize` prompt) → `{ patterns: [ { name, symptom, evidence, suggested_target_metric } ] }`.

`evolve_propose` prompt is extended to receive `mine_patterns` output alongside the existing metrics/tunables/audit, so proposals target real recurring problems instead of guessing from metrics alone. The `evolution_history` (accepted + rejected) already feeds in, giving run-over-run memory.

### B5. Ratchet — flagged notes become regression fixtures

New golden kind `guide` (extends `evals/golden.py` + `seed.py`):
- A fixture = { concept claims (frozen), the axis that failed, a pass condition }.
- `POST /api/evals/golden/guide/from-feedback` promotes a negatively-rated concept into a `guide` golden fixture (snapshot its claims + the failing tag).
- `judge_guide` scores against active `guide` fixtures too, so once a class of problem is fixed the gate holds it. This is the mechanism that makes improvement **monotonic** rather than oscillating.

### B6. Hybrid autonomy (tunables auto, prompts ask)

Change `evolve_once` accept path:
- **Tunable** proposal that passes shadow-eval + gates → **auto-apply** (unchanged).
- **Prompt** proposal that passes → do **not** apply; record the `evolution_runs` row in a new `pending` state (add a `status` column: `pending` | `applied` | `rejected`, back-compatible with existing `accepted` flag via migration in `opsdb._ensure_column`).

New review endpoints in `server/routers/evals.py`:
- `GET /api/evals/pending` → pending prompt proposals with before/after shadow metrics + diff.
- `POST /api/evals/pending/{run_id}/approve` → applies the prompt (`set_setting(prompt:...)`), marks `applied`.
- `POST /api/evals/pending/{run_id}/reject` → marks `rejected`.

**UI (`EvalsView.tsx`)**: a "Pending prompt changes" section showing each proposal's target metric, why, before→after shadow metrics, and the prompt diff, with Approve/Reject buttons.

## Files touched

Backend: `evals/judged.py`, `evals/metrics.py`, `evals/gates.py`, `evals/evolve.py`, `evals/golden.py`, `evals/seed.py`, new `evals/patterns.py`, `prompts.py` (`rubric_guide`, `patterns_summarize`, extended `evolve_propose`), `opsdb.py` (`note_feedback` table + `evolution_runs.status` column + methods), `server/routers/study.py`, `server/routers/evals.py`.
Frontend: new `NoteFeedback` component + wire into `ConceptView.tsx`, `EvalsView.tsx`, `api/types.ts`, `api/client.ts`, `app.css`.

## Explicitly not doing

- No autonomous prompt application (hybrid gate by decision).
- No per-section feedback (note-level only by decision).
- Not deleting the legacy `teach_concept` path; B1 simply adds guide-path coverage alongside it.

## Testing

- `judged.py`: `judge_guide` returns all four axes on a stub guide; grounding axis flags an item citing an out-of-concept claim id.
- `opsdb.py`: feedback insert/aggregate; `status` column migration on an existing DB; tag validation rejects unknown tags.
- `evolve.py`: prompt proposal that passes gates ends `pending` (not applied); tunable proposal still auto-applies; approve endpoint applies the prompt; small-sample feedback gate is reported-not-enforced.
- `patterns.py`: `mine_patterns` degrades to `[]` on bad model output without raising.
- `metrics.py`: feedback metrics present and correct from seeded feedback rows.
- Frontend: `NoteFeedback` posts the right payload and prefills; `EvalsView` renders pending proposals and calls approve/reject.

## Success criteria

The user rates notes with 👍/👎 + tags; those become metrics gating the evolve loop; the loop auto-tunes numbers, queues prompt rewrites for the user's approval, targets mined recurring failures, and locks fixes in as guide golden fixtures so each run is at least as good as the last on everything the user cares about.
