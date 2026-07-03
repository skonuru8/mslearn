# Plan 8/8: Evals, Release Gates & Self-Evolution — Implementation Plan

> **For the implementer (Cursor):** TDD per task, commit per task, repo root commands. STOP on divergence. This plan is the DEFINITION OF DONE for the whole system — gate values come from the spec and are not negotiable downward.
>
> **Contract check:**
> ```
> .venv/bin/python -c "
> from mslearn.pipeline.trust import check_claim
> from mslearn.pipeline.extraction_graph import run_extraction
> from mslearn.pipeline.synthesis import cluster_new_claims, process_dirty_concepts
> from mslearn.pipeline.teaching import generate_teaching
> from mslearn.opsdb import OpsDB, TUNABLE_DEFAULTS
> from mslearn.prompts import PROMPTS, get_prompt
> print('contract ok')"
> ```

**Goal:** Golden-set infrastructure (seeding + user review), deterministic metrics, judged evals, the numeric release gates from the spec, the memory-provenance gate, and the eval-gated self-evolution loop (judge proposes tunable/prompt changes; accepted only when golden-set scores improve; audited; rollback-able) with its API/UI panel hookup.

**Architecture:** `mslearn/evals/` — `golden.py` (JSONL golden-set load/save/validate), `seed.py` (strong-model candidate labeling from the live graph), `metrics.py` (component evals, pure functions over golden sets + a context), `judged.py` (rubric scoring), `gates.py` (the spec constants + gate evaluation), `runner.py` (full run → `eval_runs`/`eval_metrics` tables + exit code), `evolve.py` (the self-evolution loop). Golden sets live in `evals/golden/*.jsonl` (repo, human-reviewed). Endpoints in `mslearn/server/routers/evals.py` (report, history, golden review queue, evolve trigger, tunable rollback).

## Docs to read

| Topic | Where | Need |
|---|---|---|
| Existing eval surface | `mslearn/pipeline/trust.py`, `extraction_graph.py`, `synthesis.py`, `mslearn/opsdb.py` (tunables/audit), `mslearn/prompts.py` | what gets measured/tuned |
| Spec §6 + self-evolution paragraph | `docs/superpowers/specs/2026-07-02-multi-source-learning-system-design.md` | gates + evolution contract |
| rapidfuzz token_set_ratio | https://rapidfuzz.github.io/RapidFuzz/Usage/fuzz.html | golden claim matching |

## Global Constraints

- **Release gates (spec, exact):** extraction precision ≥ 0.90, recall ≥ 0.85; grounding false-accept ≤ 0.02; clustering F1 ≥ 0.80; tension accuracy ≥ 0.75; schema validity ≥ 0.99; memory-provenance violations = 0 (deterministic part). `GATES` constants in `gates.py` — never tunables.
- Evolution may adjust ONLY registered tunables within per-key bounds (`TUNABLE_BOUNDS` in evolve.py) and prompt overrides; a candidate is applied to live settings ONLY after shadow evaluation shows: target metric improves AND no gate metric regresses. Every application via `set_tunable(..., reason="evolve run <id>: <why>")` / `set_setting("prompt:...")` with an `evolution_runs` row. Rollback = restore previous value from audit history (endpoint).
- Eval model calls: role `evals`. Component metrics that need no model are pure/deterministic and run offline in CI with fixture golden sets.
- All eval results durable: `eval_runs` (id, ts, kind, git_sha, passed) + `eval_metrics` (run_id, metric, value, gate, passed) in OpsDB.
- Tests: metric functions unit-tested against tiny fixture golden sets + fakes (offline); judged/seeding paths tested with ScriptedRouter; one `neo4j`-marked smoke; live full run is a manual/CI-optional command.
- Prior suite green; ruff clean; commit per task.

---

### Task 1: Golden-set format + loaders + fixtures

`evals/golden/` (repo dir) + `mslearn/evals/golden.py` + `tests/test_golden.py`.

JSONL record shapes (one dataclass each in golden.py, with `load_golden(kind) -> list`, `save_golden(kind, records)`, strict validation raising `GoldenFormatError`):
- `extraction.jsonl`: `{chunk_text, expected_claims: [{text, stance}], source_type}`
- `grounding.jsonl`: `{chunk_text, claim_text, quote, valid: bool}` (valid=False rows are fabricated/mis-cited quotes)
- `clustering.jsonl`: `{text_a, text_b, same_concept: bool}`
- `tension.jsonl`: `{claim_a, claim_b, domain_profile, classification}` (taxonomy-validated)
- Review status field on every record: `{"review": "pending"|"approved"|"corrected"}` — only `approved`/`corrected` rows count in metrics.
Ship tiny starter fixtures (5–8 rows each, hand-written plausible content, review=approved) so CI metrics run offline: `evals/golden/*.jsonl` committed.
**Commit:** `feat(evals): golden-set formats, loaders, starter fixtures`

### Task 2: Seeding pipeline + review API

`mslearn/evals/seed.py` + `mslearn/server/routers/evals.py` (review part) + tests.
- `seed_extraction(ctx, n_chunks=50)`: sample chunks across source types from graph → role `evals` labels candidate claims (schema like extraction) → append as `review="pending"` rows. Similar `seed_grounding` (generate one valid + one perturbed-quote row per sampled claim), `seed_clustering` (near-neighbor claim pairs via vector search + random far pairs), `seed_tension` (existing CONFLICTS_WITH pairs + judge-proposed labels).
- Review endpoints: `GET /api/evals/golden/{kind}?status=pending` (queue), `POST /api/evals/golden/{kind}/{index}` body `{action: approve|correct|reject, corrected?: {...}}` (reject deletes; correct replaces payload, marks corrected). (Frontend panel optional — endpoints suffice; note for user.)
**Tests:** seeding with ScriptedRouter appends pending rows of valid shape; review transitions; metrics loaders exclude pending.
**Commit:** `feat(evals): golden-set seeding and review queue API`

### Task 3: Deterministic + component metrics

`mslearn/evals/metrics.py` + tests (offline, fixture golden sets, fakes/ScriptedRouter where a pipeline function needs a model).
- `extraction_pr(ctx) -> {precision, recall}`: for each approved extraction row, run `run_extraction` (ScriptedRouter in tests; live router in real runs) on chunk_text; match predicted↔expected claims greedily by `fuzz.token_set_ratio >= 80` AND stance equal; P/R over all rows.
- `grounding_rates(ctx) -> {false_accept, false_reject}`: run `check_claim` (current tunables) on each grounding row; false_accept = fraction of `valid=False` rows passing.
- `clustering_f1(ctx)`: for each pair, embed both (`router.embed`), plus judge `concept_match` verdict exactly as `cluster_new_claims` does (factor the pair-verdict into a reusable helper in synthesis.py if not already callable in isolation — small refactor allowed, keep public API); F1 vs same_concept.
- `tension_accuracy(ctx)`: judge `conflict_scan` on each pair with row's domain profile; accuracy of classification.
- `schema_validity(ctx)`: from model_calls log — `1 - (BadOutput errors / extraction+synthesis calls)`; plus live-graph `quote_match_rate` and `chunk_coverage` (claims-bearing chunks / done chunks) as informational metrics.
**Commit:** `feat(evals): component metrics over golden sets + deterministic log metrics`

### Task 4: Judged evals + memory-provenance gate

`mslearn/evals/judged.py` + `PROMPTS` additions (`rubric_teach`, `rubric_answer`, `provenance_check`) + tests.
- `judge_teaching(ctx, n=5)`: sample curriculum concepts → `generate_teaching` → role `evals` rubric score JSON `{clarity_1_5, grounding_1_5, tension_handled: bool}`; aggregate.
- **Provenance gate** two layers: deterministic `provenance_citations(md, ctx) -> violations` (every `[claim:<id>]` in output must exist in graph AND belong to the concept; any factual paragraph with zero citations = violation — heuristic: paragraphs outside `## Worked example` lacking any citation chip count) + judged spot check (`provenance_check` prompt: "does any factual statement lack support from the provided claims?" bool + offending sentence).
- `judge_answers(ctx, questions: list)` same pattern over `qa` path.
**Tests:** provenance deterministic layer with crafted markdown (valid, unknown-id, uncited-paragraph); judged paths scripted.
**Commit:** `feat(evals): rubric-judged quality + two-layer memory-provenance gate`

### Task 5: Gates + runner + report API

`mslearn/evals/gates.py`, `mslearn/evals/runner.py`, opsdb tables (`eval_runs`, `eval_metrics`), CLI `python -m mslearn.evals.run [--component X] [--offline]`, endpoints `GET /api/evals/report` (latest run: metrics vs gates), `GET /api/evals/history`; pytest marker `evals` entry (`tests/test_eval_gates.py -m evals` live path optional) + offline unit tests of gate math + runner persistence with fake metrics.
```python
GATES = {
    "extraction.precision": (">=", 0.90), "extraction.recall": (">=", 0.85),
    "grounding.false_accept": ("<=", 0.02), "clustering.f1": (">=", 0.80),
    "tension.accuracy": (">=", 0.75), "schema.validity": (">=", 0.99),
    "provenance.violations": ("==", 0),
}
```
Runner: computes all (component selection flag), writes run+metrics rows with git sha, prints table, exit 0/1 on gates.
**Commit:** `feat(evals): release gates, persistent runner, report API`

### Task 6: Self-evolution loop

`mslearn/evals/evolve.py` + opsdb `evolution_runs` table + `PROMPTS["evolve_propose"]` + endpoints (`POST /api/evals/evolve`, `GET /api/evals/evolve/history`, `POST /api/admin/tunables/{key}/rollback`) + tests.
```python
TUNABLE_BOUNDS = {
    "trust.quote_threshold": (70.0, 98.0),
    "trust.embed_sim_threshold": (0.1, 0.8),
    "extract.max_attempts": (1.0, 4.0),
    "synth.candidate_k": (3.0, 20.0),
    "synth.similarity_floor": (0.5, 0.95),
    "monitor.failure_rate_threshold": (0.2, 0.9),
    "monitor.min_chunks": (5.0, 50.0),
}
```
`evolve_once(ctx) -> dict`:
1. latest eval run (require one; else run components first)
2. failing/weakest metrics + current tunables + last N audit rows → role `evals` `evolve_propose` (schema: `{"proposals": [{"kind": "tunable"|"prompt", "key", "value"|"new_prompt", "targets_metric", "why"}], max 3}`)
3. per proposal: validate (known key, in bounds / prompt key exists, `{placeholders}` preserved — reuse a `required_placeholders(name)` check) else drop+log
4. **shadow evaluation**: apply candidate to a THROWAWAY OpsDB clone (copy tunables into a temp OpsDB, or implement `ctx_with_overrides(ctx, tunables={...}, prompts={...})` — context whose db `get_tunable/get_setting` consults an overlay first; implement overlay wrapper class in evolve.py) → recompute ONLY `targets_metric` + all gate metrics that could plausibly regress for that key (map per key: trust.* → extraction+grounding; synth.* → clustering+tension; prompt:extraction → extraction+grounding+schema; be conservative: recompute all component metrics — golden sets are small)
5. accept iff target improves strictly AND every gate metric ≥ its previous value (or still passes gate); then apply live (`set_tunable`/`set_setting`) with reason `evolve run <run_id>: <why>`; record `evolution_runs` row (proposal, shadow before/after, accepted bool)
6. return summary
Rollback endpoint: `tunable_history(key)` → set previous value with reason "rollback via api".
CLI `python -m mslearn.evals.evolve --once`.
**Tests (offline, fixture golden sets + scripted judge):** bounds/placeholder validation drops invalid proposals; overlay context isolates shadow (live db untouched on rejection); accept path writes audit with evolve reason + evolution_runs row; reject path when shadow shows regression (scripted metrics via monkeypatched metric fns — acceptable seam); rollback restores prior audit value.
**Commit:** `feat(evals): eval-gated self-evolution loop with shadow evaluation, bounds, audit, rollback`

### Task 7: Final verification harness + README + spec checklist

`scripts/release_check.sh` (services up → suite → `-m neo4j` → `mslearn.evals.run` → summary), README "Definition of done" section mapping spec verification steps 1–6 to commands, smoke corpus note (`tests/fixtures/blog.html` + tiny generated pdf via a tiny `scripts/make_smoke_corpus.py`), ledger-style CHANGELOG touch. Run everything; report which spec verification items pass in THIS environment and which need live backends (list them explicitly in your summary).
**Commit:** `chore(evals): release check harness and definition-of-done docs`

## Self-Review (write-time)
- Gates = constants, evolution bounded + shadow-gated + audited + rollback: matches amended spec §6 exactly; evolution can never touch gate values or unbounded keys.
- Shadow isolation via overlay context avoids mutating live tunables during evaluation; tests pin the no-mutation-on-reject property.
- Golden review workflow keeps the human in the loop (only approved/corrected rows count) per spec ("seeded by strong model, corrected by user").
- Small starter fixtures ship in-repo so CI runs metrics offline; real golden sets grow via seeding+review against the user's corpus.
- Provenance gate two-layer (deterministic + judged) closes the memory-integrity eval promised in spec §6.
```
