import logging

from mslearn.graph.records import ConceptRecord
from mslearn.opsdb import OpsDB
from mslearn.pipeline.synthesis import process_dirty_concepts
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore
from tests.test_extraction_graph import ScriptedRouter


def make_ctx(tmp_path, graph, outputs):
    db = OpsDB(tmp_path / "ops.db")
    router = ScriptedRouter(outputs)
    return PipelineContext(settings=None, db=db, router=router, graph=graph), router


def test_process_dirty_writes_conflict_and_name(tmp_path):
    graph = InMemoryGraphStore()
    graph.add_claim("cl1", "use cache", "recommends", "s1", [1.0, 0.0, 0.0])
    graph.add_claim("cl2", "avoid cache", "warns_against", "s1", [0.0, 1.0, 0.0])
    graph.upsert_concept(ConceptRecord(concept_id="k1", name=""))
    graph.assign_claim("cl1", "k1")
    graph.assign_claim("cl2", "k1")
    graph.mark_concept_dirty("k1")
    outputs = [
        {
            "conflicts": [
                {
                    "claim_a": "cl1",
                    "claim_b": "cl2",
                    "classification": "context_dependent",
                    "rationale": "depends on traffic pattern",
                }
            ]
        },
        {"name": "Caching Tradeoffs", "summary": "Use cache with context. Avoid blanket rules."},
    ]
    ctx, router = make_ctx(tmp_path, graph, outputs)
    assert process_dirty_concepts(ctx) == 1
    conflicts = graph.conflicts_in_concept("k1")
    assert len(conflicts) == 1
    assert conflicts[0]["rationale"] == "depends on traffic pattern"
    assert graph.all_concepts()[0]["name"] == "Caching Tradeoffs"
    assert graph.dirty_concepts() == []
    assert router.calls == ["synthesis", "synthesis"]


def test_process_dirty_drops_invalid_classification(tmp_path):
    graph = InMemoryGraphStore()
    graph.add_claim("cl1", "use cache", "recommends", "s1", [1.0, 0.0, 0.0])
    graph.add_claim("cl2", "avoid cache", "warns_against", "s1", [0.0, 1.0, 0.0])
    graph.upsert_concept(ConceptRecord(concept_id="k1", name=""))
    graph.assign_claim("cl1", "k1")
    graph.assign_claim("cl2", "k1")
    graph.mark_concept_dirty("k1")
    outputs = [
        {"conflicts": [{"claim_a": "cl1", "claim_b": "cl2", "classification": "vibes", "rationale": "nope"}]},
        {"name": "Caching", "summary": "Two sentence summary. Second sentence."},
    ]
    ctx, _router = make_ctx(tmp_path, graph, outputs)
    assert process_dirty_concepts(ctx) == 1
    assert graph.conflicts_in_concept("k1") == []
    assert graph.all_concepts()[0]["name"] == "Caching"


def test_self_conflict_is_dropped(tmp_path, caplog):
    graph = InMemoryGraphStore()
    graph.add_claim("cl1", "use cache", "recommends", "s1", [1.0, 0.0, 0.0])
    graph.add_claim("cl2", "avoid cache", "warns_against", "s1", [0.0, 1.0, 0.0])
    graph.upsert_concept(ConceptRecord(concept_id="k1", name=""))
    graph.assign_claim("cl1", "k1")
    graph.assign_claim("cl2", "k1")
    graph.mark_concept_dirty("k1")
    outputs = [
        {
            "conflicts": [
                {
                    "claim_a": "cl1",
                    "claim_b": "cl1",  # self-pair
                    "classification": "context_dependent",
                    "rationale": "same claim both sides",
                }
            ]
        },
        {"name": "Self Conflict", "summary": "A two sentence summary. Second sentence here."},
    ]
    ctx, _ = make_ctx(tmp_path, graph, outputs)
    with caplog.at_level(logging.WARNING, logger="mslearn.pipeline.synthesis"):
        process_dirty_concepts(ctx)
    assert graph.conflicts_in_concept("k1") == []
    assert any("dropped" in r.message and "self-pair" in r.message for r in caplog.records)


def test_process_dirty_singleton_only_names(tmp_path):
    graph = InMemoryGraphStore()
    graph.add_claim("cl1", "use cache", "recommends", "s1", [1.0, 0.0, 0.0])
    graph.upsert_concept(ConceptRecord(concept_id="k1", name=""))
    graph.assign_claim("cl1", "k1")
    graph.mark_concept_dirty("k1")
    ctx, router = make_ctx(
        tmp_path,
        graph,
        [{"name": "Cache Basics", "summary": "Caching basics. Keep invalidation explicit."}],
    )
    assert process_dirty_concepts(ctx) == 1
    assert graph.conflicts_in_concept("k1") == []
    assert graph.all_concepts()[0]["name"] == "Cache Basics"
    assert router.calls == ["synthesis"]
