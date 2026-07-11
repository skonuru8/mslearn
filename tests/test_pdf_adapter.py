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


def test_load_pdf_no_toc_is_flat(tiny_pdf):
    doc = load_pdf(tiny_pdf)
    assert all(u.section_path == () for u in doc.units)


def test_load_pdf_toc_sets_section_path(tmp_path):
    import fitz

    path = tmp_path / "outline.pdf"
    doc = fitz.open()
    for text in ["Page one text.", "Page two text."]:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.set_toc([[1, "Chapter 1", 1], [2, "1.1 Intro", 1], [1, "Chapter 2", 2]])
    doc.save(path)
    doc.close()

    loaded = load_pdf(path)
    by_page = {u.locator.page: u.section_path for u in loaded.units}
    assert by_page[1] == ("Chapter 1", "1.1 Intro")
    assert by_page[2] == ("Chapter 2",)
