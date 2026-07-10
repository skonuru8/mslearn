# Notes Quality + Synthesis Robustness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make study guides deliver real understanding (own-words grounded notes + a labeled model-analysis layer) while keeping the anti-hallucination guarantee, and make extraction + synthesis robust to deepseek-v4-flash's imperfect JSON so a 1-hour video no longer crashes synthesis or silently loses claims.

**Architecture:** Grounding = traceability, not verbatim. Grounded guide items get reworded but must cite their claim; interpretation items are labeled model reasoning, excluded from fact checks. Every synthesis model call degrades on failure instead of crashing the run; the provider salvages malformed JSON before erroring; concept-match parsing maps positional answers back to real claim ids.

**Tech Stack:** Python 3.12 / FastAPI / Celery / Neo4j / OpenRouter deepseek-v4-flash / pydantic / React+TS. New dep: `json-repair`.

## Global Constraints

- **Anti-hallucination preserved.** The trust gate on *claims* (extraction) is UNCHANGED. Every **grounded** guide item still cites a claim; **interpretation** items are labeled "model's analysis — not from your source" and never presented as source fact. The provenance eval (`provenance.violations`) must stay **0**.
- **No raw claim-id strings in the UI** (existing invariant) — interpretation items render without exposing claim ids.
- **Behavior of assignments unchanged** except where a spec part explicitly changes it (Part 4 recovers previously-dropped matches; Part 2 changes only failure paths).
- **Degrade, never crash:** no single model call may fail a synthesis run.
- **Values:** large-spine deps skip threshold = **60 concepts**; interpretation angles = `assumption|evidence|steelman|verdict|synthesis`; interpretation is **always on** (no depth toggle), omitted when a concept is too thin.
- TDD: failing test first. `make check` green before each commit. Baseline: **401 passed, 24 skipped**. Frontend: `make ui-test` green (baseline 46).

---

## File Structure

- `mslearn/providers/openrouter.py` — JSON salvage (Part 3).
- `pyproject.toml` — `json-repair` dep.
- `mslearn/pipeline/synthesis.py` — degrade each model call; skip deps for large spines (Part 2); positional-id mapping (Part 4).
- `mslearn/pipeline/guide.py` — `InterpretationItem`, `StudyGuide.interpretation`, schema, `drop_ungrounded` (Part 1).
- `mslearn/prompts.py` — rewrite `guide` prompt; tighten `concept_match` (Parts 1, 4).
- `mslearn/pipeline/guide_gen.py` — wire interpretation + `drop_ungrounded` (Part 1).
- `frontend/src/api/types.ts`, `frontend/src/components/InteractiveGuide.tsx`, `frontend/src/app.css` — render interpretation (Part 1).
- Tests: `tests/test_openrouter_provider.py`, `tests/test_synthesis_task.py`/`test_clustering.py`, `tests/test_guide.py`, `frontend/src/components/InteractiveGuide.test.tsx`.

---

## Task 1: JSON salvage in the OpenRouter provider (Part 3)

**Files:**
- Modify: `pyproject.toml` (add `json-repair>=0.30`)
- Modify: `mslearn/providers/openrouter.py` (`complete`, the `json.loads(text)` block ~line 117-126)
- Test: `tests/test_openrouter_provider.py` (extend)

**Interfaces:**
- Produces: when `request.json_schema is not None` and the raw text is malformed JSON, the provider attempts `json-repair` before raising `ProviderBadOutputError`.

- [ ] **Step 1: Failing test** (respx like the existing provider tests): mock a completion whose `content` is malformed but repairable — e.g. `'{"claims":[{"text":""Everyone has their price"","kind":"claim","quote":"x","stance":"neutral"}]}'` (the real unescaped-quote failure) or a truncated tail. Assert `complete(...).parsed` returns a dict with the recovered structure instead of raising.

```python
@respx.mock
def test_complete_salvages_malformed_json():
    bad = '{"claims": [{"text": "a" "b", "kind": "claim"}]}'  # invalid; repairable
    respx.post(OR_URL).mock(return_value=httpx.Response(200, json={
        "choices": [{"message": {"content": bad}, "finish_reason": "stop"}]}))
    resp = OpenRouterProvider("k").complete("m", ModelRequest(messages=[...], json_schema={"type":"object"}))
    assert isinstance(resp.parsed, dict)   # salvaged, not raised
```

- [ ] **Step 2: Run — FAIL** (currently raises `ProviderBadOutputError`).

- [ ] **Step 3: Implement.** Install `json-repair` into the venv (`.venv/bin/pip install "json-repair>=0.30"`). In `openrouter.py`, in the `except json.JSONDecodeError` branch, before raising:

```python
            except json.JSONDecodeError as exc:
                from json_repair import repair_json
                try:
                    salvaged = repair_json(text, return_objects=True)
                except Exception:
                    salvaged = None
                if isinstance(salvaged, (dict, list)) and salvaged != {} and salvaged != []:
                    parsed = salvaged
                else:
                    finish_reason = choice.get("finish_reason")
                    raise ProviderBadOutputError(
                        f"invalid JSON from openrouter (finish_reason={finish_reason!r}): {text[:200]!r}"
                    ) from exc
```
(Keep the existing empty-content/`finish_reason=length` guards above unchanged.)

- [ ] **Step 4: Run — PASS**; `make check` (baseline 401 → 402+).

- [ ] **Step 5: Commit** `feat(providers): salvage malformed JSON before failing (json-repair)`.

---

## Task 2: Fault-tolerant synthesis calls + large-spine deps skip (Part 2 / P1)

**Files:**
- Modify: `mslearn/pipeline/synthesis.py` (`_compute_anchor_matches`, `_process_one_concept`, `build_curriculum`)
- Test: `tests/test_clustering.py`, `tests/test_synthesis_task.py`

**Interfaces:**
- Produces: each synthesis model call degrades on `ProviderError` (base class of `ProviderBadOutputError`/`ProviderTransientError` — confirm in `mslearn/providers/base.py`): `concept_match`→no match; `conflict_scan`→no conflicts; `concept_name`→fallback name; `concept_deps`→skipped (natural order) on failure OR when `len(spine_ids) > 60`.

- [ ] **Step 1: Failing tests:**
  - `test_cluster_survives_bad_match_response`: a router whose `concept_match` call raises `ProviderBadOutputError`; assert `cluster_new_claims` still completes and the anchor gets its own concept (no exception).
  - `test_process_dirty_survives_bad_conflict_and_name`: router raising on `conflict_scan` and/or `concept_name`; assert `process_dirty_concepts` completes, the concept still gets a (fallback) name, no exception.
  - `test_build_curriculum_survives_bad_deps`: router raising on `concept_deps`; assert `build_curriculum` returns a natural-order list (no exception).
  - `test_build_curriculum_skips_deps_for_large_spine`: 61 spine concepts + a spy router; assert `concept_deps` is NOT called and curriculum still returns ordered.

- [ ] **Step 2: Run — FAIL** (calls currently propagate).

- [ ] **Step 3: Implement.** Import `ProviderError` from `mslearn.providers.base`. Wrap:
  - In `_compute_anchor_matches`: `try: response = ctx.router.complete(...) except ProviderError: return anchor_id, candidates, [], 0`.
  - In `_process_one_concept`: wrap the conflict-scan call `try/except ProviderError` → skip the conflict loop (treat as no conflicts); wrap the name call `try/except ProviderError` → set `parsed_name = {}` so the fallback name path runs. Ensure there IS a fallback name when `name` comes back empty: if `str(parsed_name.get("name",""))` is empty, use a deterministic fallback (first claim's first ~6 words, else `"Untitled concept"`). Do not emit a raw concept id as the name.
  - In `build_curriculum`: change the guard to `if 2 <= len(spine_ids) <= 60:` and wrap the `concept_deps` `ctx.router.complete(...)` in `try/except ProviderError` → skip the edge loop (deps stays whatever was already in the graph; topo-sort falls back to first_seq).

- [ ] **Step 4: Run — PASS**; `make check`; run `tests/test_synthesis_task.py` (end-to-end synthesis still green).

- [ ] **Step 5: Commit** `fix(synthesis): degrade each model call instead of crashing; skip deps for large spines`.

---

## Task 3: concept_match positional-id mapping + prompt tighten (Part 4 / P3)

**Files:**
- Modify: `mslearn/pipeline/synthesis.py` (`_compute_anchor_matches` match validation; `concept_match_claim_ids` helper)
- Modify: `mslearn/prompts.py` (`concept_match` prompt)
- Test: `tests/test_clustering.py`

**Interfaces:**
- Produces: a returned match value that is a valid 1-based index into the presented candidate list maps to that candidate's claim id (counted as a match, not dropped). Genuinely stray values still drop + warn.

- [ ] **Step 1: Failing test** `test_concept_match_maps_positional_ids`: candidates `[c-a, c-b, c-c]`; model returns `{"matches": ["2"]}`; assert the anchor matches `c-b` (index 2 → second candidate), NOT dropped.

- [ ] **Step 2: Run — FAIL** ("2" not in candidate_ids → dropped today).

- [ ] **Step 3: Implement** a helper `_resolve_match(value, candidate_ids)`: if `value in candidate_ids` → value; elif `value` is an int-parseable `1..len` → `candidate_ids[int(value)-1]`; else → None (drop+warn). Use it in both `_compute_anchor_matches` and `concept_match_claim_ids`. Tighten the `concept_match` prompt: state candidates are listed with ids and the model MUST return the exact `claim_id` strings, never the list number.

- [ ] **Step 4: Run — PASS**; `make check`.

- [ ] **Step 5: Commit** `fix(synthesis): map concept_match positional answers to claim ids`.

---

## Task 4: Guide data model — interpretation layer + `drop_ungrounded` (Part 1)

**Files:**
- Modify: `mslearn/pipeline/guide.py`
- Test: `tests/test_guide.py`

**Interfaces:**
- Produces: `INTERPRETATION_ANGLES = ("assumption","evidence","steelman","verdict","synthesis")`; `InterpretationItem(BaseModel){angle:str (validated), text:str, claims:list[str]=[]}`; `StudyGuide.interpretation: list[InterpretationItem] = []`; `GUIDE_SCHEMA` includes `interpretation`; `drop_ungrounded(guide)` replaces `drop_uncited` — drops **grounded** section items lacking citations (as before, incl. tl_dr backfill/blank), but KEEPS all interpretation items regardless of citation.

- [ ] **Step 1: Failing tests** in `tests/test_guide.py`:
  - `test_drop_ungrounded_keeps_interpretation`: a guide with one uncited section item (dropped) and one uncited interpretation item (kept). Assert the section item is gone and the interpretation item survives.
  - `test_interpretation_angle_validated`: an unknown angle raises `GuideParseError` via `parse_guide`.
  - Keep/rename existing `drop_uncited` tests to `drop_ungrounded` (section-drop + tl_dr backfill behavior unchanged for grounded items).

- [ ] **Step 2: Run — FAIL** (`InterpretationItem`/`drop_ungrounded` missing).

- [ ] **Step 3: Implement** the model, angle validator, `StudyGuide.interpretation`, `GUIDE_SCHEMA` addition (interpretation array of `{angle:enum, text:string, claims:array}`, `interpretation` NOT in top-level `required` so old caches/thin concepts parse), and `drop_ungrounded` (copy `drop_uncited` logic for sections + tl_dr, then pass `guide.interpretation` through untouched). Keep a thin `drop_uncited = drop_ungrounded` alias only if other modules import it — otherwise update callers in Task 5.

- [ ] **Step 4: Run — PASS**; `make check`.

- [ ] **Step 5: Commit** `feat(guide): interpretation layer model + drop_ungrounded gate`.

---

## Task 5: Guide prompt rewrite + generation wiring (Part 1)

**Files:**
- Modify: `mslearn/prompts.py` (`guide` prompt)
- Modify: `mslearn/pipeline/guide_gen.py` (use `drop_ungrounded`; ensure interpretation flows through; provenance-safe)
- Test: `tests/test_guide_gen.py` (or wherever guide_gen is tested), plus a provenance check

**Interfaces:**
- Consumes: `GUIDE_SCHEMA` (now with interpretation), `drop_ungrounded`.
- Produces: guides whose grounded items are reworded (own words) + cite claims, plus an interpretation block; `generate_guide` still returns `(dict, cached_bool)` and caches JSON in `teach_md`.

- [ ] **Step 1: Failing test**: a fake router returning a guide payload with (a) grounded items whose `text` differs from the claim text but cite a claim id, and (b) an interpretation block. Assert `generate_guide` returns data where `data["interpretation"]` is present and grounded items survive; assert `guide_gen` calls `drop_ungrounded` (not the old `drop_uncited`). Add `test_guide_gen_interpretation_survives_when_uncited`.

- [ ] **Step 2: Run — FAIL**.

- [ ] **Step 3: Implement.** Rewrite the `guide` prompt in `prompts.py`:
  - Grounded sections: "Rewrite each claim IN YOUR OWN WORDS explaining what it means and why it matters — never copy the source wording or a near-paraphrase (swapped synonyms). Set `text` to your rewrite and `claims` to the claim id(s) it rests on. Every supplied claim id must be covered by exactly one grounded item."
  - Interpretation: "Also produce an `interpretation` array — your analysis of these claims: pick the angles that yield real content from {assumption, evidence, steelman, verdict, synthesis}; `text` is your reasoning; you MAY reference claim ids in `claims` but need not. Omit an angle (or the whole array) if the concept is too thin — never pad. This is YOUR analysis, presented to the reader as such, not as source fact."
  - Keep tl_dr + skeleton + open_questions rules.
  In `guide_gen.py`, replace `drop_uncited` with `drop_ungrounded`; keep `_disagreements` attach; keep cache write.

- [ ] **Step 4: Provenance gate check.** Read `mslearn/evals/metrics.py` provenance metric (`grep -n "provenance" mslearn/evals/metrics.py`). Confirm it treats a cited-but-reworded grounded item as grounded and does NOT verbatim-match guide text against claims (interpretation items must be excluded from fact checks). If it verbatim-matches (which reworded grounded text would now break), adjust the metric to check citation-traceability (grounded item has ≥1 valid claim id) rather than string equality, and to skip interpretation items. Add/adjust a test so `provenance.violations == 0` for a reworded-but-cited guide.

- [ ] **Step 5: Run — PASS**; `make check`.

- [ ] **Step 6: Commit** `feat(guide): own-words grounded notes + interpretation generation`.

---

## Task 6: Frontend — render the interpretation layer (Part 1)

**Files:**
- Modify: `frontend/src/api/types.ts` (`InterpretationItem` + `StudyGuide.interpretation`)
- Modify: `frontend/src/components/InteractiveGuide.tsx` (render a labeled interpretation block)
- Modify: `frontend/src/app.css` (distinct interpretation styling)
- Test: `frontend/src/components/InteractiveGuide.test.tsx`

**Interfaces:**
- Consumes: `StudyGuide.interpretation: InterpretationItem[]`.
- Produces: an `InterpretationBlock` rendered after the grounded sections, clearly labeled "Model's analysis — not from your source", each item showing its angle + text. No raw claim-id strings.

- [ ] **Step 1: Failing test** in `InteractiveGuide.test.tsx`: render a guide with an interpretation item (angle `verdict`, text "…"); assert the "Model's analysis" label and the item text appear, and that no raw claim-id-looking string is in the DOM (reuse the existing no-raw-id lock-in pattern in that test file).

- [ ] **Step 2: Run — FAIL** (`make ui-test`).

- [ ] **Step 3: Implement.** Add `InterpretationItem` interface + `interpretation: InterpretationItem[]` to `StudyGuide` in types.ts. Add an `InterpretationBlock` component (mirror `SectionCard` styling but a distinct class, e.g. `guide-interpretation`, with the label header and an angle chip per item). Render it in the guide after sections. Add `.guide-interpretation` styles in app.css (visually separated — e.g. tinted panel + "analysis" accent, consistent with the existing kind-color system).

- [ ] **Step 4: Run — PASS** (`make ui-test`, baseline 46 → 47+).

- [ ] **Step 5: Commit** `feat(ui): render labeled interpretation layer in the study guide`.

---

## Task 7: Docs + full verification

**Files:** `README.md`.

- [ ] **Step 1:** Update the guide/notes description in README: guides now give own-words grounded notes (each cited; verbatim quote in Sources) plus a labeled "model's analysis" interpretation layer; note synthesis is now fault-tolerant (a bad model response degrades that piece, never the whole run) and deps ordering is skipped for very large sources.
- [ ] **Step 2:** `make check` (all backend green) + `make ui-test` (frontend green).
- [ ] **Step 3:** Re-run evals (`grep` the eval entrypoint) and record: `provenance.violations == 0`, grounding false-accept ≤ gate, and note extraction recall (salvage should help). If any regressed, STOP and report.
- [ ] **Step 4:** Manual smoke (user-run): re-ingest the 1-hour video → synthesis completes (no crash), guides appear; a concept reads as own-words understanding + a labeled analysis block; Sources footer still shows verbatim quotes.
- [ ] **Step 5: Commit** `docs: two-layer notes + synthesis robustness`.

---

## Self-Review

- **Spec coverage:** Part 1=Tasks 4/5/6, Part 2=Task 2, Part 3=Task 1, Part 4=Task 3; docs/verify=Task 7. All mapped.
- **Provenance invariant:** explicitly gated in Task 5 Step 4 (grounded=cited, interpretation=labeled-excluded) with a required 0-violations check.
- **Ordering:** robustness first (1→2→3) so materials appear, then notes (4→5→6). Task 5 depends on Task 4's `drop_ungrounded`/schema; Task 6 depends on Task 5's payload shape.
- **Type consistency:** `InterpretationItem{angle,text,claims}`, `StudyGuide.interpretation`, `drop_ungrounded`, `_resolve_match`, deps threshold 60 — consistent across tasks and mirrored in frontend types.
- **Placeholder scan:** provider salvage, degradation wraps, positional mapping, prompt text, and frontend block all specified with concrete code/behavior; no TBDs.
