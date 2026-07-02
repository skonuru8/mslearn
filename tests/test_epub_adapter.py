import pytest

from mslearn.adapters.epub import load_epub


@pytest.fixture(scope="module")
def nav_named_epub(tmp_path_factory):
    from ebooklib import epub

    path = tmp_path_factory.mktemp("fixtures") / "navnamed.epub"
    book = epub.EpubBook()
    book.set_identifier("nav-named-1")
    book.set_title("Nav Named")
    book.set_language("en")
    ch = epub.EpubHtml(title="Tips", file_name="navigation-tips.xhtml", lang="en")
    ch.content = "<html><body><p>Real content about navigation tips.</p></body></html>"
    book.add_item(ch)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.spine = ["nav", ch]
    epub.write_epub(str(path), book)
    return path


def test_content_file_with_nav_in_name_not_skipped(nav_named_epub):
    doc = load_epub(nav_named_epub)
    assert any(u.locator.href == "navigation-tips.xhtml" for u in doc.units)
    assert "Real content" in doc.full_text()


def test_load_epub_units_and_locators(tiny_epub):
    doc = load_epub(tiny_epub)
    assert doc.source_type == "epub" and doc.title == "Tiny Book"
    hrefs = [u.locator.href for u in doc.units]
    assert "ch1.xhtml" in hrefs and "ch2.xhtml" in hrefs
    assert all(u.locator.kind == "href" for u in doc.units)
    assert not any("nav" in (h or "") for h in hrefs)  # nav doc skipped
    joined = doc.full_text()
    assert "Immutability" in joined and "Composition" in joined
