# Interactive Study Guide + Lossless Gated Extraction + High-Throughput Ingest

Date: 2026-07-06
Builds on: `docs/superpowers/specs/2026-07-02-multi-source-learning-system-design.md`

## Context

Three coupled changes to the running mslearn app, agreed in a brainstorming
session:

1. **Interactive study guide** — replace the per-concept teaching view
   (markdown notes) with a colorful, fluid, card-based interactive guide
   rendered from a structured JSON the model emits. No iframes, no
   model-generated code execution.
2. **Lossless gated extraction** — make claim extraction less lossy so
   mechanisms, caveats, examples, definitions, and actionable steps are
   captured *as trust-gated claims* (still verbatim-quoted), so the interactive
   guide is rich without breaking the anti-hallucination guarantee.
3. **High-throughput ingest** — extraction now runs on OpenRouter
   deepseek-v4-flash (fast remote API) but is throttled by a worker topology
   sized for local Ollama. Re-architect for throughput with no single choke
   point, keeping the durable/resumable queue.

Plus a route-safety fix: resource routes (e.g. a concept page) must only render
within their owning project, else render nothing — no cross-project error.

Non-negotiable invariants carried from the base spec:
- **Trust gate unchanged**: every claim keeps a verbatim quote that must match
  its chunk. Nothing here weakens it.
- **Every displayed fact traces to a trust-gated claim.** The guide renders
  claims; it never invents.
- **Memory is advisory/personalization-only** and can never break an endpoint.
- **Durable, resumable, per-chunk-idempotent ingest queue** (crash/sleep safe).
- **Exports stay deterministic** and offline (unaffected by the view change).

---

## Part 1 — Interactive study guide (replaces the teaching view)

### Approach: model emits data, app renders UI

The model does **not** write React/HTML. It emits a compact structured guide as
JSON; the app renders it with native React components. This is safe (no code
execution, no XSS, no iframe), fluid (real themed app UI, CSS-animated), and
the lowest-token option (JSON structure + one TL;DR line beats prose/JSX).

A concept page = **one concept** (name, summary, claims, conflicts). The guide
is the layered/tagged view of that concept's claims, following the
`lossless-notes` framework (TL;DR → skeleton → tagged full detail → open
questions) rendered in the `interactive-study-guide` shape (cards + progress).

### Data model (guide JSON)

```json
{
  "concept_id": "…",
  "title": "Merge sort",
  "tl_dr": { "text": "Divide-and-conquer sort, guaranteed O(n log n).", "claims": ["c3"] },
  "skeleton": ["Time complexity", "How it works", "When to use"],
  "sections": [
    {
      "id": "s1",
      "title": "Time complexity",
      "items": [
        { "kind": "claim",      "text": "…", "claims": ["c3"] },
        { "kind": "mechanism",  "text": "…", "claims": ["c3", "c4"] },
        { "kind": "example",    "text": "…", "claims": ["c5"] },
        { "kind": "caveat",     "text": "…", "claims": ["c6"] }
      ]
    }
  ],
  "disagreements": [
    {
      "summary": "…",
      "classification": "genuine_debate",
      "a": { "label": "Source X", "text": "…", "claims": ["c7"] },
      "b": { "label": "Source Y", "text": "…", "claims": ["c8"] }
    }
  ],
  "open_questions": ["…"]
}
```

Field rules:
- `tl_dr` — one line, always visible. Cited internally.
- `skeleton` — ordered section titles; drives the sticky mini-map. Always visible.
- `sections[].items[].kind` ∈ the 6 claim kinds (see Part 2). Drives color.
- `sections[].items[].text` — **the claim's text as-is.** The generator
  organizes and groups; it does not rewrite item bodies. (Max grounding, min
  tokens.)
- `sections[].items[].claims` — the `claim_id`s the item is built from.
  **Every item MUST carry ≥1 claim id.** This is the grounding gate at render
  time — an item with no claim is dropped.
- `disagreements[]` — present only when the concept has conflicts; each side
  cites claims. Built from the graph's `CONFLICTS_WITH` classification.
- `open_questions[]` — the one non-claim field. Restricted to "what the source
  did not cover" (gaps). Rendered visually distinct as advisory, never as fact.

### Generator

- One structured-output call, schema-enforced, replacing the current
  teach-markdown generation in `mslearn/pipeline/teaching.py`.
- Input: the concept's claims (each tagged with its `kind`), conflicts, and
  memory hints (personalization only). Output: the guide JSON.
- The model emits **structure + TL;DR + claim_id grouping only** — it assigns
  each claim to a section and orders sections. Item bodies are the claim texts.
- Cached exactly like teaching is today (regenerate button forces a pass;
  dirty-propagation invalidates on flag/re-synthesis).
- Degrades gracefully on thin concepts: 1–2 claims → TL;DR + one short section.
  No minimum, no invented padding.
- Memory failure → no personalization, never a 500 (existing isolation).

### Endpoint / contract change

- `GET /api/study/concepts/{id}/teach` returns the **guide JSON** instead of
  `{ markdown }`. New Pydantic response models for the guide.
- `ConceptDetail` (claims + citations) endpoint unchanged — still the source of
  the quote/locator data the Sources footer needs.

### Rendering (frontend)

- New `InteractiveGuide` component replaces `MarkdownWithCitations` in
  `ConceptView`. `splitTeachMarkdown` util is retired from the view.
- **Layout: stacked collapsible cards + sticky skeleton mini-map.** One fluid
  scroll; the mini-map lists skeleton titles as jump-links and highlights the
  section in view. Colorful, CSS-animated expand/collapse.
- **Items color-coded by kind**: definition, claim, mechanism, example, caveat,
  actionable — each a distinct accent (e.g. definition=key-term callout,
  mechanism=blue, example=green, caveat=amber, actionable=purple).
- **Disagreements** render as a two-column compare block, colored by
  classification.
- **Citations UI — no raw claim-id strings shown.** Each cited item gets a
  numbered superscript (¹²³). A per-section **"Sources"** footer maps each
  number → the claim's quote + locator (page/timestamp). Hovering a superscript
  shows the quote as a tooltip. The underlying `claim_id` stays in the data for
  flag/verify but is never rendered as text.

### Progress tracking (persisted)

- Mark-section-reviewed control per section + an "X / N reviewed" bar.
- **Persisted per project in OpsDB**, keyed `(project_id, concept_id,
  section_id, reviewed)`. New table `study_progress` + accessors. Survives
  reload. Endpoints: read progress for a concept, toggle a section.

### On-demand flashcards + self-check (NOT in base guide)

- Base guide never contains flashcards or self-checks.
- A user action generates them on demand and **may specify a count** (e.g.
  "make 5 flashcards"). Separate endpoint, e.g.
  `POST /api/study/concepts/{id}/flashcards { count }` and
  `POST /api/study/concepts/{id}/selfcheck { count }`.
- Generated from the concept's claims, cited (same superscript/Sources model),
  and **omitted when the claims don't support them** — no invented Q&A.
- Flashcards flip (question front / answer back), animated. Self-check reveals a
  cited answer.

---

## Part 2 — Lossless gated extraction (upstream)

Losslessness moves into extraction and stays grounded. The `lossless-notes`
content tags become **claim kinds** carried on the claim, each still backed by a
verbatim quote.

### Claim schema

- Add a `kind` field to the claim: one of
  `definition | claim | mechanism | example | caveat | actionable`.
- The verbatim `quote` and the trust gate are **unchanged** — a mechanism or
  caveat is only captured if a verbatim quote supports it.
- Propagate `kind` through: `EXTRACTION_SCHEMA`, `ClaimDraft`,
  `to_claim_record`, the Neo4j `Claim` node (new property), `store.py`
  read/write Cypher, and `tests/fakes.py` `InMemoryGraphStore`.
- Default/back-compat: existing claims without a `kind` render as `claim`.

### Extraction prompt

- Revised to explicitly capture, as **separate** gated claims: the core
  claim/definition (what), the mechanism (how/why), examples, caveats/edge
  cases, and actionable steps — each with its own verbatim quote and `kind`.
- If a chunk genuinely has no mechanism/caveat/example, emit none — never
  invent (mirrors `lossless-notes` Step 2).

### Cap

- New tunable `extract.max_claims`, default **15** (was hardcoded ≤8). Trades a
  heavier one-time ingest for lossless gated claims. Wire it into the prompt and
  any parse-side cap.

---

## Part 3 — High-throughput ingest

Package = **A + #3 + #4 + #5** (C deferred). Keeps the durable/resumable
Celery queue, sync Neo4j driver, and Whisper in-process.

### A. Split the ingest queue; thread pool for extraction

- Two workloads currently share the `ingest` queue at prefork concurrency 2:
  `chunk_source_task` (Whisper/embedding, memory-heavy, must stay low) and
  `extract_chunk_task` (pure remote I/O, wants high concurrency). Split them:
  - `prepare` queue → `chunk_source_task`, **prefork `--concurrency=2`**
    (Whisper/memory cage + transcribe lock unchanged).
  - `extract` queue → `extract_chunk_task`, **`--pool=threads
    --concurrency=N`**. Threads fit because every wait releases the GIL
    (OpenRouter httpx, rapidfuzz C-ext, Neo4j bolt socket, Ollama embed httpx).
  - `judge` queue → `synthesize_task`, `--concurrency=1` (unchanged).
- Update `task_routes` in `worker/app.py` (add `prepare`; keep the
  "every task routed" invariant + its regression test), `Makefile`
  (`make worker` split or add `make worker-extract`), and README.
- `N` is a config/env value (e.g. `MSL_EXTRACT_CONCURRENCY`), **bounded to
  `OLLAMA_NUM_PARALLEL`** so local embeds don't become the new choke point.

### #3. Build the extraction graph once per worker process

- `run_extraction` currently calls `build_extraction_graph` per chunk
  (recompiles the StateGraph + re-reads tunables every chunk). Build once per
  worker process (cache on the worker context, alongside router/graph/db) and
  reuse. Tunables read at build time; a worker restart picks up changes.

### #4. Drop no-op escalation

- Escalation re-runs extraction under the `synthesis` role. On the openrouter
  profile both roles resolve to the same model (deepseek-v4-flash), so
  escalation doubles calls/tokens for zero model change. Skip the escalate edge
  when `extraction` and `synthesis` resolve to the same provider+model; keep it
  when they differ (e.g. claude-code profile).

### #5. Batch trust-check embeds + bound concurrency

- The validate step embeds per-draft inside `check_claim`. Batch all drafts'
  embeds into one `router.embed([...])` call per chunk (commit already batches).
- Bound `extract` concurrency to `OLLAMA_NUM_PARALLEL` (see A) so parallel
  extract tasks don't overrun local embedding throughput.

### C. Deferred — chunk-batching knob (documented, not built)

- Packing K chunks into one extraction call is the higher throughput ceiling but
  adds grounding-precision surface (cross-chunk quote attribution; truncation
  fails all K). Ship a `extract.chunk_batch_size` tunable **defaulting to 1**
  (off); raise only against measured rate-limit/overhead data. Design noted so
  it slots in without rework; **not implemented in this effort.**

---

## Part 4 — Route safety / project scoping

- **Bug:** switching project while on `/concepts/:id` keeps the URL; the concept
  id doesn't exist in the new project → the page errors. Ideal: switching
  project shows that project's course.
- **Fix (frontend):** on project switch, navigate to `/curriculum` (the new
  project's course list). `ProjectProvider` sits inside `BrowserRouter`, so
  `useNavigate` is available.
- **Fix (guard, generalized):** id-bearing resource routes validate project
  ownership. Server returns 404 for a concept not in the current project; the
  frontend concept route renders **nothing / a neutral empty state**, never a
  raw error. Applies to any per-resource route reachable by direct URL.

---

## Explicitly out of scope

- Chunk-batching (Part 3 C) — designed, deferred behind a tunable defaulting off.
- gevent/eventlet pool (unsafe with the sync Neo4j bolt driver + torch).
- Async rewrite of extraction (would drop the durable/resumable queue).
- Model-generated code execution / iframes (rejected: security + tokens).
- Chunk-augmented (non-claim) guide content (rejected: keep 100% gated).

## Testing / definition of done

- Extraction: `kind` present on drafts + claims; trust gate still rejects
  non-verbatim quotes; `extract.max_claims` respected. Fakes updated.
- Guide: generator returns schema-valid JSON; every item carries ≥1 claim id;
  items with no claim are dropped; thin concept degrades gracefully; conflicts
  produce a `disagreements` block; memory failure → no personalization, no 500.
- Rendering: superscript → Sources-footer mapping; no raw claim-id text in DOM;
  mini-map jump-links; color-by-kind; progress persists across reload.
- Route safety: cross-project concept URL renders empty (no error); project
  switch navigates to curriculum. Regression test for the guard.
- Throughput: task-routing invariant test covers `prepare`/`extract`/`judge`;
  extraction graph built once per process; escalation skipped when roles match;
  trust-check embeds batched. Existing suites stay green
  (`make check`, `make ui-test`, `make graph-test`).
- No weakening of any base-spec invariant; changes are production-level, not
  patches.
