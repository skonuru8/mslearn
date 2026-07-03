from __future__ import annotations

import pytest

from mslearn.chunking import chunk_source
from mslearn.graph.records import ClaimRecord, ConceptRecord
from tests.test_graph_claims import unit_vec
from tests.test_graph_ingest import embed_stub, make_doc

pytestmark = pytest.mark.neo4j


def _claim(claim_id: str, chunk_id: str, source_id: str = "srcA") -> ClaimRecord:
    return ClaimRecord(
        claim_id=claim_id,
        chunk_id=chunk_id,
        source_id=source_id,
        text=f"{claim_id} project-local claim",
        stance="neutral",
        quote="project local quote",
        trust="trusted",
    )


def test_project_a_claim_not_in_project_b_curriculum(clean_graph):
    doc = make_doc()
    chunks = chunk_source(doc)
    chunk_id = chunks[0].chunk_id

    clean_graph.upsert_source(doc, project_id="alpha")
    clean_graph.upsert_chunks(chunks, embed_stub(chunks), project_id="alpha")
    clean_graph.upsert_claim(_claim("claim-alpha", chunk_id), unit_vec(0), project_id="alpha")
    clean_graph.upsert_concept(ConceptRecord("concept-alpha", "Alpha"), project_id="alpha")
    clean_graph.assign_claim("claim-alpha", "concept-alpha", project_id="alpha")
    clean_graph.set_concept_meta("concept-alpha", order_index=0, project_id="alpha")

    clean_graph.upsert_source(doc, project_id="beta")
    clean_graph.upsert_chunks(chunks, embed_stub(chunks), project_id="beta")
    clean_graph.upsert_claim(_claim("claim-beta", chunk_id), unit_vec(1), project_id="beta")
    clean_graph.upsert_concept(ConceptRecord("concept-beta", "Beta"), project_id="beta")
    clean_graph.assign_claim("claim-beta", "concept-beta", project_id="beta")
    clean_graph.set_concept_meta("concept-beta", order_index=0, project_id="beta")

    assert [row["concept_id"] for row in clean_graph.curriculum(project_id="alpha")] == [
        "concept-alpha"
    ]
    assert [row["concept_id"] for row in clean_graph.curriculum(project_id="beta")] == [
        "concept-beta"
    ]
    assert clean_graph.claims_in_concept("concept-beta", project_id="alpha") == []
