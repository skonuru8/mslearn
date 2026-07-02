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
