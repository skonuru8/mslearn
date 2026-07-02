import pytest

from mslearn.adapters.base import Locator, SourceDocument, StructuralUnit
from mslearn.chunking import chunk_source

pytestmark = pytest.mark.neo4j


def make_doc():
    return SourceDocument(
        source_id="srcA", source_type="pdf", role="spine", title="Book A",
        units=[StructuralUnit(0, "p1", "Caching is hard.\n\nInvalidation is harder.",
                              Locator(kind="page", page=1))],
    )


def embed_stub(chunks):
    return [[float(i)] * 768 for i, _ in enumerate(chunks)]


def test_upsert_source_and_chunks_idempotent(clean_graph):
    doc = make_doc()
    chunks = chunk_source(doc)
    clean_graph.upsert_source(doc)
    clean_graph.upsert_chunks(chunks, embed_stub(chunks))
    clean_graph.upsert_source(doc)  # re-run: no duplicates
    clean_graph.upsert_chunks(chunks, embed_stub(chunks))

    rows = clean_graph.chunks_for_source("srcA")
    assert len(rows) == len(chunks)
    assert rows[0]["seq"] == 0
    assert rows[0]["kind"] == "page" and rows[0]["page"] == 1
    assert clean_graph.node_count() == 1 + len(chunks)


def test_chunk_embedding_length_mismatch_raises(clean_graph):
    doc = make_doc()
    chunks = chunk_source(doc)
    clean_graph.upsert_source(doc)
    with pytest.raises(ValueError, match="embeddings length"):
        clean_graph.upsert_chunks(chunks, [])
