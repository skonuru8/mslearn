from mslearn.adapters.markdown_src import load_markdown

MD = """# A

intro

## A.1

body

### A.1.1

deep
"""


def test_load_markdown_heading_ancestry(tmp_path):
    path = tmp_path / "doc.md"
    path.write_text(MD, encoding="utf-8")

    doc = load_markdown(path, role="supplement")

    assert doc.source_type == "markdown" and doc.role == "supplement"
    by_text = {u.text.strip(): u.section_path for u in doc.units}
    assert by_text["intro"] == ("A",)
    assert by_text["body"] == ("A", "A.1")
    assert by_text["deep"] == ("A", "A.1", "A.1.1")


def test_load_markdown_no_leading_heading_is_flat(tmp_path):
    path = tmp_path / "flat.md"
    path.write_text("just text, no headings\n", encoding="utf-8")

    doc = load_markdown(path)

    assert len(doc.units) == 1
    assert doc.units[0].section_path == ()


def test_load_markdown_text_before_first_heading_is_flat(tmp_path):
    path = tmp_path / "preamble.md"
    path.write_text("preamble text\n\n# A\n\nafter\n", encoding="utf-8")

    doc = load_markdown(path)

    by_text = {u.text.strip(): u.section_path for u in doc.units}
    assert by_text["preamble text"] == ()
    assert by_text["after"] == ("A",)
