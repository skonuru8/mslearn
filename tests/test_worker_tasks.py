import pytest

from mslearn.opsdb import OpsDB
from mslearn.pipeline.contracts import derive_claim_id
from mslearn.worker import tasks as worker_tasks
from mslearn.worker.app import app
from mslearn.worker.context import PipelineContext, set_context
from tests.test_extraction_graph import BAD, GOOD, CHUNK, ScriptedRouter


class FakeGraph:
    def __init__(self, chunks):
        self.chunks = chunks
        self.claims = {}

    def get_chunk(self, chunk_id):
        return self.chunks.get(chunk_id)

    def upsert_claim(self, claim, embedding):
        self.claims[claim.claim_id] = (claim, embedding)


@pytest.fixture(autouse=True)
def eager_app():
    app.conf.task_always_eager = True
    yield
    app.conf.task_always_eager = False


@pytest.fixture()
def ctx(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    graph = FakeGraph({"s1:0": {"chunk_id": "s1:0", "source_id": "s1", "text": CHUNK}})
    db.register_source("s1", ref="r", role="spine", total_chunks=1)
    db.register_chunk_jobs("s1", ["s1:0"])

    def make(router):
        context = PipelineContext(settings=None, db=db, router=router, graph=graph)
        set_context(context)
        return context

    return make


def test_successful_chunk_commits_claims(ctx):
    context = ctx(ScriptedRouter([GOOD]))
    worker_tasks.extract_chunk_task.delay("s1:0").get()
    cid = derive_claim_id("s1:0", "Cache invalidation is hard.")
    claim, embedding = context.graph.claims[cid]
    assert claim.trust == "trusted" and claim.source_id == "s1"
    assert context.db.source_row("s1")["done_chunks"] == 1


def test_escalated_claims_marked(ctx):
    context = ctx(ScriptedRouter([BAD, BAD, GOOD]))
    worker_tasks.extract_chunk_task.delay("s1:0").get()
    (claim, _), = context.graph.claims.values()
    assert claim.trust == "escalated"


def test_paused_source_skips(ctx):
    context = ctx(ScriptedRouter([GOOD]))
    context.db.set_source_status("s1", "paused")
    worker_tasks.extract_chunk_task.delay("s1:0").get()
    assert context.graph.claims == {}
    assert context.db.pending_chunks("s1") == []  # marked skipped_paused, not pending


def test_missing_chunk_marks_failed(ctx):
    context = ctx(ScriptedRouter([GOOD]))
    context.db.register_chunk_jobs("s1", ["s1:9"])
    worker_tasks.extract_chunk_task.delay("s1:9").get()
    assert context.db.failure_stats("s1")["failed"] == 1


def test_failure_monitor_pauses_source(ctx, tmp_path):
    db = OpsDB(tmp_path / "ops2.db")
    graph = FakeGraph({})
    db.register_source("s2", ref="r", role="spine", total_chunks=12)
    chunk_ids = [f"s2:{i}" for i in range(12)]
    db.register_chunk_jobs("s2", chunk_ids)
    set_context(PipelineContext(settings=None, db=db, router=ScriptedRouter([]), graph=graph))
    for cid in chunk_ids[:10]:  # min_chunks=10, threshold=0.5 — source pauses once failed/total > 0.5 with total>=10 (trips at the 7th failure; remaining iterations skip as paused)
        worker_tasks.extract_chunk_task.delay(cid).get()
    assert db.source_row("s2")["status"] == "paused"


def test_persistent_parse_failure_marks_failed(ctx):
    context = ctx(ScriptedRouter([BAD, BAD, BAD, BAD]))
    worker_tasks.extract_chunk_task.delay("s1:0").get()
    assert context.db.failure_stats("s1")["failed"] == 1
    assert context.graph.claims == {}


def test_transient_exhaustion_marks_failed(ctx):
    from mslearn.providers.base import ProviderTransientError

    context = ctx(ScriptedRouter([ProviderTransientError("net down")] * 10))
    try:
        worker_tasks.extract_chunk_task.delay("s1:0").get()
    except ProviderTransientError:
        pass
    assert context.db.failure_stats("s1")["failed"] == 1
