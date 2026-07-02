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
