# Formats + Structure Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Read `.txt`/`.md`/`.docx`, and capture a `section_path` (chapter→section hierarchy) on every chunk for pdf/epub/html/md/docx sources; unstructured sources stay flat.

**Architecture:** Extend `StructuralUnit` and `Chunk` with `section_path`; enrich existing adapters and add three new ones; persist `section_path` on graph chunks. Ingestion layer only — no rollup/UI (that is the outline-tree plan).

**Tech Stack:** Python, pytest, PyMuPDF, ebooklib, python-docx (new).

Spec: `docs/superpowers/specs/2026-07-11-formats-and-structure-capture-design.md`

## Global Constraints

- Run tests from repo root: `.venv/bin/pytest tests/<file> -v`.
- `section_path` is `tuple[str, ...]`, outermost→innermost; `()` = no structure. Persisted on the graph as a JSON string.
- New adapters import heavy deps lazily inside the loader (like youtube/audio).
- A source that lacks structure MUST behave exactly as before (empty `section_path`).

---

### Task 1: `section_path` on StructuralUnit and Chunk

**Files:**
- Modify: `mslearn/adapters/base.py`, `mslearn/chunking.py`
- Test: `tests/test_chunking.py`

**Interfaces:**
- Produces: `StructuralUnit(..., section_path: tuple[str, ...] = ())`; `Chunk(..., section_path: tuple[str, ...] = ())`; `chunk_source` copies each chunk's `section_path` from its unit.

- [ ] **Step 1: Write failing test** in `tests/test_chunking.py`: build a `SourceDocument` with two units, one `section_path=("Ch1","1.1")`; assert every emitted chunk from that unit has `chunk.section_path == ("Ch1","1.1")` and the other unit's chunks have `()`.
- [ ] **Step 2: Run** `.venv/bin/pytest tests/test_chunking.py -k section -v` → FAIL.
- [ ] **Step 3: Implement.** Add `section_path: tuple[str, ...] = ()` to `StructuralUnit` (base.py) and to `Chunk` (chunking.py). In `chunk_source.emit`, pass `section_path=unit.section_path` (thread `unit` through `emit`, or capture it in the loop closure).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(adapters): section_path on StructuralUnit and Chunk"`

---

### Task 2: Persist section_path on graph chunks

**Files:**
- Modify: `mslearn/graph/store.py` (`upsert_chunks`, `chunks_for_source`, `citations_for_claims`), `tests/fakes.py`
- Test: `tests/test_graph_store.py`

**Interfaces:**
- Produces: chunk rows from `chunks_for_source`/`citations_for_claims` include `section_path` as a JSON string (callers `json.loads`).

- [ ] **Step 1: Write failing test** in `tests/test_graph_store.py`: upsert a chunk with `section_path=("A","B")`; read it back via `chunks_for_source`; assert `json.loads(row["section_path"]) == ["A","B"]`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** In `upsert_chunks` param dict add `"section_path": json.dumps(list(c.section_path))` and set `ch.section_path = $section_path` in the Cypher (add `import json` if missing). Add `ch.section_path AS section_path` to the RETURN in `chunks_for_source` and `citations_for_claims` (coalesce to `'[]'`). Mirror in `tests/fakes.py::InMemoryGraphStore` (store list, return JSON string).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(store): persist chunk section_path"`

---

### Task 3: Registry suffixes + dispatch

**Files:**
- Modify: `mslearn/adapters/registry.py`
- Test: `tests/test_adapter_registry.py` (create if absent)

- [ ] **Step 1: Write failing test:** `detect_source_type("x.txt") == "text"`, `.md`/`.markdown` → `"markdown"`, `.docx` → `"docx"`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Add to `_SUFFIX_TYPES`: `".txt": "text", ".md": "markdown", ".markdown": "markdown", ".docx": "docx"`. In `load_source` add branches: `text→load_text`, `markdown→load_markdown`, `docx→load_docx` (import each lazily inside the branch). (Loaders land in Tasks 4-6; for now import them — those tasks create the modules; if executing strictly in order, add the dispatch branches in Tasks 4-6 instead and keep this task to suffix detection only.)
- [ ] **Step 4: Run** the detect test → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(registry): detect txt/md/docx"`

---

### Task 4: Plain-text reader

**Files:**
- Create: `mslearn/adapters/text.py`; Test: `tests/test_text_adapter.py`
- Modify: `mslearn/adapters/registry.py` (dispatch branch)

**Interfaces:**
- Produces: `load_text(path, role="supplement") -> SourceDocument` with `source_type="text"`, one `StructuralUnit` per blank-line paragraph, `section_path=()`.

- [ ] **Step 1: Write failing test:** write a `.txt` with two paragraphs; `load_text` yields ≥1 unit, all `section_path == ()`, and full text present.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** `load_text` (read UTF-8 errors="replace"; split on `\n\s*\n`; one unit per paragraph with `Locator(kind="para", para_index=i)`). Wire `text→load_text` in registry.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(adapters): plain-text reader"`

---

### Task 5: Markdown reader with heading hierarchy

**Files:**
- Create: `mslearn/adapters/markdown_src.py`; Test: `tests/test_markdown_adapter.py`
- Modify: `mslearn/adapters/registry.py`

**Interfaces:**
- Produces: `load_markdown(path, role="supplement") -> SourceDocument`, `source_type="markdown"`; body between ATX headings becomes a unit whose `section_path` is the heading ancestry (titles only).

- [ ] **Step 1: Write failing test:** markdown `# A\n\nintro\n\n## A.1\n\nbody\n\n### A.1.1\n\ndeep` → unit for "body" has `section_path == ("A","A.1")`, unit for "deep" has `("A","A.1","A.1.1")`, and pre-heading has `()`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Line scan; `re.match(r'^(#{1,6})\s+(.*)$', line)` sets level=len(hashes), title=group(2). Maintain `stack: list[tuple[int,str]]`; on a heading, pop while `stack and stack[-1][0] >= level`, then push `(level,title)`; flush the accumulated body as a unit with `section_path=tuple(t for _,t in stack_before_push)`; start a new body. Emit trailing body. Body before any heading → `section_path=()`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(adapters): markdown reader with heading paths"`

---

### Task 6: Word (.docx) reader

**Files:**
- Modify: `pyproject.toml` (add `python-docx>=1.1` to deps), `mslearn/adapters/registry.py`
- Create: `mslearn/adapters/docx_src.py`; Test: `tests/test_docx_adapter.py`

**Interfaces:**
- Produces: `load_docx(path, role="supplement") -> SourceDocument`, `source_type="docx"`; `Heading N`/`Title` styles drive `section_path`.

- [ ] **Step 1: Add dep** and install: add `"python-docx>=1.1"` to `[project].dependencies` in `pyproject.toml`; run `.venv/bin/pip install python-docx`.
- [ ] **Step 2: Write failing test:** build a docx in-test (`import docx; d=docx.Document(); d.add_heading("A",level=1); d.add_paragraph("body"); d.add_heading("A.1",level=2); d.add_paragraph("deep"); d.save(tmp)`) → `load_docx` yields the "deep" body under `("A","A.1")`.
- [ ] **Step 3: Run** → FAIL.
- [ ] **Step 4: Implement** `load_docx` (lazy `import docx`). Iterate `document.paragraphs`; detect heading level from `p.style.name` (`"Heading 1".."Heading 9"` → level; `"Title"` → level 1); update the stack (same pop/push as markdown); accumulate non-heading paragraph text into the current section's buffer; emit a unit per section with its `section_path`.
- [ ] **Step 5: Run** → PASS.
- [ ] **Step 6: Commit** `git commit -am "feat(adapters): Word .docx reader with heading paths"`

---

### Task 7: PDF outline → section_path

**Files:**
- Modify: `mslearn/adapters/pdf.py`; Test: `tests/test_pdf_adapter.py` (extend)

- [ ] **Step 1: Write failing test:** build a small PDF with an outline (use `fitz`: create pages, `doc.set_toc([[1,"Chapter 1",1],[2,"1.1 Intro",1],[1,"Chapter 2",2]])`), load it, assert page-1 units have `section_path == ("Chapter 1","1.1 Intro")` and page-2 units `("Chapter 2",)`. Add a second test: a PDF with no TOC → all `()`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** After opening, `toc = doc.get_toc(simple=True)` (list of `[level,title,page]`). Build a helper `path_for_page(page_no)`: walk toc entries with `entry_page <= page_no`, maintaining a level stack (pop while top level >= entry level, push), return the stack titles as of the last entry at/["before"] this page. Attach `section_path=path_for_page(page_no)` to each page unit. Empty toc → `()`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(adapters): pdf outline to section_path"`

---

### Task 8: EPUB nav → section_path + chapter titles

**Files:**
- Modify: `mslearn/adapters/epub.py`; Test: `tests/test_epub_adapter.py` (extend or create)

- [ ] **Step 1: Write failing test:** using a small fixture epub (or construct via `ebooklib`), assert a document item's unit carries the nav-derived `section_path` and its `title` is the chapter title (not the raw filename). If constructing an epub in-test is impractical, test the pure `_flatten_toc` helper directly with a synthetic `book.toc` structure.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Add `_flatten_toc(toc, ancestors=()) -> dict[str, tuple[str,...]]`: recurse `book.toc` (items are `Link` or `(Section, [children])` tuples); for a `Link`, map `href.split('#')[0]` → `ancestors + (link.title,)`; for a section tuple, recurse children with `ancestors + (section.title,)`. In `load_epub`, build the map once, and for each document item set `section_path = href_map.get(name, ())` and `title = section_path[-1] if section_path else name`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(adapters): epub nav to section_path and chapter titles"`

---

### Task 9: HTML headings → segments

**Files:**
- Modify: `mslearn/adapters/htmltext.py`, `mslearn/adapters/blog.py`; Test: `tests/test_blog_adapter.py`, `tests/test_htmltext.py` (create if absent)

**Interfaces:**
- Produces: `html_to_segments(html) -> list[tuple[tuple[str,...], str]]`; `html_to_text` unchanged (wrapper joining segment texts).

- [ ] **Step 1: Write failing test:** `html_to_segments("<h1>A</h1><p>x</p><h2>B</h2><p>y</p>")` → `[((), no leading), (("A",), "x"), (("A","B"), "y")]` (assert the "x" segment path is `("A",)`, "y" is `("A","B")`). Also assert `html_to_text` still returns "x y"-style flattened text (regression).
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Extend `_TextExtractor` to detect `h1..h6` start/end and capture their inner text as a heading; maintain a level stack; when a heading closes, update the stack (pop>=level, push title) and start a new segment; accumulate block text into the current segment. Expose `html_to_segments`. Rewrite `html_to_text` to `" ".join`/`"\n\n".join` of segment texts (preserve prior output shape — verify against existing blog/epub tests). In `load_blog`, build one `StructuralUnit` per segment with its `section_path`.
- [ ] **Step 4: Run** blog + htmltext + epub (epub uses html_to_text) tests → PASS.
- [ ] **Step 5: Commit** `git commit -am "feat(adapters): html heading segments with section_path"`

---

### Task 10: Full-suite verify

- [ ] **Step 1:** `.venv/bin/pytest -q` → all pass (fix any adapter/ingest test that asserted old flat behavior; the neo4j-marked `test_end_to_end_synthesis` remains a known pre-existing skip/failure — ignore).
- [ ] **Step 2: Commit** stragglers.

## Self-Review

- S2.1→T1,T2. S2.2→T3,T4,T5,T6. S2.3→T7,T8,T9. S2.4→covered (defaults). Registry (T3) precedes loaders; T4-6 wire their own dispatch branch.
- `section_path` type (`tuple[str,...]`) and JSON persistence consistent across T1/T2 and consumers.
