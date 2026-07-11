# Notes Redesign — Categories, Depth, Declutter

Date: 2026-07-11
Status: Approved (design decisions locked with user)
Sequence: **Subsystem A** — must land before Subsystem B (self-improving evals) because B judges the new guide and attaches feedback to the new note view.

## Problem

From a real user run against a JS cheatsheet:

1. **The same one-liner shows three times.** A concept's `summary` appears in the curriculum list, again as a `<p>` in the concept header (`ConceptView.tsx:191`), and again inside the guide's `TL;DR` block. Opening a note reveals nothing the list didn't already say.
2. **`TL;DR` label reads as clutter** — it heads a sentence that just restates the list summary.
3. **No categorization.** The curriculum is one flat list of concepts. The user wants notes grouped.
4. **Two blocks the user dislikes:** "Open questions" and "Model's analysis — not from your source" (the interpretation layer).
5. **Notes feel shallow** — sections restate claims rather than explain them.

## Decisions (locked)

- **Categories are model-grouped**, not source-structure-derived. Source headings are *not* captured by the pipeline (blog/YouTube/HTML units are created with `title=""`; only unit_index is persisted on chunks — no heading survives), so mirroring source structure would require adapter rework with weak results on PDF/image/video. Instead, after concepts exist, one cheap model pass clusters concept **names** into a handful of named categories. Source-independent. On failure or too-many-concepts, fall back to flat/"Other" — never crash.
- **Open = short pointer → real depth.** List one-liner is the pointer; opening delivers genuine explanation.

## Design

### A1. Remove the triple redundancy + `TL;DR` chip

- `ConceptView.tsx`: delete the duplicate `<p>{detail.concept.summary}</p>` under the `<h1>`.
- `InteractiveGuide.tsx`: remove the `TL;DR` label/chip (`guide-tldr-label`). The `tl_dr.text` still renders — as a plain, unlabeled lede paragraph at the top of the guide, keeping its citation superscripts and `SourcesFooter`. So the note opens straight into depth.
- CSS: drop `.guide-tldr-label`; keep the lede readable.

### A2. Remove interpretation + open_questions

- **UI (`InteractiveGuide.tsx`)**: delete `InterpretationBlock`, the `open_questions` rendering, `ANGLE_LABELS`, `angleClass`, and the `InterpretationItem` import + `guide.interpretation`/`guide.open_questions` usage.
- **Generation (`prompts.py` `guide` prompt)**: remove the `interpretation` and `open_questions` instruction bullets.
- **Schema (`guide.py`)**: drop `open_questions` and `interpretation` from `GUIDE_SCHEMA` and from the `required` list. Keep the pydantic `StudyGuide` fields as optional defaults (`[]`) so **cached guides** persisted before this change still parse (pydantic ignores extra keys; keeping the fields means old JSON with them still validates).
- `frontend/src/api/types.ts`: keep `open_questions`/`interpretation` optional on `StudyGuide` for back-compat, but they are no longer rendered.
- `drop_ungrounded` in `guide.py`: remove the interpretation-preservation comment/branch that is now moot.

### A3. Real depth in the guide

Rewrite the `guide` prompt so each section item is a genuine explanation — *what it means, why it holds, how it connects to the concept and neighbouring claims, and a concrete example where a claim supports one* — multiple sentences, not a one-line restatement. Unchanged, load-bearing rules:

- Every item still ties to claim id(s) in `claims`; every supplied claim covered by exactly one grounded item; no invented facts, text, or ids.
- Depth stays **bounded by supplied claims** — go deep on what they support, never pad with generic filler.
- `tl_dr.text` stays one plain sentence (now rendered as the unlabeled lede).

### A4. Categories

**Data.** Add a `category` string property to the `Concept` graph node. No SQL migration (graph node gains a property). `set_concept_meta` gains an optional `category` param; `curriculum()` and `all_concepts()`/`get_concept` return `category` (coalesced to `""`).

**Generation.** New synthesis step after `build_curriculum` ordering: `assign_categories(ctx, project_id)`.
- Collects ordered `(concept_id, name)` pairs.
- One model call with a new `concept_categories` prompt → `{ "categories": [ { "name": "...", "concept_ids": [...] } ] }`.
- Bounded exactly like the existing `concept_deps` call: attempt only when concept count is within a sane cap; on `ProviderBadOutputError`, truncation, or unknown ids, log + leave categories empty (concepts render flat). Persist `category` per concept via a bulk write mirroring `set_concept_orders`.
- New prompt `concept_categories` added to `PROMPTS` in `prompts.py` (so it is overrideable + evolvable).

**API.** `ConceptMeta` (`api/types.ts`) gains `category?: string`. `curriculum` router already returns whatever `graph.curriculum()` yields.

**UI (`CurriculumView.tsx`).** Group concepts by `category`, preserving `order_index` within each group; render collapsible category headers. Concepts with empty category fall under an "Other" group. Category order = order of first appearance by `order_index`. When no concept has a category, render the existing flat list unchanged.

## Files touched

Frontend: `InteractiveGuide.tsx`, `ConceptView.tsx`, `CurriculumView.tsx`, `api/types.ts`, `app.css`.
Backend: `pipeline/guide.py`, `prompts.py`, `pipeline/synthesis.py`, `graph/store.py`, `worker/tasks.py` (wire `assign_categories` immediately after the `build_curriculum` call at ~tasks.py:375, in the same synthesis phase).

## Explicitly not doing

- Adapter/heading capture rework (decided against).
- New note-detail screen/architecture — depth comes from the prompt, not a new view.
- Removing the legacy `teach_concept` path (out of scope here; evals subsystem addresses coverage).

## Testing

- `guide.py`: cached guide JSON containing `open_questions`/`interpretation` still parses; new `GUIDE_SCHEMA` validates a guide lacking them.
- `synthesis.py`: `assign_categories` persists categories on success; leaves them empty and does not raise on bad output / over-cap.
- `store.py`: `curriculum()` returns `category`.
- Frontend: `CurriculumView` groups by category and renders flat when none; `InteractiveGuide` no longer renders interpretation/open-questions/TL;DR-label; `ConceptView` no longer double-renders summary.

## Success criteria

Opening a note shows a categorized list → an unlabeled lede → genuinely deeper, still-grounded sections, with no interpretation/open-questions blocks and no repeated one-liner.
