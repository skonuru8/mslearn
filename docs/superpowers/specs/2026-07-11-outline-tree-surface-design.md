# Surface the Source Outline Tree

Date: 2026-07-11
Status: Approved (architecture approved by user)
Sequence: **Spec 3**. Depends on Spec 2 (chunks carry `section_path`).

## Goal

Roll each source's captured `section_path` up to concepts, expose a chapter→section→concept outline, and render `CurriculumView` as a navigable table-of-contents tree in the book's order. When no source has structure, fall back to the existing category-grouped flat list unchanged.

## Current state (post Spec 2)

- Every chunk carries `section_path` (JSON tuple). Claims link to chunks (`EXTRACTED_FROM`); concepts to claims (`IN_CONCEPT`).
- Concepts already carry `category` (model-grouped) and `order_index` (spine order). `curriculum()` returns them. `CurriculumView` groups by `category`.
- Synthesis runs `assign_categories` after `build_curriculum` (`worker/tasks.py`).

## Design

### S3.1 Roll section_path up to concepts
- Graph read `store.concept_section_paths(project_id) -> dict[concept_id, list[tuple[path, min_seq]]]`: for each concept, its claims' chunks' `section_path` (JSON-decoded) with the chunk `seq`, so the caller can pick a home section.
- New synthesis step `assign_sections(ctx, project_id)` (in `pipeline/synthesis.py`), run **after** `build_curriculum` and **before** `assign_categories` in `worker/tasks.py`:
  - For each concept, home `section_path` = the most frequent non-empty path among its claims' chunks; tie-break by earliest chunk `seq`. Concepts whose claims are all structure-less → empty path.
  - Persist via `store.set_concept_sections(pairs)` (bulk, mirrors `set_concept_categories`). Add `section_path` (JSON) to the concept node; return it (JSON-decoded to list) from `get_concept`, `all_concepts`, `curriculum`.
  - **Category reconciliation:** when a concept has a non-empty `section_path`, set its `category` to `section_path[0]` (the top chapter) so the existing category grouping stays consistent with structure. `assign_categories` then only needs to name categories for concepts still lacking one (structure-less), preserving the hybrid.

### S3.2 Outline endpoint
- `GET /api/study/outline` (`server/routers/study.py`) → builds the tree server-side from `curriculum()` rows (each has `concept_id`, `name`, `order_index`, `category`, `section_path`, `conflict_count`):
  ```json
  { "tree": [ { "title": "Chapter 3: Numbers", "concepts": [...], "children": [
        { "title": "3.1 Number type", "concepts": [ {"concept_id","name","conflict_count"} ], "children": [] } ] } ],
    "flat": [ ...concepts with empty section_path... ],
    "has_structure": true }
  ```
  Build: insert each concept into the tree by walking its `section_path`; concepts with empty path go to `flat`. Order children by the min `order_index` of concepts beneath them; order concepts within a node by `order_index`. `has_structure = any concept has a non-empty path`.
- Pure function `build_outline(rows) -> dict` in `pipeline/` (or a small `study_outline.py`) so it is unit-testable without the DB.

### S3.3 UI — TOC tree
- `CurriculumView` fetches `/api/study/outline` alongside curriculum.
- If `has_structure`: render a nested tree — each node a collapsible `<details open>` with its `title`; leaf/attached concepts as the existing `<Link to={/concepts/:id}>` rows (reuse current markup + conflict badge). `flat` concepts render under an "Unstructured" group at the end.
- If `!has_structure`: render the existing category-grouped list (from Spec A) unchanged — no visual change for structure-less projects.
- Keep the existing building/empty states.

## Files touched

- `mslearn/graph/store.py` (`concept_section_paths`, `set_concept_sections`, `section_path` in concept RETURNs)
- `mslearn/pipeline/synthesis.py` (`assign_sections`, category reconciliation)
- `mslearn/worker/tasks.py` (call `assign_sections` before `assign_categories`)
- New: `mslearn/pipeline/study_outline.py` (`build_outline`)
- `mslearn/server/routers/study.py` (`GET /api/study/outline`)
- `tests/fakes.py` (concept `section_path` + `concept_section_paths`)
- `frontend/src/api/types.ts` (`OutlineNode`, `OutlineResponse`)
- `frontend/src/views/CurriculumView.tsx` (tree render + fallback)

## Explicitly not doing

- No per-section progress/quizzing — the tree is navigation + grouping only.
- No manual re-ordering or editing of the outline.
- No change to concept detail / guide views.

## Testing

- `build_outline`: nests concepts by `section_path`; orders chapters/sections/concepts by `order_index`; routes empty-path concepts to `flat`; `has_structure` false when all paths empty.
- `assign_sections`: dominant-path selection + earliest-seq tie-break; empty when structure-less; reconciles `category = section_path[0]`.
- `store.concept_section_paths`/`set_concept_sections`: round-trip through the graph; `curriculum()` returns `section_path`.
- `GET /api/study/outline`: tree for a structured project; `has_structure:false` + populated `flat` for an unstructured one.
- `CurriculumView`: renders the tree when `has_structure`; renders the flat category list when not (regression of Spec A behavior).

## Success criteria

A JS textbook (PDF outline / EPUB nav / Markdown / Word) shows in My course as its real chapter→section tree with concepts nested where they belong, in book order; a structure-less source (image cheatsheet, audio) shows exactly the flat category list it does today.
