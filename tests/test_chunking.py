import re

from hypothesis import given
from hypothesis import strategies as st

from mslearn.adapters.base import Locator, SourceDocument, StructuralUnit
from mslearn.chunking import CHUNK_TARGET_TOKENS, Chunk, chunk_source, estimate_tokens


def make_doc(unit_texts: list[str]) -> SourceDocument:
    return SourceDocument(
        source_id="src", source_type="pdf", role="spine", title="t",
        units=[
            StructuralUnit(i, f"u{i}", text, Locator(kind="page", page=i + 1))
            for i, text in enumerate(unit_texts)
        ],
    )


def strip_ws(s: str) -> str:
    return re.sub(r"\s+", "", s)


def test_small_unit_single_chunk_with_unit_locator():
    doc = make_doc(["Short paragraph one.\n\nShort paragraph two."])
    chunks = chunk_source(doc)
    assert len(chunks) == 1
    c = chunks[0]
    assert isinstance(c, Chunk)
    assert c.chunk_id == "src:0" and c.unit_index == 0
    assert c.locator.kind == "page" and c.locator.page == 1
    assert strip_ws(c.text) == strip_ws(doc.units[0].text)


def test_long_unit_splits_into_bounded_chunks():
    para = "This sentence talks about caching behavior in distributed systems. " * 120
    doc = make_doc([para])
    chunks = chunk_source(doc)
    assert len(chunks) > 1
    for c in chunks:
        assert estimate_tokens(c.text) <= CHUNK_TARGET_TOKENS


def test_chunks_inherit_unit_section_path():
    doc = SourceDocument(
        source_id="src", source_type="pdf", role="spine", title="t",
        units=[
            StructuralUnit(0, "u0", "Short paragraph one.", Locator(kind="page", page=1),
                            section_path=("Ch1", "1.1")),
            StructuralUnit(1, "u1", "Short paragraph two.", Locator(kind="page", page=2)),
        ],
    )
    chunks = chunk_source(doc)
    by_unit = {}
    for c in chunks:
        by_unit.setdefault(c.unit_index, []).append(c)
    assert all(c.section_path == ("Ch1", "1.1") for c in by_unit[0])
    assert all(c.section_path == () for c in by_unit[1])


unit_texts = st.lists(
    st.text(
        alphabet=st.characters(blacklist_categories=("Cs",)),
        min_size=1, max_size=4000,
    ),
    min_size=1, max_size=6,
)


@given(unit_texts)
def test_property_no_text_loss(texts):
    doc = make_doc(texts)
    chunks = chunk_source(doc)
    assert strip_ws("".join(c.text for c in chunks)) == strip_ws("".join(texts))


@given(unit_texts)
def test_property_bounds_and_locators(texts):
    doc = make_doc(texts)
    chunks = chunk_source(doc)
    for i, c in enumerate(chunks):
        assert estimate_tokens(c.text) <= CHUNK_TARGET_TOKENS
        assert c.seq == i and c.chunk_id == f"src:{i}"
        assert 0 <= c.unit_index < len(doc.units)
        assert c.locator is doc.units[c.unit_index].locator
