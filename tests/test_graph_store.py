import pytest

pytestmark = pytest.mark.neo4j


def test_ping_and_schema(clean_graph):
    indexes = clean_graph.list_index_names()
    assert "claim_embedding" in indexes and "chunk_embedding" in indexes


def test_wipe_empties_graph(clean_graph):
    clean_graph.run_write("CREATE (:Source {source_id: 'tmp'})")
    assert clean_graph.node_count() == 1
    clean_graph.wipe()
    assert clean_graph.node_count() == 0


def test_counted_write_detects_silent_noop(clean_graph):
    from mslearn.graph.store import GraphWriteError

    counters = clean_graph.run_write("CREATE (:Source {source_id: 'x'})")
    assert counters.contains_updates
    with pytest.raises(GraphWriteError):
        clean_graph.run_write_checked(
            "MATCH (c:Chunk {chunk_id: $cid}) SET c.text = 'y'", cid="missing"
        )


def test_get_chunk(clean_graph):
    from mslearn.chunking import chunk_source
    from tests.test_graph_ingest import embed_stub, make_doc

    doc = make_doc()
    chunks = chunk_source(doc)
    clean_graph.upsert_source(doc)
    clean_graph.upsert_chunks(chunks, embed_stub(chunks))
    row = clean_graph.get_chunk(chunks[0].chunk_id)
    assert row["text"] == chunks[0].text and row["kind"] == "page"
    assert clean_graph.get_chunk("missing") is None


def test_set_and_read_concept_category(clean_graph):
    from mslearn.graph.records import ConceptRecord

    clean_graph.upsert_concept(ConceptRecord(concept_id="k1", name="N"))
    clean_graph.set_concept_categories([("k1", "Numbers")])
    assert clean_graph.get_concept("k1")["category"] == "Numbers"
    rows = {c["concept_id"]: c for c in clean_graph.all_concepts()}
    assert rows["k1"]["category"] == "Numbers"
