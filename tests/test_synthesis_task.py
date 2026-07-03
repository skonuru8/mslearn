import json

import pytest

from mslearn.chunking import chunk_source
from mslearn.graph.records import ConceptRecord
from mslearn.opsdb import OpsDB
from mslearn.pipeline.synthesis import build_curriculum, cluster_new_claims, process_dirty_concepts
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
    set_context(PipelineContext(settings=None, db=db, router=router, graph=graph))

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
    set_context(PipelineContext(settings=None, db=db, router=router, graph=graph))

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
        ]
    )
    ctx = PipelineContext(settings=None, db=db, router=router, graph=clean_graph)
    set_context(ctx)

    dirty = cluster_new_claims(ctx)
    processed = process_dirty_concepts(ctx)
    ordered = build_curriculum(ctx)
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

    assert router.calls == ["synthesis", "synthesis", "synthesis", "synthesis", "synthesis"]
    assert clean_graph.conflicts_in_concept("k-cl1")[0]["classification"] == "genuine_debate"
    cur = clean_graph.curriculum()
    assert [row["concept_id"] for row in cur][:2] == ["k-cl1", "k-cl3"]
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
