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


def test_flatten_toc_pure_helper():
    from ebooklib import epub

    from mslearn.adapters.epub import _flatten_toc

    toc = (
        epub.Link("ch1.xhtml", "Chapter 1", "ch1"),
        (epub.Section("Part 2"), (epub.Link("ch2.xhtml", "Chapter 2", "ch2"),)),
    )
    result = _flatten_toc(toc)
    assert result == {
        "ch1.xhtml": ("Chapter 1",),
        "ch2.xhtml": ("Part 2", "Chapter 2"),
    }


@pytest.fixture(scope="module")
def nav_toc_epub(tmp_path_factory):
    from ebooklib import epub

    path = tmp_path_factory.mktemp("fixtures") / "navtoc.epub"
    book = epub.EpubBook()
    book.set_identifier("nav-toc-1")
    book.set_title("Nav Toc Book")
    book.set_language("en")
    ch1 = epub.EpubHtml(title="Ch 1", file_name="ch1.xhtml", lang="en")
    ch1.content = "<html><body><p>First chapter body.</p></body></html>"
    ch2 = epub.EpubHtml(title="Ch 2", file_name="ch2.xhtml", lang="en")
    ch2.content = "<html><body><p>Second chapter body.</p></body></html>"
    book.add_item(ch1)
    book.add_item(ch2)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = (
        epub.Link("ch1.xhtml", "Chapter One", "ch1"),
        (epub.Section("Part Two"), (epub.Link("ch2.xhtml", "Chapter Two", "ch2"),)),
    )
    book.spine = ["nav", ch1, ch2]
    epub.write_epub(str(path), book)
    return path


def test_load_epub_nav_derived_section_path_and_titles(nav_toc_epub):
    doc = load_epub(nav_toc_epub)
    by_href = {u.locator.href: u for u in doc.units}
    assert by_href["ch1.xhtml"].section_path == ("Chapter One",)
    assert by_href["ch1.xhtml"].title == "Chapter One"
    assert by_href["ch2.xhtml"].section_path == ("Part Two", "Chapter Two")
    assert by_href["ch2.xhtml"].title == "Chapter Two"
