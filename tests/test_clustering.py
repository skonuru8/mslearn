from mslearn.graph.records import ConceptRecord
from mslearn.opsdb import OpsDB
from mslearn.pipeline.synthesis import cluster_new_claims
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore
from tests.test_extraction_graph import ScriptedRouter


def make_ctx(tmp_path, graph, outputs):
    db = OpsDB(tmp_path / "ops.db")
    router = ScriptedRouter(outputs)
    return PipelineContext(settings=None, db=db, router=router, graph=graph), router


def test_cluster_singleton_when_no_candidates(tmp_path):
    graph = InMemoryGraphStore()
    graph.add_claim("cl1", "cache ttl", "neutral", "s1", [1.0, 0.0, 0.0])
    ctx, router = make_ctx(tmp_path, graph, [])
    dirty = cluster_new_claims(ctx)
    assert dirty == {"k-cl1"}
    assert graph.concept_id_of_claim("cl1") == "k-cl1"
    assert router.calls == []


def test_cluster_joins_existing_concept(tmp_path):
    graph = InMemoryGraphStore()
    graph.add_claim("cl0", "cache ttl", "neutral", "s1", [1.0, 0.0, 0.0])
    graph.add_claim("cl1", "cache expiry", "neutral", "s1", [0.99, 0.01, 0.0])
    graph.upsert_concept(ConceptRecord(concept_id="k-existing", name=""))
    graph.assign_claim("cl0", "k-existing")
    ctx, router = make_ctx(tmp_path, graph, [{"matches": ["cl0"]}])
    dirty = cluster_new_claims(ctx)
    assert dirty == {"k-existing"}
    assert graph.concept_id_of_claim("cl1") == "k-existing"
    assert router.calls == ["synthesis"]


def test_cluster_forms_new_concept_from_unassigned_matches(tmp_path):
    graph = InMemoryGraphStore()
    graph.add_claim("cl1", "cache ttl", "neutral", "s1", [1.0, 0.0, 0.0])
    graph.add_claim("cl2", "cache expiry", "neutral", "s1", [0.99, 0.01, 0.0])
    ctx, router = make_ctx(tmp_path, graph, [{"matches": ["cl2"]}])
    dirty = cluster_new_claims(ctx)
    assert dirty == {"k-cl1"}
    assert graph.concept_id_of_claim("cl1") == "k-cl1"
    assert graph.concept_id_of_claim("cl2") == "k-cl1"
    assert router.calls == ["synthesis"]


def test_cluster_drops_hallucinated_match_ids(tmp_path):
    graph = InMemoryGraphStore()
    graph.add_claim("cl1", "cache ttl", "neutral", "s1", [1.0, 0.0, 0.0])
    graph.add_claim("cl2", "cache expiry", "neutral", "s1", [0.99, 0.01, 0.0])
    ctx, router = make_ctx(tmp_path, graph, [{"matches": ["fake-id"]}, {"matches": []}])
    dirty = cluster_new_claims(ctx)
    assert dirty == {"k-cl1", "k-cl2"}
    assert graph.concept_id_of_claim("cl1") == "k-cl1"
    assert graph.concept_id_of_claim("cl2") == "k-cl2"
    assert router.calls == ["synthesis", "synthesis"]


def test_cluster_skips_rejected_claims(tmp_path):
    graph = InMemoryGraphStore()
    graph.add_claim("cl_bad", "bad", "neutral", "s1", [1.0, 0.0, 0.0], trust="rejected")
    graph.add_claim("cl_ok", "good", "neutral", "s1", [1.0, 0.0, 0.0], trust="trusted")
    ctx, router = make_ctx(tmp_path, graph, [])
    dirty = cluster_new_claims(ctx)
    assert dirty == {"k-cl_ok"}
    assert graph.concept_id_of_claim("cl_bad") is None
    assert router.calls == []
