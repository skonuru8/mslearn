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
