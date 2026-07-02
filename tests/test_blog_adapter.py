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
