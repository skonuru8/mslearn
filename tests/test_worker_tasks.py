import pytest
from celery.exceptions import SoftTimeLimitExceeded

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
    def __init__(self, chunks, source_types=None):
        self.chunks = chunks
        self.claims = {}
        self.source_types = source_types or {}

    def get_chunk(self, chunk_id, *, project_id="default"):
        return self.chunks.get(chunk_id)

    def upsert_claim(self, claim, embedding, *, project_id="default"):
        self.claims[claim.claim_id] = (claim, embedding)

    def source_type_of(self, source_id, *, project_id="default"):
        return self.source_types.get(source_id)


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


def test_image_source_claims_get_image_observed_tier(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    graph = FakeGraph(
        {"s1:0": {"chunk_id": "s1:0", "source_id": "s1", "text": CHUNK}},
        source_types={"s1": "image"},
    )
    db.register_source("s1", ref="shot.png", role="spine", total_chunks=1)
    db.register_chunk_jobs("s1", ["s1:0"])
    set_context(PipelineContext(settings=None, db=db, router=ScriptedRouter([GOOD]), graph=graph))
    worker_tasks.extract_chunk_task.delay("default", "s1:0").get()
    (claim, _), = graph.claims.values()
    assert claim.trust == "image_observed"


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


def test_deleted_source_skips_extraction_with_one_info_line(ctx, caplog):
    import logging

    class NoCallRouter:
        def complete(self, role, request):
            raise AssertionError("no model calls for a deleted source")

        def embed(self, texts):
            raise AssertionError("no embed calls for a deleted source")

    context = ctx(NoCallRouter())
    context.db.delete_source("s1")
    with caplog.at_level(logging.INFO, logger="mslearn"):
        worker_tasks.extract_chunk_task.delay("default", "s1:0").get()
    assert context.graph.claims == {}
    skip_lines = [
        r.getMessage()
        for r in caplog.records
        if r.levelno == logging.INFO and "source deleted" in r.getMessage()
    ]
    assert skip_lines == ["chunk s1:0 skipped: source deleted"]


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


class EmbedWithSideEffect:
    """Router whose embed() runs a hook first — simulates a delete/re-add
    landing while the slow load/chunk/embed phase of chunk_source_task runs."""

    def __init__(self, hook):
        self.hook = hook

    def embed(self, texts):
        self.hook()
        return [[1.0, 0.0] for _ in texts]

    def complete(self, role, request):
        raise AssertionError("chunk_source_task must not call complete()")


def test_chunk_source_task_aborts_when_source_deleted_mid_task(tmp_path, tiny_pdf, caplog):
    import logging

    db = OpsDB(tmp_path / "ops.db")
    graph = InMemoryGraphStore()
    source_id = make_source_id(str(tiny_pdf))
    db.register_source(source_id, ref=str(tiny_pdf), role="spine", total_chunks=0)
    db.set_source_status(source_id, "chunking")
    router = EmbedWithSideEffect(lambda: db.delete_source(source_id))
    set_context(PipelineContext(settings=None, db=db, router=router, graph=graph))

    with caplog.at_level(logging.INFO, logger="mslearn"):
        worker_tasks.chunk_source_task.delay(
            "default", source_id, str(tiny_pdf), "spine", "pdf", False
        ).get()

    assert db.source_row(source_id) is None  # not resurrected
    assert graph.sources == {} and graph.chunks == {}  # no graph writes
    assert db.pending_chunks(source_id) == []  # no chunk jobs registered
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("deleted mid-task" in m and source_id in m for m in infos)


def test_chunk_source_task_aborts_when_source_readded_mid_task(tmp_path, tiny_pdf, caplog):
    import logging

    db = OpsDB(tmp_path / "ops.db")
    graph = InMemoryGraphStore()
    source_id = make_source_id(str(tiny_pdf))
    db.register_source(source_id, ref=str(tiny_pdf), role="spine", total_chunks=0)
    db.set_source_status(source_id, "chunking")

    def delete_and_readd():
        # Fresh incarnation: same PK, new ts, back in "chunking" — exactly
        # what a delete followed by re-adding the same file produces.
        db.delete_source(source_id)
        db.register_source(source_id, ref=str(tiny_pdf), role="spine", total_chunks=0)
        db.set_source_status(source_id, "chunking")

    router = EmbedWithSideEffect(delete_and_readd)
    set_context(PipelineContext(settings=None, db=db, router=router, graph=graph))

    with caplog.at_level(logging.INFO, logger="mslearn"):
        worker_tasks.chunk_source_task.delay(
            "default", source_id, str(tiny_pdf), "spine", "pdf", False
        ).get()

    row = db.source_row(source_id)
    assert row["status"] == "chunking"  # fresh row untouched, still owned by its own task
    assert row["total_chunks"] == 0
    assert graph.sources == {} and graph.chunks == {}
    assert db.pending_chunks(source_id) == []
    infos = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("re-added mid-task" in m and source_id in m for m in infos)


def test_synthesis_failure_records_last_error(ctx, monkeypatch):
    context = ctx(ScriptedRouter([]))

    def boom(_ctx, _project_id):
        raise RuntimeError("synthesis exploded")

    monkeypatch.setattr(worker_tasks, "cluster_new_claims", boom)
    with pytest.raises(RuntimeError):
        worker_tasks.synthesize_task.delay("default").get()
    raw = context.db.get_project_setting("default", "synthesis:last_error")
    assert raw and "synthesis exploded" in raw


def test_synthesize_task_retries_transient_provider_errors():
    """A single read timeout killed whole synthesis runs — the task must
    autoretry ProviderTransientError like extract_chunk_task does."""
    from mslearn.providers.base import ProviderTransientError as PTE

    assert PTE in worker_tasks.synthesize_task.autoretry_for


def test_hung_tasks_have_soft_and_hard_time_limits():
    # A wedged task (the incident: a request survived the 600s httpx read
    # timeout entirely) must die and release its worker slot instead of
    # occupying it for hours. Hard limit always leaves headroom past soft so
    # the except-clause gets a chance to write error state first.
    for task, soft, hard in [
        (worker_tasks.chunk_source_task, 1800, 2100),
        (worker_tasks.extract_chunk_task, 1800, 2100),
        (worker_tasks.synthesize_task, 3600, 3900),
    ]:
        assert task.soft_time_limit == soft
        assert task.time_limit == hard
        assert task.time_limit > task.soft_time_limit


def test_extract_chunk_soft_time_limit_marks_chunk_failed(ctx, monkeypatch):
    context = ctx(ScriptedRouter([]))

    def boom(router, db, chunk_id, text):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(worker_tasks, "run_extraction", boom)
    with pytest.raises(SoftTimeLimitExceeded):
        worker_tasks.extract_chunk_task.delay("default", "s1:0").get()
    assert context.db.failure_stats("s1")["failed"] == 1


def test_chunk_source_soft_time_limit_marks_source_failed(tmp_path, tiny_pdf):
    class Wedged:
        def embed(self, texts):
            raise SoftTimeLimitExceeded()

        def complete(self, role, request):
            raise AssertionError("chunk_source_task must not call complete()")

    db = OpsDB(tmp_path / "ops.db")
    graph = InMemoryGraphStore()
    source_id = make_source_id(str(tiny_pdf))
    db.register_source(source_id, ref=str(tiny_pdf), role="spine", total_chunks=0)
    db.set_source_status(source_id, "chunking")
    set_context(PipelineContext(settings=None, db=db, router=Wedged(), graph=graph))

    with pytest.raises(SoftTimeLimitExceeded):
        worker_tasks.chunk_source_task.delay(
            "default", source_id, str(tiny_pdf), "spine", "pdf", False
        ).get()

    row = db.source_row(source_id)
    assert row["status"] == "failed"
    assert "time limit" in row["error"]


def test_synthesize_soft_time_limit_records_error_and_clears_running_since(ctx, monkeypatch):
    context = ctx(ScriptedRouter([]))

    def boom(c, p):
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(worker_tasks, "cluster_new_claims", boom)
    with pytest.raises(SoftTimeLimitExceeded):
        worker_tasks.synthesize_task.delay("default").get()
    raw = context.db.get_project_setting("default", "synthesis:last_error")
    assert raw and "time limit" in raw
    assert not context.db.get_project_setting("default", "synthesis:running_since")


def test_synthesis_sets_and_clears_running_since(ctx, monkeypatch):
    context = ctx(ScriptedRouter([]))
    seen = {}

    def snapshot(c, p):
        seen["during"] = context.db.get_project_setting("default", "synthesis:running_since")
        return []

    monkeypatch.setattr(worker_tasks, "cluster_new_claims", snapshot)
    monkeypatch.setattr(worker_tasks, "process_dirty_concepts", lambda c, p: 0)
    monkeypatch.setattr(worker_tasks, "build_curriculum", lambda c, p: [])
    worker_tasks.synthesize_task.delay("default").get()
    assert seen["during"]  # set while running
    assert not context.db.get_project_setting("default", "synthesis:running_since")


def test_synthesis_sets_and_clears_progress(ctx, monkeypatch):
    import json

    context = ctx(ScriptedRouter([]))
    seen = {}

    def snapshot_grouping(c, p):
        raw = context.db.get_project_setting("default", "synthesis:progress")
        seen["grouping"] = json.loads(raw) if raw else None
        return []

    def snapshot_ordering(c, p):
        raw = context.db.get_project_setting("default", "synthesis:progress")
        seen["ordering"] = json.loads(raw) if raw else None
        return []

    monkeypatch.setattr(worker_tasks, "cluster_new_claims", snapshot_grouping)
    monkeypatch.setattr(worker_tasks, "process_dirty_concepts", lambda c, p: 0)
    monkeypatch.setattr(worker_tasks, "build_curriculum", snapshot_ordering)
    worker_tasks.synthesize_task.delay("default").get()
    assert seen["grouping"]["phase"] == "grouping"
    assert seen["ordering"]["phase"] == "ordering"
    # cleared once the run finishes, like running_since
    assert not context.db.get_project_setting("default", "synthesis:progress")


def test_synthesis_failure_clears_progress(ctx, monkeypatch):
    context = ctx(ScriptedRouter([]))
    monkeypatch.setattr(
        worker_tasks, "cluster_new_claims",
        lambda c, p: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError):
        worker_tasks.synthesize_task.delay("default").get()
    assert not context.db.get_project_setting("default", "synthesis:progress")


def test_synthesis_heartbeat_refreshes_between_phases(ctx, monkeypatch):
    # The 107-chunk-video incident ran 78+ minutes on one project; without a
    # heartbeat, the status endpoint's abandoned-build self-heal (2x the
    # running TTL) would eventually treat a still-alive run as wedged.
    context = ctx(ScriptedRouter([]))
    ticks = iter([1000, 2000, 3000, 4000])
    monkeypatch.setattr(worker_tasks.time, "time", lambda: next(ticks))
    seen = []

    def cluster(c, p):
        seen.append(context.db.get_project_setting("default", "synthesis:running_since"))
        return []

    def process(c, p):
        seen.append(context.db.get_project_setting("default", "synthesis:running_since"))
        return 0

    def curriculum(c, p):
        seen.append(context.db.get_project_setting("default", "synthesis:running_since"))
        return []

    monkeypatch.setattr(worker_tasks, "cluster_new_claims", cluster)
    monkeypatch.setattr(worker_tasks, "process_dirty_concepts", process)
    monkeypatch.setattr(worker_tasks, "build_curriculum", curriculum)
    worker_tasks.synthesize_task.delay("default").get()
    assert seen == ["1000", "2000", "3000"]  # strictly increasing heartbeat, one per phase boundary


def test_synthesis_failure_clears_running_since(ctx, monkeypatch):
    context = ctx(ScriptedRouter([]))
    monkeypatch.setattr(
        worker_tasks, "cluster_new_claims",
        lambda c, p: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError):
        worker_tasks.synthesize_task.delay("default").get()
    assert not context.db.get_project_setting("default", "synthesis:running_since")


def test_synthesis_success_clears_last_error(ctx, monkeypatch):
    context = ctx(ScriptedRouter([]))
    context.db.set_project_setting("default", "synthesis:last_error", '{"error": "old"}')
    monkeypatch.setattr(worker_tasks, "cluster_new_claims", lambda c, p: [])
    monkeypatch.setattr(worker_tasks, "process_dirty_concepts", lambda c, p: 0)
    monkeypatch.setattr(worker_tasks, "build_curriculum", lambda c, p: [])
    worker_tasks.synthesize_task.delay("default").get()
    assert not context.db.get_project_setting("default", "synthesis:last_error")


def test_try_enqueue_synthesis_collapses_rapid_triggers(ctx, monkeypatch):
    context = ctx(ScriptedRouter([]))

    class Trigger:
        def __init__(self):
            self.calls = 0

        def delay(self, project_id="default"):
            self.calls += 1

    trigger = Trigger()
    monkeypatch.setattr(worker_tasks, "synthesize_task", trigger)

    assert worker_tasks.try_enqueue_synthesis(context.db, "default") is True
    assert worker_tasks.try_enqueue_synthesis(context.db, "default") is False
    assert worker_tasks.try_enqueue_synthesis(context.db, "default") is False
    assert trigger.calls == 1


def test_synthesis_queued_marker_expires_by_timestamp(tmp_path):
    from mslearn.opsdb import SYNTHESIS_QUEUED_TTL_S

    db = OpsDB(tmp_path / "ops.db")
    t0 = 1_000_000.0
    assert db.try_mark_synthesis_queued("default", now=t0) is True
    assert db.try_mark_synthesis_queued("default", now=t0 + 60) is False
    # a crashed worker can't wedge synthesis forever — a stale queued marker
    # (older than the TTL) lets the next trigger re-enqueue
    assert db.try_mark_synthesis_queued("default", now=t0 + SYNTHESIS_QUEUED_TTL_S + 1) is True


def test_fresh_running_marker_blocks_enqueue_stale_one_does_not(tmp_path):
    from mslearn.opsdb import SYNTHESIS_RUNNING_TTL_S

    db = OpsDB(tmp_path / "ops.db")
    t0 = 1_000_000.0
    db.set_project_setting("default", "synthesis:running_since", str(int(t0)))
    assert db.try_mark_synthesis_queued("default", now=t0 + 60) is False
    assert db.try_mark_synthesis_queued("default", now=t0 + SYNTHESIS_RUNNING_TTL_S + 1) is True


def test_synthesis_dedup_marker_is_per_project(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    assert db.try_mark_synthesis_queued("default") is True
    assert db.try_mark_synthesis_queued("other-project") is True  # independent marker


def test_synthesize_task_start_clears_queued_marker(ctx, monkeypatch):
    context = ctx(ScriptedRouter([]))
    assert context.db.try_mark_synthesis_queued("default") is True
    monkeypatch.setattr(worker_tasks, "cluster_new_claims", lambda c, p: [])
    monkeypatch.setattr(worker_tasks, "process_dirty_concepts", lambda c, p: 0)
    monkeypatch.setattr(worker_tasks, "build_curriculum", lambda c, p: [])
    worker_tasks.synthesize_task.delay("default").get()
    assert context.db.get_project_setting("default", "synthesis:queued") is None
    # marker cleared + running_since cleared -> a follow-up trigger may queue again
    assert context.db.try_mark_synthesis_queued("default") is True


def test_all_tasks_routed_to_consumed_queues():
    """An unrouted task lands in the default 'celery' queue, which no worker
    consumes (dev_up.sh / make worker consume prepare,extract,judge only) —
    the task silently never runs. Every mslearn task must have an explicit
    route, and chunk_source/extract_chunk/synthesize must land on their own
    dedicated queue (prepare/extract/judge respectively) — see plan
    2026-07-06 Phase 4 (ingest throughput: prepare vs extract split)."""
    routes = app.conf.task_routes
    assert routes["mslearn.worker.tasks.chunk_source_task"]["queue"] == "prepare"
    assert routes["mslearn.worker.tasks.extract_chunk_task"]["queue"] == "extract"
    assert routes["mslearn.worker.tasks.synthesize_task"]["queue"] == "judge"
    consumed = {"prepare", "extract", "judge"}
    mslearn_tasks = [name for name in app.tasks if name.startswith("mslearn.")]
    assert mslearn_tasks, "task autodiscovery broken"
    for name in mslearn_tasks:
        assert name in routes, f"{name} has no task_routes entry"
        assert routes[name].get("queue") in consumed, f"{name} routed to unconsumed queue"
