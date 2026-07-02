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


def test_non_youtube_hosts_are_blog():
    assert detect_source_type("https://notayoutube.com/watch?v=dQw4w9WgXcQ") == "blog"
    assert detect_source_type("https://example.com/?ref=youtube.com") == "blog"
    assert detect_source_type("https://m.youtube.com/watch?v=dQw4w9WgXcQ") == "youtube"


def test_load_source_explicit_type_overrides_detection(tiny_pdf):
    doc = load_source(str(tiny_pdf), source_type="pdf", role="spine")
    assert doc.role == "spine"
