# Outline Tree Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Roll chunk `section_path` up to concepts, expose `GET /api/study/outline`, and render `CurriculumView` as a navigable chapter→section→concept tree; unstructured projects render the existing flat category list unchanged.

**Architecture:** New synthesis step `assign_sections` persists a concept `section_path` and reconciles `category`; a pure `build_outline` nests curriculum rows into a tree; a new endpoint serves it; `CurriculumView` renders it.

**Tech Stack:** Python (FastAPI, graph store), pytest; React + TypeScript, vitest.

Spec: `docs/superpowers/specs/2026-07-11-outline-tree-surface-design.md`
**Depends on:** Formats + Structure Capture plan (chunks carry `section_path`).

## Global Constraints

- Backend tests `.venv/bin/pytest tests/<f> -v`; frontend `cd frontend && npx vitest run <f>`.
- Concept `section_path` persisted as JSON on the graph node; returned JSON-decoded (list) from store reads.
- `has_structure=false` ⇒ CurriculumView must render exactly the Spec-A category-grouped list (regression).

---

### Task 1: Concept section_path in the store

**Files:**
- Modify: `mslearn/graph/store.py` (`get_concept`, `all_concepts`, `curriculum` RETURNs; new `set_concept_sections`, `concept_section_paths`), `tests/fakes.py`
- Test: `tests/test_graph_store.py`

**Interfaces:**
- Produces:
  - `store.set_concept_sections(pairs: list[tuple[str, list[str]]], *, project_id)` — bulk write concept `section_path` (JSON).
  - `store.concept_section_paths(*, project_id) -> dict[str, list[tuple[list[str], int]]]` — per concept, its claims' chunk `(section_path, seq)`.
  - `get_concept`/`all_concepts`/`curriculum` rows include `section_path` (JSON-decoded list; default `[]`).

- [ ] **Step 1: Write failing test:** seed a concept with claims on chunks having `section_path=["Ch1","1.1"]` (seq 0) and `["Ch1","1.1"]` (seq 1); `set_concept_sections([("k1",["Ch1","1.1"])])`; assert `get_concept("k1")["section_path"] == ["Ch1","1.1"]` and `concept_section_paths()["k1"]` lists the two `([...], seq)` pairs.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Add `coalesce(k.section_path,'[]') AS section_path` to the three RETURNs, decoding via `json.loads` in the row-mapping (or return raw JSON and decode in callers — pick one and be consistent; recommend decoding in the store so callers get a list). Add `set_concept_sections` (UNWIND bulk, `SET k.section_path = row.path_json`). Add `concept_section_paths` (MATCH claim→concept and claim→chunk, RETURN concept_id, ch.section_path, ch.seq; group in Python). Mirror all in `InMemoryGraphStore`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(store): concept section_path rollup queries"`

---

### Task 2: `assign_sections` synthesis step + category reconciliation

**Files:**
- Modify: `mslearn/pipeline/synthesis.py`; Test: `tests/test_sections.py`

**Interfaces:**
- Produces: `assign_sections(ctx, project_id="default") -> int` — persists each concept's home `section_path` (dominant path; tie-break earliest seq); when non-empty also sets `category = section_path[0]`.

- [ ] **Step 1: Write failing test:** with an `InMemoryGraphStore` where concept k1's claims map to paths `[["Ch1","1.1"] seq2, ["Ch1","1.1"] seq5, ["Ch2"] seq9]`, `assign_sections` sets k1 `section_path=["Ch1","1.1"]` and `category="Ch1"`. A concept with only empty paths → `section_path=[]`, category untouched.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** For each `(cid, entries)` from `concept_section_paths`: filter non-empty paths; pick the path with the highest count, tie-break by min seq; if found, collect `(cid, path)` and `(cid, path[0])`. Bulk `set_concept_sections(section_pairs)` and `set_concept_categories(cat_pairs)` (reuse existing). Return count with non-empty section.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(synthesis): assign_sections rollup + category reconciliation"`

---

### Task 3: Wire `assign_sections` into synthesis

**Files:**
- Modify: `mslearn/worker/tasks.py`; Test: `tests/test_synthesis_task.py`

- [ ] **Step 1:** Import `assign_sections`; call it right after `ordered = build_curriculum(...)` and **before** `assign_categories(...)` so category reconciliation happens before model-grouping fills the rest.
- [ ] **Step 2:** Update/extend a synthesis-task test to assert `assign_sections` runs (monkeypatch it like the other steps in tests that patch the pipeline). Run `.venv/bin/pytest tests/test_synthesis_task.py tests/test_worker_tasks.py -v` → PASS.
- [ ] **Step 3: Commit** `git commit -am "feat(synthesis): run assign_sections before assign_categories"`

---

### Task 4: `build_outline` pure function

**Files:**
- Create: `mslearn/pipeline/study_outline.py`; Test: `tests/test_study_outline.py`

**Interfaces:**
- Produces: `build_outline(rows: list[dict]) -> dict` with keys `tree`, `flat`, `has_structure`. Each row has `concept_id`, `name`, `order_index`, `section_path` (list), `conflict_count`.

- [ ] **Step 1: Write failing test:** rows for concepts under `["Ch1","1.1"]`, `["Ch1","1.2"]`, `["Ch2"]`, and one with `[]`. Assert: `has_structure` true; `tree[0]["title"]=="Ch1"` with two children titled "1.1"/"1.2"; `tree[1]["title"]=="Ch2"`; the empty-path concept in `flat`; ordering by `order_index`. Second test: all rows empty path → `has_structure` false, all in `flat`, `tree==[]`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Walk each row's `section_path`, creating/reusing nested nodes `{title, concepts: [], children: []}` keyed by title at each level; attach the concept `{concept_id,name,conflict_count}` to the deepest node. Empty path → `flat`. Sort: each node's `children` and `concepts` by the min `order_index` beneath/of them; top-level `tree` likewise. `has_structure = bool(tree)`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(outline): build_outline tree builder"`

---

### Task 5: Outline endpoint

**Files:**
- Modify: `mslearn/server/routers/study.py`; Test: `tests/test_study_outline_api.py`

- [ ] **Step 1: Write failing test:** seed a project whose curriculum rows carry `section_path`; `GET /api/study/outline` returns `has_structure:true` with the nested tree; a flat project returns `has_structure:false` + populated `flat`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** `@router.get("/outline")` → `build_outline(ctx.graph.curriculum(project_id=project_id))`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(api): study outline endpoint"`

---

### Task 6: CurriculumView TOC tree

**Files:**
- Modify: `frontend/src/api/types.ts`, `frontend/src/views/CurriculumView.tsx`
- Test: `frontend/src/views/CurriculumView.test.tsx`

**Interfaces:**
- Consumes: `OutlineResponse { tree: OutlineNode[]; flat: OutlineConcept[]; has_structure: boolean }`, `OutlineNode { title; concepts: OutlineConcept[]; children: OutlineNode[] }`, `OutlineConcept { concept_id; name; conflict_count? }`.

- [ ] **Step 1: Add types** to `types.ts`.
- [ ] **Step 2: Write failing test:** mock `/api/study/outline` returning a 2-chapter tree (Ch1 → 1.1 with a concept); assert chapter + section titles render and the concept link appears at the section. Second test: `has_structure:false` with `flat` → renders the existing category-grouped list (reuse/keep the Spec-A grouping test behavior).
- [ ] **Step 3: Run** → FAIL.
- [ ] **Step 4: Implement.** Fetch `/api/study/outline` in the existing `refresh`. When `has_structure`, render a recursive `<OutlineTree nodes={tree} />` component: each node a `<details className="outline-node" open><summary>{title}</summary>` containing its `concepts` as the existing `<li><Link>` rows and its `children` recursively; render `flat` under an "Unstructured" node at the end. When `!has_structure`, render the current `groupByCategory` list unchanged. Keep loading/empty/building states.
- [ ] **Step 5: Run** frontend tests → PASS; `npx tsc --noEmit` → clean.
- [ ] **Step 6: Commit** `git commit -am "feat(ui): curriculum outline tree with flat fallback"`

---

### Task 7: Verify

- [ ] **Step 1:** `.venv/bin/pytest -q` → pass.
- [ ] **Step 2:** `cd frontend && npx vitest run` → pass; `npx tsc --noEmit` → clean.
- [ ] **Step 3: Commit** stragglers.

## Self-Review

- S3.1→T1,T2,T3. S3.2→T4,T5. S3.3→T6. Fallback regression covered in T6 step 2.
- `assign_sections` runs before `assign_categories` (T3) so category reconciliation precedes model-grouping — matches spec. Types match between endpoint (T5) and UI (T6).
