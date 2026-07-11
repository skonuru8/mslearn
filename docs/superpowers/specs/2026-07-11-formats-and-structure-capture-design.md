# Read New Formats + Capture Source Structure

Date: 2026-07-11
Status: Approved (architecture approved by user)
Sequence: **Spec 2**. Independent of Spec 1 (corpus UX fixes). Spec 3 (outline-tree surfacing) depends on this.

## Goal

Read `.txt`, `.md`/`.markdown`, and `.docx` (new), and capture each structured source's chapter/section hierarchy as a `section_path` on every chunk, so downstream (Spec 3) can render a navigable outline tree. Sources without real structure stay flat exactly as today.

## Current state

- Adapters produce `SourceDocument(units: list[StructuralUnit])`; `StructuralUnit(index, title, text, locator)` has **no hierarchy**. PDF units are per-page titled "Page N"; EPUB units titled by filename; HTML/blog flatten headings to `title=""`.
- `chunk_source` (`chunking.py`) splits unit text into `Chunk(chunk_id, source_id, unit_index, seq, text, locator)`; chunks persist `unit_index` but no section info (`store.py:upsert_chunks`).
- Registry (`adapters/registry.py`) dispatches by suffix; no txt/md/docx.
- Deps: `pymupdf`, `ebooklib` present; **`python-docx` absent** (must add).

## Design

### S2.1 Data model: `section_path`
- Add `section_path: tuple[str, ...] = ()` to `StructuralUnit` (`adapters/base.py`). Ordered outermost→innermost, e.g. `("Chapter 3: Numbers", "3.1 Number type")`. Empty tuple = no structure (flat).
- Add `section_path: tuple[str, ...] = ()` to `Chunk` (`chunking.py`); `chunk_source` copies it from the emitting unit (like `locator`/`unit_index`).
- Persist on the graph: `store.upsert_chunks` writes `section_path` (as a JSON string, since the graph props are scalar) — add `"section_path": json.dumps(list(c.section_path))`. `chunks_for_source` and `citations_for_claims` RETURN it (JSON-decoded by callers in Spec 3). Mirror in `tests/fakes.py::InMemoryGraphStore`.

### S2.2 Registry + new readers
- `_SUFFIX_TYPES` gains `.txt→text`, `.md→markdown`, `.markdown→markdown`, `.docx→docx`. `load_source` dispatches to new loaders.
- **`adapters/text.py` `load_text`**: read UTF-8 (errors="replace"); split on blank lines into paragraphs; emit one `StructuralUnit` per paragraph (or a single unit holding all text — match blog's per-paragraph style), `section_path=()`, `title=""`, `locator=Locator(kind="para", para_index=i)`.
- **`adapters/markdown_src.py` `load_markdown`**: line-scan for ATX headings (`^#{1,6}\s+`). Maintain a running heading stack keyed by level; text between headings becomes a unit whose `section_path` is the current heading ancestry (titles only). Body before the first heading → `section_path=()`.
- **`adapters/docx_src.py` `load_docx`** (adds `python-docx>=1.1` to `pyproject.toml` deps): iterate `document.paragraphs`; a paragraph whose `style.name` matches `Heading N` (or `Title`) opens/updates the heading stack at level N; non-heading paragraphs accumulate into the current section's unit; `section_path` = heading ancestry. Import `docx` lazily inside the loader (like youtube/audio) so the dep is only needed when a `.docx` is actually loaded.

### S2.3 Enrich existing structured adapters
- **PDF (`adapters/pdf.py`)**: read `doc.get_toc(simple=True)` → `[[level, title, page], ...]`. For each page, `section_path` = titles of the outline entries on the path to the deepest entry whose `page <= page_no` (respecting levels: keep a stack, pop to `level-1` before pushing). No TOC → all units `section_path=()` (flat, unchanged behavior). Keep per-page units and "Page N" title; add the path.
- **EPUB (`adapters/epub.py`)**: build an href→path map from `book.toc` (recursively flatten nested `Link`/tuple structure, carrying ancestor titles). For each document item, `section_path` = the map entry for its `href` (fall back to `()`), and set `title` to the leaf chapter title when known instead of the raw filename.
- **HTML (`adapters/htmltext.py` + `blog.py`)**: extend `_TextExtractor` to track `h1`–`h6`. Emit structured blocks: instead of only returning flat text, return a list of `(section_path, text)` segments where a heading updates the stack (pop to `level-1`, push title). Add a `html_to_segments(html) -> list[tuple[tuple[str,...], str]]`; keep `html_to_text` as a thin wrapper (join segment texts) for callers that don't need structure. `load_blog` builds one `StructuralUnit` per segment carrying its `section_path`.

### S2.4 Non-structured sources
`audio`, `image`, `youtube`, `text` (txt) emit `section_path=()`. No behavior change beyond the field defaulting empty.

## Files touched

- `pyproject.toml` (add `python-docx>=1.1`)
- `mslearn/adapters/base.py` (StructuralUnit field)
- `mslearn/chunking.py` (Chunk field + copy)
- `mslearn/graph/store.py` (`upsert_chunks`, `chunks_for_source`, `citations_for_claims`)
- `mslearn/adapters/registry.py` (suffixes + dispatch)
- New: `mslearn/adapters/text.py`, `mslearn/adapters/markdown_src.py`, `mslearn/adapters/docx_src.py`
- `mslearn/adapters/pdf.py`, `mslearn/adapters/epub.py`, `mslearn/adapters/htmltext.py`, `mslearn/adapters/blog.py`
- `tests/fakes.py` (chunk section_path)

## Explicitly not doing

- No concept rollup, API, or UI — that is Spec 3.
- No structure from images (vision) or YouTube chapters — flat.
- No font-size heading heuristic for outline-less PDFs — such PDFs stay flat (documented limitation).

## Testing

- `load_markdown`: nested `#/##/###` produce units with correct `section_path` ancestry; pre-heading text has empty path.
- `load_docx`: a small in-test `.docx` (built with `python-docx`) with Heading 1/2 yields correct paths; body paragraphs attach to the right section.
- `load_text`: plain file → units with empty `section_path`.
- PDF: a fixture PDF with a TOC yields page→path mapping; a TOC-less PDF yields empty paths (flat).
- EPUB: nav-derived chapter titles populate `section_path` and leaf `title`.
- HTML: `html_to_segments` splits by headings with correct nesting; `html_to_text` still returns the flattened text unchanged (regression).
- `chunk_source`: chunks inherit their unit's `section_path`.
- `store.upsert_chunks`/`citations_for_claims`: round-trip `section_path` (JSON) through the graph.
- Registry: `.txt/.md/.markdown/.docx` detected and dispatched.

## Success criteria

Uploading a `.docx`/`.md`/PDF-with-outline/EPUB produces chunks each tagged with the correct chapter→section path; `.txt` and unstructured sources ingest fine with empty paths; the app can now read Word, Markdown, and plain-text files it previously rejected.
