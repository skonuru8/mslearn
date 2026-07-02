# Plan 2/8: Source Adapters & Chunking — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** All four source adapters (PDF/EPUB books, blogs, YouTube, audio) emitting one normalized `SourceDocument`, plus the structure-aware chunker — the ingestion pipeline's input layer.

**Architecture:** `mslearn/adapters/` package: shared types in `base.py`, one module per source type, a `registry.py` dispatcher. Transcription sits behind a `Transcriber` protocol (`mslearn/transcribe.py`) so tests use fakes and faster-whisper loads lazily. `mslearn/chunking.py` packs unit paragraphs into 200–500-token chunks with locators preserved. Binary fixtures (PDF/EPUB) are generated at test time in `tests/conftest.py` — no binaries in git.

**Tech Stack (added):** pymupdf, ebooklib, trafilatura, youtube-transcript-api, yt-dlp, faster-whisper; dev: hypothesis.

## Global Constraints

- All tests pass with no network and no models downloaded: YouTube fetchers and transcribers are injectable and faked; blog tests use a checked-in HTML fixture file; PDF/EPUB fixtures generated in conftest
- Heavy imports (`faster_whisper`, `yt_dlp`, `youtube_transcript_api`) are lazy — importing `mslearn.adapters` or `mslearn.transcribe` must not load them
- Every `StructuralUnit` carries a valid `Locator` (page / href / url+para_index / time) — citations depend on this
- Chunker invariants: no text loss (whitespace-normalized), every chunk ≤ 500 estimated tokens (`len(text)//4`), every chunk's locator comes from its unit
- Existing suite (52 tests) stays green; ruff clean; commits per task

---

### Task 1: Dependencies + adapter base types

**Files:**
- Modify: `pyproject.toml` (dependencies)
- Create: `mslearn/adapters/__init__.py` (empty), `mslearn/adapters/base.py`, `tests/test_adapter_base.py`

**Interfaces:**
- Produces (every later task uses these): `Locator{kind: str, page=None, href=None, url=None, para_index=None, start_s=None, end_s=None}`; `StructuralUnit{index: int, title: str, text: str, locator: Locator}`; `SourceDocument{source_id, source_type, role, title, units: list[StructuralUnit]}` with `full_text() -> str`; `make_source_id(ref: str) -> str` (stable slug + 8-hex-digest).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_adapter_base.py
from mslearn.adapters.base import Locator, SourceDocument, StructuralUnit, make_source_id


def test_make_source_id_stable_and_distinct():
    a = make_source_id("/books/My Book.pdf")
    assert a == make_source_id("/books/My Book.pdf")
    assert a != make_source_id("/books/Other Book.pdf")
    assert " " not in a and a == a.lower()


def test_full_text_joins_nonempty_units():
    doc = SourceDocument(
        source_id="s", source_type="pdf", role="spine", title="T",
        units=[
            StructuralUnit(0, "p1", "alpha", Locator(kind="page", page=1)),
            StructuralUnit(1, "p2", "", Locator(kind="page", page=2)),
            StructuralUnit(2, "p3", "beta", Locator(kind="page", page=3)),
        ],
    )
    assert doc.full_text() == "alpha\n\nbeta"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_adapter_base.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mslearn.adapters'`

- [ ] **Step 3: Add dependencies and implement**

In `pyproject.toml`, extend `dependencies` (keep existing entries):

```toml
dependencies = [
    "httpx>=0.27",
    "pydantic>=2.7",
    "pydantic-settings>=2.2",
    "pyyaml>=6.0",
    "pymupdf>=1.24",
    "ebooklib>=0.18",
    "trafilatura>=1.8",
    "youtube-transcript-api>=1.0",
    "yt-dlp>=2024.4.9",
    "faster-whisper>=1.0",
]
```

and dev deps: `dev = ["pytest>=8.0", "respx>=0.21", "ruff>=0.4", "hypothesis>=6.100"]`

`mslearn/adapters/__init__.py` is an empty file.

```python
# mslearn/adapters/base.py
import hashlib
import re
from dataclasses import dataclass, field


@dataclass
class Locator:
    kind: str  # "page" | "href" | "url" | "time"
    page: int | None = None          # PDF, 1-based
    href: str | None = None          # EPUB internal document name
    url: str | None = None           # blog / video source URL
    para_index: int | None = None    # paragraph index within the unit
    start_s: float | None = None     # audio/video timestamps (seconds)
    end_s: float | None = None


@dataclass
class StructuralUnit:
    index: int
    title: str
    text: str
    locator: Locator


@dataclass
class SourceDocument:
    source_id: str
    source_type: str  # "pdf" | "epub" | "blog" | "youtube" | "audio"
    role: str         # "spine" | "supplement"
    title: str
    units: list[StructuralUnit] = field(default_factory=list)

    def full_text(self) -> str:
        return "\n\n".join(u.text for u in self.units if u.text)


def make_source_id(ref: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", ref.lower()).strip("-")[-40:]
    digest = hashlib.sha256(ref.encode()).hexdigest()[:8]
    return f"{stem}-{digest}"
```

- [ ] **Step 4: Install and run tests**

Run: `.venv/bin/pip install -e ".[dev]" -q && .venv/bin/pytest tests/test_adapter_base.py -v && .venv/bin/ruff check .`
Expected: 2 PASSED; ruff clean

- [ ] **Step 5: Full suite, then commit**

Run: `.venv/bin/pytest -q` — expected: 54 passed.

```bash
git add pyproject.toml mslearn/adapters/ tests/test_adapter_base.py
git commit -m "feat: adapter base types (SourceDocument, StructuralUnit, Locator) + ingestion deps"
```

---

### Task 2: Stdlib HTML→text extractor

**Files:**
- Create: `mslearn/adapters/htmltext.py`, `tests/test_htmltext.py`

**Interfaces:**
- Produces: `html_to_text(html: str) -> str` — block-level tags become paragraph breaks, `script`/`style`/`nav`/`head`/`noscript` content dropped, whitespace normalized. Used by the EPUB adapter (Task 4).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_htmltext.py
from mslearn.adapters.htmltext import html_to_text


def test_paragraphs_separated_and_scripts_dropped():
    html = (
        "<html><head><title>T</title><script>var x=1;</script></head>"
        "<body><nav>menu junk</nav>"
        "<h1>Heading</h1><p>First   para.</p><p>Second\npara.</p>"
        "<style>.a{color:red}</style></body></html>"
    )
    text = html_to_text(html)
    assert "var x" not in text and "menu junk" not in text and "color:red" not in text
    paras = text.split("\n\n")
    assert paras == ["Heading", "First para.", "Second para."]


def test_empty_html_gives_empty_string():
    assert html_to_text("<html><body></body></html>") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_htmltext.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# mslearn/adapters/htmltext.py
import re
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "section", "article", "tr", "blockquote", "pre",
}
_SKIP_TAGS = {"script", "style", "head", "nav", "noscript"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    raw = "".join(parser.parts)
    paragraphs = []
    for block in raw.split("\n\n"):
        cleaned = re.sub(r"\s+", " ", block).strip()
        if cleaned:
            paragraphs.append(cleaned)
    return "\n\n".join(paragraphs)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_htmltext.py -v`
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add mslearn/adapters/htmltext.py tests/test_htmltext.py
git commit -m "feat: stdlib HTML-to-text extractor for EPUB content"
```

---

### Task 3: PDF adapter (+ generated fixture)

**Files:**
- Create: `mslearn/adapters/pdf.py`, `tests/conftest.py`, `tests/test_pdf_adapter.py`

**Interfaces:**
- Consumes: base types (Task 1).
- Produces: `load_pdf(path, role="supplement") -> SourceDocument` — one unit per non-empty page, `Locator(kind="page", page=N)` 1-based, title from PDF metadata falling back to filename stem. Also produces the session-scoped `tiny_pdf` pytest fixture other tests reuse.

- [ ] **Step 1: Write conftest fixture + failing test**

```python
# tests/conftest.py
import pytest


@pytest.fixture(scope="session")
def tiny_pdf(tmp_path_factory):
    import fitz  # PyMuPDF

    path = tmp_path_factory.mktemp("fixtures") / "tiny.pdf"
    doc = fitz.open()
    for text in [
        "Chapter one. Global mutable state is risky in concurrent code.",
        "Chapter two. Pure functions compose and are easy to test.",
    ]:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()
    return path
```

```python
# tests/test_pdf_adapter.py
from mslearn.adapters.pdf import load_pdf


def test_load_pdf_units_pages_and_locators(tiny_pdf):
    doc = load_pdf(tiny_pdf, role="spine")
    assert doc.source_type == "pdf" and doc.role == "spine"
    assert doc.title == "tiny"  # no metadata title -> filename stem
    assert len(doc.units) == 2
    assert doc.units[0].locator.kind == "page" and doc.units[0].locator.page == 1
    assert "Global mutable state" in doc.units[0].text
    assert doc.units[1].locator.page == 2
    assert [u.index for u in doc.units] == [0, 1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_pdf_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mslearn.adapters.pdf'`

- [ ] **Step 3: Implement**

```python
# mslearn/adapters/pdf.py
from pathlib import Path

import fitz  # PyMuPDF

from mslearn.adapters.base import Locator, SourceDocument, StructuralUnit, make_source_id


def load_pdf(path: Path | str, role: str = "supplement") -> SourceDocument:
    path = Path(path)
    doc = fitz.open(path)
    try:
        title = (doc.metadata or {}).get("title") or path.stem
        units: list[StructuralUnit] = []
        for page_no, page in enumerate(doc, start=1):
            text = page.get_text("text").strip()
            if not text:
                continue
            units.append(
                StructuralUnit(
                    index=len(units),
                    title=f"Page {page_no}",
                    text=text,
                    locator=Locator(kind="page", page=page_no),
                )
            )
    finally:
        doc.close()
    return SourceDocument(
        source_id=make_source_id(str(path)), source_type="pdf",
        role=role, title=title, units=units,
    )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_pdf_adapter.py -v`
Expected: 1 PASSED

- [ ] **Step 5: Commit**

```bash
git add mslearn/adapters/pdf.py tests/conftest.py tests/test_pdf_adapter.py
git commit -m "feat: PDF adapter with page locators and generated test fixture"
```

---

### Task 4: EPUB adapter (+ generated fixture)

**Files:**
- Create: `mslearn/adapters/epub.py`, `tests/test_epub_adapter.py`
- Modify: `tests/conftest.py` (add `tiny_epub` fixture)

**Interfaces:**
- Consumes: base types (Task 1), `html_to_text` (Task 2).
- Produces: `load_epub(path, role="supplement") -> SourceDocument` — one unit per content document (nav docs skipped), `Locator(kind="href", href=<item name>)`, title from DC metadata.

- [ ] **Step 1: Add fixture + failing test**

Append to `tests/conftest.py`:

```python
@pytest.fixture(scope="session")
def tiny_epub(tmp_path_factory):
    from ebooklib import epub

    path = tmp_path_factory.mktemp("fixtures") / "tiny.epub"
    book = epub.EpubBook()
    book.set_identifier("tiny-epub-1")
    book.set_title("Tiny Book")
    book.set_language("en")
    ch1 = epub.EpubHtml(title="Ch 1", file_name="ch1.xhtml", lang="en")
    ch1.content = "<html><body><h1>Ch 1</h1><p>Immutability avoids shared-state bugs.</p></body></html>"
    ch2 = epub.EpubHtml(title="Ch 2", file_name="ch2.xhtml", lang="en")
    ch2.content = "<html><body><h1>Ch 2</h1><p>Composition beats inheritance for reuse.</p></body></html>"
    book.add_item(ch1)
    book.add_item(ch2)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", ch1, ch2]
    epub.write_epub(str(path), book)
    return path
```

```python
# tests/test_epub_adapter.py
from mslearn.adapters.epub import load_epub


def test_load_epub_units_and_locators(tiny_epub):
    doc = load_epub(tiny_epub)
    assert doc.source_type == "epub" and doc.title == "Tiny Book"
    hrefs = [u.locator.href for u in doc.units]
    assert "ch1.xhtml" in hrefs and "ch2.xhtml" in hrefs
    assert all(u.locator.kind == "href" for u in doc.units)
    assert not any("nav" in (h or "") for h in hrefs)  # nav doc skipped
    joined = doc.full_text()
    assert "Immutability" in joined and "Composition" in joined
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_epub_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# mslearn/adapters/epub.py
from pathlib import Path

import ebooklib
from ebooklib import epub as epub_lib

from mslearn.adapters.base import Locator, SourceDocument, StructuralUnit, make_source_id
from mslearn.adapters.htmltext import html_to_text


def load_epub(path: Path | str, role: str = "supplement") -> SourceDocument:
    path = Path(path)
    book = epub_lib.read_epub(str(path))
    meta = book.get_metadata("DC", "title")
    title = meta[0][0] if meta else path.stem
    units: list[StructuralUnit] = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        name = item.get_name()
        if "nav" in name.lower():
            continue
        text = html_to_text(item.get_content().decode("utf-8", errors="replace"))
        if not text:
            continue
        units.append(
            StructuralUnit(
                index=len(units), title=name, text=text,
                locator=Locator(kind="href", href=name),
            )
        )
    return SourceDocument(
        source_id=make_source_id(str(path)), source_type="epub",
        role=role, title=title, units=units,
    )
```

- [ ] **Step 4: Run tests** (ebooklib may emit warnings — if reported output is noisy, silence at the source with `epub.read_epub(str(path), options={"ignore_ncx": True})` if supported, else add a targeted `filterwarnings` entry in pyproject for ebooklib's known FutureWarning and note it in the report)

Run: `.venv/bin/pytest tests/test_epub_adapter.py -v`
Expected: 1 PASSED, pristine output

- [ ] **Step 5: Commit**

```bash
git add mslearn/adapters/epub.py tests/conftest.py tests/test_epub_adapter.py
git commit -m "feat: EPUB adapter with href locators, nav docs skipped"
```

---

### Task 5: Blog adapter (trafilatura)

**Files:**
- Create: `mslearn/adapters/blog.py`, `tests/fixtures/blog.html`, `tests/test_blog_adapter.py`

**Interfaces:**
- Consumes: base types (Task 1).
- Produces: `load_blog(ref, role="supplement") -> SourceDocument` (URL via httpx or local path), `load_blog_html(html, url, role) -> SourceDocument` — one unit per extracted paragraph with `Locator(kind="url", url=..., para_index=i)`; raises `BlogExtractionError` when trafilatura finds no content. URL fetching is tested with respx.

- [ ] **Step 1: Create fixture + failing test**

```html
<!-- tests/fixtures/blog.html -->
<html><head><title>Why Global State Hurts</title></head>
<body>
<nav><a href="/">Home</a><a href="/about">About</a></nav>
<article>
<h1>Why Global State Hurts</h1>
<p>Global mutable state couples distant parts of a codebase and makes tests order-dependent, which is why experienced teams isolate it aggressively behind narrow interfaces.</p>
<p>Dependency injection is the standard remedy: pass collaborators in explicitly so each unit can be exercised in isolation with fakes, and the object graph is visible at construction time.</p>
<p>There are exceptions, of course. Process-wide caches and connection pools are global by nature, and pretending otherwise just moves the problem around without solving anything.</p>
</article>
<footer>Copyright 2026</footer>
</body></html>
```

```python
# tests/test_blog_adapter.py
from pathlib import Path

import pytest
import respx

from mslearn.adapters.blog import BlogExtractionError, load_blog, load_blog_html

FIXTURE = Path("tests/fixtures/blog.html")


def test_load_blog_html_paragraph_units():
    html = FIXTURE.read_text()
    doc = load_blog_html(html, url="https://example.com/post")
    assert doc.source_type == "blog"
    assert doc.title == "Why Global State Hurts"
    assert len(doc.units) >= 3
    for i, unit in enumerate(doc.units):
        assert unit.locator.kind == "url"
        assert unit.locator.url == "https://example.com/post"
        assert unit.locator.para_index == i
    assert "Dependency injection" in doc.full_text()
    assert "Copyright" not in doc.full_text()  # boilerplate stripped


def test_no_content_raises():
    with pytest.raises(BlogExtractionError):
        load_blog_html("<html><body></body></html>", url="https://example.com/empty")


@respx.mock
def test_load_blog_fetches_url():
    respx.get("https://example.com/post").respond(text=FIXTURE.read_text())
    doc = load_blog("https://example.com/post")
    assert doc.title == "Why Global State Hurts"


def test_load_blog_local_path():
    doc = load_blog(str(FIXTURE))
    assert doc.title == "Why Global State Hurts"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_blog_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# mslearn/adapters/blog.py
import re
from pathlib import Path

import httpx
import trafilatura

from mslearn.adapters.base import Locator, SourceDocument, StructuralUnit, make_source_id


class BlogExtractionError(Exception):
    """trafilatura found no extractable article content."""


def _title_of(html: str, fallback: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else fallback


def load_blog_html(html: str, url: str, role: str = "supplement") -> SourceDocument:
    text = trafilatura.extract(html)
    if not text:
        raise BlogExtractionError(f"no extractable content at {url!r}")
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    units = [
        StructuralUnit(
            index=i, title="", text=p,
            locator=Locator(kind="url", url=url, para_index=i),
        )
        for i, p in enumerate(paragraphs)
    ]
    return SourceDocument(
        source_id=make_source_id(url), source_type="blog",
        role=role, title=_title_of(html, url), units=units,
    )


def load_blog(ref: str, role: str = "supplement") -> SourceDocument:
    if ref.startswith(("http://", "https://")):
        resp = httpx.get(ref, follow_redirects=True, timeout=60.0)
        resp.raise_for_status()
        return load_blog_html(resp.text, url=ref, role=role)
    path = Path(ref)
    return load_blog_html(path.read_text(), url=str(path), role=role)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_blog_adapter.py -v`
Expected: 4 PASSED (if trafilatura keeps the footer or merges paragraphs differently, adjust ONLY the fixture HTML — e.g. lengthen paragraphs — never weaken the boilerplate assertion; note any fixture change in the report)

- [ ] **Step 5: Commit**

```bash
git add mslearn/adapters/blog.py tests/fixtures/blog.html tests/test_blog_adapter.py
git commit -m "feat: blog adapter via trafilatura with paragraph-level url locators"
```

---

### Task 6: Transcriber protocol + audio adapter

**Files:**
- Create: `mslearn/transcribe.py`, `mslearn/adapters/audio.py`, `tests/test_audio_adapter.py`

**Interfaces:**
- Consumes: base types (Task 1).
- Produces: `TranscriptSegment{start_s, end_s, text}`; `Transcriber` protocol with `transcribe(audio_path: Path) -> list[TranscriptSegment]`; `FasterWhisperTranscriber(model_name="small", device="auto", compute_type="int8")` (lazy model load); `load_audio(path, transcriber, role="supplement") -> SourceDocument` with `Locator(kind="time", start_s, end_s)`. Task 7 reuses `Transcriber`; Plan 4's transcribe queue instantiates `FasterWhisperTranscriber`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_audio_adapter.py
from pathlib import Path

from mslearn.adapters.audio import load_audio
from mslearn.transcribe import TranscriptSegment


class FakeTranscriber:
    def transcribe(self, audio_path: Path) -> list[TranscriptSegment]:
        return [
            TranscriptSegment(0.0, 4.2, "Welcome to the show."),
            TranscriptSegment(4.2, 9.8, "Today we discuss caching."),
            TranscriptSegment(9.8, 10.0, "   "),  # whitespace-only -> dropped
        ]


def test_load_audio_units_and_time_locators(tmp_path):
    audio = tmp_path / "episode.mp3"
    audio.write_bytes(b"\x00")  # adapter never reads it; transcriber is faked
    doc = load_audio(audio, transcriber=FakeTranscriber())
    assert doc.source_type == "audio" and doc.title == "episode"
    assert len(doc.units) == 2
    loc = doc.units[1].locator
    assert loc.kind == "time" and loc.start_s == 4.2 and loc.end_s == 9.8


def test_heavy_import_is_lazy():
    import sys

    import mslearn.transcribe  # noqa: F401

    assert "faster_whisper" not in sys.modules
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_audio_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# mslearn/transcribe.py
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class TranscriptSegment:
    start_s: float
    end_s: float
    text: str


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path) -> list[TranscriptSegment]: ...


class FasterWhisperTranscriber:
    def __init__(self, model_name: str = "small", device: str = "auto",
                 compute_type: str = "int8"):
        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        self._model = None

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # heavy — lazy import

            self._model = WhisperModel(
                self._model_name, device=self._device, compute_type=self._compute_type
            )
        return self._model

    def transcribe(self, audio_path: Path) -> list[TranscriptSegment]:
        segments, _info = self._load().transcribe(str(audio_path))
        return [TranscriptSegment(s.start, s.end, s.text.strip()) for s in segments]
```

```python
# mslearn/adapters/audio.py
from pathlib import Path

from mslearn.adapters.base import Locator, SourceDocument, StructuralUnit, make_source_id
from mslearn.transcribe import Transcriber


def load_audio(path: Path | str, transcriber: Transcriber,
               role: str = "supplement") -> SourceDocument:
    path = Path(path)
    units: list[StructuralUnit] = []
    for seg in transcriber.transcribe(path):
        text = seg.text.strip()
        if not text:
            continue
        units.append(
            StructuralUnit(
                index=len(units), title="", text=text,
                locator=Locator(kind="time", start_s=seg.start_s, end_s=seg.end_s),
            )
        )
    return SourceDocument(
        source_id=make_source_id(str(path)), source_type="audio",
        role=role, title=path.stem, units=units,
    )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_audio_adapter.py -v`
Expected: 2 PASSED

- [ ] **Step 5: Commit**

```bash
git add mslearn/transcribe.py mslearn/adapters/audio.py tests/test_audio_adapter.py
git commit -m "feat: Transcriber protocol, lazy faster-whisper impl, audio adapter"
```

---

### Task 7: YouTube adapter (captions + transcription fallback)

**Files:**
- Create: `mslearn/adapters/youtube.py`, `tests/test_youtube_adapter.py`

**Interfaces:**
- Consumes: base types (Task 1), `Transcriber`/`TranscriptSegment` (Task 6).
- Produces: `video_id_of(url) -> str`; `TranscriptUnavailable(Exception)`; `load_youtube(url, role="supplement", *, fetch_transcript=None, transcriber=None, download_audio=None, work_dir=None) -> SourceDocument`. Caption path: fetcher returns `list[dict]` with `text`/`start`/`duration` keys → time-locator units. Fallback path: any fetcher exception routes to `download_audio` + `transcriber`; if no transcriber given, raises `TranscriptUnavailable`. Defaults lazily import `youtube_transcript_api` / `yt_dlp`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_youtube_adapter.py
from pathlib import Path

import pytest

from mslearn.adapters.youtube import TranscriptUnavailable, load_youtube, video_id_of
from mslearn.transcribe import TranscriptSegment

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_video_id_parsing():
    assert video_id_of(URL) == "dQw4w9WgXcQ"
    assert video_id_of("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    with pytest.raises(ValueError):
        video_id_of("https://example.com/nope")


def test_caption_path_builds_time_units():
    def fake_fetch(video_id):
        assert video_id == "dQw4w9WgXcQ"
        return [
            {"text": "Never gonna give", "start": 0.0, "duration": 2.5},
            {"text": "you up", "start": 2.5, "duration": 1.5},
        ]

    doc = load_youtube(URL, fetch_transcript=fake_fetch)
    assert doc.source_type == "youtube"
    assert len(doc.units) == 2
    loc = doc.units[0].locator
    assert loc.kind == "time" and loc.url == URL and loc.start_s == 0.0 and loc.end_s == 2.5


def test_fallback_uses_downloader_and_transcriber(tmp_path):
    def failing_fetch(video_id):
        raise RuntimeError("captions disabled")

    downloaded = []

    def fake_download(url, out_dir):
        downloaded.append(url)
        p = Path(out_dir) / "a.m4a"
        p.write_bytes(b"\x00")
        return p

    class FakeTranscriber:
        def transcribe(self, audio_path):
            return [TranscriptSegment(0.0, 3.0, "transcribed text")]

    doc = load_youtube(URL, fetch_transcript=failing_fetch, transcriber=FakeTranscriber(),
                       download_audio=fake_download, work_dir=tmp_path)
    assert downloaded == [URL]
    assert doc.units[0].text == "transcribed text"
    assert doc.units[0].locator.kind == "time"


def test_no_captions_no_transcriber_raises():
    def failing_fetch(video_id):
        raise RuntimeError("captions disabled")

    with pytest.raises(TranscriptUnavailable):
        load_youtube(URL, fetch_transcript=failing_fetch)


def test_heavy_imports_lazy():
    import sys

    import mslearn.adapters.youtube  # noqa: F401

    assert "youtube_transcript_api" not in sys.modules
    assert "yt_dlp" not in sys.modules
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_youtube_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# mslearn/adapters/youtube.py
import re
from pathlib import Path
from typing import Callable

from mslearn.adapters.base import Locator, SourceDocument, StructuralUnit, make_source_id
from mslearn.transcribe import Transcriber


class TranscriptUnavailable(Exception):
    """No captions available and no transcriber was provided."""


_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/)([A-Za-z0-9_-]{11})")


def video_id_of(url: str) -> str:
    match = _ID_RE.search(url)
    if not match:
        raise ValueError(f"cannot parse a YouTube video id from {url!r}")
    return match.group(1)


def _default_fetch(video_id: str) -> list[dict]:
    from youtube_transcript_api import YouTubeTranscriptApi  # lazy

    return YouTubeTranscriptApi().fetch(video_id).to_raw_data()


def _default_download_audio(url: str, out_dir: Path) -> Path:
    import yt_dlp  # lazy

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
        "quiet": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return Path(ydl.prepare_filename(info))


def load_youtube(
    url: str,
    role: str = "supplement",
    *,
    fetch_transcript: Callable[[str], list[dict]] | None = None,
    transcriber: Transcriber | None = None,
    download_audio: Callable[[str, Path], Path] | None = None,
    work_dir: Path | None = None,
) -> SourceDocument:
    video_id = video_id_of(url)
    fetch = fetch_transcript or _default_fetch
    try:
        raw = fetch(video_id)
    except Exception:
        # Caption absence surfaces as library-specific exceptions; every
        # failure routes to the transcription fallback below.
        raw = None

    units: list[StructuralUnit] = []
    if raw:
        for entry in raw:
            text = entry["text"].strip()
            if not text:
                continue
            start = float(entry["start"])
            units.append(
                StructuralUnit(
                    index=len(units), title="", text=text,
                    locator=Locator(kind="time", url=url, start_s=start,
                                    end_s=start + float(entry.get("duration", 0.0))),
                )
            )
    else:
        if transcriber is None:
            raise TranscriptUnavailable(
                f"no captions for {url!r} and no transcriber provided"
            )
        downloader = download_audio or _default_download_audio
        audio_path = downloader(url, work_dir or Path("."))
        for seg in transcriber.transcribe(audio_path):
            text = seg.text.strip()
            if not text:
                continue
            units.append(
                StructuralUnit(
                    index=len(units), title="", text=text,
                    locator=Locator(kind="time", url=url,
                                    start_s=seg.start_s, end_s=seg.end_s),
                )
            )
    return SourceDocument(
        source_id=make_source_id(url), source_type="youtube",
        role=role, title=url, units=units,
    )
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_youtube_adapter.py -v`
Expected: 5 PASSED

- [ ] **Step 5: Commit**

```bash
git add mslearn/adapters/youtube.py tests/test_youtube_adapter.py
git commit -m "feat: YouTube adapter with caption fetch and whisper fallback (injectable)"
```

---

### Task 8: Chunker with property tests

**Files:**
- Create: `mslearn/chunking.py`, `tests/test_chunking.py`

**Interfaces:**
- Consumes: `SourceDocument`/`Locator` (Task 1).
- Produces: `CHUNK_TARGET_TOKENS = 500`; `estimate_tokens(text) -> int` (`max(1, len(text)//4)`); `Chunk{chunk_id, source_id, unit_index, seq, text, locator}` with `chunk_id = f"{source_id}:{seq}"`; `chunk_source(doc) -> list[Chunk]`. Plan 4's extraction pipeline consumes `Chunk`.

- [ ] **Step 1: Write the failing tests (including hypothesis properties)**

```python
# tests/test_chunking.py
import re

from hypothesis import given
from hypothesis import strategies as st

from mslearn.adapters.base import Locator, SourceDocument, StructuralUnit
from mslearn.chunking import CHUNK_TARGET_TOKENS, Chunk, chunk_source, estimate_tokens


def make_doc(unit_texts: list[str]) -> SourceDocument:
    return SourceDocument(
        source_id="src", source_type="pdf", role="spine", title="t",
        units=[
            StructuralUnit(i, f"u{i}", text, Locator(kind="page", page=i + 1))
            for i, text in enumerate(unit_texts)
        ],
    )


def strip_ws(s: str) -> str:
    return re.sub(r"\s+", "", s)


def test_small_unit_single_chunk_with_unit_locator():
    doc = make_doc(["Short paragraph one.\n\nShort paragraph two."])
    chunks = chunk_source(doc)
    assert len(chunks) == 1
    c = chunks[0]
    assert isinstance(c, Chunk)
    assert c.chunk_id == "src:0" and c.unit_index == 0
    assert c.locator.kind == "page" and c.locator.page == 1
    assert strip_ws(c.text) == strip_ws(doc.units[0].text)


def test_long_unit_splits_into_bounded_chunks():
    para = "This sentence talks about caching behavior in distributed systems. " * 120
    doc = make_doc([para])
    chunks = chunk_source(doc)
    assert len(chunks) > 1
    for c in chunks:
        assert estimate_tokens(c.text) <= CHUNK_TARGET_TOKENS


unit_texts = st.lists(
    st.text(
        alphabet=st.characters(blacklist_categories=("Cs",)),
        min_size=1, max_size=4000,
    ),
    min_size=1, max_size=6,
)


@given(unit_texts)
def test_property_no_text_loss(texts):
    doc = make_doc(texts)
    chunks = chunk_source(doc)
    assert strip_ws("".join(c.text for c in chunks)) == strip_ws("".join(texts))


@given(unit_texts)
def test_property_bounds_and_locators(texts):
    doc = make_doc(texts)
    chunks = chunk_source(doc)
    for i, c in enumerate(chunks):
        assert estimate_tokens(c.text) <= CHUNK_TARGET_TOKENS
        assert c.seq == i and c.chunk_id == f"src:{i}"
        assert 0 <= c.unit_index < len(doc.units)
        assert c.locator is doc.units[c.unit_index].locator
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_chunking.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# mslearn/chunking.py
import re
from dataclasses import dataclass

from mslearn.adapters.base import Locator, SourceDocument

CHUNK_TARGET_TOKENS = 500


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class Chunk:
    chunk_id: str
    source_id: str
    unit_index: int
    seq: int
    text: str
    locator: Locator


def _paragraphs(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]


def _split_oversize(paragraph: str) -> list[str]:
    parts: list[str] = []
    buf = ""
    for sentence in re.split(r"(?<=[.!?])\s+", paragraph):
        candidate = f"{buf} {sentence}".strip() if buf else sentence
        if buf and estimate_tokens(candidate) > CHUNK_TARGET_TOKENS:
            parts.append(buf)
            buf = sentence
        else:
            buf = candidate
    if buf:
        parts.append(buf)

    out: list[str] = []
    window = CHUNK_TARGET_TOKENS * 4
    for part in parts:
        while estimate_tokens(part) > CHUNK_TARGET_TOKENS:
            out.append(part[:window])
            part = part[window:]
        if part:
            out.append(part)
    return out


def chunk_source(doc: SourceDocument) -> list[Chunk]:
    chunks: list[Chunk] = []

    def emit(unit_index: int, locator: Locator, buf: list[str]) -> None:
        seq = len(chunks)
        chunks.append(
            Chunk(
                chunk_id=f"{doc.source_id}:{seq}", source_id=doc.source_id,
                unit_index=unit_index, seq=seq,
                text="\n\n".join(buf), locator=locator,
            )
        )

    for unit in doc.units:
        pieces: list[str] = []
        for para in _paragraphs(unit.text):
            if estimate_tokens(para) > CHUNK_TARGET_TOKENS:
                pieces.extend(_split_oversize(para))
            else:
                pieces.append(para)

        buf: list[str] = []
        for piece in pieces:
            if buf and estimate_tokens("\n\n".join([*buf, piece])) > CHUNK_TARGET_TOKENS:
                emit(unit.index, unit.locator, buf)
                buf = []
            buf.append(piece)
        if buf:
            emit(unit.index, unit.locator, buf)
    return chunks
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/test_chunking.py -v`
Expected: 4 PASSED (hypothesis runs its default 100 examples per property)

- [ ] **Step 5: Commit**

```bash
git add mslearn/chunking.py tests/test_chunking.py
git commit -m "feat: structure-aware chunker with no-loss and bound property tests"
```

---

### Task 9: Registry dispatch + cross-adapter integration test

**Files:**
- Create: `mslearn/adapters/registry.py`, `tests/test_registry.py`
- Modify: `README.md` (adapters section)

**Interfaces:**
- Consumes: all adapters (Tasks 3–7), chunker (Task 8).
- Produces: `detect_source_type(ref) -> str`; `load_source(ref, *, source_type=None, role="supplement", **kwargs) -> SourceDocument` — the single entry point Plan 4's ingestion uses. Audio/YouTube kwargs (`transcriber`, `fetch_transcript`, ...) pass through.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_registry.py
from pathlib import Path

import pytest

from mslearn.adapters.registry import detect_source_type, load_source
from mslearn.chunking import chunk_source


def test_detection():
    assert detect_source_type("book.pdf") == "pdf"
    assert detect_source_type("book.epub") == "epub"
    assert detect_source_type("post.html") == "blog"
    assert detect_source_type("https://example.com/post") == "blog"
    assert detect_source_type("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube"
    assert detect_source_type("https://youtu.be/dQw4w9WgXcQ") == "youtube"
    assert detect_source_type("episode.mp3") == "audio"
    with pytest.raises(ValueError):
        detect_source_type("mystery.xyz")


def test_load_source_dispatches_and_chunks(tiny_pdf, tiny_epub):
    fixture_html = str(Path("tests/fixtures/blog.html"))
    for ref in (str(tiny_pdf), str(tiny_epub), fixture_html):
        doc = load_source(ref, role="supplement")
        assert doc.units, ref
        chunks = chunk_source(doc)
        assert chunks, ref
        assert all(c.source_id == doc.source_id for c in chunks)


def test_load_source_explicit_type_overrides_detection(tiny_pdf):
    doc = load_source(str(tiny_pdf), source_type="pdf", role="spine")
    assert doc.role == "spine"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement**

```python
# mslearn/adapters/registry.py
from pathlib import Path

from mslearn.adapters.base import SourceDocument
from mslearn.adapters.blog import load_blog
from mslearn.adapters.epub import load_epub
from mslearn.adapters.pdf import load_pdf

_YOUTUBE_HOSTS = ("youtube.com", "youtu.be")
_SUFFIX_TYPES = {
    ".pdf": "pdf", ".epub": "epub",
    ".html": "blog", ".htm": "blog",
    ".mp3": "audio", ".m4a": "audio", ".wav": "audio", ".flac": "audio", ".ogg": "audio",
}


def detect_source_type(ref: str) -> str:
    if ref.startswith(("http://", "https://")):
        return "youtube" if any(h in ref for h in _YOUTUBE_HOSTS) else "blog"
    source_type = _SUFFIX_TYPES.get(Path(ref).suffix.lower())
    if source_type is None:
        raise ValueError(f"cannot detect source type for {ref!r}")
    return source_type


def load_source(
    ref: str, *, source_type: str | None = None, role: str = "supplement", **kwargs
) -> SourceDocument:
    stype = source_type or detect_source_type(ref)
    if stype == "pdf":
        return load_pdf(ref, role)
    if stype == "epub":
        return load_epub(ref, role)
    if stype == "blog":
        return load_blog(ref, role)
    if stype == "youtube":
        from mslearn.adapters.youtube import load_youtube  # keeps yt deps lazy

        return load_youtube(ref, role, **kwargs)
    if stype == "audio":
        from mslearn.adapters.audio import load_audio

        return load_audio(ref, kwargs["transcriber"], role)
    raise ValueError(f"unknown source type {stype!r}")
```

Append to `README.md` under a new `## Sources` heading:

```markdown
## Sources

`load_source(ref)` ingests any supported source into a normalized `SourceDocument`:
PDF/EPUB books (page/href citations), blog URLs or saved HTML (trafilatura),
YouTube videos (captions, whisper fallback), and audio files (faster-whisper).
`chunk_source(doc)` packs it into ≤500-token chunks with locators preserved.
Audio/caption-less-video ingestion downloads a Whisper model on first use.
```

- [ ] **Step 4: Run full suite + lint**

Run: `.venv/bin/ruff check . && .venv/bin/pytest -q`
Expected: ruff clean; ~75 tests passing (52 prior + ~23 new)

- [ ] **Step 5: Commit**

```bash
git add mslearn/adapters/registry.py tests/test_registry.py README.md
git commit -m "feat: source-type detection and load_source dispatch with integration tests"
```

---

## Self-Review (performed at write time)

- **Spec coverage (Plan 2 scope from design §2):** all four adapter families ✓ (PDF+EPUB Task 3/4, blog Task 5, YouTube+fallback Task 7, audio Task 6), normalized `SourceDocument` with locators ✓ (Task 1), structure-aware ~200–500-token chunking with locator preservation ✓ (Task 8), property tests for no-text-loss and valid locators ✓ (Task 8), registry for "new source type = one new adapter" extensibility ✓ (Task 9).
- **Placeholder scan:** none — complete code and commands in every step.
- **Type consistency:** `Locator`/`StructuralUnit`/`SourceDocument`/`make_source_id` names identical across Tasks 3–9; `TranscriptSegment`/`Transcriber` (Task 6) match Task 7's imports; `Chunk`/`chunk_source`/`estimate_tokens`/`CHUNK_TARGET_TOKENS` (Task 8) match Task 9's integration test.
- **Known plan-level choices (recorded, not hidden):** token estimation is `len//4` chars (deterministic, no tokenizer dep — thresholds get tuned against golden sets in Plan 8); YouTube fetch failures all route to the fallback via broad `except Exception` (commented in code); chunker's min-size target is soft (trailing chunks may be <200 tokens — packing behavior, acceptable per spec's "~200–500").
```
