# Corpus UX Fixes — Navigation, Multi-Upload, Concurrency, Polling, Tab State

Date: 2026-07-11
Status: Approved (design decisions locked with user)
Scope: Frontend only (`frontend/src/...`). No backend changes. One spec → one plan → one Sonnet agent.

## Problems

1. Creating a new project from anywhere leaves you on the current page (e.g. My course), which is empty for a brand-new project. It should jump to **My materials** so there's something to do.
2. A first-time multi-file upload applies one role (via the single "main course" checkbox) to *all* files — no way to say which file is the main source.
3. Multi-file upload processes files **sequentially** (client-side `for`-loop awaits each POST), so "one file is read after another."
4. The frontend makes frequent background REST calls (sources every 3s, synthesis status every 10s, curriculum every 10s) — too chatty.
5. Choosing files on the "From my computer" tab and then clicking "From a link" (or vice versa) should not lose the earlier selection.

## Decisions (locked)

1. **New project → `navigate("/corpus")`.** Switching to an *existing* project keeps its current behavior (`navigate("/curriculum")`).
2. **Multi-file spine = pick exactly one main.** When >1 file is selected, show the file list with a radio "main source" selector; exactly one file is `spine`, the rest are `supplement`. The first file is preselected. A **single** file upload keeps the current simple "Is this your main book or course?" checkbox.
3. **Concurrent uploads, no artificial cap.** Fire all files' upload POSTs concurrently (`Promise.allSettled`) and let the existing backend/worker concurrency read them in parallel, matching how chunk processing already fans out. No new client-side limit.
4. **Slower + smarter polling.** Raise intervals, stop entirely when nothing is active/building, and pause when the tab is hidden (clear the interval, don't just skip inside it). No backend/SSE work.
5. **Preserve tab state.** `uploadFiles`/`mainSourceName` and `linkRef` are independent state, never cleared on tab switch; the file-selection summary re-renders when returning to the file tab.

## Design

### F1. New-project navigation
`ProjectSwitcher.onCreate` (`components/ProjectSwitcher.tsx`) awaits `createProject(name)` then calls `navigate("/corpus")`. `useNavigate` is already imported. No change to `ProjectContext.createProject` (it stays navigation-agnostic; navigation is a view concern).

### F2. Multi-file main-source selection (`views/CorpusView.tsx`)
- State: keep `uploadFiles: File[]`. Add `mainSourceIndex: number | null` — the array position of the chosen main file (index, not name, to avoid duplicate-name ambiguity). `null` = none yet.
- When files are chosen: set `uploadFiles` and default `mainSourceIndex` to `files.length ? 0 : null`.
- Rendering:
  - **0–1 files**: render the existing single "main course" checkbox (drives `isMainCourse`), unchanged, including the spine-default-off behavior already shipped.
  - **>1 files**: render a `<fieldset>` list of the selected files, each a radio (`name="main-source"`), checked when its array index `=== mainSourceIndex`; selecting sets `mainSourceIndex`. Label: "Which of these is your main source? The rest become extra reading." Hide the single checkbox in this mode.
- Per-file role at upload, by index `i`: `role = (uploadFiles.length > 1) ? (i === mainSourceIndex ? "spine" : "supplement") : (isMainCourse ? "spine" : "supplement")`.

### F3. Concurrent upload (`views/CorpusView.tsx onUpload`)
Replace the sequential `for` loop with concurrent uploads:
- Build an array of upload promises, one per file, each calling `uploadSource(file, roleFor(file), false, perFileProgress)`.
- `await Promise.allSettled(promises)`; collect rejected file names into `failed`.
- Progress: since files upload in parallel, replace the single "file X of N" counter with an aggregate determinate bar of `completed/total` (a `completed` counter incremented as each promise settles), labeled "Uploading N files… (C of N done)".
- After settle: clear `uploadFiles`, reset progress, `spineTouched.current = false`, `refreshSources()`, and surface `failed` via `captureError` if any.

### F4. Polling reduction
- `CorpusView` active-sources poll: **3s → 5s** (keep existing visibility start/stop + stop-when-no-active-source).
- `CorpusView` synthesis-status poll: **10s → 15s**, and convert it to the same start/stop-on-visibility pattern the sources poll uses (clear the interval when the tab is hidden, restart on show), instead of only checking `visibilityState` inside the tick.
- `CurriculumView` building poll: **10s → 15s**, add hidden-pause (clear interval when hidden, restart on show) in addition to the existing `building` gate.
- No polling when idle: all three already gate on "active / running / building"; keep those guards.

### F5. Tab state preservation
- Confirm `setAddTab` never resets `uploadFiles`, `mainSourceName`/`mainSourceIndex`, or `linkRef` (currently it doesn't). Add a regression test.
- When returning to the file tab, the "N files selected" / "Selected: name" summary and the main-source picker re-render from persisted state so the selection is visibly retained.

## Files touched

- `frontend/src/components/ProjectSwitcher.tsx` (F1)
- `frontend/src/views/CorpusView.tsx` (F2, F3, F5)
- `frontend/src/views/CurriculumView.tsx` (F4)
- Tests: `frontend/src/components/ProjectSwitcher.test.tsx`, `frontend/src/views/CorpusView.test.tsx`, `frontend/src/views/CurriculumView.test.tsx`

## Explicitly not doing

- No backend changes; no SSE.
- No change to `ProjectContext` navigation semantics.
- No per-file pause/resume in the upload picker (YAGNI).

## Testing

- ProjectSwitcher: creating a project navigates to `/corpus` (mock `useNavigate`).
- CorpusView F2: selecting 3 files shows a radio list; the first is preselected; changing selection updates which file uploads as `spine` (assert the roles passed to `uploadSource`, via a mockable seam).
- CorpusView F3: two files upload concurrently — both POSTs are in flight before either resolves (assert via a deferred `uploadSource` mock), and a single failure still lets the other complete + surfaces an error.
- CorpusView F5: pick files → switch to link tab → switch back → selection summary still shown; type a link → switch to file tab → link still present.
- CurriculumView F4: interval is 15s and does not fire while `document.hidden`.

Note: `uploadSource` uses `XMLHttpRequest`, not `fetch`, so the existing `installFetchMock` can't intercept it. Introduce a thin injectable seam (e.g. import `uploadSource` and `vi.mock("../api/client", ...)` for it) so upload concurrency/role tests can assert calls without real network.

## Success criteria

New projects land on My materials; multi-file uploads ask which file is the main source and upload all files concurrently; background REST chatter drops noticeably (idle = zero calls, hidden tab = zero calls); switching between the file and link tabs never loses what you already entered.
