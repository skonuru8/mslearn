# Corpus UX Round 2 — Retry, Spine Picker, Tab Browse, .env

Date: 2026-07-11
Status: Approved (decisions locked with user)
Scope: mostly frontend + one backend endpoint + run-script config. Builds on branch `fix/blog-user-agent` (User-Agent, prepare-concurrency, SSL-retry, claim-idempotency fixes already landed there).

## Problems / requests

1. **Failed-source retry.** Pause/Resume + failed-*chunk* retry exist, but a source that FAILED TO LOAD (SSL/403 → no chunks) has no way back.
2. **Spine picker UI** on multi-file upload is cramped inline radios — "looks dumb".
3. **Tab click-to-browse.** Clicking the "From my computer" tab when it's already active should open the file dialog (today it only selects the tab).
4. **Constants in `.env`.** Worker knobs (`MSL_PREPARE_CONCURRENCY`, `MSL_EXTRACT_CONCURRENCY`) are read as shell vars by the run scripts, which don't load `.env`.

## Decisions (locked)

1. **Retry = hybrid.** If the source failed at LOAD (status `failed` and `total_chunks == 0`), re-run the whole source (re-prepare). If chunks exist but some failed, reuse the existing failed-chunk retry.
2. **Spine picker = radio-card list** (per ui-ux-pro-max: Forms field-grouping, Touch ≥44px, Style state-clarity).
3. **Tab already-active → browse.** Clicking "From my computer" when active triggers the hidden file input.
4. **`.env` for `MSL_*`.** App already loads `.env` (pydantic `env_file`). Make the run scripts source it too, and ship `.env.example`. `OLLAMA_*` stay with the separate `ollama serve`.

## Design

### R1. Retry a failed source (backend + UI)
- **Backend** (`server/routers/corpus.py`): `POST /api/corpus/sources/{source_id}/retry`.
  - Load the source row. If `status == "failed"` and `total_chunks == 0` (load never produced chunks): reset status to `chunking`, bump `ts`, and re-enqueue `chunk_source_task` (re-run the whole prepare). Reuse `ingest_source`-style enqueue against the stored `ref`.
  - Else (chunks exist): delegate to the existing failed-chunk retry path (`resume_pending` / the current retry-failed handler).
  - Returns `{ source_id, mode: "reload" | "chunks", ... }`.
  - 404 if the source is unknown.
- **Frontend** (`CorpusView.tsx`): show a **Retry** button in the actions cell when `row.status === "failed"` (in addition to the existing failed-chunk "Retry" that shows when `failed_chunks > 0`). Calls the new endpoint, then `refreshSources()`. Keep the copy clear: "Retry".

### R2. Spine picker redesign (`CorpusView.tsx` + `app.css`)
Replace the inline `<fieldset>` radios with a **radio-card list** (only rendered when `uploadFiles.length > 1`):
- Section header: "Which file is the main source?" + helper "The rest become extra reading."
- One row per file: a `<label className="source-card">` wrapping a visually-hidden `<input type="radio" name="main-source">`; the whole row is the click target.
  - Left: file name, truncated with ellipsis (`title={name}` for full text).
  - Right: a **"Main source"** pill when selected; muted "Supplement" when not.
  - Selected state: accent left-border + subtle accent background + a filled dot/check indicator. Unselected: neutral border.
- A11y: real radio semantics (keyboard arrow-key selection, focus ring on the card), `min-height: 44px`, 8px gaps. No emoji — use a CSS dot or an inline SVG check.
- Single-file (≤1) keeps the existing simple checkbox unchanged.
- Selection stays index-based (`mainSourceIndex`) as already implemented.

### R3. Tab click-to-browse (`CorpusView.tsx`)
- Give the file `<input>` a ref. The "From my computer" tab button's `onClick`: if the tab is already active (`addTab === "file"`), call `fileInputRef.current?.click()`; otherwise `setAddTab("file")` as today. The "From a link" tab is unchanged (selecting it focuses the URL field is optional, out of scope).

### R4. `.env` for worker knobs
- Add `.env.example` documenting: `MSL_PREPARE_CONCURRENCY`, `MSL_EXTRACT_CONCURRENCY`, `MSL_OPENROUTER_API_KEY`, `MSL_REDIS_URL` (and a note that `OLLAMA_NUM_PARALLEL`/`OLLAMA_KEEP_ALIVE` belong with `ollama serve`).
- `scripts/dev_up.sh`: near the top, `set -a; [ -f .env ] && . ./.env; set +a` so the celery worker commands see the `.env` values.
- `Makefile`: add a leading `-include .env` / `export` so `worker-prepare`/`worker-extract` pick up `.env` values (or document that `dev_up.sh`/`make run` is the supported path). Keep the existing `${MSL_*:-default}` fallbacks.
- `.env` stays gitignored (verify `.gitignore` has it; `.env.example` is committed).

## Files touched

- `mslearn/server/routers/corpus.py` (retry endpoint)
- `frontend/src/views/CorpusView.tsx` (retry button, spine cards, tab browse), `frontend/src/api/client.ts` (if a helper is added), `frontend/src/app.css` (source-card styles)
- `scripts/dev_up.sh`, `Makefile`, new `.env.example`
- Tests: `tests/test_corpus_*` (retry endpoint), `frontend/src/views/CorpusView.test.tsx`

## Explicitly not doing

- No SSE/polling changes (done in round 1).
- No per-file supplement/spine multi-select (one main, decided prior).
- No moving `OLLAMA_*` into `.env` (separate process).

## Testing

- Retry endpoint: failed+0-chunks → re-enqueues prepare (status back to `chunking`); failed-with-chunks → chunk retry path; unknown id → 404.
- CorpusView: Retry button appears only for `status==="failed"`; clicking calls the endpoint.
- Spine cards: 3 files → 3 selectable cards, first selected, selecting another updates `mainSourceIndex` and the uploaded role (assert via the existing `uploadSource` mock seam).
- Tab browse: clicking the active "From my computer" tab triggers the file input's click (spy on `HTMLInputElement.prototype.click` or the ref).
- `.env`: `.env.example` exists and lists the `MSL_*` keys; `dev_up.sh` sources `.env` (grep the script).

## Success criteria

A load-failed source can be retried in one click; the multi-file main-source picker looks like clean selectable cards; clicking the active upload tab opens the file dialog; and worker concurrency is driven by `.env` so `MSL_PREPARE_CONCURRENCY=8` there (with workers restarted) actually takes effect.
