# Notes Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Group notes into model-derived categories, make opening a note deliver genuinely deeper grounded explanation, and remove the interpretation block, open-questions block, the `TL;DR` label, and the triple-repeated one-liner.

**Architecture:** Backend generates a `category` per concept during synthesis (one bounded model call) and writes deeper guide sections via a rewritten prompt; the guide schema drops `open_questions`/`interpretation` from output while keeping the pydantic fields optional for cached back-compat. Frontend groups the curriculum by category and strips the removed blocks.

**Tech Stack:** Python (FastAPI, pydantic, custom graph store), pytest; React + TypeScript + Vite, vitest.

## Global Constraints

- Every guide section item must remain tied to claim id(s); no invented facts, text, or claim ids. Depth is bounded by supplied claims — never pad with generic filler.
- No SQL migration: the `Concept` graph node simply gains a `category` property.
- Cached guides persisted before this change (JSON containing `open_questions`/`interpretation`) MUST still parse.
- Category generation must never crash synthesis: on bad model output or too many concepts, leave categories empty and render flat.
- Backend tests: `pytest tests/<file> -v`. Frontend tests: `cd frontend && npx vitest run <file>`.

---

### Task 1: Drop open_questions + interpretation from guide output schema

**Files:**
- Modify: `mslearn/pipeline/guide.py` (GUIDE_SCHEMA properties + `required`; `drop_ungrounded` comment)
- Test: `tests/test_guide_contract.py`, `tests/test_guide_gen.py`

**Interfaces:**
- Produces: `StudyGuide` still has optional `open_questions: list[str] = []` and `interpretation: list[InterpretationItem] = []` (kept for cached back-compat), but `GUIDE_SCHEMA` no longer declares them and no longer requires `open_questions`.

- [ ] **Step 1: Write failing test** in `tests/test_guide_contract.py`:

```python
def test_guide_schema_omits_open_questions_and_interpretation():
    from mslearn.pipeline.guide import GUIDE_SCHEMA
    props = GUIDE_SCHEMA["properties"]
    assert "open_questions" not in props
    assert "interpretation" not in props
    assert "open_questions" not in GUIDE_SCHEMA["required"]


def test_cached_guide_with_legacy_fields_still_parses():
    from mslearn.pipeline.guide import parse_guide
    legacy = {
        "concept_id": "c", "title": "T",
        "tl_dr": {"text": "x", "claims": ["c1"]},
        "skeleton": ["S"], "sections": [], "disagreements": [],
        "open_questions": ["old q"],
        "interpretation": [{"angle": "verdict", "text": "y", "claims": []}],
    }
    guide = parse_guide(legacy)
    assert guide.title == "T"
```

- [ ] **Step 2: Run to verify fail:** `pytest tests/test_guide_contract.py -v` → FAIL (open_questions still in props).

- [ ] **Step 3: Edit `GUIDE_SCHEMA`** in `mslearn/pipeline/guide.py`: delete the `"open_questions": {...}` and `"interpretation": {...}` entries from `properties`, and remove `"open_questions"` from `"required"` (final required: `["concept_id", "title", "tl_dr", "skeleton", "sections"]`). Leave the `StudyGuide` pydantic fields and `INTERPRETATION_ANGLES`/`InterpretationItem` as-is. In `drop_ungrounded`, delete the two-line comment about interpretation preservation (lines ~115-116) — it is now moot but the code is unchanged.

- [ ] **Step 4: Run:** `pytest tests/test_guide_contract.py -v` → PASS.

- [ ] **Step 5:** Update `tests/test_guide_gen.py`: delete `test_generate_guide_includes_interpretation` and `test_generate_guide_uses_drop_ungrounded`'s interpretation assertions (keep the grounded-items assertions; drop the two `assert ... interpretation ...` lines and the `payload["interpretation"] = [...]` setup). Run `pytest tests/test_guide_gen.py -v` → PASS.

- [ ] **Step 6: Commit** `git add -A && git commit -m "feat(guide): drop open_questions + interpretation from output schema"`

---

### Task 2: Rewrite the guide prompt for depth, remove interpretation/open_questions instructions

**Files:**
- Modify: `mslearn/prompts.py` (`PROMPTS["guide"]`)
- Test: `tests/test_guide_gen.py`

**Interfaces:**
- Produces: `PROMPTS["guide"]` keeps placeholders `{domain_guidance}`, `{concept_name}`, `{concept_summary}`, `{claims}`, `{memory_hints}` (evolve.py validates placeholders survive).

- [ ] **Step 1: Replace `test_guide_prompt_requests_interpretation_layer`** in `tests/test_guide_gen.py` with:

```python
def test_guide_prompt_asks_for_depth_and_drops_interpretation():
    prompt = PROMPTS["guide"].lower()
    assert "interpretation" not in prompt
    assert "open_questions" not in prompt
    assert "own words" in prompt
    # depth cues
    assert "why" in prompt and "example" in prompt
```

- [ ] **Step 2: Run:** `pytest tests/test_guide_gen.py::test_guide_prompt_asks_for_depth_and_drops_interpretation -v` → FAIL.

- [ ] **Step 3: Rewrite `PROMPTS["guide"]`** in `mslearn/prompts.py`. Keep the header, domain_guidance, concept/summary/claims/memory lines and the JSON-schema instruction. Replace the rules block with:

```
"Return JSON only matching the schema. Rules:\n"
"- For each claim, write a section item that EXPLAINS it in your own words:"
" what it means, why it holds, how it connects to the concept and to the"
" other claims, and — where a claim supports one — a concrete example."
" Several sentences, not a one-line restatement, and never a near-paraphrase"
" of the source wording. Set 'text' to your explanation, 'kind' to the"
" claim's kind, and 'claims' to the id(s) it rests on.\n"
"- Every supplied claim id must be covered by exactly one grounded item."
" Never invent items, text, or claim ids. Go deep on what the claims"
" support; never pad with generic filler or facts beyond them.\n"
"- Group items into 2-6 sections; 'skeleton' lists section titles in order."
" Give each section a short id (s1,s2,...).\n"
"- 'tl_dr.text' is one plain orienting sentence citing the 1-2 claim ids it"
" rests on in tl_dr.claims.\n"
```

Delete the old `open_questions` and `interpretation` bullets entirely.

- [ ] **Step 4: Run:** `pytest tests/test_guide_gen.py -v` → PASS (all).

- [ ] **Step 5: Commit** `git commit -am "feat(guide): deeper own-words sections, remove interpretation/open-questions prompt"`

---

### Task 3: Add `category` to the concept store

**Files:**
- Modify: `mslearn/graph/store.py` (`get_concept`, `all_concepts`, `curriculum`, `set_concept_meta`, new `set_concept_categories`)
- Modify: `tests/fakes.py` (`InMemoryGraphStore`: mirror `category` in the same methods + add `set_concept_categories`)
- Test: `tests/test_graph_store.py`

**Interfaces:**
- Produces:
  - `store.set_concept_meta(concept_id, ..., category: str | None = None, *, project_id=...)`
  - `store.set_concept_categories(pairs: list[tuple[str, str]], *, project_id=...)` — bulk `(concept_id, category)` write.
  - `get_concept`/`all_concepts`/`curriculum` rows include `category` (coalesced to `""`).

- [ ] **Step 1: Write failing test** in `tests/test_graph_store.py` (follow existing fixture pattern for a real `GraphStore`; if tests there use an in-memory neo, mirror it):

```python
def test_set_and_read_concept_category(graph_store):
    from mslearn.graph.records import ConceptRecord
    graph_store.upsert_concept(ConceptRecord(concept_id="k1", name="N"))
    graph_store.set_concept_categories([("k1", "Numbers")])
    assert graph_store.get_concept("k1")["category"] == "Numbers"
    rows = {c["concept_id"]: c for c in graph_store.all_concepts()}
    assert rows["k1"]["category"] == "Numbers"
```

- [ ] **Step 2: Run** `pytest tests/test_graph_store.py -k category -v` → FAIL.

- [ ] **Step 3: Implement** in `mslearn/graph/store.py`:
  - `get_concept`: add `k.category AS category` (use `coalesce(k.category, '') AS category`) to RETURN.
  - `all_concepts`: same addition to RETURN.
  - `curriculum`: add `coalesce(k.category, '') AS category` to RETURN (it aggregates, so add to RETURN list, unaffected by the `count(c)`).
  - `set_concept_meta`: add param `category: str | None = None`; when not None append `"k.category = $category"` + `params["category"] = category`.
  - Add:

```python
    def set_concept_categories(self, pairs, *, project_id: str = "default") -> None:
        if not pairs:
            return
        rows = [{"concept_id": cid, "category": str(cat)} for cid, cat in pairs]
        self.run_write(
            "UNWIND $rows AS row "
            "MATCH (k:Concept {concept_id: row.concept_id, project_id: $project_id}) "
            "SET k.category = row.category",
            rows=rows, project_id=project_id,
        )
```

  - Mirror all of the above in `tests/fakes.py::InMemoryGraphStore` (store `category` on the concept dict, default `""`, and return it from `get_concept`/`all_concepts`/`curriculum`).

- [ ] **Step 4: Run** `pytest tests/test_graph_store.py -k category -v` → PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(store): concept category property + bulk write"`

---

### Task 4: `assign_categories` synthesis step + `concept_categories` prompt

**Files:**
- Modify: `mslearn/prompts.py` (new `PROMPTS["concept_categories"]`)
- Modify: `mslearn/pipeline/synthesis.py` (new `assign_categories`, `_concept_categories_prompt`, schema const)
- Test: `tests/test_synthesis_task.py` or new `tests/test_categories.py`

**Interfaces:**
- Consumes: `ctx.graph.all_concepts`, `ctx.graph.set_concept_categories`, `ctx.router.complete("synthesis", ...)`, `get_prompt(db, "concept_categories")`.
- Produces: `assign_categories(ctx, project_id: str = "default") -> int` — returns number of concepts categorized (0 on skip/failure).

- [ ] **Step 1: Add prompt** to `PROMPTS` in `mslearn/prompts.py`:

```python
    "concept_categories": (
        "You group numbered study concepts into a few coherent categories.\n"
        "Return JSON only: {\"categories\": [{\"name\": \"<2-4 words>\","
        " \"concept_ids\": [\"...\"]}]}\n"
        "Rules:\n"
        "- Use only the provided concept ids; assign each to exactly one category.\n"
        "- Aim for 2-8 categories; group by subject, not by order.\n"
        "- Category names are short and human-readable.\n"
    ),
```

- [ ] **Step 2: Write failing test** in `tests/test_categories.py`:

```python
from mslearn.pipeline.synthesis import assign_categories
from tests.fakes import InMemoryGraphStore, ScriptedRouter
from mslearn.graph.records import ConceptRecord
from mslearn.opsdb import OpsDB
from mslearn.settings import Settings
from mslearn.worker.context import PipelineContext
from pathlib import Path


def _ctx(tmp_path, outputs):
    g = InMemoryGraphStore()
    for cid in ("k1", "k2", "k3"):
        g.upsert_concept(ConceptRecord(concept_id=cid, name=f"Name {cid}"))
        g.set_concept_meta(cid, order_index=int(cid[-1]))
    return PipelineContext(
        settings=Settings(profiles_path=Path("profiles.yaml")),
        db=OpsDB(tmp_path / "o.db"), router=ScriptedRouter(outputs=outputs),
        graph=g, memory=None,
    )


def test_assign_categories_persists(tmp_path):
    ctx = _ctx(tmp_path, [{"categories": [
        {"name": "Alpha", "concept_ids": ["k1", "k2"]},
        {"name": "Beta", "concept_ids": ["k3"]},
    ]}])
    n = assign_categories(ctx)
    assert n == 3
    cats = {c["concept_id"]: c["category"] for c in ctx.graph.all_concepts()}
    assert cats == {"k1": "Alpha", "k2": "Alpha", "k3": "Beta"}


def test_assign_categories_bad_output_leaves_empty(tmp_path):
    from mslearn.providers.base import ProviderBadOutputError
    class Boom(ScriptedRouter):
        def complete(self, role, request):
            raise ProviderBadOutputError("truncated")
    ctx = _ctx(tmp_path, [])
    ctx = ctx.__class__(**{**ctx.__dict__, "router": Boom(outputs=[])})
    assert assign_categories(ctx) == 0
    assert all(c["category"] == "" for c in ctx.graph.all_concepts())
```

- [ ] **Step 3: Run** `pytest tests/test_categories.py -v` → FAIL.

- [ ] **Step 4: Implement** in `mslearn/pipeline/synthesis.py`:

```python
_CONCEPT_CATEGORIES_SCHEMA = {"type": "object", "properties": {"categories": {"type": "array"}}}
_MAX_CATEGORIZE_CONCEPTS = 200


def _concept_categories_prompt(base: str, concepts: list[dict]) -> str:
    lines = [base, "", "Concepts:"]
    for idx, c in enumerate(concepts, start=1):
        lines.append(f"{idx}. {c['concept_id']} | {c.get('name', '')}")
    return "\n".join(lines)


def assign_categories(ctx, project_id: str = "default") -> int:
    graph = ctx.graph
    db = ctx.db
    concepts = [c for c in graph.all_concepts(project_id=project_id)]
    valid_ids = {c["concept_id"] for c in concepts}
    if not (2 <= len(concepts) <= _MAX_CATEGORIZE_CONCEPTS):
        return 0
    prompt = get_prompt(db, "concept_categories")
    try:
        resp = ctx.router.complete("synthesis", ModelRequest(
            messages=[ModelMessage(role="user",
                content=_concept_categories_prompt(prompt, concepts))],
            json_schema=_CONCEPT_CATEGORIES_SCHEMA,
            max_tokens=int(db.get_tunable("synth.max_tokens")),
        ))
    except ProviderBadOutputError:
        logger.warning("concept_categories call failed; leaving categories empty")
        return 0
    parsed = resp.parsed if isinstance(resp.parsed, dict) else {}
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for group in parsed.get("categories", []):
        if not isinstance(group, dict):
            continue
        name = str(group.get("name", "")).strip()
        if not name:
            continue
        for cid in group.get("concept_ids", []):
            if cid in valid_ids and cid not in seen:
                pairs.append((cid, name))
                seen.add(cid)
    if not pairs:
        return 0
    graph.set_concept_categories(pairs, project_id=project_id)
    return len(pairs)
```

- [ ] **Step 5: Run** `pytest tests/test_categories.py -v` → PASS.

- [ ] **Step 6: Commit** `git commit -am "feat(synthesis): assign_categories model pass + prompt"`

---

### Task 5: Wire `assign_categories` into the synthesis run

**Files:**
- Modify: `mslearn/worker/tasks.py` (import + call after `build_curriculum`, ~line 375)
- Test: `tests/test_synthesis_task.py`

- [ ] **Step 1: Add import** to the synthesis-imports block in `mslearn/worker/tasks.py`:

```python
    assign_categories,
```

- [ ] **Step 2: Call it** right after `ordered = build_curriculum(ctx, project_id)`:

```python
        assign_categories(ctx, project_id)
```

- [ ] **Step 3:** In `tests/test_synthesis_task.py`, extend the existing end-to-end synthesis test (or add one) to assert concepts come out with a non-empty `category` when the scripted router returns a categories payload as its final call. Run `pytest tests/test_synthesis_task.py -v` → PASS.

- [ ] **Step 4: Commit** `git commit -am "feat(synthesis): run assign_categories after curriculum ordering"`

---

### Task 6: Frontend type + curriculum grouping

**Files:**
- Modify: `frontend/src/api/types.ts` (`ConceptMeta.category`)
- Modify: `frontend/src/views/CurriculumView.tsx`
- Test: `frontend/src/views/CurriculumView.test.tsx`

**Interfaces:**
- Consumes: `ConceptMeta` now has `category?: string`.

- [ ] **Step 1: Add field** to `ConceptMeta` in `types.ts`: `category?: string;`

- [ ] **Step 2: Write failing test** in `CurriculumView.test.tsx` (follow existing render-with-providers + fetchMock pattern already used in the file): mock `/api/study/curriculum` returning two concepts with `category: "Numbers"` and one with `category: ""`, assert a "Numbers" header and an "Other" header render, and that all three concept names appear. Run `cd frontend && npx vitest run src/views/CurriculumView.test.tsx` → FAIL.

- [ ] **Step 3: Implement grouping** in `CurriculumView.tsx`. Replace the flat `<ul className="concept-list">` block with grouping logic:

```tsx
function groupByCategory(concepts: ConceptMeta[]): [string, ConceptMeta[]][] {
  const order: string[] = [];
  const groups = new Map<string, ConceptMeta[]>();
  for (const c of concepts) {
    const key = c.category && c.category.trim() ? c.category : "Other";
    if (!groups.has(key)) {
      groups.set(key, []);
      order.push(key);
    }
    groups.get(key)!.push(c);
  }
  // "Other" always last
  order.sort((a, b) => (a === "Other" ? 1 : 0) - (b === "Other" ? 1 : 0));
  return order.map((k) => [k, groups.get(k)!]);
}
```

Render: if every category is "Other" (i.e. none set), render the existing flat list unchanged; otherwise render each `[category, items]` as a `<details className="concept-category" open>` with `<summary>{category}</summary>` wrapping the existing `<ul className="concept-list">` of that group's items (reuse the current `<li>`/`<Link>` markup verbatim).

- [ ] **Step 4: Run** `cd frontend && npx vitest run src/views/CurriculumView.test.tsx` → PASS.

- [ ] **Step 5: Commit** `git commit -am "feat(ui): group curriculum by category"`

---

### Task 7: Strip interpretation, open-questions, TL;DR label from the guide view

**Files:**
- Modify: `frontend/src/components/InteractiveGuide.tsx`
- Test: `frontend/src/components/InteractiveGuide.test.tsx`

- [ ] **Step 1: Update tests** in `InteractiveGuide.test.tsx`: remove/replace any assertion that "Model's analysis", "Open questions", or "TL;DR" render; add assertions that a guide with `interpretation`/`open_questions` populated does NOT render those strings, and that `tl_dr.text` still appears. Run → FAIL.

- [ ] **Step 2: Edit `InteractiveGuide.tsx`:**
  - Delete `InterpretationBlock`, `ANGLE_LABELS`, `angleClass`, the `InterpretationItem` import, and the `<InterpretationBlock ... />` usage.
  - Delete the `guide.open_questions.length > 0 ? (...)` block entirely.
  - In the `guide-tldr` div, delete `<span className="guide-tldr-label">TL;DR</span>`. Keep `<p className="guide-tldr-text">` with the `ClaimText` + `SourcesFooter`.

- [ ] **Step 3: Run** `cd frontend && npx vitest run src/components/InteractiveGuide.test.tsx` → PASS.

- [ ] **Step 4: Commit** `git commit -am "feat(ui): remove interpretation, open-questions, TL;DR label from guide"`

---

### Task 8: Remove the duplicate summary in the concept header

**Files:**
- Modify: `frontend/src/views/ConceptView.tsx:191`
- Test: `frontend/src/views/ConceptView.test.tsx`

- [ ] **Step 1: Update test** in `ConceptView.test.tsx`: assert the concept `summary` string appears at most once (it should still appear via the guide lede, not as a standalone header paragraph). Run → FAIL if it currently asserts the header `<p>`.

- [ ] **Step 2: Delete** the line `<p>{detail.concept.summary}</p>` under `<h1>{detail.concept.name}</h1>` in `ConceptView.tsx`.

- [ ] **Step 3: Run** `cd frontend && npx vitest run src/views/ConceptView.test.tsx` → PASS.

- [ ] **Step 4: Commit** `git commit -am "feat(ui): drop duplicate summary in concept header"`

---

### Task 9: CSS cleanup + category styling

**Files:**
- Modify: `frontend/src/app.css`

- [ ] **Step 1:** Delete the `.guide-tldr-label` rule (~line 673) and the `.guide-interpretation*` and `.guide-open-questions*` rules. Keep `.guide-tldr` / `.guide-tldr-text` (adjust the lede to read as a plain intro paragraph — remove any label-specific spacing).

- [ ] **Step 2:** Add minimal styles for `.concept-category` (summary as a group header) and ensure nested `.concept-list` still renders. Example:

```css
.concept-category > summary { font-weight: 600; cursor: pointer; margin: 0.75rem 0 0.25rem; }
.concept-category[open] > summary { margin-bottom: 0.5rem; }
```

- [ ] **Step 3: Run full frontend suite** `cd frontend && npx vitest run` → PASS.

- [ ] **Step 4: Commit** `git commit -am "style: remove tldr-label/interpretation/open-questions css, add category group"`

---

### Task 10: Full-suite verification

- [ ] **Step 1:** `pytest -q` → all pass (confirm no other test references the removed prompt/interpretation behavior; fix any stragglers, e.g. `tests/test_guides_queued.py`, `tests/test_warm_guides.py`).
- [ ] **Step 2:** `cd frontend && npx vitest run` → all pass.
- [ ] **Step 3:** `cd frontend && npx tsc --noEmit` → clean.
- [ ] **Step 4: Commit** any straggler fixes.

## Self-Review Notes

- Spec A1 → Tasks 7,8,9. A2 → Tasks 1,2,7. A3 → Task 2. A4 → Tasks 3,4,5,6.
- Back-compat: Task 1 keeps pydantic fields, tested by `test_cached_guide_with_legacy_fields_still_parses`.
- Category crash-safety: Task 4 `test_assign_categories_bad_output_leaves_empty` + over-cap guard.
