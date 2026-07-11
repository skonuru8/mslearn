from pathlib import Path

from mslearn.adapters.text import load_text


def test_load_text_paragraphs_flat(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("First paragraph.\n\nSecond paragraph.\n", encoding="utf-8")

    doc = load_text(path, role="supplement")

    assert doc.source_type == "text" and doc.role == "supplement"
    assert len(doc.units) >= 1
    assert all(u.section_path == () for u in doc.units)
    assert "First paragraph." in doc.full_text()
    assert "Second paragraph." in doc.full_text()


def test_load_text_default_role(tmp_path):
    path: Path = tmp_path / "single.txt"
    path.write_text("Just one paragraph.", encoding="utf-8")

    doc = load_text(path)

    assert doc.role == "supplement"
    assert len(doc.units) == 1
    assert doc.units[0].section_path == ()
