import pytest

from mslearn.chunking import chunk_source
from mslearn.graph.records import ConceptRecord
from tests.test_graph_claims import claim, unit_vec
from tests.test_graph_ingest import embed_stub, make_doc

pytestmark = pytest.mark.neo4j


def seed(clean_graph, n_claims=3):
    doc = make_doc()
    chunks = chunk_source(doc)
    clean_graph.upsert_source(doc)
    clean_graph.upsert_chunks(chunks, embed_stub(chunks))
    for i in range(n_claims):
        clean_graph.upsert_claim(claim(f"cl{i}", chunks[0].chunk_id, f"claim {i}"), unit_vec(i))
    return clean_graph


def test_unassigned_trusted_claims_and_assignment(clean_graph):
    store = seed(clean_graph)
    store.set_claim_trust("cl2", "rejected")
    rows = store.unassigned_trusted_claims()
    assert [r["claim_id"] for r in rows] == ["cl0", "cl1"]
    assert len(rows[0]["embedding"]) == 768
    store.upsert_concept(ConceptRecord(concept_id="k1", name=""))
    store.assign_claim("cl0", "k1")
    assert [r["claim_id"] for r in store.unassigned_trusted_claims()] == ["cl1"]
    assert store.concept_id_of_claim("cl0") == "k1"
    assert store.concept_id_of_claim("cl1") is None


def test_conflict_pair_normalized(clean_graph):
    store = seed(clean_graph)
    store.upsert_concept(ConceptRecord(concept_id="k1", name=""))
    store.assign_claim("cl0", "k1")
    store.assign_claim("cl1", "k1")
    store.add_conflict("cl1", "cl0", "outdated", "first")
    store.add_conflict("cl0", "cl1", "outdated", "second")
    conflicts = store.conflicts_in_concept("k1")
    assert len(conflicts) == 1
    assert conflicts[0]["rationale"] == "second"
    # Task 3.3: the guide generator's server-built disagreements need each
    # conflict's two claim texts, not just ids — conflicts_in_concept must
    # RETURN them directly (Cypher lives only in store.py).
    assert conflicts[0]["text_a"] == "claim 0"
    assert conflicts[0]["text_b"] == "claim 1"


def test_concept_meta_and_curriculum(clean_graph):
    store = seed(clean_graph)
    store.upsert_concept(ConceptRecord(concept_id="k1", name=""))
    store.upsert_concept(ConceptRecord(concept_id="k2", name=""))
    store.set_concept_meta("k1", name="Caching", summary="s", order_index=1)
    store.set_concept_meta("k2", name="Invalidation", order_index=0)
    cur = store.curriculum()
    assert [c["concept_id"] for c in cur] == ["k2", "k1"]
    assert cur[1]["name"] == "Caching"
    assert [c["conflict_count"] for c in cur] == [0, 0]

    store.assign_claim("cl0", "k1")
    store.assign_claim("cl1", "k1")
    store.add_conflict("cl0", "cl1", "outdated", "r")
    cur = store.curriculum()
    by_id = {c["concept_id"]: c["conflict_count"] for c in cur}
    assert by_id == {"k1": 1, "k2": 0}


def test_spine_concept_order(clean_graph):
    store = seed(clean_graph)
    store.upsert_concept(ConceptRecord(concept_id="k1", name=""))
    store.assign_claim("cl0", "k1")
    rows = store.spine_concept_order()
    assert rows and rows[0]["concept_id"] == "k1" and rows[0]["first_seq"] == 0


def test_vector_hits_exclude_embedding_by_default(clean_graph):
    store = seed(clean_graph)
    hits = store.vector_search_claims(unit_vec(0), k=2)
    assert hits and all("embedding" not in h for h in hits)
    hits_with = store.vector_search_claims(unit_vec(0), k=2, include_embedding=True)
    assert "embedding" in hits_with[0]
