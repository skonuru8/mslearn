from pathlib import Path

from mslearn.graph.records import ConceptRecord
from mslearn.opsdb import OpsDB
from mslearn.pipeline.qa import retrieve
from mslearn.settings import Settings
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore, ScriptedRouter


def make_ctx(tmp_path, graph, router):
    return PipelineContext(
        settings=Settings(profiles_path=Path("profiles.yaml")),
        db=OpsDB(tmp_path / "ops.db"),
        router=router,
        graph=graph,
    )


def test_retrieve_filters_untrusted_claims_and_keeps_retrieved_conflicts(tmp_path):
    graph = InMemoryGraphStore()
    graph.upsert_concept(ConceptRecord("k1", "Cache TTLs", "TTL tradeoffs."))
    graph.add_claim("trusted", "TTLs bound stale cache data.", "neutral", "s1", [1.0, 0.0])
    graph.add_claim("rejected", "Rejected cache folklore.", "neutral", "s2", [0.99, 0.0], "rejected")
    graph.add_claim("escalated", "Tiny TTLs can cause churn.", "warns_against", "s3", [0.0, 1.0], "escalated")
    graph.assign_claim("trusted", "k1")
    graph.assign_claim("rejected", "k1")
    graph.assign_claim("escalated", "k1")
    graph.add_conflict("trusted", "escalated", "context_dependent", "Latency versus freshness.")
    graph.chunks["ch1"] = {
        "chunk_id": "ch1",
        "source_id": "s1",
        "seq": 1,
        "unit_index": 0,
        "text": "TTLs bound stale cache data.",
        "kind": "blog",
        "page": None,
        "href": None,
        "url": "https://example.com/cache",
        "para_index": 4,
        "start_s": None,
        "end_s": None,
        "embedding": [1.0, 0.0],
    }
    router = ScriptedRouter(embeddings=[[1.0, 0.0]])
    ctx = make_ctx(tmp_path, graph, router)

    result = retrieve(ctx, "How should I choose a cache TTL?", k=3)

    assert router.embed_texts == ["How should I choose a cache TTL?"]
    assert [row["claim_id"] for row in result["claims"]] == ["trusted", "escalated"]
    assert [row["chunk_id"] for row in result["chunks"]] == ["ch1"]
    assert result["conflicts"] == [
        {
            "claim_a": "escalated",
            "claim_b": "trusted",
            "classification": "context_dependent",
            "rationale": "Latency versus freshness.",
        }
    ]
