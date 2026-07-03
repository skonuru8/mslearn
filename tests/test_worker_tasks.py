import pytest

from mslearn.adapters.base import make_source_id
from mslearn.opsdb import OpsDB
from mslearn.pipeline.contracts import derive_claim_id
from mslearn.providers.base import ProviderTransientError
from mslearn.worker import tasks as worker_tasks
from mslearn.worker.app import app
from mslearn.worker.context import PipelineContext, set_context
from tests.fakes import InMemoryGraphStore
from tests.test_extraction_graph import BAD, GOOD, CHUNK, ScriptedRouter


class FakeGraph:
    def __init__(self, chunks):
        self.chunks = chunks
        self.claims = {}

    def get_chunk(self, chunk_id, *, project_id="default"):
        return self.chunks.get(chunk_id)

    def upsert_claim(self, claim, embedding, *, project_id="default"):
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
    worker_tasks.extract_chunk_task.delay("default", "s1:0").get()
    cid = derive_claim_id("s1:0", "Cache invalidation is hard.")
    claim, embedding = context.graph.claims[cid]
    assert claim.trust == "trusted" and claim.source_id == "s1"
    assert context.db.source_row("s1")["done_chunks"] == 1


def test_escalated_claims_marked(ctx):
    context = ctx(ScriptedRouter([BAD, BAD, GOOD]))
    worker_tasks.extract_chunk_task.delay("default", "s1:0").get()
    (claim, _), = context.graph.claims.values()
    assert claim.trust == "escalated"


def test_paused_source_skips(ctx):
    context = ctx(ScriptedRouter([GOOD]))
    context.db.set_source_status("s1", "paused")
    worker_tasks.extract_chunk_task.delay("default", "s1:0").get()
    assert context.graph.claims == {}
    assert context.db.pending_chunks("s1") == []  # marked skipped_paused, not pending


def test_missing_chunk_marks_failed(ctx):
    context = ctx(ScriptedRouter([GOOD]))
    context.db.register_chunk_jobs("s1", ["s1:9"])
    worker_tasks.extract_chunk_task.delay("default", "s1:9").get()
    assert context.db.failure_stats("s1")["failed"] == 1


def test_failure_monitor_pauses_source(ctx, tmp_path):
    db = OpsDB(tmp_path / "ops2.db")
    graph = FakeGraph({})
    db.register_source("s2", ref="r", role="spine", total_chunks=12)
    chunk_ids = [f"s2:{i}" for i in range(12)]
    db.register_chunk_jobs("s2", chunk_ids)
    set_context(PipelineContext(settings=None, db=db, router=ScriptedRouter([]), graph=graph))
    for cid in chunk_ids[:10]:  # min_chunks=10, threshold=0.5 — source pauses once failed/total > 0.5 with total>=10 (trips at the 7th failure; remaining iterations skip as paused)
        worker_tasks.extract_chunk_task.delay("default", cid).get()
    assert db.source_row("s2")["status"] == "paused"


def test_failure_monitor_trips_on_combined_failed_and_rejected(tmp_path):
    # 5 infra failures + 5 gate rejections = 10/12 problems > 0.5 threshold,
    # even though neither counter alone crosses it.
    db = OpsDB(tmp_path / "ops3.db")
    graph = FakeGraph({f"s3:{i}": {"chunk_id": f"s3:{i}", "source_id": "s3", "text": CHUNK}
                        for i in range(5, 10)})
    db.register_source("s3", ref="r", role="spine", total_chunks=12)
    chunk_ids = [f"s3:{i}" for i in range(12)]
    db.register_chunk_jobs("s3", chunk_ids)
    db.set_source_status("s3", "running")
    router = ScriptedRouter([BAD, BAD, BAD, BAD] * 5)  # plenty of gate-rejection responses
    set_context(PipelineContext(settings=None, db=db, router=router, graph=graph))
    for cid in chunk_ids[:5]:  # missing from graph -> genuine failed
        worker_tasks.extract_chunk_task.delay("default", cid).get()
    assert db.source_row("s3")["status"] == "running"  # 5/12 problems, not over threshold yet
    for cid in chunk_ids[5:10]:  # present in graph, but gate rejects everything -> rejected
        worker_tasks.extract_chunk_task.delay("default", cid).get()
    # Once paused, remaining chunks short-circuit to `skipped_paused` rather
    # than being processed further — that's existing (correct) behaviour;
    # what matters here is that failed+rejected together tripped the pause.
    assert db.source_row("s3")["status"] == "paused"
    stats = db.failure_stats("s3")
    assert stats["failed"] == 5
    assert stats["rejected"] >= 1
    assert stats["problems"] == stats["failed"] + stats["rejected"]


def test_persistent_gate_rejection_marks_rejected_not_failed(ctx):
    # The model returns well-formed claims, but the trust gate rejects every one
    # (bad quote match) on every attempt/escalation — the pipeline behaved
    # correctly, so this must count as `rejected`, not `failed`.
    context = ctx(ScriptedRouter([BAD, BAD, BAD, BAD]))
    context.db.set_source_status("s1", "running")
    worker_tasks.extract_chunk_task.delay("default", "s1:0").get()
    stats = context.db.failure_stats("s1")
    assert stats["failed"] == 0
    assert stats["rejected"] == 1
    assert context.graph.claims == {}
    assert context.db.source_row("s1")["status"] == "done"  # complete, not falsely "failed"


def test_transient_exhaustion_marks_failed(ctx):
    from mslearn.providers.base import ProviderTransientError

    context = ctx(ScriptedRouter([ProviderTransientError("net down")] * 10))
    try:
        worker_tasks.extract_chunk_task.delay("default", "s1:0").get()
    except ProviderTransientError:
        pass
    assert context.db.failure_stats("s1")["failed"] == 1


def test_unexpected_extraction_error_marks_chunk_failed(ctx):
    context = ctx(ScriptedRouter([TypeError("the JSON object must be str")]))
    worker_tasks.extract_chunk_task.delay("default", "s1:0").get()
    assert context.db.failure_stats("s1")["failed"] == 1
    assert context.db.source_row("s1")["failed_chunks"] == 1


def test_chunk_failure_logs_one_warning_line(ctx, caplog):
    import logging

    context = ctx(ScriptedRouter([TypeError("boom")]))
    with caplog.at_level(logging.INFO, logger="mslearn"):
        worker_tasks.extract_chunk_task.delay("default", "s1:0").get()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("s1:0" in r.getMessage() and "failed" in r.getMessage() for r in warnings)
    assert context.db.failure_stats("s1")["failed"] == 1


class EmbedFlaky:
    """Router whose embed() fails transiently `fail_times` calls before succeeding."""

    def __init__(self, fail_times: int):
        self.fail_times = fail_times
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ProviderTransientError("embedding blip")
        return [[1.0, 0.0] for _ in texts]

    def complete(self, role, request):
        raise AssertionError("chunk_source_task must not call complete()")


def test_chunk_source_task_success_flips_to_running_and_enqueues(tmp_path, tiny_pdf, monkeypatch):
    db = OpsDB(tmp_path / "ops.db")
    graph = InMemoryGraphStore()
    source_id = make_source_id(str(tiny_pdf))
    db.register_source(source_id, ref=str(tiny_pdf), role="spine", total_chunks=0)
    db.set_source_status(source_id, "chunking")
    set_context(PipelineContext(settings=None, db=db, router=ScriptedRouter([]), graph=graph))

    scheduled = []
    monkeypatch.setattr(
        worker_tasks, "extract_chunk_task", type("F", (), {"delay": lambda self, *a: scheduled.append(a)})()
    )

    worker_tasks.chunk_source_task.delay("default", source_id, str(tiny_pdf), "spine", "pdf").get()

    row = db.source_row(source_id)
    assert row["status"] == "running"
    assert row["total_chunks"] == len(scheduled) > 0
    assert db.pending_chunks(source_id) == sorted(c for _pid, c in scheduled)


def test_chunk_source_task_adapter_failure_marks_failed(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    graph = InMemoryGraphStore()
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf")
    source_id = make_source_id(str(bad))
    db.register_source(source_id, ref=str(bad), role="spine", total_chunks=0)
    db.set_source_status(source_id, "chunking")
    set_context(PipelineContext(settings=None, db=db, router=ScriptedRouter([]), graph=graph))

    worker_tasks.chunk_source_task.delay("default", source_id, str(bad), "spine", "pdf").get()

    row = db.source_row(source_id)
    assert row["status"] == "failed"
    assert row["error"]


def test_chunk_source_task_guard_noop_when_not_chunking(tmp_path, tiny_pdf):
    db = OpsDB(tmp_path / "ops.db")
    graph = InMemoryGraphStore()
    source_id = make_source_id(str(tiny_pdf))
    db.register_source(source_id, ref=str(tiny_pdf), role="spine", total_chunks=3)
    db.set_source_status(source_id, "running")  # already past chunking
    set_context(PipelineContext(settings=None, db=db, router=ScriptedRouter([]), graph=graph))

    worker_tasks.chunk_source_task.delay("default", source_id, str(tiny_pdf), "spine", "pdf").get()

    row = db.source_row(source_id)
    assert row["status"] == "running"  # untouched — redelivered/duplicate task is a no-op
    assert graph.sources == {}


def test_chunk_source_task_guard_noop_when_source_deleted(tmp_path, tiny_pdf):
    db = OpsDB(tmp_path / "ops.db")
    graph = InMemoryGraphStore()
    set_context(PipelineContext(settings=None, db=db, router=ScriptedRouter([]), graph=graph))

    worker_tasks.chunk_source_task.delay("default", "gone", str(tiny_pdf), "spine", "pdf").get()

    assert db.source_row("gone") is None
    assert graph.sources == {}


def test_chunk_source_task_transient_embed_retries_then_succeeds(tmp_path, tiny_pdf):
    db = OpsDB(tmp_path / "ops.db")
    graph = InMemoryGraphStore()
    source_id = make_source_id(str(tiny_pdf))
    db.register_source(source_id, ref=str(tiny_pdf), role="spine", total_chunks=0)
    db.set_source_status(source_id, "chunking")
    router = EmbedFlaky(fail_times=2)
    set_context(PipelineContext(settings=None, db=db, router=router, graph=graph))

    worker_tasks.chunk_source_task.delay(
        "default", source_id, str(tiny_pdf), "spine", "pdf", False
    ).get()

    row = db.source_row(source_id)
    assert row["status"] == "running"
    assert row["total_chunks"] > 0
    assert router.calls == 3


def test_chunk_source_task_transient_embed_exhausted_marks_failed(tmp_path, tiny_pdf):
    db = OpsDB(tmp_path / "ops.db")
    graph = InMemoryGraphStore()
    source_id = make_source_id(str(tiny_pdf))
    db.register_source(source_id, ref=str(tiny_pdf), role="spine", total_chunks=0)
    db.set_source_status(source_id, "chunking")
    router = EmbedFlaky(fail_times=99)
    set_context(PipelineContext(settings=None, db=db, router=router, graph=graph))

    try:
        worker_tasks.chunk_source_task.delay(
            "default", source_id, str(tiny_pdf), "spine", "pdf", False
        ).get()
    except ProviderTransientError:
        pass
    row = db.source_row(source_id)
    assert row["status"] == "failed"
    assert graph.sources == {}  # never reached the graph upsert step


def test_synthesis_failure_records_last_error(ctx, monkeypatch):
    context = ctx(ScriptedRouter([]))

    def boom(_ctx, _project_id):
        raise RuntimeError("synthesis exploded")

    monkeypatch.setattr(worker_tasks, "cluster_new_claims", boom)
    with pytest.raises(RuntimeError):
        worker_tasks.synthesize_task.delay("default").get()
    raw = context.db.get_project_setting("default", "synthesis:last_error")
    assert raw and "synthesis exploded" in raw


def test_synthesis_success_clears_last_error(ctx, monkeypatch):
    context = ctx(ScriptedRouter([]))
    context.db.set_project_setting("default", "synthesis:last_error", '{"error": "old"}')
    monkeypatch.setattr(worker_tasks, "cluster_new_claims", lambda c, p: [])
    monkeypatch.setattr(worker_tasks, "process_dirty_concepts", lambda c, p: 0)
    monkeypatch.setattr(worker_tasks, "build_curriculum", lambda c, p: [])
    worker_tasks.synthesize_task.delay("default").get()
    assert not context.db.get_project_setting("default", "synthesis:last_error")
