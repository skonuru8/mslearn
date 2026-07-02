import pytest

from mslearn.graph.records import ConceptRecord
from tests.test_graph_claims import claim, unit_vec
from tests.test_graph_ingest import embed_stub, make_doc
from mslearn.chunking import chunk_source

pytestmark = pytest.mark.neo4j


def seeded_with_claims(clean_graph):
    doc = make_doc()
    chunks = chunk_source(doc)
    clean_graph.upsert_source(doc)
    clean_graph.upsert_chunks(chunks, embed_stub(chunks))
    clean_graph.upsert_claim(claim("cl1", chunks[0].chunk_id, "caching helps"), unit_vec(0))
    clean_graph.upsert_claim(claim("cl2", chunks[0].chunk_id, "caching hurts"), unit_vec(1))
    return clean_graph


def test_concept_assignment_and_queries(clean_graph):
    store = seeded_with_claims(clean_graph)
    store.upsert_concept(ConceptRecord(concept_id="k1", name="Caching"))
    store.assign_claim("cl1", "k1")
    store.assign_claim("cl2", "k1")
    rows = store.claims_in_concept("k1")
    assert {r["claim_id"] for r in rows} == {"cl1", "cl2"}


def test_conflict_edge_validated_and_queryable(clean_graph):
    store = seeded_with_claims(clean_graph)
    store.upsert_concept(ConceptRecord(concept_id="k1", name="Caching"))
    store.assign_claim("cl1", "k1")
    store.assign_claim("cl2", "k1")
    with pytest.raises(ValueError):
        store.add_conflict("cl1", "cl2", "vibes", "nope")
    store.add_conflict("cl1", "cl2", "genuine_debate", "sources disagree on tradeoff")
    store.add_conflict("cl1", "cl2", "genuine_debate", "sources disagree on tradeoff")
    conflicts = store.conflicts_in_concept("k1")
    assert len(conflicts) == 1
    assert conflicts[0]["classification"] == "genuine_debate"


def test_depends_on_and_dirty(clean_graph):
    store = seeded_with_claims(clean_graph)
    store.upsert_concept(ConceptRecord(concept_id="k1", name="Caching"))
    store.upsert_concept(ConceptRecord(concept_id="k2", name="Invalidation"))
    store.add_depends_on("k2", "k1")
    deps = store.concept_dependencies()
    assert {"from_id": "k2", "to_id": "k1"} in [
        {"from_id": d["from_id"], "to_id": d["to_id"]} for d in deps
    ]
    store.mark_concept_dirty("k1")
    assert store.dirty_concepts() == ["k1"]
    store.mark_concept_dirty("k1", dirty=False)
    assert store.dirty_concepts() == []
