from mslearn.graph.records import ConceptRecord
from mslearn.opsdb import OpsDB
from mslearn.pipeline.synthesis import build_curriculum
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore
from tests.test_extraction_graph import ScriptedRouter


def make_ctx(tmp_path, graph, outputs):
    db = OpsDB(tmp_path / "ops.db")
    router = ScriptedRouter(outputs)
    return PipelineContext(settings=None, db=db, router=router, graph=graph), router


def seed_spine(graph):
    graph.upsert_concept(ConceptRecord(concept_id="k1", name="Basics"))
    graph.upsert_concept(ConceptRecord(concept_id="k2", name="Middle"))
    graph.upsert_concept(ConceptRecord(concept_id="k3", name="Advanced"))
    graph.add_claim("cl1", "b", "neutral", "s1", [1.0, 0.0], spine_seq=0)
    graph.add_claim("cl2", "m", "neutral", "s1", [0.0, 1.0], spine_seq=1)
    graph.add_claim("cl3", "a", "neutral", "s1", [0.5, 0.5], spine_seq=2)
    graph.assign_claim("cl1", "k1")
    graph.assign_claim("cl2", "k2")
    graph.assign_claim("cl3", "k3")


def test_curriculum_respects_dependency_and_spine_order(tmp_path):
    graph = InMemoryGraphStore()
    seed_spine(graph)
    ctx, router = make_ctx(
        tmp_path,
        graph,
        [{"edges": [{"from_concept": "k3", "to_concept": "k1"}]}],
    )
    order = build_curriculum(ctx)
    assert order == ["k1", "k2", "k3"]
    assert graph.curriculum()[0]["order_index"] == 0
    assert router.calls == ["synthesis"]


def test_curriculum_drops_cycle_edge(tmp_path):
    graph = InMemoryGraphStore()
    seed_spine(graph)
    ctx, _router = make_ctx(
        tmp_path,
        graph,
        [{"edges": [{"from_concept": "k1", "to_concept": "k2"}, {"from_concept": "k2", "to_concept": "k1"}]}],
    )
    order = build_curriculum(ctx)
    assert order == ["k2", "k1", "k3"]
    deps = graph.concept_dependencies()
    assert {"from_id": "k1", "to_id": "k2"} in deps
    assert {"from_id": "k2", "to_id": "k1"} not in deps


def test_curriculum_appends_non_spine_by_name(tmp_path):
    graph = InMemoryGraphStore()
    seed_spine(graph)
    graph.upsert_concept(ConceptRecord(concept_id="k9", name="Appendix"))
    graph.upsert_concept(ConceptRecord(concept_id="k8", name="Zeta"))
    ctx, _router = make_ctx(tmp_path, graph, [{"edges": []}])
    order = build_curriculum(ctx)
    assert order[:3] == ["k1", "k2", "k3"]
    assert order[3:] == ["k9", "k8"]


def test_curriculum_idempotent_rerun(tmp_path):
    graph = InMemoryGraphStore()
    seed_spine(graph)
    outputs = [{"edges": [{"from_concept": "k3", "to_concept": "k1"}]}] * 2
    ctx, _router = make_ctx(tmp_path, graph, outputs)
    first = build_curriculum(ctx)
    second = build_curriculum(ctx)
    assert first == second
    assert [r["concept_id"] for r in graph.curriculum()] == second
