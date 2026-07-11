# Corpus UX Round 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add failed-source retry, a clean radio-card spine picker, tab click-to-browse, and `.env`-driven worker knobs.

**Architecture:** One backend endpoint + frontend changes in `CorpusView` + run-script config. Builds on branch `fix/blog-user-agent`.

**Tech Stack:** Python (FastAPI), pytest; React + TypeScript, vitest; shell/Make.

Spec: `docs/superpowers/specs/2026-07-11-corpus-ux-round2-design.md`

## Global Constraints

- Work on branch `fix/blog-user-agent`. Backend tests `.venv/bin/pytest tests/<f> -v`; frontend `cd frontend && npx vitest run <f>`, `npx tsc --noEmit`.
- `uploadSource` is XHR — test upload/role behavior via `vi.mock("../api/client", ...)`.
- Spine selection stays index-based (`mainSourceIndex`, already implemented in Round 1).

---

### Task 1: Failed-source retry endpoint

**Files:**
- Modify: `mslearn/server/routers/corpus.py`
- Test: `tests/test_corpus_retry.py` (or extend an existing corpus router test)

**Interfaces:**
- Produces: `POST /api/corpus/sources/{source_id}/retry` → `{ "source_id", "mode": "reload"|"chunks" }`. `reload` when the row is `status=="failed"` and `total_chunks==0`; else `chunks`.

- [ ] **Step 1: Write failing test** (FastAPI TestClient, following existing corpus API tests): register a source, set it `failed` with `total_chunks=0`; POST `/retry` → 200, `mode=="reload"`, and the source status is back to `chunking`. Second case: a source with `failed_chunks>0` and chunks present → `mode=="chunks"`. Unknown id → 404.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** the endpoint. Read the row via `ctx.db.source_row`. If `None` → 404. If `row["status"]=="failed" and row["total_chunks"]==0`: `ctx.db.set_source_status(source_id,"chunking", clear_error=True)`, then re-enqueue prepare — call `chunk_source_task.delay(project_id, source_id, row["ref"], row["role"], None, True)` (import already present in the router's module or add it), bumping `ts` via `ctx.db.register_source(...)` if that is how ts is refreshed (match how `ingest_source` re-registers). Return `mode="reload"`. Else: run the existing failed-chunk retry (reuse `resume_pending(project_id)` or whatever the current `/retry-failed` handler calls) and return `mode="chunks"`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(corpus): retry a failed source (reload vs chunk retry)"`

---

### Task 2: Retry button in the UI

**Files:**
- Modify: `frontend/src/views/CorpusView.tsx`
- Test: `frontend/src/views/CorpusView.test.tsx`

- [ ] **Step 1: Write failing test:** render CorpusView with a source row `status:"failed"`; assert a "Retry" button renders; mock `/api/corpus/sources/s1/retry` and assert clicking calls it.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement:** add `onRetrySource(sourceId)` that POSTs the new endpoint then `refreshSources()`. In the actions cell, when `row.status === "failed"`, render a `Retry` button. (Keep the existing failed-chunk Retry that shows when `failed_chunks > 0`.)
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(corpus): retry button for failed sources"`

---

### Task 3: Radio-card spine picker

**Files:**
- Modify: `frontend/src/views/CorpusView.tsx`, `frontend/src/app.css`
- Test: `frontend/src/views/CorpusView.test.tsx`

- [ ] **Step 1: Update/keep test:** the Round-1 test that selects 3 files and checks the radios must still pass with the new markup (radios keep `name="main-source"` and accessible names = file names). Add an assertion that selecting the 2nd card sets it checked. Run → adjust if the markup change breaks the query.
- [ ] **Step 2: Implement** the radio-card list. Replace the inline `<fieldset className="main-source-picker">` block (rendered when `uploadFiles.length > 1`) with:
  ```tsx
  <fieldset className="main-source-picker">
    <legend>Which file is the main source?</legend>
    <p className="hint">The rest become extra reading.</p>
    {uploadFiles.map((file, i) => (
      <label key={i} className={`source-card ${i === mainSourceIndex ? "is-main" : ""}`}>
        <input type="radio" name="main-source" className="source-card-radio"
          checked={i === mainSourceIndex} onChange={() => setMainSourceIndex(i)} />
        <span className="source-card-name" title={file.name}>{file.name}</span>
        <span className="source-card-tag">{i === mainSourceIndex ? "Main source" : "Supplement"}</span>
      </label>
    ))}
  </fieldset>
  ```
- [ ] **Step 3: Add CSS** to `app.css`:
  ```css
  .main-source-picker { border: none; padding: 0; margin: 0.5rem 0; }
  .main-source-picker legend { font-weight: 600; padding: 0; }
  .source-card { display: flex; align-items: center; gap: 0.75rem; min-height: 44px;
    padding: 0.5rem 0.75rem; margin-top: 0.5rem; border: 1px solid var(--border, #d0d5dd);
    border-radius: 8px; cursor: pointer; }
  .source-card:focus-within { outline: 2px solid var(--accent, #2563eb); outline-offset: 1px; }
  .source-card.is-main { border-color: var(--accent, #2563eb); background: rgba(37,99,235,0.06); }
  .source-card-radio { accent-color: var(--accent, #2563eb); width: 18px; height: 18px; flex: none; }
  .source-card-name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .source-card-tag { font-size: 0.8rem; color: #667085; flex: none; }
  .source-card.is-main .source-card-tag { color: var(--accent, #2563eb); font-weight: 600; }
  ```
  (Use existing CSS variables if the file defines them; otherwise the fallbacks above.)
- [ ] **Step 4: Run** `cd frontend && npx vitest run src/views/CorpusView.test.tsx` → PASS; `npx tsc --noEmit` → clean.
- [ ] **Step 5: Commit** `git commit -am "feat(corpus): radio-card main-source picker"`

---

### Task 4: Tab click-to-browse

**Files:**
- Modify: `frontend/src/views/CorpusView.tsx`
- Test: `frontend/src/views/CorpusView.test.tsx`

- [ ] **Step 1: Write failing test:** the "From my computer" tab is active by default; spy on the file input's `click` (e.g. `vi.spyOn(HTMLInputElement.prototype, "click")`); click the "From my computer" tab button; assert the file input `click` was invoked. (When the tab is NOT active, clicking it only switches tabs — assert no file click.)
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement:** add `const fileInputRef = useRef<HTMLInputElement>(null);` on the file `<input>`. Change the "From my computer" tab button `onClick` to: `if (addTab === "file") fileInputRef.current?.click(); else setAddTab("file");`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(corpus): active upload tab click opens file dialog"`

---

### Task 5: `.env` for worker knobs

**Files:**
- Create: `.env.example`
- Modify: `scripts/dev_up.sh`, `Makefile`
- Verify: `.gitignore` ignores `.env`

- [ ] **Step 1:** Create `.env.example`:
  ```
  # Worker concurrency (read by scripts/dev_up.sh and make run)
  MSL_PREPARE_CONCURRENCY=8
  MSL_EXTRACT_CONCURRENCY=8
  # App settings (loaded by pydantic Settings)
  MSL_OPENROUTER_API_KEY=
  MSL_REDIS_URL=redis://localhost:6380/0
  # NOTE: OLLAMA_NUM_PARALLEL / OLLAMA_KEEP_ALIVE belong with your `ollama serve`, not here.
  ```
- [ ] **Step 2:** In `scripts/dev_up.sh`, near the top (before the celery launches), add:
  ```sh
  set -a; [ -f .env ] && . ./.env; set +a
  ```
- [ ] **Step 3:** In `Makefile`, add at the top (after `.PHONY`): `-include .env` and `export` so recipe env includes `.env` values (keep the `${MSL_*:-default}` fallbacks intact).
- [ ] **Step 4:** Confirm `.gitignore` contains `.env` (add if missing). Do NOT commit a real `.env`.
- [ ] **Step 5: Verify** `bash -n scripts/dev_up.sh` parses; `grep MSL_PREPARE_CONCURRENCY .env.example` matches.
- [ ] **Step 6: Commit** `git commit -am "chore(config): .env for worker knobs, sourced by run scripts"`

---

### Task 6: Verify

- [ ] **Step 1:** `.venv/bin/pytest -q` → pass (neo4j-marked skip as usual).
- [ ] **Step 2:** `cd frontend && npx vitest run` → pass; `npx tsc --noEmit` → clean.
- [ ] **Step 3:** Commit stragglers.

## Self-Review

- R1→T1,T2. R2→T3. R3→T4. R4→T5. `mainSourceIndex` consistent with Round 1. Retry endpoint name matches UI call.
