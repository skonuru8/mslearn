# Interactive Study Guide + Lossless Extraction + Throughput — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace per-concept markdown teaching with a colorful interactive study guide rendered from model-emitted JSON; capture mechanisms/caveats/examples as verbatim-gated claim kinds; re-architect ingest for throughput; make resource routes project-safe.

**Architecture:** The model emits a structured guide JSON (never code); native React renders it (no iframe/exec). Lossless-notes tags become a `kind` field on each trust-gated claim. Ingest splits into `prepare` (memory-caged prefork) and `extract` (I/O thread pool) queues. Concept routes render nothing when the id isn't in the active project.

**Tech Stack:** Python 3.12 / FastAPI / Celery+Redis / Neo4j / LangGraph / SQLite (OpsDB) · React+Vite+TypeScript · pytest / vitest.

## Global Constraints

- **Trust gate unchanged:** every claim keeps a verbatim `quote` that must match its chunk (rapidfuzz + embed cosine). No change here weakens it.
- **Every displayed fact traces to a trust-gated claim.** Guide items with no `claim` id are dropped at build time. The model never invents.
- **Memory is advisory/personalization-only:** any memory call is wrapped so failure degrades to "no personalization" and never 500s an endpoint.
- **Durable/resumable/per-chunk-idempotent ingest queue** stays intact; sync Neo4j bolt driver and Whisper stay in-process (no gevent, no async rewrite).
- **All Cypher lives in `mslearn/graph/store.py`.** Callers never write Cypher.
- **Model IDs live only in `profiles.yaml`.** Tunables live in `TUNABLE_DEFAULTS`.
- **Project scoping:** every graph/DB access is `project_id`-scoped; endpoints resolve it via `get_project_id`.
- Backend tests run with `make check`; UI with `make ui-test`; graph with `make graph-test` (disposable Neo4j, never production).

---

## Phase 1 — Route safety (independent; fixes the live project-switch bug)

### Task 1.1: Switching project navigates to the course list

**Files:**
- Modify: `frontend/src/components/ProjectSwitcher.tsx`
- Test: `frontend/src/views/CorpusView.test.tsx` pattern → new `frontend/src/components/ProjectSwitcher.test.tsx`

**Interfaces:**
- Consumes: `useProject().setProjectId`, `react-router-dom` `useNavigate`.
- Produces: on project change, `navigate("/curriculum")` fires after `setProjectId`.

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/components/ProjectSwitcher.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { describe, it, expect, vi } from "vitest";
import { ProjectSwitcher } from "./ProjectSwitcher";
import { ProjectProvider } from "../context/ProjectContext";

vi.mock("../api/client", () => ({
  api: vi.fn(async (path: string) =>
    path === "/api/projects"
      ? [
          { project_id: "default", name: "Default", created_ts: 0 },
          { project_id: "p2", name: "Biology", created_ts: 0 },
        ]
      : {},
  ),
}));

it("navigates to the curriculum when the project changes", async () => {
  render(
    <MemoryRouter initialEntries={["/concepts/abc123"]}>
      <ProjectProvider>
        <ProjectSwitcher />
        <Routes>
          <Route path="/concepts/:id" element={<div>concept page</div>} />
          <Route path="/curriculum" element={<div>my course list</div>} />
        </Routes>
      </ProjectProvider>
    </MemoryRouter>,
  );
  await screen.findByRole("combobox", { name: /learning project/i });
  fireEvent.change(screen.getByRole("combobox", { name: /learning project/i }), {
    target: { value: "p2" },
  });
  expect(await screen.findByText("my course list")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `cd frontend && npx vitest run src/components/ProjectSwitcher.test.tsx`
Expected: FAIL — still shows "concept page" (no navigation).

- [ ] **Step 3: Implement**

In `ProjectSwitcher.tsx`, add `import { useNavigate } from "react-router-dom";`, call `const navigate = useNavigate();` inside the component, and change the select handler:

```tsx
onChange={(event) => {
  setProjectId(event.target.value);
  navigate("/curriculum");
}}
```

- [ ] **Step 4: Run it, confirm it passes**

Run: `cd frontend && npx vitest run src/components/ProjectSwitcher.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ProjectSwitcher.tsx frontend/src/components/ProjectSwitcher.test.tsx
git commit -m "fix(ui): switching project returns to the course list, not a stale concept"
```

### Task 1.2: Concept route renders a neutral empty state (not an error) when the concept isn't in this project

**Files:**
- Modify: `frontend/src/views/ConceptView.tsx`
- Modify: `frontend/src/views/ConceptView.test.tsx`

**Interfaces:**
- Consumes: `ApiError.status` from `../api/client`.
- Produces: a 404 from the concept fetch renders a "not in this project" panel with a link to the course, never the red `ErrorBanner`.

- [ ] **Step 1: Write the failing test**

```tsx
// add to ConceptView.test.tsx
it("shows a neutral 'not in this project' panel on 404, not an error", async () => {
  const { ApiError } = await import("../api/client");
  vi.mocked(api).mockRejectedValue(new ApiError("unknown concept 'x'", 404));
  renderConcept("x"); // existing helper that mounts <ConceptView/> at /concepts/x
  expect(await screen.findByText(/not part of this project/i)).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `cd frontend && npx vitest run src/views/ConceptView.test.tsx`
Expected: FAIL — renders the ErrorBanner (role="alert") instead.

- [ ] **Step 3: Implement**

In `ConceptView.tsx`: import `ApiError`, track a `notInProject` state, set it in the `catch`:

```tsx
import { api, ApiError } from "../api/client";
// ...
const [notInProject, setNotInProject] = useState(false);
// inside load()'s catch:
} catch (err) {
  if (err instanceof ApiError && err.status === 404) {
    setNotInProject(true);
    setError(null);
  } else {
    setError(err instanceof Error ? err.message : "Failed to load concept");
  }
}
```

Reset `setNotInProject(false)` in the `useEffect` that clears state on id change, and render before the error branch:

```tsx
if (notInProject) {
  return (
    <section className="panel">
      <h1>Topic not in this project</h1>
      <p>This topic isn’t part of the project you’re viewing.</p>
      <Link to="/curriculum">Go to this project’s course</Link>
    </section>
  );
}
```

(Add `import { Link } from "react-router-dom";`.)

- [ ] **Step 4: Run it, confirm it passes**

Run: `cd frontend && npx vitest run src/views/ConceptView.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/ConceptView.tsx frontend/src/views/ConceptView.test.tsx
git commit -m "fix(ui): cross-project concept URL renders a neutral empty state, not an error"
```

---

## Phase 2 — Lossless gated extraction (foundation for the guide)

### Task 2.1: Add `kind` to the claim contract

**Files:**
- Modify: `mslearn/pipeline/contracts.py`
- Modify: `mslearn/graph/records.py`
- Test: `tests/test_contracts.py` (add cases)

**Interfaces:**
- Produces: `ClaimDraft.kind: str`; `EXTRACTION_SCHEMA` requires `kind`; `ClaimRecord.kind: str`; `to_claim_record(..., )` copies `draft.kind`.
- Consumers (Task 2.2, 2.3, Phase 3) read `claim["kind"]`.

`CLAIM_KINDS = ("definition", "claim", "mechanism", "example", "caveat", "actionable")`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_contracts.py
from mslearn.pipeline.contracts import CLAIM_KINDS, ClaimDraft, parse_extraction, to_claim_record

def test_claim_draft_carries_kind():
    d = ClaimDraft(text="t", stance="neutral", quote="q", kind="mechanism")
    assert d.kind == "mechanism"

def test_parse_extraction_reads_kind():
    drafts = parse_extraction({"claims": [
        {"text": "t", "stance": "neutral", "quote": "q", "kind": "caveat"}]})
    assert drafts[0].kind == "caveat"

def test_unknown_kind_rejected():
    import pytest
    with pytest.raises(Exception):
        ClaimDraft(text="t", stance="neutral", quote="q", kind="bogus")

def test_to_claim_record_copies_kind():
    d = ClaimDraft(text="t", stance="neutral", quote="q", kind="example")
    rec = to_claim_record(d, chunk_id="s:1", source_id="s", trust="trusted")
    assert rec.kind == "example"

def test_claim_kinds_membership():
    assert set(CLAIM_KINDS) == {"definition","claim","mechanism","example","caveat","actionable"}
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `.venv/bin/pytest tests/test_contracts.py -q`
Expected: FAIL — `kind` unknown / unexpected keyword.

- [ ] **Step 3: Implement**

In `contracts.py`:

```python
CLAIM_KINDS = ("definition", "claim", "mechanism", "example", "caveat", "actionable")

EXTRACTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "stance": {"enum": list(STANCES)},
                    "quote": {"type": "string"},
                    "kind": {"enum": list(CLAIM_KINDS)},
                },
                "required": ["text", "stance", "quote", "kind"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}

class ClaimDraft(BaseModel):
    text: str
    stance: str
    quote: str
    kind: str = "claim"

    @field_validator("stance")
    @classmethod
    def _stance_known(cls, value: str) -> str:
        if value not in STANCES:
            raise ValueError(f"unknown stance {value!r}")
        return value

    @field_validator("kind")
    @classmethod
    def _kind_known(cls, value: str) -> str:
        if value not in CLAIM_KINDS:
            raise ValueError(f"unknown kind {value!r}")
        return value
```

Update `to_claim_record` to pass `kind=draft.kind`. In `records.py` add `kind: str = "claim"` to `ClaimRecord` (after `quote`).

- [ ] **Step 4: Run it, confirm it passes**

Run: `.venv/bin/pytest tests/test_contracts.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mslearn/pipeline/contracts.py mslearn/graph/records.py tests/test_contracts.py
git commit -m "feat(extract): add lossless 'kind' to claim contract (definition/claim/mechanism/example/caveat/actionable)"
```

### Task 2.2: Persist and read `kind` in the graph + fakes

**Files:**
- Modify: `mslearn/graph/store.py` (upsert_claim, claims_in_concept, claims_for_source)
- Modify: `tests/fakes.py` (`InMemoryGraphStore`)
- Test: `tests/fakes_selftest` via existing pipeline tests; graph: `tests/graph/test_store.py`

**Interfaces:**
- Produces: `upsert_claim` writes `c.kind`; `claims_in_concept`/`claims_for_source` return `kind`. `InMemoryGraphStore` mirrors it.

- [ ] **Step 1: Write the failing test**

```python
# tests/fakes-backed test, e.g. tests/test_guide_claims_kind.py
from tests.fakes import InMemoryGraphStore
from mslearn.graph.records import ClaimRecord

def test_inmemory_store_roundtrips_kind():
    g = InMemoryGraphStore()
    g.upsert_concept_min("con1")  # existing helper; if absent, use g.upsert_claim path
    rec = ClaimRecord(claim_id="c1", chunk_id="s:1", source_id="s",
                      text="t", stance="neutral", quote="q", trust="trusted", kind="caveat")
    g.upsert_claim(rec, [0.0], project_id="default")
    g.assign_claim_to_concept("c1", "con1", project_id="default")  # existing test helper
    rows = g.claims_in_concept("con1", project_id="default")
    assert rows[0]["kind"] == "caveat"
```

(Use whatever concept-assignment helper `tests/fakes.py` already exposes; match the existing test style in `tests/`.)

- [ ] **Step 2: Run it, confirm it fails**

Run: `.venv/bin/pytest tests/test_guide_claims_kind.py -q`
Expected: FAIL — `kind` KeyError / not returned.

- [ ] **Step 3: Implement**

In `store.py` `upsert_claim`, add `c.kind = $kind` to the SET clause and `kind=getattr(claim, "kind", "claim")` to params. In `claims_in_concept` and `claims_for_source`, add `c.kind AS kind` to the RETURN. In `tests/fakes.py` `InMemoryGraphStore.upsert_claim`, store `record.kind`; in its `claims_in_concept`/`claims_for_source` dicts include `"kind": rec.get("kind", "claim")`.

- [ ] **Step 4: Run it, confirm it passes**

Run: `.venv/bin/pytest tests/test_guide_claims_kind.py -q && MSL_TEST_NEO4J_URI=bolt://localhost:7690 .venv/bin/pytest tests/graph/test_store.py -q` (graph part only when the disposable container is up via `make graph-test`).
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mslearn/graph/store.py tests/fakes.py tests/test_guide_claims_kind.py
git commit -m "feat(graph): persist and read claim 'kind'"
```

### Task 2.3: Lossless extraction prompt + `extract.max_claims` tunable

**Files:**
- Modify: `mslearn/prompts.py` (`extraction`)
- Modify: `mslearn/opsdb.py` (`TUNABLE_DEFAULTS`)
- Modify: `mslearn/pipeline/extraction_graph.py` (inject cap into prompt)
- Test: `tests/test_extraction_graph.py` (add), `tests/test_opsdb.py`

**Interfaces:**
- Produces: `db.get_tunable("extract.max_claims")` default 15; extraction prompt instructs kind tagging + cap.

- [ ] **Step 1: Write the failing test**

```python
def test_extract_max_claims_default():
    from mslearn.opsdb import TUNABLE_DEFAULTS
    assert TUNABLE_DEFAULTS["extract.max_claims"] == 15.0

def test_extraction_prompt_mentions_kind(tmp_path):
    from mslearn.opsdb import OpsDB
    from mslearn.prompts import get_prompt
    db = OpsDB(tmp_path / "ops.db")
    p = get_prompt(db, "extraction")
    assert "kind" in p and "mechanism" in p and "caveat" in p
```

- [ ] **Step 2: Run it, confirm it fails**

Run: `.venv/bin/pytest tests/test_opsdb.py -q -k max_claims tests/test_prompts_extraction.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `TUNABLE_DEFAULTS`: `"extract.max_claims": 15.0,`. Rewrite the `extraction` prompt in `prompts.py`:

```python
"extraction": (
    "You extract factual claims from one text chunk of a learning source.\n"
    "Return JSON only, matching the given schema.\n"
    "Rules:\n"
    "- Each claim is one self-contained factual or prescriptive statement.\n"
    "- 'quote' MUST be a verbatim substring copied character-for-character from"
    " the chunk that supports the claim. Never paraphrase inside 'quote'.\n"
    "- 'kind' tags what the claim is, one of: 'definition' (what a term/idea is),"
    " 'claim' (a core factual assertion), 'mechanism' (how/why it works),"
    " 'example' (a concrete instance), 'caveat' (an exception/edge case/limit),"
    " 'actionable' (a step to take). Capture mechanisms, caveats, and examples"
    " as their OWN claims when the chunk supports them with a verbatim quote —"
    " do not fold them into a single headline claim. If the chunk has none of a"
    " kind, emit none; never invent one.\n"
    "- 'stance' is 'recommends' if the source advises doing it, 'warns_against'"
    " if it advises against it, else 'neutral'.\n"
    "- Extract at most {max_claims} claims. Skip greetings, filler, and"
    " table-of-contents text.\n"
    "- If the chunk contains no claims, return {{\"claims\": []}}.\n"
),
```

(Note the doubled braces so `.format` leaves the JSON example intact.) In `extraction_graph.py`, read `max_claims = int(db.get_tunable("extract.max_claims"))` in `build_extraction_graph`, and format the base prompt: `base_prompt = get_prompt(db, "extraction").format(max_claims=max_claims)`.

- [ ] **Step 4: Run it, confirm it passes + full suite**

Run: `.venv/bin/pytest tests/test_opsdb.py tests/test_prompts_extraction.py tests/test_extraction_graph.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mslearn/prompts.py mslearn/opsdb.py mslearn/pipeline/extraction_graph.py tests/
git commit -m "feat(extract): lossless prompt (kind-tagged claims) + extract.max_claims tunable (15)"
```

---

## Phase 3 — Interactive study guide

### Task 3.1: `study_progress` table + accessors + `guide.max_tokens`

**Files:**
- Modify: `mslearn/opsdb.py` (schema + methods + tunable)
- Test: `tests/test_opsdb.py`

**Interfaces:**
- Produces: `db.set_section_reviewed(project_id, concept_id, section_id, reviewed: bool)`, `db.section_progress(project_id, concept_id) -> dict[str, bool]`. Tunable `guide.max_tokens` default 8192.

- [ ] **Step 1: Write the failing test**

```python
def test_study_progress_roundtrip(tmp_path):
    from mslearn.opsdb import OpsDB
    db = OpsDB(tmp_path / "ops.db")
    db.set_section_reviewed("default", "con1", "s1", True)
    assert db.section_progress("default", "con1") == {"s1": True}
    db.set_section_reviewed("default", "con1", "s1", False)
    assert db.section_progress("default", "con1") == {"s1": False}
```

- [ ] **Step 2: Run it, confirm it fails** — `.venv/bin/pytest tests/test_opsdb.py -q -k study_progress` → FAIL.

- [ ] **Step 3: Implement**

Add to `_SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS study_progress (
  project_id TEXT NOT NULL,
  concept_id TEXT NOT NULL,
  section_id TEXT NOT NULL,
  reviewed   INTEGER NOT NULL DEFAULT 0,
  ts         REAL NOT NULL,
  PRIMARY KEY (project_id, concept_id, section_id)
);
```

Add methods (mirror the `_lock`+`conn` pattern used by existing methods):

```python
def set_section_reviewed(self, project_id, concept_id, section_id, reviewed):
    with self._lock:
        self.conn.execute(
            "INSERT INTO study_progress(project_id,concept_id,section_id,reviewed,ts) "
            "VALUES(?,?,?,?,?) ON CONFLICT(project_id,concept_id,section_id) "
            "DO UPDATE SET reviewed=excluded.reviewed, ts=excluded.ts",
            (project_id, concept_id, section_id, 1 if reviewed else 0, time.time()),
        )
        self.conn.commit()

def section_progress(self, project_id, concept_id):
    with self._lock:
        rows = self.conn.execute(
            "SELECT section_id, reviewed FROM study_progress "
            "WHERE project_id=? AND concept_id=?", (project_id, concept_id),
        ).fetchall()
    return {r["section_id"]: bool(r["reviewed"]) for r in rows}
```

Add `"guide.max_tokens": 8192.0,` to `TUNABLE_DEFAULTS`. (Ensure `import time` present.)

- [ ] **Step 4: Run it, confirm it passes** — PASS.

- [ ] **Step 5: Commit**

```bash
git add mslearn/opsdb.py tests/test_opsdb.py
git commit -m "feat(db): study_progress table + accessors + guide.max_tokens tunable"
```

### Task 3.2: Guide contract (pydantic models + JSON schema)

**Files:**
- Create: `mslearn/pipeline/guide.py`
- Test: `tests/test_guide_contract.py`

**Interfaces:**
- Produces: `GUIDE_SCHEMA: dict`; models `GuideItem{kind,text,claims:list[str]}`, `GuideSection{id,title,items}`, `Disagreement{summary,classification,a,b}`, `DisagreeSide{label,text,claims}`, `StudyGuide{concept_id,title,tl_dr,skeleton,sections,disagreements,open_questions}`; `parse_guide(obj)->StudyGuide`; `drop_uncited(guide)->StudyGuide` (removes items with empty `claims`, then empty sections).

- [ ] **Step 1: Write the failing test**

```python
from mslearn.pipeline.guide import parse_guide, drop_uncited

RAW = {
  "concept_id": "con1", "title": "Merge sort",
  "tl_dr": {"text": "Sorts in O(n log n).", "claims": ["c3"]},
  "skeleton": ["Cost"],
  "sections": [{"id": "s1", "title": "Cost", "items": [
      {"kind": "claim", "text": "O(n log n).", "claims": ["c3"]},
      {"kind": "example", "text": "hallucinated", "claims": []}]}],
  "disagreements": [], "open_questions": [],
}

def test_parse_and_drop_uncited():
    g = drop_uncited(parse_guide(RAW))
    assert g.title == "Merge sort"
    kinds = [i.kind for i in g.sections[0].items]
    assert kinds == ["claim"]  # uncited example dropped

def test_empty_section_dropped_when_all_items_uncited():
    raw = {**RAW, "sections": [{"id":"s1","title":"x","items":[
        {"kind":"claim","text":"t","claims":[]}]}]}
    g = drop_uncited(parse_guide(raw))
    assert g.sections == []
```

- [ ] **Step 2: Run it, confirm it fails** — module missing → FAIL.

- [ ] **Step 3: Implement `mslearn/pipeline/guide.py`**

```python
from __future__ import annotations
from pydantic import BaseModel, ValidationError
from mslearn.pipeline.contracts import CLAIM_KINDS
from mslearn.graph.records import CONFLICT_CLASSIFICATIONS

class GuideParseError(Exception): ...

class GuideItem(BaseModel):
    kind: str
    text: str
    claims: list[str] = []

class GuideSection(BaseModel):
    id: str
    title: str
    items: list[GuideItem] = []

class DisagreeSide(BaseModel):
    label: str
    text: str
    claims: list[str] = []

class Disagreement(BaseModel):
    summary: str
    classification: str
    a: DisagreeSide
    b: DisagreeSide

class TlDr(BaseModel):
    text: str
    claims: list[str] = []

class StudyGuide(BaseModel):
    concept_id: str
    title: str
    tl_dr: TlDr
    skeleton: list[str] = []
    sections: list[GuideSection] = []
    disagreements: list[Disagreement] = []
    open_questions: list[str] = []

GUIDE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "concept_id": {"type": "string"},
        "title": {"type": "string"},
        "tl_dr": {"type": "object", "properties": {
            "text": {"type": "string"},
            "claims": {"type": "array", "items": {"type": "string"}}},
            "required": ["text", "claims"], "additionalProperties": False},
        "skeleton": {"type": "array", "items": {"type": "string"}},
        "sections": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "title": {"type": "string"},
            "items": {"type": "array", "items": {"type": "object", "properties": {
                "kind": {"enum": list(CLAIM_KINDS)}, "text": {"type": "string"},
                "claims": {"type": "array", "items": {"type": "string"}}},
                "required": ["kind", "text", "claims"], "additionalProperties": False}}},
            "required": ["id", "title", "items"], "additionalProperties": False}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["concept_id", "title", "tl_dr", "skeleton", "sections", "open_questions"],
    "additionalProperties": False,
}

def parse_guide(obj: object) -> StudyGuide:
    try:
        return StudyGuide.model_validate(obj)
    except ValidationError as exc:
        raise GuideParseError(str(exc)[:500]) from exc

def drop_uncited(guide: StudyGuide) -> StudyGuide:
    sections = []
    for s in guide.sections:
        kept = [i for i in s.items if i.claims]
        if kept:
            sections.append(GuideSection(id=s.id, title=s.title, items=kept))
    guide.sections = sections
    guide.skeleton = [t for t in guide.skeleton if any(s.title == t for s in sections)] or [s.title for s in sections]
    return guide
```

(Note: `disagreements` are built server-side from graph conflicts in Task 3.3, so they're excluded from `GUIDE_SCHEMA`'s model output and appended after parse — keeps the model's job to structuring claims only.)

- [ ] **Step 4: Run it, confirm it passes** — PASS.

- [ ] **Step 5: Commit**

```bash
git add mslearn/pipeline/guide.py tests/test_guide_contract.py
git commit -m "feat(guide): StudyGuide contract + schema + drop-uncited grounding gate"
```

### Task 3.3: Guide generator

**Files:**
- Create: `mslearn/pipeline/guide_gen.py`
- Modify: `mslearn/prompts.py` (add `guide` prompt)
- Test: `tests/test_guide_gen.py` (fake router/graph)

**Interfaces:**
- Consumes: `parse_guide`, `drop_uncited`, `ctx.graph.claims_in_concept`, `ctx.graph.conflicts_in_concept`, `ctx.graph.citations_for_claims`, `ctx.graph.set_concept_teaching` (reused to cache the JSON string), memory-hint helper (reuse teaching's, import-safe).
- Produces: `generate_guide(ctx, concept_id, force=False, project_id="default") -> dict` returning the guide as a JSON-able dict; caches the JSON string in the concept's `teach_md` field; appends server-built `disagreements` from graph conflicts; runs `drop_uncited`.

- [ ] **Step 1: Write the failing test**

```python
def test_generate_guide_drops_uncited_and_adds_disagreements(fake_ctx):
    # fake_ctx.router.complete returns a StudyGuide-shaped dict with one cited
    # and one uncited item; graph has one conflict between c3 and c4.
    out = generate_guide(fake_ctx, "con1", force=True, project_id="default")
    assert out["title"]
    assert all(item["claims"] for sec in out["sections"] for item in sec["items"])
    assert out["disagreements"][0]["classification"] in {
        "context_dependent","outdated","genuine_debate","evidence_mismatch"}

def test_generate_guide_memory_failure_degrades(fake_ctx_raising_memory):
    out = generate_guide(fake_ctx_raising_memory, "con1", force=True)
    assert out["title"]  # no raise
```

- [ ] **Step 2: Run it, confirm it fails** — module missing → FAIL.

- [ ] **Step 3: Implement**

Add the `guide` prompt to `prompts.py`:

```python
"guide": (
    "You organize a concept's already-extracted claims into a study guide.\n"
    "{domain_guidance}\n"
    "Concept: {concept_name}\nSummary: {concept_summary}\n"
    "Claims (each is a verbatim-grounded fact you must NOT reword):\n{claims}\n"
    "Memory hints:\n{memory_hints}\n"
    "Return JSON only matching the schema. Rules:\n"
    "- Group the claims into 2-6 sections; 'skeleton' lists the section titles"
    " in order. Give each section a short id (s1,s2,...).\n"
    "- Each section item copies one claim: set 'text' to the claim's text"
    " VERBATIM, 'kind' to the claim's kind, and 'claims' to [that claim_id].\n"
    "- Every claim id supplied must appear in exactly one item. Never invent"
    " items, text, or claim ids. Items with no claim id are forbidden.\n"
    "- 'tl_dr.text' is one plain sentence naming what the concept is; cite the"
    " 1-2 claim ids it rests on in tl_dr.claims.\n"
    "- 'open_questions' may list what the source did NOT cover (gaps only), or"
    " be empty. It is the only field not tied to a claim; never put facts there.\n"
    "- Memory hints personalize ordering only; never a source of facts.\n"
),
```

Create `guide_gen.py`:

```python
from __future__ import annotations
import json
from mslearn.pipeline.guide import GUIDE_SCHEMA, drop_uncited, parse_guide
from mslearn.pipeline.teaching import _format_memory_hints, _trusted_claims
from mslearn.prompts import domain_guidance, get_domain_profile, get_prompt
from mslearn.providers.base import ModelMessage, ModelRequest

def _format_claims(claims):
    return "\n".join(
        f"- id={c['claim_id']} kind={c.get('kind','claim')} stance={c.get('stance','')}: {c['text']}"
        for c in claims) or "(none)"

def _disagreements(graph, concept_id, project_id):
    out = []
    for r in graph.conflicts_in_concept(concept_id, project_id=project_id):
        out.append({
            "summary": r.get("rationale", ""),
            "classification": r.get("classification", ""),
            "a": {"label": f"claim {r.get('claim_a','')}", "text": r.get("text_a", ""), "claims": [r.get("claim_a","")]},
            "b": {"label": f"claim {r.get('claim_b','')}", "text": r.get("text_b", ""), "claims": [r.get("claim_b","")]},
        })
    return out

def generate_guide(ctx, concept_id, force=False, project_id="default") -> dict:
    concept = ctx.graph.get_concept(concept_id, project_id=project_id)
    if concept is None:
        raise KeyError(f"unknown concept {concept_id!r}")
    cached = concept.get("teach_md") or ""
    if cached and not force and not concept.get("dirty", False):
        try:
            return json.loads(cached)
        except json.JSONDecodeError:
            pass  # stale markdown from before the guide migration → regenerate
    claims = _trusted_claims(ctx.graph.claims_in_concept(concept_id, project_id=project_id))
    profile = get_domain_profile(ctx.db, project_id)
    prompt = get_prompt(ctx.db, "guide").format(
        domain_guidance=domain_guidance(profile),
        concept_name=concept.get("name", ""),
        concept_summary=concept.get("summary", ""),
        claims=_format_claims(claims),
        memory_hints=_format_memory_hints(ctx.memory, concept.get("name", ""), project_id),
    )
    resp = ctx.router.complete("interactive", ModelRequest(
        messages=[ModelMessage(role="user", content=prompt)],
        json_schema=GUIDE_SCHEMA,
        max_tokens=int(ctx.db.get_tunable("guide.max_tokens")),
    ))
    guide = drop_uncited(parse_guide({**resp.parsed, "concept_id": concept_id,
                                      "title": concept.get("name", "")}))
    data = guide.model_dump()
    data["disagreements"] = _disagreements(ctx.graph, concept_id, project_id)
    ctx.graph.set_concept_teaching(concept_id, json.dumps(data), project_id=project_id)
    ctx.graph.mark_concept_dirty(concept_id, False, project_id=project_id)
    return data
```

(If `conflicts_in_concept` doesn't already return `text_a`/`text_b`, extend that Cypher in `store.py` to also return the two claim texts — keep it in the store, add a test in `tests/graph/test_store.py`.)

- [ ] **Step 4: Run it, confirm it passes** — PASS.

- [ ] **Step 5: Commit**

```bash
git add mslearn/pipeline/guide_gen.py mslearn/prompts.py mslearn/graph/store.py tests/
git commit -m "feat(guide): generator organizes gated claims into a StudyGuide (grounded, cached)"
```

### Task 3.4: Endpoints — teach returns guide JSON; progress; on-demand flashcards + self-check

**Files:**
- Modify: `mslearn/server/routers/study.py`
- Modify: `mslearn/prompts.py` (`flashcards`, `selfcheck` prompts)
- Create: `mslearn/pipeline/study_extras.py` (flashcard/self-check generators)
- Test: `tests/test_study_router.py`

**Interfaces:**
- Produces:
  - `GET /api/study/concepts/{id}/teach` → `{ "guide": {...}, "cached": bool, "progress": {section_id: bool} }`.
  - `POST /api/study/concepts/{id}/progress { section_id, reviewed }` → `{ progress }`.
  - `POST /api/study/concepts/{id}/flashcards { count }` → `{ cards: [{front, back, claims}] }`.
  - `POST /api/study/concepts/{id}/selfcheck { count }` → `{ checks: [{question, answer, claims}] }`.
- All 404 when the concept isn't in the project (drives Phase 1 Task 1.2's empty state).

- [ ] **Step 1: Write the failing test**

```python
def test_teach_returns_guide_and_progress(client, seeded_concept):
    r = client.get(f"/api/study/concepts/{seeded_concept}/teach")
    assert r.status_code == 200
    body = r.json()
    assert "guide" in body and "sections" in body["guide"]
    assert "progress" in body

def test_progress_toggle_persists(client, seeded_concept):
    client.post(f"/api/study/concepts/{seeded_concept}/progress",
                json={"section_id": "s1", "reviewed": True})
    body = client.get(f"/api/study/concepts/{seeded_concept}/teach").json()
    assert body["progress"].get("s1") is True

def test_flashcards_count_and_grounding(client, seeded_concept):
    r = client.post(f"/api/study/concepts/{seeded_concept}/flashcards", json={"count": 3})
    cards = r.json()["cards"]
    assert len(cards) <= 3
    assert all(c["claims"] for c in cards)

def test_cross_project_concept_404(client):
    assert client.get("/api/study/concepts/nope/teach").status_code == 404
```

- [ ] **Step 2: Run it, confirm it fails** — FAIL.

- [ ] **Step 3: Implement**

Rewrite the `teach` handler and add routes:

```python
from mslearn.pipeline.guide_gen import generate_guide
from mslearn.pipeline.study_extras import make_flashcards, make_selfcheck

class ProgressRequest(BaseModel):
    section_id: str
    reviewed: bool

class CountRequest(BaseModel):
    count: int = 5

@router.get("/concepts/{concept_id}/teach")
def teach(concept_id: str, force: bool = False, ctx=Depends(get_ctx),
          project_id: str = Depends(get_project_id)):
    concept = ctx.graph.get_concept(concept_id, project_id=project_id)
    if concept is None:
        raise HTTPException(status_code=404, detail=f"unknown concept {concept_id!r}")
    cached = bool(concept.get("teach_md")) and not force and not concept.get("dirty", False)
    try:
        guide = generate_guide(ctx, concept_id, force=force, project_id=project_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return {"guide": guide, "cached": cached,
            "progress": ctx.db.section_progress(project_id, concept_id)}

@router.post("/concepts/{concept_id}/progress")
def set_progress(concept_id: str, body: ProgressRequest, ctx=Depends(get_ctx),
                 project_id: str = Depends(get_project_id)):
    if ctx.graph.get_concept(concept_id, project_id=project_id) is None:
        raise HTTPException(status_code=404, detail="unknown concept")
    ctx.db.set_section_reviewed(project_id, concept_id, body.section_id, body.reviewed)
    return {"progress": ctx.db.section_progress(project_id, concept_id)}

@router.post("/concepts/{concept_id}/flashcards")
def flashcards(concept_id: str, body: CountRequest, ctx=Depends(get_ctx),
               project_id: str = Depends(get_project_id)):
    if ctx.graph.get_concept(concept_id, project_id=project_id) is None:
        raise HTTPException(status_code=404, detail="unknown concept")
    return {"cards": make_flashcards(ctx, concept_id, body.count, project_id)}

@router.post("/concepts/{concept_id}/selfcheck")
def selfcheck(concept_id: str, body: CountRequest, ctx=Depends(get_ctx),
              project_id: str = Depends(get_project_id)):
    if ctx.graph.get_concept(concept_id, project_id=project_id) is None:
        raise HTTPException(status_code=404, detail="unknown concept")
    return {"checks": make_selfcheck(ctx, concept_id, body.count, project_id)}
```

Add `flashcards`/`selfcheck` prompts to `prompts.py` (schema-constrained, each item must cite claim ids, omit when unsupported). Create `study_extras.py` with `make_flashcards`/`make_selfcheck` — same shape as `guide_gen`: load trusted claims, one structured call, drop any item whose `claims` is empty, cap to `count`.

- [ ] **Step 4: Run it, confirm it passes** — PASS.

- [ ] **Step 5: Commit**

```bash
git add mslearn/server/routers/study.py mslearn/pipeline/study_extras.py mslearn/prompts.py tests/test_study_router.py
git commit -m "feat(study): teach returns guide JSON + persisted progress + on-demand flashcards/self-check"
```

### Task 3.5: Frontend types + `InteractiveGuide` component

**Files:**
- Modify: `frontend/src/api/types.ts`
- Create: `frontend/src/components/InteractiveGuide.tsx`
- Create: `frontend/src/components/InteractiveGuide.test.tsx`
- Modify: `frontend/src/app.css` (guide styles)

**Interfaces:**
- Consumes: `TeachResponse = { guide: StudyGuide; cached: boolean; progress: Record<string,boolean> }`.
- Produces: `<InteractiveGuide guide progress citations onToggleSection />` rendering the sticky skeleton mini-map, stacked collapsible sections, kind-colored items, numbered superscripts + per-section Sources footer.

**Design note (execution):** before writing styles, load the design skill so this isn't a default-Tailwind look. Follow the existing `app.css` tokens.

- [ ] **Step 1: Write the failing test**

```tsx
it("renders sections, no raw claim ids, and a Sources footer", () => {
  const guide = { concept_id:"c", title:"Merge sort",
    tl_dr:{text:"Fast sort.", claims:["c3"]}, skeleton:["Cost"],
    sections:[{id:"s1",title:"Cost",items:[{kind:"claim",text:"O(n log n).",claims:["c3"]}]}],
    disagreements:[], open_questions:[] };
  const citations = [{ claim_id:"c3", quote:"n log n", page:12 }];
  render(<InteractiveGuide guide={guide} progress={{}} citations={citations}
                           onToggleSection={()=>{}} />);
  expect(screen.getByText("Cost")).toBeInTheDocument();
  expect(screen.queryByText(/c3/)).not.toBeInTheDocument(); // no raw id
  expect(screen.getByText(/Sources/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run it, confirm it fails** — component missing → FAIL.

- [ ] **Step 3: Implement**

Add types to `types.ts`:

```ts
export interface GuideItem { kind: string; text: string; claims: string[]; }
export interface GuideSection { id: string; title: string; items: GuideItem[]; }
export interface DisagreeSide { label: string; text: string; claims: string[]; }
export interface Disagreement { summary: string; classification: string; a: DisagreeSide; b: DisagreeSide; }
export interface StudyGuide {
  concept_id: string; title: string;
  tl_dr: { text: string; claims: string[] };
  skeleton: string[]; sections: GuideSection[];
  disagreements: Disagreement[]; open_questions: string[];
}
export interface TeachResponse { guide: StudyGuide; cached: boolean; progress: Record<string, boolean>; }
```

Build `InteractiveGuide.tsx`: a `KIND_COLORS` map (definition/claim/mechanism/example/caveat/actionable → accent class); per-section `useState` expanded; a sticky mini-map listing `skeleton` as jump-links; each item renders its `text` plus numbered superscripts derived from a per-section claim→number map; a "Sources" `<details>` footer mapping each number to the matching `citations` row (`quote` + locator page/timestamp). Never render a `claim_id` as text. Disagreements render two columns. `open_questions` render in a visually-distinct advisory box. A per-section "Mark reviewed" checkbox calls `onToggleSection(section.id, next)`.

- [ ] **Step 4: Run it, confirm it passes** — PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/types.ts frontend/src/components/InteractiveGuide.tsx frontend/src/components/InteractiveGuide.test.tsx frontend/src/app.css
git commit -m "feat(ui): InteractiveGuide — mini-map, kind-colored cards, numbered sources, no raw ids"
```

### Task 3.6: Wire `ConceptView` to the guide + on-demand flashcards/self-check UI

**Files:**
- Modify: `frontend/src/views/ConceptView.tsx`
- Modify: `frontend/src/views/ConceptView.test.tsx`

**Interfaces:**
- Consumes: `TeachResponse`, `POST .../progress|flashcards|selfcheck`.
- Produces: ConceptView renders `<InteractiveGuide>`; a "Make N flashcards" / "Self-check" control fetches on demand and renders flip cards / reveal answers.

- [ ] **Step 1: Write the failing test** — assert the guide (not markdown) renders and a "Make flashcards" button exists; clicking with count 3 calls `/flashcards` and shows a flip card.

- [ ] **Step 2: Run it, confirm it fails** — FAIL (still markdown).

- [ ] **Step 3: Implement** — replace the `TeachResponse` type usage and `MarkdownWithCitations` block with `<InteractiveGuide guide={guide} progress={progress} citations={detail.citations} onToggleSection={toggle} />`. `toggle` POSTs to `/progress` and updates local `progress`. Add a small count input + "Make flashcards"/"Self-check" buttons that POST and render results (flip animation via a CSS class; reveal via `<details>`). Keep the existing Claims list + Flag control.

- [ ] **Step 4: Run it, confirm it passes** — PASS. Then `make ui-test`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/ConceptView.tsx frontend/src/views/ConceptView.test.tsx
git commit -m "feat(ui): concept page renders the interactive guide + on-demand flashcards/self-check"
```

---

## Phase 4 — Ingest throughput (A + #3 + #4 + #5)

### Task 4.1: Split `ingest` → `prepare` + `extract`; thread pool for extraction

**Files:**
- Modify: `mslearn/worker/app.py` (task_routes)
- Modify: `Makefile` (worker targets)
- Modify: `mslearn/settings.py` (add `extract_concurrency`)
- Modify: `README.md`
- Test: `tests/test_worker_routing.py`

**Interfaces:**
- Produces: `chunk_source_task` → `prepare`; `extract_chunk_task` → `extract`; `synthesize_task` → `judge`. `make worker` runs three workers (prepare prefork=2, extract threads=N, judge=1).

- [ ] **Step 1: Update the routing regression test**

```python
def test_all_tasks_routed_to_consumed_queues():
    from mslearn.worker.app import app
    routes = app.conf.task_routes
    assert routes["mslearn.worker.tasks.chunk_source_task"]["queue"] == "prepare"
    assert routes["mslearn.worker.tasks.extract_chunk_task"]["queue"] == "extract"
    assert routes["mslearn.worker.tasks.synthesize_task"]["queue"] == "judge"
    consumed = {"prepare", "extract", "judge"}
    for name in ["chunk_source_task", "extract_chunk_task", "synthesize_task"]:
        assert routes[f"mslearn.worker.tasks.{name}"]["queue"] in consumed
```

- [ ] **Step 2: Run it, confirm it fails** — chunk_source still routes to `ingest` → FAIL.

- [ ] **Step 3: Implement**

In `app.py` `task_routes`: change `chunk_source_task` → `"prepare"`, `extract_chunk_task` → `"extract"`. In `settings.py` add `extract_concurrency: int = int(os.getenv("MSL_EXTRACT_CONCURRENCY", "8"))`. In `Makefile`:

```make
worker-prepare:
	.venv/bin/celery -A mslearn.worker.app worker -Q prepare --concurrency=2 -n prepare@%h -l info
worker-extract:
	.venv/bin/celery -A mslearn.worker.app worker -Q extract --pool=threads --concurrency=$${MSL_EXTRACT_CONCURRENCY:-8} -n extract@%h -l info
worker-judge:
	.venv/bin/celery -A mslearn.worker.app worker -Q judge --concurrency=1 -n judge@%h -l info
worker: ## run all three ingest/judge workers (Ctrl-C stops all)
	$(MAKE) -j3 worker-prepare worker-extract worker-judge
```

Update `make run` to launch all three. Update README's worker section (queues `prepare`/`extract`/`judge`; note `MSL_EXTRACT_CONCURRENCY` should be ≤ `OLLAMA_NUM_PARALLEL`).

- [ ] **Step 4: Run it, confirm it passes** — `.venv/bin/pytest tests/test_worker_routing.py -q` PASS.

- [ ] **Step 5: Commit**

```bash
git add mslearn/worker/app.py mslearn/settings.py Makefile README.md tests/test_worker_routing.py
git commit -m "perf(ingest): split prepare/extract queues, thread-pool extraction (MSL_EXTRACT_CONCURRENCY)"
```

### Task 4.2: Build the extraction graph once per worker process

**Files:**
- Modify: `mslearn/pipeline/extraction_graph.py`
- Modify: `mslearn/worker/context.py` (cache compiled graph)
- Modify: `mslearn/worker/tasks.py` (use cached graph)
- Test: `tests/test_extraction_graph.py`

**Interfaces:**
- Produces: `ctx.extraction_graph` (compiled once at process init); `run_extraction(graph, chunk_id, chunk_text)` accepts a prebuilt graph.

- [ ] **Step 1: Write the failing test**

```python
def test_run_extraction_reuses_prebuilt_graph(monkeypatch):
    import mslearn.pipeline.extraction_graph as eg
    calls = {"n": 0}
    orig = eg.build_extraction_graph
    def counting(router, db): calls["n"] += 1; return orig(router, db)
    monkeypatch.setattr(eg, "build_extraction_graph", counting)
    graph = eg.build_extraction_graph(fake_router, fake_db)
    eg.run_extraction(graph, "s:1", "text one")
    eg.run_extraction(graph, "s:2", "text two")
    assert calls["n"] == 1  # built once, not per chunk
```

- [ ] **Step 2: Run it, confirm it fails** — `run_extraction` currently rebuilds internally → FAIL.

- [ ] **Step 3: Implement**

Change `run_extraction(graph, chunk_id, chunk_text)` to take the compiled graph (drop the internal `build_extraction_graph`). In `context.py`, build and store `self.extraction_graph = build_extraction_graph(self.router, self.db)` at context construction. In `tasks.py` `extract_chunk_task`, call `run_extraction(ctx.extraction_graph, chunk_id, chunk["text"])`. Keep `build_extraction_graph` reading tunables at build time (worker restart re-reads).

- [ ] **Step 4: Run it, confirm it passes** — PASS + `make check`.

- [ ] **Step 5: Commit**

```bash
git add mslearn/pipeline/extraction_graph.py mslearn/worker/context.py mslearn/worker/tasks.py tests/test_extraction_graph.py
git commit -m "perf(extract): compile the LangGraph extraction graph once per worker process"
```

### Task 4.3: Skip no-op escalation when roles resolve to the same model

**Files:**
- Modify: `mslearn/pipeline/extraction_graph.py`
- Modify: `mslearn/providers/router.py` (expose `resolves_same(role_a, role_b) -> bool` if not present)
- Test: `tests/test_extraction_graph.py`

**Interfaces:**
- Produces: `route()` returns `"done"` instead of `"escalate"` when `router.resolves_same("extraction", "synthesis")`.

- [ ] **Step 1: Write the failing test**

```python
def test_no_escalation_when_roles_same_model(fake_router_same_model, fake_db):
    # extraction and synthesis map to the same provider+model
    graph = build_extraction_graph(fake_router_same_model, fake_db)
    state = run_extraction(graph, "s:1", "text with an unquotable claim")
    assert state["escalated"] is False  # never escalated to an identical model
```

- [ ] **Step 2: Run it, confirm it fails** — currently escalates → FAIL.

- [ ] **Step 3: Implement**

Add `resolves_same(self, a, b)` to the router: compare the resolved `(provider, model)` tuples for the two roles from the active profile. In `build_extraction_graph`, compute `escalation_useful = not router.resolves_same("extraction", "synthesis")` once; in `route()`, only return `"escalate"` when `escalation_useful` is true (else `"done"`).

- [ ] **Step 4: Run it, confirm it passes** — PASS.

- [ ] **Step 5: Commit**

```bash
git add mslearn/pipeline/extraction_graph.py mslearn/providers/router.py tests/test_extraction_graph.py
git commit -m "perf(extract): drop escalation when extraction and synthesis resolve to the same model"
```

### Task 4.4: Batch the trust-check embeds

**Files:**
- Modify: `mslearn/pipeline/extraction_graph.py` (validate node)
- Modify: `mslearn/pipeline/trust.py` (accept precomputed embeddings)
- Test: `tests/test_trust.py`

**Interfaces:**
- Produces: `validate` computes `router.embed([d.text for d in drafts])` once and passes each draft its embedding into `check_claim(..., claim_embedding=...)`, instead of `check_claim` embedding per draft.

- [ ] **Step 1: Write the failing test**

```python
def test_validate_embeds_drafts_in_one_batch(monkeypatch):
    calls = {"n": 0}
    def counting_embed(texts): calls["n"] += 1; return [[0.0] for _ in texts]
    # run the validate node with 3 drafts; router.embed monkeypatched
    ...
    assert calls["n"] == 1  # one batched call, not 3
```

- [ ] **Step 2: Run it, confirm it fails** — per-draft embed → 3 calls → FAIL.

- [ ] **Step 3: Implement**

Add an optional `claim_embedding: list[float] | None = None` param to `check_claim`; when provided, skip the internal `embedder(...)` call and use it for the cosine sanity check. In the `validate` node, batch-embed all `state["drafts"]` texts once, zip embeddings with drafts, and pass `claim_embedding=emb` per draft. Keep the verbatim-quote check unchanged.

- [ ] **Step 4: Run it, confirm it passes** — PASS + `make check`.

- [ ] **Step 5: Commit**

```bash
git add mslearn/pipeline/extraction_graph.py mslearn/pipeline/trust.py tests/test_trust.py
git commit -m "perf(extract): batch trust-check embeds into one call per chunk"
```

---

## Final verification

- [ ] `make check` — full offline backend suite green.
- [ ] `make ui-test` — frontend suite green.
- [ ] `make graph-test` — Neo4j integration (disposable container) green; `kind` round-trips.
- [ ] Manual: restart `make run`; ingest a source → confirm mechanism/caveat claims appear (kinds visible); open a concept → interactive guide renders (mini-map, colored cards, superscript Sources, no raw ids); mark a section reviewed → survives reload; request 3 flashcards → ≤3 cited flip cards; switch project on a concept page → lands on that project's course, no error.
- [ ] Confirm no base-spec invariant weakened: trust gate unchanged, every guide item cites a claim, memory failure degrades silently.

## Deferred (documented, not built)

- **Chunk batching (spec Part 3 C):** `extract.chunk_batch_size` tunable defaulting to 1 (off). Higher throughput ceiling, adds cross-chunk quote-attribution surface. Slot in later behind the tunable; no rework needed given the per-chunk task shape stays.
