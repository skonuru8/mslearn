# Notes Quality + Synthesis Robustness

**Date:** 2026-07-10
**Status:** design for approval

## Problem

Two things are wrong with the generated study guides:

1. **Notes just restate the source.** The `guide` prompt instructs the model to *"copy one claim... set 'text' to the claim's text VERBATIM... you must NOT reword... Items with no claim id are forbidden"*, and `drop_uncited` deletes anything not tied to a claim. So the guide is a verbatim ledger of the extracted claims, grouped into sections — the LLM adds only grouping + one `tl_dr` sentence. Users correctly ask "the same thing is in my material — what's the point of the LLM call?"

2. **Synthesis crashes and loses claims on real corpora.** A 1-hour video (hundreds of concepts) reliably kills synthesis: `build_curriculum` asks one `concept_deps` model call to return *all* dependency edges among *all* spine concepts; the output overflows `synth.max_tokens=8192` → `finish_reason='length'` → truncated JSON → uncaught `ProviderBadOutputError` → `synthesize_task` dies → no curriculum, no guide warming, nothing shown. More broadly, EVERY synthesis model call (`concept_match`, `conflict_scan`, `concept_name`, `concept_deps`) crashes the whole run on a single malformed response, and deepseek-v4-flash emits malformed JSON often enough (unescaped inner quotes, e.g. `"text":""Everyone has their price"`) that this is frequent. That same malformed JSON also silently drops extracted claims (eval: extraction recall 0.67, precision 0.57 — both below gate).

## Goal

Make the guide deliver genuine understanding (own words + analysis) while preserving the anti-hallucination guarantee, and make synthesis + extraction robust to the model's imperfect JSON. **Grounding means traceability, not verbatim:** a reworded fact is still grounded if it cites the claim it came from; pure interpretation is allowed only when clearly labeled as the model's own analysis, never presented as source fact.

## Design

Four parts. Part 1 is the notes redesign; Parts 2–4 are robustness/quality.

### Part 1 — Two-layer notes (grounded + labeled interpretation)

The guide gains two clearly separated layers:

- **Grounded layer** — the existing sections, but each item is **rewritten in the model's own words** ("what it means / why it matters / how it connects"), and still **cites** the claim(s) it rests on. The claim's verbatim `quote` stays available in the per-section **Sources** footer as the receipt. This is your note-making algorithm's Layer 1 (comprehension) applied over the trust-gated claims. Guardrail: no item may be a near-paraphrase of the source (swapped synonyms) — full rewrite only.
- **Interpretation layer** — a NEW, visually distinct block carrying the model's Layer-2 analysis: assumptions, evidence quality, steelman objection, verdict, and a short synthesis ("what this adds up to"). Every interpretation item is **labeled** "Model's analysis — not from your source." Interpretation items are NOT required to cite a claim (they are reasoning, not source facts) but may reference claim ids for context.

**Data model / gate changes:**
- `StudyGuide` gains an `interpretation: list[InterpretationItem]` field. `InterpretationItem = {angle: "assumption"|"evidence"|"steelman"|"verdict"|"synthesis", text: str, claims: list[str] = []}`.
- Grounded `GuideItem.claims` stays REQUIRED (grounded facts must trace to a claim).
- `drop_uncited` → `drop_ungrounded`: still drops **grounded** items with no citation and grounds `tl_dr` as today; **keeps** interpretation items regardless of citation (they are labeled non-fact). It must never let an *unlabeled* item through uncited.
- `guide` prompt rewritten: grounded sections in own words + cite; produce the interpretation block; **interpretation always on** (no depth toggle), but omit an angle (or the whole block) when the concept is too thin for real analysis rather than padding (algo guardrail #3).
- **Provenance invariant preserved:** the memory-provenance / fact-grounding eval must stay at 0 violations — every *grounded* fact traces to a claim; interpretation is labeled and excluded from fact checks. If the eval inspects guide text, confirm it treats cited-but-reworded grounded items as grounded and interpretation items as non-fact.

**Frontend:** `frontend/src/api/types.ts` gains `InterpretationItem` + the guide's `interpretation` field; `InteractiveGuide.tsx` renders the interpretation block in a distinct, labeled style (clearly "model's analysis," visually separated from grounded sections). Raw claim-id strings still never shown (existing invariant).

### Part 2 — Fault-tolerant synthesis (P1)

No single model call may crash a synthesis run. Wrap each synthesis model call so a `ProviderError`/`ProviderBadOutputError`/bad-JSON response degrades gracefully:
- `concept_deps`: on failure OR when the spine is large (**> 60 concepts** — a one-shot DAG over hundreds of concepts both overflows `max_tokens` and is low quality), **skip the call and use natural spine order** (`first_seq`; the existing topo-sort already falls back). Curriculum always builds.
- `concept_match` (in parallel Phase A): on failure → treat that anchor as no-match (its claim gets its own concept). One anchor's bad response must not fail clustering.
- `conflict_scan`: on failure → no conflicts recorded for that concept.
- `concept_name`: on failure → a deterministic fallback name (e.g. first claim's leading text, or the concept id-free "Untitled concept").

`synthesize_task` completes (and warms guides) even when some model calls degraded.

### Part 3 — JSON salvage (P2)

Add a salvage step in the OpenRouter provider's structured-output parse path (`openrouter.py` `complete`, the `json.loads(text)` on `request.json_schema is not None`): on `JSONDecodeError`, attempt a repair (a `json-repair`-style pass fixing unescaped quotes and truncated tails) before raising `ProviderBadOutputError`. If salvage yields schema-valid JSON, use it; else raise as today. This recovers claims lost to malformed extraction output (improves recall) and reduces synthesis failures (complements Part 2's degradation net). Dependency: add `json-repair` (small, pure-Python) or an equivalent bounded manual repair — decided in the plan.

### Part 4 — concept_match id discipline (P3)

The `concept_match` model frequently returns candidate **list positions** ("1","5") or stray ids instead of real claim ids, so valid matches are dropped and concepts over-split. Fix in `concept_match_claim_ids` / the batched-free per-anchor parse: when a returned match value is not a candidate id but IS a valid 1-based index into the presented candidate list, map it back to that candidate's real claim id (count it as a match, not a drop). Tighten the prompt to demand ids. This recovers legitimate matches; the drop path stays for genuinely stray values.

## Components / files

- `mslearn/pipeline/guide.py` — `InterpretationItem`, `StudyGuide.interpretation`, schema, `drop_ungrounded` (rename/replace `drop_uncited`).
- `mslearn/pipeline/guide_gen.py` — build/attach interpretation; keep idempotent cache; provenance-safe.
- `mslearn/prompts.py` — rewrite `guide` prompt (own-words grounded + interpretation block); tighten `concept_match`.
- `mslearn/pipeline/synthesis.py` — degrade each model call; skip `concept_deps` for large spines; positional-id mapping in match parse.
- `mslearn/providers/openrouter.py` — JSON salvage before erroring.
- `pyproject.toml` — `json-repair` (if chosen).
- `frontend/src/api/types.ts`, `frontend/src/components/InteractiveGuide.tsx`, `frontend/src/app.css` — render the interpretation layer.
- Tests: guide two-layer parse/gate, interpretation kept-uncited but grounded-dropped, degradation-per-call (each synthesis call failing → run still completes), JSON salvage recovers a mangled payload, positional-id mapping, frontend render + no-raw-id lock-in.

## Non-goals

- Depth toggle (comprehension vs deep) — interpretation is always on; add a toggle only if later requested.
- Path B (query-time RAG), fastembed, D (batched clustering) — remain deferred roadmap items.
- Changing the trust gate on claims (extraction) — unchanged; only the guide's *presentation* of gated claims is reworded, and interpretation is additive + labeled.

## Verification

- All new tests + `make check` green.
- Provenance eval stays 0 violations; grounding false-accept stays ≤ gate.
- Extraction recall improves (salvage recovers claims) — re-run evals.
- Manual: ingest the 1-hour video → synthesis completes (no crash), curriculum + guides appear; open a concept → grounded sections read as own-words understanding (not a copy) with a labeled interpretation block; Sources footer still shows verbatim quotes.
