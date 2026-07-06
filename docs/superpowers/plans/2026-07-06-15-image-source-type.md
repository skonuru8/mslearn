# Plan 15 — Images as a Source Type (notes from images)

**Status:** ready for implementation
**Depends on:** `9b9b91b` (Plan 14 complete)
**User decisions (locked):**
- Images become a **new source type** — they join a project like PDFs/videos,
  flow through claim extraction → concept graph → course/quiz/chat/export. "Notes
  from images" = the same per-concept Markdown the app already produces, now
  covering image content.
- **Grounding:** image-derived claims are honestly labeled. A multimodal model
  transcribes everything readable in the image (verbatim, including text inside
  nested screenshots) plus describes non-text visuals; all image claims carry a
  distinct **`image_observed`** trust tier so provenance is visible (they came
  from model-read pixels, not an authoritative text quote).
- **Model:** NO separate "vision model." Images go to a multimodal model over the
  **same OpenRouter key/API**, added as an `image` role in `profiles.yaml`.
  Probe first (Task 1.4): if `deepseek/deepseek-v4-flash` accepts image input,
  point the role at it (zero new model); if not, point at another multimodal
  model on the same OpenRouter account. Reasoning off, like the other roles.
- Nested content ("screenshot of a browser page, image inside image") is read in
  ONE pass — the model sees pixels and reads nested text/UI naturally. No
  recursive cropping / object detection.
- Batch upload: each uploaded image is its own source in the project.

## Task 1 — Provider image support + `image` role

Currently `ModelMessage.content` is a plain string (`providers/base.py:9-11`);
no provider can send an image.

- `providers/base.py`: add an optional `images: list[str] | None = None` field to
  `ModelMessage` (each entry a base64 `data:` URL, e.g. `data:image/png;base64,…`).
  Text-only messages are unchanged (field defaults None) so nothing else breaks.
- `providers/openrouter.py::_body`: when a message has images, emit OpenAI-style
  content-array format for that message:
  `content: [{type:"text", text:…}, {type:"image_url", image_url:{url:<data-url>}}, …]`.
  Text-only messages keep the plain-string content. (OpenRouter/OpenAI multimodal
  wire format.)
- `providers/ollama.py::_body`: Ollama `/api/chat` takes a per-message
  `images: [<base64-no-prefix>]` field — support it (strip the `data:…;base64,`
  prefix). Enables a local VLM via the offline profile if the user ever wants it.
- `providers/router.py` + `profiles.py`: add `image` to the known roles. Router
  resolves it like any other role.
- `profiles.yaml`: add an `image` role to the `openrouter` profile,
  `params: {reasoning: {enabled: false}}`, model set per the probe below. Add to
  `offline`/`claude-code` too (offline → a local Ollama VLM id as a placeholder,
  clearly commented as needing a pulled model; don't block on it).
- **1.4 Probe result (live, 2026-07-06, key present):** `deepseek/deepseek-v4-flash`
  is **text-only** — text completion works, but adding an image content part
  returns `404 Not Found`. Probed real multimodal models: `openai/gpt-4o-mini`
  ✅ read a text image correctly (~$0.0013/img), `anthropic/claude-3-haiku` ✅
  (~$0.0000245/img, weaker on dense layouts), `meta-llama/llama-3.2-11b-vision`
  ✅ (~$0.00056/img); `anthropic/claude-3.5-sonnet`, `google/gemini-*`,
  `qwen/qwen-2-vl-7b` returned 404 (not available on this account). **Chosen:
  `openai/gpt-4o-mini`** for the openrouter profile's `image` role — reliable on
  dense/nested screenshots, cheap enough, same key, no reasoning param. offline/
  claude-code profiles use a local `qwen2.5vl:7b` (needs `ollama pull`).
- Tests (offline, fake provider): a `ModelMessage` with images round-trips
  through `openrouter._body` into content-array shape; text-only stays a string;
  ollama body carries the `images` field with the prefix stripped.

## Task 2 — Image adapter

New `mslearn/adapters/image.py::load_image(path, role="supplement", *, describe=None) -> SourceDocument`:
- `describe` is an injectable callable `(image_bytes, media_type) -> str` (defaults
  to a real implementation that calls the router's `image` role with a fixed
  prompt). Injectable so tests never call a live model.
- Prompt (registry entry `image_transcribe` in `prompts.py`, so it's tunable):
  instruct the model to output faithful Markdown of the image — transcribe ALL
  readable text verbatim (including text inside nested screenshots/browser
  windows), preserve reading order and headings, and for non-text visual content
  (diagrams, photos, charts) write a bracketed description like
  `[image: bar chart showing …]`. One pass; nested content included.
- Encode the file as a base64 data URL, detect media type from suffix, build a
  `ModelMessage(role="user", content=<prompt>, images=[data_url])`, call the
  router's `image` role, take the returned Markdown as the document text.
- Build a `SourceDocument(source_type="image", role=role, title=<filename>,
  units=[…])` — split the returned Markdown into structural units by heading /
  paragraph so chunking + locators work (locator kind `"image"`, carry the
  source filename; no page/time). Reuse the blog/markdown unit-splitting approach
  if one exists, else a simple paragraph splitter.
- `adapters/registry.py`: add `.png/.jpg/.jpeg/.webp/.gif/.bmp/.heic` → `"image"`
  in `_SUFFIX_TYPES`; in `load_source`, route `"image"` to `load_image`, passing
  a `describe` built from `ctx`/router (thread it like the transcriber in
  `chunk_source_task`, OR construct the default describe inside `load_image` from
  `get_context().router` — match whichever pattern the transcriber wiring used in
  Plan 14 so it's consistent). NOTE: images are refs = local file paths (uploads);
  no URL form needed for v1.
- `server/routers/corpus.py`: add the image suffixes to `_UPLOAD_SUFFIXES`.
- Frontend `CorpusView` file input `accept` + `utils/userMessages` type detection:
  add image extensions; batch-select allowed (each file → one `uploadSource` call
  → one source).
- Tests: `load_image` with a fake `describe` returning canned Markdown (with a
  simulated nested-screenshot text block + a `[image: …]` visual block) produces
  a `SourceDocument` with the expected units; registry routes image suffixes;
  upload endpoint accepts a `.png`.

## Task 3 — `image_observed` trust tier

Image claims must be included in teaching/notes but visibly labeled as
image-sourced (they are model-read, not verbatim quotes from an authored text).

- Trust values today: `trusted` / `escalated` / `rejected` (grep
  `graph/store.py`, `pipeline/trust.py`, `pipeline/contracts.py`,
  `worker/tasks.py`). Add `image_observed` as an accepted value everywhere the
  set is enumerated, and include it wherever `trusted`/`escalated` are treated as
  usable (retrieval filters in `qa.py`, `quiz.py`, `teaching.py`, exports —
  search for the `{"trusted","escalated"}` frozensets and add it).
- Assignment: when `extract_chunk_task` commits claims for a chunk whose source
  is an image (source_type `"image"`), set `trust="image_observed"` instead of
  `"trusted"`/`"escalated"`. Simplest signal: the task already loads the chunk;
  thread the source_type (or check `source_id`/graph) — pick the cheapest
  reliable check. The trust gate still runs (quote must fuzzy-match the
  transcription, so a claim still has to be grounded in what the image said);
  only the resulting tier differs.
- UI: teaching/claim rows and the concept view show a small "from image" badge
  when a claim's trust is `image_observed` (types + a badge in the claim
  rendering components). Plain language.
- Tests: an image source's committed claims carry `image_observed`; retrieval/
  teaching include them; a badge renders for them (vitest).

## Task 4 — End-to-end + docs

- End-to-end offline test: eager Celery, fake `describe`, ingest one image source
  → chunks → claims (`image_observed`) → a concept forms → teaching Markdown
  includes the image-derived content with the badge/label. Mirror the existing
  full-pipeline test for other source types.
- README: one paragraph under Sources — "Images (screenshots, slides, photos,
  diagrams): a multimodal model reads all visible text (including nested
  screenshots) plus describes visuals; claims are labeled *from image*."
- Update `docs/superpowers/audits/2026-07-04-architecture-conformance.md` adapters
  row to include image (5th source type) with its grounding note.

## Out of scope (v1)
- Recursive sub-image cropping / object detection (one pass reads nested content).
- Image URLs (only uploaded files); PDFs-of-scanned-images (that's the PDF path).
- Deterministic Tesseract OCR cross-check (VLM transcription is the source text;
  note Tesseract as optional future hardening).

## Conventions
Cypher in `graph/store.py`; tunables/prompts via registry; providers stay behind
the `ModelProvider` interface; every model call logged; offline tests with fakes
(NO live model in tests); graph tests only via `make graph-test`. Suites green
per commit; conventional commits with the standard trailer. Do not push. Do not
restart/kill the user's running processes.

## Verification
1. `make check` + `make ui-test` + `make ui-build` green; `make graph-test` green.
2. Offline end-to-end: a fake-described image ingests to `image_observed` claims
   that surface in teaching with a "from image" label.
3. Live smoke (only if key present, manual, in final report — not a committed
   test): upload a real screenshot containing a nested browser image, confirm the
   transcription captures nested text and the course shows image-labeled notes.
4. The `image` role model is set from the probe result, documented in this file.
