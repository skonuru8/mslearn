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


def test_upsert_claim_is_idempotent_and_flags_missing_chunk(clean_graph):
    from mslearn.chunking import chunk_source
    from mslearn.graph.records import ClaimRecord
    from mslearn.graph.store import GraphWriteError
    from tests.test_graph_ingest import embed_stub, make_doc

    doc = make_doc()
    chunks = chunk_source(doc)
    clean_graph.upsert_source(doc)
    clean_graph.upsert_chunks(chunks, embed_stub(chunks))
    chunk_id = chunks[0].chunk_id
    record = ClaimRecord(
        claim_id="cl1", chunk_id=chunk_id, source_id=doc.source_id,
        text="A grounded fact.", stance="neutral", quote="fact", trust="trusted",
    )
    emb = [0.1] * 768
    # First commit creates it; a second identical commit (duplicate claim text
    # across sources, or a Celery redelivery) is a benign no-op, not an error.
    clean_graph.upsert_claim(record, emb)
    clean_graph.upsert_claim(record, emb)  # must not raise
    # A genuinely missing chunk is still an error.
    orphan = ClaimRecord(
        claim_id="cl2", chunk_id="nope:0", source_id=doc.source_id,
        text="x", stance="neutral", quote="x", trust="trusted",
    )
    with pytest.raises(GraphWriteError):
        clean_graph.upsert_claim(orphan, emb)


def test_set_and_read_concept_category(clean_graph):
    from mslearn.graph.records import ConceptRecord

    clean_graph.upsert_concept(ConceptRecord(concept_id="k1", name="N"))
    clean_graph.set_concept_categories([("k1", "Numbers")])
    assert clean_graph.get_concept("k1")["category"] == "Numbers"
    rows = {c["concept_id"]: c for c in clean_graph.all_concepts()}
    assert rows["k1"]["category"] == "Numbers"


def test_chunk_section_path_round_trips(clean_graph):
    import json

    from mslearn.chunking import chunk_source
    from tests.test_graph_ingest import embed_stub, make_doc

    doc = make_doc()
    chunks = chunk_source(doc)
    chunks[0].section_path = ("A", "B")
    clean_graph.upsert_source(doc)
    clean_graph.upsert_chunks(chunks, embed_stub(chunks))
    rows = clean_graph.chunks_for_source(doc.source_id)
    assert json.loads(rows[0]["section_path"]) == ["A", "B"]


def test_concept_section_path_rollup(clean_graph):
    from mslearn.adapters.base import Locator, SourceDocument, StructuralUnit
    from mslearn.chunking import chunk_source
    from mslearn.graph.records import ConceptRecord
    from tests.test_graph_ingest import embed_stub

    doc = SourceDocument(
        source_id="srcA", source_type="pdf", role="spine", title="Book A",
        units=[
            StructuralUnit(0, "p1", "Caching is hard.", Locator(kind="page", page=1),
                            section_path=("Ch1", "1.1")),
            StructuralUnit(1, "p2", "Invalidation is harder.", Locator(kind="page", page=2),
                            section_path=("Ch1", "1.1")),
        ],
    )
    chunks = chunk_source(doc)
    clean_graph.upsert_source(doc)
    clean_graph.upsert_chunks(chunks, embed_stub(chunks))

    clean_graph.upsert_concept(ConceptRecord(concept_id="k1", name="N"))
    from mslearn.graph.records import ClaimRecord

    for chunk in chunks[:2]:
        claim = ClaimRecord(
            claim_id=f"cl-{chunk.chunk_id}",
            text="t",
            stance="neutral",
            source_id=chunk.source_id,
            chunk_id=chunk.chunk_id,
            trust="trusted",
            quote="",
        )
        clean_graph.upsert_claim(claim, [0.1, 0.2])
        clean_graph.assign_claim(claim.claim_id, "k1")

    clean_graph.set_concept_sections([("k1", ["Ch1", "1.1"])])

    assert clean_graph.get_concept("k1")["section_path"] == ["Ch1", "1.1"]

    paths = clean_graph.concept_section_paths()
    entries = paths["k1"]
    assert sorted(entries) == sorted(
        [(["Ch1", "1.1"], chunks[0].seq), (["Ch1", "1.1"], chunks[1].seq)]
    )
