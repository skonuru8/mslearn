from mslearn.adapters.base import Locator, SourceDocument, StructuralUnit, make_source_id


def test_make_source_id_stable_and_distinct():
    a = make_source_id("/books/My Book.pdf")
    assert a == make_source_id("/books/My Book.pdf")
    assert a != make_source_id("/books/Other Book.pdf")
    assert " " not in a and a == a.lower()


def test_full_text_joins_nonempty_units():
    doc = SourceDocument(
        source_id="s", source_type="pdf", role="spine", title="T",
        units=[
            StructuralUnit(0, "p1", "alpha", Locator(kind="page", page=1)),
            StructuralUnit(1, "p2", "", Locator(kind="page", page=2)),
            StructuralUnit(2, "p3", "beta", Locator(kind="page", page=3)),
        ],
    )
    assert doc.full_text() == "alpha\n\nbeta"
