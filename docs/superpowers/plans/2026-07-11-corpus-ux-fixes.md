# Corpus UX Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** New projects jump to My materials; multi-file uploads pick one main source and upload concurrently; background polling is reduced; the file/link tab selection is preserved.

**Architecture:** Frontend-only React/TypeScript changes in `ProjectSwitcher`, `CorpusView`, `CurriculumView`. No backend changes.

**Tech Stack:** React + TypeScript + Vite, vitest, @testing-library/react.

Spec: `docs/superpowers/specs/2026-07-11-corpus-ux-fixes-design.md`

## Global Constraints

- Frontend only. Run tests: `cd frontend && npx vitest run <file>`; typecheck `cd frontend && npx tsc --noEmit`.
- `uploadSource` uses `XMLHttpRequest`, not `fetch`; to test upload behavior, `vi.mock("../api/client", ...)` the `uploadSource` export.
- Never clear `uploadFiles`, `mainSourceIndex`, or `linkRef` on tab switch.

---

### Task 1: New project navigates to My materials

**Files:**
- Modify: `frontend/src/components/ProjectSwitcher.tsx`
- Test: `frontend/src/components/ProjectSwitcher.test.tsx`

- [ ] **Step 1: Write failing test.** Mock `react-router-dom`'s `useNavigate` (`vi.mock`), render `ProjectSwitcher` with a stub `useProject` (createProject resolves), type a name, click "Add", assert `navigate` was called with `"/corpus"`.
- [ ] **Step 2: Run** `cd frontend && npx vitest run src/components/ProjectSwitcher.test.tsx` → FAIL.
- [ ] **Step 3: Implement.** In `onCreate`, after `await createProject(name); setNewName("");` add `navigate("/corpus");`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(ui): new project navigates to My materials"`

---

### Task 2: Multi-file main-source picker

**Files:**
- Modify: `frontend/src/views/CorpusView.tsx`
- Test: `frontend/src/views/CorpusView.test.tsx`

**Interfaces:**
- Produces: state `mainSourceIndex: number | null`; role for file at index `i` = `uploadFiles.length > 1 ? (i === mainSourceIndex ? "spine" : "supplement") : (isMainCourse ? "spine" : "supplement")`.

- [ ] **Step 1: Write failing test.** Render CorpusView; upload 3 files via `userEvent.upload(input, [f1,f2,f3])`; assert a radiogroup with 3 radios appears, the first is checked, and selecting the 2nd checks it. (Label text: /which of these is your main source/i.)
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
  - Add `const [mainSourceIndex, setMainSourceIndex] = useState<number | null>(null);`
  - In `onFilesChosen`: after `setUploadFiles(arr)`, `setMainSourceIndex(arr.length ? 0 : null);`
  - After the single checkbox, render (only when `uploadFiles.length > 1`) a `<fieldset className="main-source-picker">` with legend "Which of these is your main source? The rest become extra reading." and a radio per file:
    ```tsx
    {uploadFiles.map((file, i) => (
      <label key={i}>
        <input type="radio" name="main-source" checked={i === mainSourceIndex}
          onChange={() => setMainSourceIndex(i)} />
        {file.name}
      </label>
    ))}
    ```
  - Hide the single "main course" checkbox when `uploadFiles.length > 1` (wrap it in `uploadFiles.length > 1 ? null : (<label className="toggle-row">...)`).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(corpus): pick one main source on multi-file upload"`

---

### Task 3: Concurrent uploads with per-file role

**Files:**
- Modify: `frontend/src/views/CorpusView.tsx` (`onUpload`)
- Test: `frontend/src/views/CorpusView.test.tsx`

- [ ] **Step 1: Write failing test.** `vi.mock("../api/client")` exposing a `uploadSource` that returns a controllable deferred promise and records `(file.name, role)`. Select 2 files (pick file 2 as main), submit; assert **both** `uploadSource` calls happen before either resolves (concurrency), and the recorded roles are `spine` for file 2 and `supplement` for file 1. Resolve both; assert `refreshSources` called.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `onUpload`:
  ```tsx
  const roleFor = (i: number) =>
    uploadFiles.length > 1
      ? (i === mainSourceIndex ? "spine" : "supplement")
      : (isMainCourse ? "spine" : "supplement");
  setUploading(true);
  let completed = 0;
  setUploadIndex({ current: 0, total: uploadFiles.length });
  const results = await Promise.allSettled(
    uploadFiles.map((file, i) =>
      uploadSource(file, roleFor(i), false).then((r) => {
        completed += 1;
        setUploadIndex({ current: completed, total: uploadFiles.length });
        return r;
      }),
    ),
  );
  const failed = uploadFiles.filter((_, i) => results[i]!.status === "rejected").map((f) => f.name);
  setUploadFiles([]); setMainSourceIndex(null); setUploadIndex(null);
  spineTouched.current = false;
  await refreshSources();
  if (failed.length) captureError(new Error(`These files failed to upload: ${failed.join(", ")}`), "Upload failed");
  else setUserError(null);
  setUploading(false);
  ```
  Replace the per-file `uploadPercent` progress with an aggregate label using `uploadIndex`: `Uploading ${uploadIndex.total} files… (${uploadIndex.current} of ${uploadIndex.total} done)` and a `<progress max={uploadIndex.total} value={uploadIndex.current} />`. Drop the old `uploadPercent` state and its onProgress usage (concurrent per-file percentages aren't shown).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(corpus): upload files concurrently with per-file role"`

---

### Task 4: Reduce polling

**Files:**
- Modify: `frontend/src/views/CorpusView.tsx`, `frontend/src/views/CurriculumView.tsx`
- Test: `frontend/src/views/CurriculumView.test.tsx`

- [ ] **Step 1: Write failing test** (CurriculumView): with fake timers, mount in a "building" state; assert no `/api/study/curriculum` refetch before 15s; advance 15s → one refetch; set `document.hidden=true` (dispatch `visibilitychange`) → advancing another 15s does NOT refetch.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
  - CorpusView sources poll: change `3000` → `5000`.
  - CorpusView synthesis-status poll: change `10_000` → `15_000`, and convert to the start/stop-on-visibility pattern (clear the interval on `visibilitychange` hidden, restart on visible) — model it on the sources-poll effect's `start`/`stop`/`onVisibilityChange` structure.
  - CurriculumView building poll: change `10_000` → `15_000`, add the same clear-on-hidden / restart-on-visible handling.
- [ ] **Step 4: Run** CurriculumView + CorpusView tests → PASS.
- [ ] **Step 5: Commit** `git commit -am "perf(ui): slower polling, stop when hidden"`

---

### Task 5: Preserve tab selection

**Files:**
- Modify: `frontend/src/views/CorpusView.tsx` (verify no reset; add CSS class only if needed)
- Test: `frontend/src/views/CorpusView.test.tsx`

- [ ] **Step 1: Write failing test.** Upload 2 files; click the "From a link" tab; type a link; click "From my computer" tab; assert "2 files selected" (and the main-source radios) still render AND the link value persists after switching back to link. (Confirms neither state is cleared.)
- [ ] **Step 2: Run** → likely PASS already (state isn't reset); if it fails, the fix is to ensure `setAddTab` only sets the tab and to render the file summary from persisted `uploadFiles`. If it passes, keep the test as a regression guard.
- [ ] **Step 3: Commit** `git commit -am "test(corpus): guard tab-switch selection preservation"`

---

### Task 6: Verify

- [ ] **Step 1:** `cd frontend && npx vitest run` → all pass.
- [ ] **Step 2:** `cd frontend && npx tsc --noEmit` → clean.
- [ ] **Step 3:** Commit any straggler.

## Self-Review

- F1→T1, F2→T2, F3→T3, F4→T4, F5→T5. All spec sections covered. `mainSourceIndex` name consistent across tasks 2/3.
