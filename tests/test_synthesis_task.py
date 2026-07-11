import json

import pytest

from mslearn.chunking import chunk_source
from mslearn.graph.records import ConceptRecord
from mslearn.opsdb import OpsDB
from mslearn.pipeline.extraction_graph import build_extraction_graph
from mslearn.pipeline.synthesis import (
    assign_categories,
    build_curriculum,
    cluster_new_claims,
    process_dirty_concepts,
)
from mslearn.worker import tasks as worker_tasks
from mslearn.worker.app import app
from mslearn.worker.context import PipelineContext, set_context
from tests.fakes import InMemoryGraphStore
from tests.fakes import ScriptedRouter as CapturingRouter
from tests.test_extraction_graph import CHUNK, GOOD, ScriptedRouter
from tests.test_graph_claims import claim, unit_vec
from tests.test_graph_ingest import embed_stub, make_doc


class FakeGraph:
    def __init__(self, chunks):
        self._chunks = chunks
        self.claims = {}

    def get_chunk(self, chunk_id, *, project_id="default"):
        return self._chunks.get(chunk_id)

    def upsert_claim(self, claim_record, embedding, *, project_id="default"):
        self.claims[claim_record.claim_id] = (claim_record, embedding)

    def source_type_of(self, source_id, *, project_id="default"):
        return None


@pytest.fixture(autouse=True)
def eager_app():
    app.conf.task_always_eager = True
    yield
    app.conf.task_always_eager = False


def test_trigger_runs_once_when_source_completes(tmp_path, monkeypatch):
    db = OpsDB(tmp_path / "ops.db")
    db.register_source("s1", ref="r", role="spine", total_chunks=2)
    db.set_source_status("s1", "running")
    db.register_chunk_jobs("s1", ["s1:0", "s1:1"])
    graph = FakeGraph(
        {
            "s1:0": {"chunk_id": "s1:0", "source_id": "s1", "text": CHUNK},
            "s1:1": {"chunk_id": "s1:1", "source_id": "s1", "text": CHUNK},
        }
    )
    router = ScriptedRouter([GOOD, GOOD])
    set_context(PipelineContext(
        settings=None, db=db, router=router, graph=graph,
        extraction_graph=build_extraction_graph(router, db),
    ))

    class Trigger:
        def __init__(self):
            self.calls = 0

        def delay(self, project_id="default"):
            self.calls += 1

    trigger = Trigger()
    monkeypatch.setattr(worker_tasks, "synthesize_task", trigger)

    worker_tasks.extract_chunk_task.delay("default", "s1:0").get()
    assert trigger.calls == 0
    worker_tasks.extract_chunk_task.delay("default", "s1:1").get()
    assert trigger.calls == 1
    assert db.source_row("s1")["status"] == "done"


def test_all_failed_source_never_triggers_synthesis(tmp_path, monkeypatch):
    from mslearn.providers.base import ProviderError

    db = OpsDB(tmp_path / "ops.db")
    db.register_source("s1", ref="r", role="spine", total_chunks=2)
    db.set_source_status("s1", "running")
    db.register_chunk_jobs("s1", ["s1:0", "s1:1"])
    graph = FakeGraph(
        {
            "s1:0": {"chunk_id": "s1:0", "source_id": "s1", "text": CHUNK},
            "s1:1": {"chunk_id": "s1:1", "source_id": "s1", "text": CHUNK},
        }
    )
    router = ScriptedRouter([ProviderError("bad"), ProviderError("bad")])
    set_context(PipelineContext(
        settings=None, db=db, router=router, graph=graph,
        extraction_graph=build_extraction_graph(router, db),
    ))

    class Trigger:
        def __init__(self):
            self.calls = 0

        def delay(self, project_id="default"):
            self.calls += 1

    trigger = Trigger()
    monkeypatch.setattr(worker_tasks, "synthesize_task", trigger)

    worker_tasks.extract_chunk_task.delay("default", "s1:0").get()
    worker_tasks.extract_chunk_task.delay("default", "s1:1").get()
    assert trigger.calls == 0
    row = db.source_row("s1")
    assert row["status"] == "failed"
    assert row["error"] == "all 2 chunks failed"


@pytest.mark.neo4j
def test_end_to_end_synthesis(clean_graph, tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    doc = make_doc()
    chunks = chunk_source(doc)
    clean_graph.upsert_source(doc)
    clean_graph.upsert_chunks(chunks, embed_stub(chunks))
    clean_graph.upsert_claim(claim("cl1", chunks[0].chunk_id, "use cache"), unit_vec(0))
    clean_graph.upsert_claim(claim("cl2", chunks[0].chunk_id, "cache can hurt"), [0.99, 0.01] + [0.0] * 766)
    clean_graph.upsert_claim(claim("cl3", chunks[0].chunk_id, "avoid cache"), unit_vec(1))
    router = ScriptedRouter(
        [
            {"matches": ["cl2"]},
            {
                "conflicts": [
                    {
                        "claim_a": "cl1",
                        "claim_b": "cl2",
                        "classification": "genuine_debate",
                        "rationale": "workload-dependent tradeoff",
                    }
                ]
            },
            {"name": "Caching Tradeoffs", "summary": "Caching helps latency. Caching can add complexity."},
            {"name": "Bypass Cache Cases", "summary": "Sometimes direct reads win. Freshness can dominate."},
            {"edges": [{"from_concept": "k-cl3", "to_concept": "k-cl1"}]},
            {"categories": [{"name": "Caching", "concept_ids": ["k-cl1", "k-cl3"]}]},
        ]
    )
    ctx = PipelineContext(settings=None, db=db, router=router, graph=clean_graph)
    set_context(ctx)

    dirty = cluster_new_claims(ctx)
    processed = process_dirty_concepts(ctx)
    ordered = build_curriculum(ctx)
    assign_categories(ctx)
    db.set_project_setting(
        "default", "synthesis:last_run",
        json.dumps(
            {
                "dirty_concepts": len(dirty),
                "processed_concepts": processed,
                "curriculum_len": len(ordered),
            },
            sort_keys=True,
        ),
    )

    assert router.calls == [
        "synthesis", "synthesis", "synthesis", "synthesis", "synthesis", "synthesis",
    ]
    assert clean_graph.conflicts_in_concept("k-cl1")[0]["classification"] == "genuine_debate"
    cur = clean_graph.curriculum()
    assert [row["concept_id"] for row in cur][:2] == ["k-cl1", "k-cl3"]
    assert all(row["category"] == "Caching" for row in cur[:2])
    assert db.get_project_setting("default", "synthesis:last_run") is not None


def test_synthesis_requests_carry_configured_max_tokens(tmp_path):
    # deepseek-v4-flash (the openrouter profile's synthesis model) is a
    # reasoning model that can burn the default 2048-token budget on hidden
    # reasoning before writing an answer. Every synthesis.py callsite must
    # request the configured synth.max_tokens budget, not the base.py
    # ModelRequest default.
    db = OpsDB(tmp_path / "ops.db")
    db.set_tunable("synth.max_tokens", 4096.0, reason="test")
    graph = InMemoryGraphStore()
    graph.upsert_concept(ConceptRecord(concept_id="k1", name="A"))
    graph.upsert_concept(ConceptRecord(concept_id="k2", name="B"))
    graph.add_claim("c1", "claim one", "neutral", "s1", [1.0, 0.0])
    graph.add_claim("c2", "claim two", "neutral", "s2", [0.9, 0.0])
    graph.assign_claim("c1", "k1")
    graph.assign_claim("c2", "k1")
    graph.mark_concept_dirty("k1", True)

    router = CapturingRouter(
        outputs=[
            {"conflicts": []},
            {"name": "Concept A", "summary": "..."},
        ]
    )
    ctx = PipelineContext(settings=None, db=db, router=router, graph=graph)

    process_dirty_concepts(ctx)

    assert router.calls == ["synthesis", "synthesis"]
    assert all(req.max_tokens == 4096 for req in router.requests)


def test_synthesize_task_enqueues_warm_guides_on_success(tmp_path, monkeypatch):
    db = OpsDB(tmp_path / "ops.db")
    graph = FakeGraph({})
    set_context(PipelineContext(settings=None, db=db, router=None, graph=graph))

    monkeypatch.setattr(worker_tasks, "cluster_new_claims", lambda c, p: set())
    monkeypatch.setattr(worker_tasks, "process_dirty_concepts", lambda c, p: 0)
    monkeypatch.setattr(worker_tasks, "build_curriculum", lambda c, p: [])
    monkeypatch.setattr(worker_tasks, "assign_categories", lambda c, p: 0)

    calls = []
    monkeypatch.setattr(
        worker_tasks.warm_guides_task, "delay", lambda project_id: calls.append(project_id)
    )

    worker_tasks.synthesize_task.delay("default").get()

    assert calls == ["default"]


def test_synthesize_task_skips_warm_guides_on_failure(tmp_path, monkeypatch):
    db = OpsDB(tmp_path / "ops.db")
    graph = FakeGraph({})
    set_context(PipelineContext(settings=None, db=db, router=None, graph=graph))

    monkeypatch.setattr(
        worker_tasks, "cluster_new_claims",
        lambda c, p: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    calls = []
    monkeypatch.setattr(
        worker_tasks.warm_guides_task, "delay", lambda project_id: calls.append(project_id)
    )

    with pytest.raises(RuntimeError):
        worker_tasks.synthesize_task.delay("default").get()

    assert calls == []


def test_process_dirty_survives_bad_conflict_and_name(tmp_path):
    # If both the conflict-scan and concept-name model calls come back
    # malformed/truncated (frequent with deepseek-v4-flash on large
    # corpora), process_dirty_concepts must still complete: no conflicts get
    # recorded, and the concept gets a deterministic fallback name derived
    # from its claims -- never the raw concept_id.
    from mslearn.providers.base import ProviderBadOutputError

    db = OpsDB(tmp_path / "ops.db")
    graph = InMemoryGraphStore()
    graph.upsert_concept(ConceptRecord(concept_id="k1", name=""))
    graph.add_claim("c1", "Caching improves latency significantly here", "neutral", "s1", [1.0, 0.0])
    graph.add_claim("c2", "Caching can add complexity", "neutral", "s1", [0.9, 0.0])
    graph.assign_claim("c1", "k1")
    graph.assign_claim("c2", "k1")
    graph.mark_concept_dirty("k1", True)

    router = ScriptedRouter(
        [
            ProviderBadOutputError("truncated conflict scan"),
            ProviderBadOutputError("truncated name"),
        ]
    )
    ctx = PipelineContext(settings=None, db=db, router=router, graph=graph)

    processed = process_dirty_concepts(ctx)

    assert processed == 1
    concept = graph.get_concept("k1")
    assert concept["name"]
    assert concept["name"] != "k1"
    assert graph.conflicts_in_concept("k1") == []


def test_build_curriculum_survives_bad_deps(tmp_path):
    # A truncated concept_deps response must not crash build_curriculum --
    # it should fall back to the natural (first_seq) spine order.
    from mslearn.providers.base import ProviderBadOutputError

    db = OpsDB(tmp_path / "ops.db")
    graph = InMemoryGraphStore(concept_first_seq={"k1": 0, "k2": 1})
    graph.upsert_concept(ConceptRecord(concept_id="k1", name="A"))
    graph.upsert_concept(ConceptRecord(concept_id="k2", name="B"))
    router = ScriptedRouter([ProviderBadOutputError("truncated deps")])
    ctx = PipelineContext(settings=None, db=db, router=router, graph=graph)

    ordered = build_curriculum(ctx)

    assert ordered == ["k1", "k2"]


def test_process_dirty_propagates_transient_error(tmp_path):
    # ProviderTransientError (retryable 429/5xx) must NOT be swallowed like
    # ProviderBadOutputError -- it must propagate out of
    # process_dirty_concepts so synthesize_task's autoretry_for triggers a
    # Celery retry of the whole synthesis run instead of silently degrading
    # to a fallback name / dropped conflicts.
    from mslearn.providers.base import ProviderTransientError

    db = OpsDB(tmp_path / "ops.db")
    graph = InMemoryGraphStore()
    graph.upsert_concept(ConceptRecord(concept_id="k1", name=""))
    graph.add_claim("c1", "Caching improves latency significantly here", "neutral", "s1", [1.0, 0.0])
    graph.add_claim("c2", "Caching can add complexity", "neutral", "s1", [0.9, 0.0])
    graph.assign_claim("c1", "k1")
    graph.assign_claim("c2", "k1")
    graph.mark_concept_dirty("k1", True)

    router = ScriptedRouter(
        [
            ProviderTransientError("rate limited"),
            ProviderTransientError("rate limited"),
        ]
    )
    ctx = PipelineContext(settings=None, db=db, router=router, graph=graph)

    with pytest.raises(ProviderTransientError):
        process_dirty_concepts(ctx)


def test_build_curriculum_propagates_transient_error(tmp_path):
    # ProviderTransientError on the concept_deps call must propagate out of
    # build_curriculum (for Celery autoretry), not be treated as a
    # malformed-output degradation that silently falls back to natural
    # spine order.
    from mslearn.providers.base import ProviderTransientError

    db = OpsDB(tmp_path / "ops.db")
    graph = InMemoryGraphStore(concept_first_seq={"k1": 0, "k2": 1})
    graph.upsert_concept(ConceptRecord(concept_id="k1", name="A"))
    graph.upsert_concept(ConceptRecord(concept_id="k2", name="B"))
    router = ScriptedRouter([ProviderTransientError("rate limited")])
    ctx = PipelineContext(settings=None, db=db, router=router, graph=graph)

    with pytest.raises(ProviderTransientError):
        build_curriculum(ctx)


def test_build_curriculum_skips_deps_for_large_spine(tmp_path):
    # A spine of 61+ concepts must skip the one-shot concept_deps DAG call
    # entirely -- it reliably overflows max_tokens on large corpora -- and
    # fall back straight to natural spine order.
    db = OpsDB(tmp_path / "ops.db")
    concept_first_seq = {f"k{i:03d}": i for i in range(61)}
    graph = InMemoryGraphStore(concept_first_seq=concept_first_seq)
    for i in range(61):
        cid = f"k{i:03d}"
        graph.upsert_concept(ConceptRecord(concept_id=cid, name=f"C{i}"))
    router = ScriptedRouter([])  # any complete() call -> IndexError -> test fails loudly
    ctx = PipelineContext(settings=None, db=db, router=router, graph=graph)

    ordered = build_curriculum(ctx)

    assert router.calls == []
    assert len(ordered) == 61


def test_process_dirty_concepts_writes_progress_per_concept(tmp_path):
    # The "analyzing" phase runs two model calls per concept and dominated
    # the 78-minute incident run — the UI needs "n of m", not a bare spinner.
    db = OpsDB(tmp_path / "ops.db")
    graph = InMemoryGraphStore()
    graph.upsert_concept(ConceptRecord(concept_id="k1", name="A"))
    graph.upsert_concept(ConceptRecord(concept_id="k2", name="B"))
    graph.add_claim("c1", "claim one", "neutral", "s1", [1.0, 0.0])
    graph.add_claim("c2", "claim two", "neutral", "s2", [0.9, 0.0])
    graph.assign_claim("c1", "k1")
    graph.assign_claim("c2", "k2")
    graph.mark_concept_dirty("k1", True)
    graph.mark_concept_dirty("k2", True)

    router = CapturingRouter(
        outputs=[
            {"name": "Concept A", "summary": "..."},
            {"name": "Concept B", "summary": "..."},
        ]
    )
    ctx = PipelineContext(settings=None, db=db, router=router, graph=graph)

    process_dirty_concepts(ctx)

    raw = db.get_project_setting("default", "synthesis:progress")
    assert raw is not None
    progress = json.loads(raw)
    # Two dirty concepts (single claim each, no conflict-scan call) processed
    # in concept_id order — the final write left on disk is the last one.
    assert progress["phase"] == "analyzing"
    assert progress["done"] == progress["total"] == 2
