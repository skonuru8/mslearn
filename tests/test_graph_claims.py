import pytest

from mslearn.graph.records import ClaimRecord
from tests.test_graph_ingest import embed_stub, make_doc
from mslearn.chunking import chunk_source

pytestmark = pytest.mark.neo4j


def seeded(clean_graph):
    doc = make_doc()
    chunks = chunk_source(doc)
    clean_graph.upsert_source(doc)
    clean_graph.upsert_chunks(chunks, embed_stub(chunks))
    return clean_graph, chunks


def claim(cid, chunk_id, text="Cache invalidation is hard.", kind="claim"):
    return ClaimRecord(claim_id=cid, chunk_id=chunk_id, source_id="srcA",
                       text=text, stance="neutral", quote="Invalidation is harder.",
                       trust="trusted", kind=kind)


def unit_vec(idx, dim=768):
    v = [0.0] * dim
    v[idx] = 1.0
    return v


def test_upsert_claim_idempotent_and_linked(clean_graph):
    store, chunks = seeded(clean_graph)
    store.upsert_claim(claim("cl1", chunks[0].chunk_id), unit_vec(0))
    store.upsert_claim(claim("cl1", chunks[0].chunk_id), unit_vec(0))  # rerun
    rows = store.claims_for_source("srcA")
    assert len(rows) == 1
    assert rows[0]["claim_id"] == "cl1" and rows[0]["chunk_id"] == chunks[0].chunk_id
    assert rows[0]["trust"] == "trusted"


def test_set_claim_trust(clean_graph):
    store, chunks = seeded(clean_graph)
    store.upsert_claim(claim("cl1", chunks[0].chunk_id), unit_vec(0))
    store.set_claim_trust("cl1", "rejected")
    assert store.claims_for_source("srcA")[0]["trust"] == "rejected"


def test_vector_search_orders_by_similarity(clean_graph):
    store, chunks = seeded(clean_graph)
    store.upsert_claim(claim("cl1", chunks[0].chunk_id, "about caching"), unit_vec(0))
    store.upsert_claim(claim("cl2", chunks[0].chunk_id, "about testing"), unit_vec(1))
    hits = store.vector_search_claims(unit_vec(0), k=2)
    assert hits[0]["claim_id"] == "cl1"
    assert hits[0]["score"] >= hits[1]["score"]


def test_delete_source_removes_data_and_dirties_shared_concepts(clean_graph):
    from mslearn.graph.records import ConceptRecord

    store, chunks = seeded(clean_graph)
    store.upsert_claim(claim("cl1", chunks[0].chunk_id), unit_vec(0))
    other = ClaimRecord(claim_id="cl-other", chunk_id=chunks[0].chunk_id,
                        source_id="srcB", text="other", stance="neutral",
                        quote="Invalidation is harder.", trust="trusted")
    store.upsert_claim(other, unit_vec(1))
    store.upsert_concept(ConceptRecord(concept_id="k1", name="Caching"))
    store.upsert_concept(ConceptRecord(concept_id="k2", name="Solo"))
    store.assign_claim("cl1", "k1")
    store.assign_claim("cl-other", "k1")
    store.set_concept_teaching("k1", "cached md")

    affected = store.delete_source("srcA")

    assert affected == ["k1"]
    assert store.claims_for_source("srcA") == []
    assert store.chunks_for_source("srcA") == []
    # shared concept survives, dirty, teaching cleared
    k1 = store.get_concept("k1")
    assert k1 is not None and k1["dirty"] is True and not k1.get("teach_md")
    # untouched concept intact; other source's claim intact
    assert store.get_concept("k2") is not None
    assert [r["claim_id"] for r in store.claims_for_source("srcB")] == []  # chunk deleted with srcA


def test_upsert_claim_roundtrips_kind(clean_graph):
    store, chunks = seeded(clean_graph)
    store.upsert_claim(claim("cl1", chunks[0].chunk_id, kind="caveat"), unit_vec(0))
    store.upsert_claim(claim("cl2", chunks[0].chunk_id, "second claim"), unit_vec(1))  # default kind
    rows = {r["claim_id"]: r for r in store.claims_for_source("srcA")}
    assert rows["cl1"]["kind"] == "caveat"
    assert rows["cl2"]["kind"] == "claim"


def test_citations_for_claims_includes_quote(clean_graph):
    store, chunks = seeded(clean_graph)
    store.upsert_claim(claim("cl1", chunks[0].chunk_id), unit_vec(0))

    rows = store.citations_for_claims(["cl1"])

    assert len(rows) == 1
    assert rows[0]["claim_id"] == "cl1"
    assert rows[0]["quote"] == "Invalidation is harder."


def test_delete_source_drops_empty_concepts(clean_graph):
    from mslearn.graph.records import ConceptRecord

    store, chunks = seeded(clean_graph)
    store.upsert_claim(claim("cl1", chunks[0].chunk_id), unit_vec(0))
    store.upsert_concept(ConceptRecord(concept_id="k1", name="Caching"))
    store.assign_claim("cl1", "k1")

    affected = store.delete_source("srcA")

    assert affected == ["k1"]
    assert store.get_concept("k1") is None
