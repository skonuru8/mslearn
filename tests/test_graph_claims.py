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


def claim(cid, chunk_id, text="Cache invalidation is hard."):
    return ClaimRecord(claim_id=cid, chunk_id=chunk_id, source_id="srcA",
                       text=text, stance="neutral", quote="Invalidation is harder.",
                       trust="trusted")


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
