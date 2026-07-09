from mslearn.graph.records import ClaimRecord, ConceptRecord
from tests.fakes import InMemoryGraphStore


def test_inmemory_store_roundtrips_kind():
    g = InMemoryGraphStore()
    g.upsert_concept(ConceptRecord(concept_id="con1", name="Concept One"))
    rec = ClaimRecord(claim_id="c1", chunk_id="s:1", source_id="s",
                      text="t", stance="neutral", quote="q", trust="trusted", kind="caveat")
    g.upsert_claim(rec, [0.0], project_id="default")
    g.assign_claim("c1", "con1", project_id="default")
    rows = g.claims_in_concept("con1", project_id="default")
    assert rows[0]["kind"] == "caveat"


def test_inmemory_store_defaults_kind_to_claim():
    g = InMemoryGraphStore()
    g.upsert_concept(ConceptRecord(concept_id="con1", name="Concept One"))
    rec = ClaimRecord(claim_id="c2", chunk_id="s:1", source_id="s",
                      text="t2", stance="neutral", quote="q2", trust="trusted")
    g.upsert_claim(rec, [0.0], project_id="default")
    g.assign_claim("c2", "con1", project_id="default")
    rows = g.claims_in_concept("con1", project_id="default")
    assert rows[0]["kind"] == "claim"
