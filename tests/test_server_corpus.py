import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mslearn.opsdb import OpsDB
from mslearn.prompts import get_domain_profile
from mslearn.server.app import create_app
from mslearn.settings import Settings
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore
from tests.test_extraction_graph import ScriptedRouter


class NoDelayTask:
    def __init__(self):
        self.delayed = []

    def delay(self, project_id, chunk_id):
        self.delayed.append((project_id, chunk_id))


class NoDelaySynthesisTask:
    def __init__(self):
        self.delayed = []

    def delay(self, project_id="default"):
        self.delayed.append(project_id)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # create_source now hands loading off to chunk_source_task (a Celery
    # task); eager mode makes it — and any extract_chunk_task it in turn
    # schedules — run inline, so these tests still see the fully-processed
    # row immediately after the POST, same as the pre-background-ingest
    # behaviour.
    from mslearn.worker.app import app as celery_app

    db = OpsDB(tmp_path / "ops.db")
    settings = Settings(profiles_path=Path("profiles.yaml"))
    ctx = PipelineContext(
        settings=settings,
        db=db,
        router=ScriptedRouter([]),
        graph=InMemoryGraphStore(),
    )
    fake_task = NoDelayTask()
    monkeypatch.setattr("mslearn.worker.tasks.extract_chunk_task", fake_task)
    monkeypatch.setattr("mslearn.pipeline.orchestrator.extract_chunk_task", fake_task)
    celery_app.conf.task_always_eager = True
    app = create_app(context=ctx)
    with TestClient(app) as c:
        yield c, db, fake_task
    celery_app.conf.task_always_eager = False


def test_create_source_returns_immediately_with_chunking_status(tmp_path, monkeypatch, tiny_pdf):
    # Without eager mode: the POST only does the synchronous registration
    # (make_source_id + register_source + status "chunking") and schedules
    # chunk_source_task — it does not wait for loading/chunking/embedding.
    db = OpsDB(tmp_path / "ops.db")
    ctx = PipelineContext(
        settings=Settings(profiles_path=Path("profiles.yaml")),
        db=db,
        router=ScriptedRouter([]),
        graph=InMemoryGraphStore(),
    )
    scheduled = []
    monkeypatch.setattr(
        "mslearn.pipeline.orchestrator.chunk_source_task",
        type("F", (), {"delay": lambda self, *a: scheduled.append(a)})(),
    )
    app = create_app(context=ctx)
    with TestClient(app) as c:
        r = c.post("/api/corpus/sources", json={"ref": str(tiny_pdf), "role": "spine"})
        assert r.status_code == 200
        source_id = r.json()["source_id"]
        row = db.source_row(source_id)
        assert row["status"] == "chunking"
        assert row["total_chunks"] == 0
        assert scheduled == [("default", source_id, str(tiny_pdf), "spine", None, True)]


def test_ingest_happy_path(client, tiny_pdf):
    c, db, fake_task = client
    r = c.post(
        "/api/corpus/sources",
        json={"ref": str(tiny_pdf), "role": "spine"},
    )
    assert r.status_code == 200
    source_id = r.json()["source_id"]
    row = db.source_row(source_id)
    assert row["status"] == "running"
    assert row["role"] == "spine"
    assert row["total_chunks"] == len(fake_task.delayed) > 0

    r = c.get("/api/corpus/sources")
    assert r.status_code == 200
    sources = r.json()
    assert len(sources) == 1
    assert sources[0]["source_id"] == source_id
    assert "done_chunks" in sources[0]
    assert "failed_chunks" in sources[0]


def test_ingest_failure_marks_failed_not_422(client, tmp_path):
    # Adapter failures now happen in the background chunk_source_task, not
    # synchronously in the request handler — the POST still succeeds (a
    # source row is created) and the failure surfaces via source status,
    # not an HTTP error. (The eager fixture runs chunk_source_task inline,
    # so by the time this returns the failure has already happened.)
    c, db, _ = client
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf")
    r = c.post(
        "/api/corpus/sources",
        json={"ref": str(bad), "role": "supplement"},
    )
    assert r.status_code == 200
    source_id = r.json()["source_id"]
    sources = db.all_sources()
    assert len(sources) == 1
    assert sources[0]["source_id"] == source_id
    assert sources[0]["status"] == "failed"
    assert sources[0]["error"]


def test_pause_and_resume(client, tiny_pdf):
    c, db, fake_task = client
    r = c.post(
        "/api/corpus/sources",
        json={"ref": str(tiny_pdf), "role": "supplement"},
    )
    source_id = r.json()["source_id"]
    pending_count = len(db.pending_chunks(source_id))
    fake_task.delayed.clear()

    r = c.post(f"/api/corpus/sources/{source_id}/pause")
    assert r.status_code == 200
    assert db.source_row(source_id)["status"] == "paused"

    r = c.post(f"/api/corpus/sources/{source_id}/resume")
    assert r.status_code == 200
    assert db.source_row(source_id)["status"] == "running"
    assert len(fake_task.delayed) == pending_count


def test_resume_clears_stale_error(client, tiny_pdf):
    c, db, _fake_task = client
    r = c.post("/api/corpus/sources", json={"ref": str(tiny_pdf), "role": "supplement"})
    source_id = r.json()["source_id"]
    db.set_source_status(source_id, "paused", error="failure rate 5/10")
    assert db.source_row(source_id)["error"] == "failure rate 5/10"

    r = c.post(f"/api/corpus/sources/{source_id}/resume")
    assert r.status_code == 200
    assert db.source_row(source_id)["error"] is None


def test_pause_resume_unknown_404(client):
    c, _, _ = client
    r = c.post("/api/corpus/sources/nope/pause")
    assert r.status_code == 404
    r = c.post("/api/corpus/sources/nope/resume")
    assert r.status_code == 404


def test_domain_profile_get_and_set(client):
    c, db, _ = client
    r = c.get("/api/corpus/settings/domain-profile")
    assert r.status_code == 200
    assert r.json()["profile"] == "technical"
    assert get_domain_profile(db) == "technical"

    r = c.post(
        "/api/corpus/settings/domain-profile",
        json={"profile": "interpretive"},
    )
    assert r.status_code == 200
    assert r.json()["profile"] == "interpretive"
    assert get_domain_profile(db) == "interpretive"


def test_domain_profile_invalid_422(client):
    c, _, _ = client
    r = c.post(
        "/api/corpus/settings/domain-profile",
        json={"profile": "nope"},
    )
    assert r.status_code == 422


def test_upload_html_file(client):
    c, db, fake_task = client
    html = Path("tests/fixtures/blog.html").read_bytes()
    r = c.post(
        "/api/corpus/upload",
        files={"file": ("my post.html", html, "text/html")},
        data={"role": "supplement", "local": "false"},
    )
    assert r.status_code == 200
    body = r.json()
    assert db.source_row(body["source_id"])["status"] == "running"
    assert body["stored_path"].endswith(".html")
    assert Path(body["stored_path"]).exists()


def test_failures_grouped_and_retry_resets_and_reenqueues(client, tiny_pdf):
    c, db, fake_task = client
    r = c.post("/api/corpus/sources", json={"ref": str(tiny_pdf), "role": "spine"})
    source_id = r.json()["source_id"]
    chunk_ids = [cid for _pid, cid in fake_task.delayed]
    assert len(chunk_ids) >= 2
    db.mark_chunk(chunk_ids[0], "failed", error="boom: bad json")
    db.mark_chunk(chunk_ids[1], "failed", error="boom: bad json")

    r = c.get(f"/api/corpus/sources/{source_id}/failures")
    assert r.status_code == 200
    groups = r.json()
    assert groups == [{"error": "boom: bad json", "count": 2,
                        "sample_chunk_ids": sorted(chunk_ids[:2])}]

    db.set_source_status(source_id, "paused", error="failure rate 2/2")
    fake_task.delayed.clear()
    r = c.post(f"/api/corpus/sources/{source_id}/retry-failed")
    assert r.status_code == 200
    body = r.json()
    assert body["retried_chunks"] == 2
    row = db.source_row(source_id)
    assert row["status"] == "running"
    assert row["error"] is None
    assert row["failed_chunks"] == 0
    assert sorted(cid for _pid, cid in fake_task.delayed) == sorted(chunk_ids[:2])


def test_failures_and_retry_unknown_source_404(client):
    c, _, _ = client
    assert c.get("/api/corpus/sources/nope/failures").status_code == 404
    assert c.post("/api/corpus/sources/nope/retry-failed").status_code == 404


def test_upload_over_size_cap_rejected_413(client, monkeypatch):
    import mslearn.server.routers.corpus as corpus_module

    monkeypatch.setattr(corpus_module, "_MAX_UPLOAD_BYTES", 10)
    c, db, _task = client
    r = c.post(
        "/api/corpus/upload",
        files={"file": ("toobig.pdf", b"x" * 1000, "application/pdf")},
        data={"role": "supplement"},
    )
    assert r.status_code == 413
    assert "upload limit" in r.json()["detail"]
    assert db.all_sources() == []
    # the partially-written destination file must be cleaned up, not left on disk
    leftovers = list(Path("data/uploads").glob("*toobig.pdf"))
    assert leftovers == []


def test_synthesize_reports_worker_online_status(client, monkeypatch):
    c, db, _task = client
    synth = NoDelaySynthesisTask()
    monkeypatch.setattr("mslearn.worker.tasks.synthesize_task", synth)
    monkeypatch.setattr("mslearn.server.routers.corpus.worker_online", lambda: True)
    r = c.post("/api/corpus/synthesize")
    assert r.status_code == 200
    assert r.json() == {"enqueued": True, "already_running": False, "worker_online": True}
    assert synth.delayed == ["default"]

    db.clear_synthesis_queued("default")
    monkeypatch.setattr("mslearn.server.routers.corpus.worker_online", lambda: False)
    r = c.post("/api/corpus/synthesize")
    assert r.json() == {"enqueued": True, "already_running": False, "worker_online": False}


def test_synthesize_twice_collapses_into_one_queued_run(client, monkeypatch):
    c, _db, _task = client
    synth = NoDelaySynthesisTask()
    monkeypatch.setattr("mslearn.worker.tasks.synthesize_task", synth)
    monkeypatch.setattr("mslearn.server.routers.corpus.worker_online", lambda: True)

    first = c.post("/api/corpus/synthesize").json()
    second = c.post("/api/corpus/synthesize").json()
    third = c.post("/api/corpus/synthesize").json()

    assert first == {"enqueued": True, "already_running": False, "worker_online": True}
    assert second == {"enqueued": False, "already_running": True, "worker_online": True}
    assert third == {"enqueued": False, "already_running": True, "worker_online": True}
    assert synth.delayed == ["default"]  # exactly one run queued


def test_synthesis_status_reflects_last_run_setting(client):
    c, db, _task = client
    r = c.get("/api/corpus/synthesis/status")
    assert r.status_code == 200
    assert r.json() == {
        "last_run": None, "last_error": None, "running_since": None, "progress": None,
    }

    db.set_project_setting(
        "default", "synthesis:last_run",
        json.dumps({"ts": 123, "dirty_concepts": 2, "processed_concepts": 2, "curriculum_len": 5}),
    )
    r = c.get("/api/corpus/synthesis/status")
    assert r.json() == {
        "last_run": {"ts": 123, "dirty_concepts": 2, "processed_concepts": 2, "curriculum_len": 5},
        "last_error": None,
        "running_since": None,
        "progress": None,
    }


def test_synthesis_status_reports_running_since(client):
    c, db, _task = client
    ts = int(time.time()) - 60  # fresh — must not be self-healed away
    db.set_project_setting("default", "synthesis:running_since", str(ts))
    r = c.get("/api/corpus/synthesis/status")
    assert r.json()["running_since"] == ts

    db.set_project_setting("default", "synthesis:running_since", "")
    r = c.get("/api/corpus/synthesis/status")
    assert r.json()["running_since"] is None


def test_synthesis_status_passes_through_fresh_marker(client):
    # A marker inside the TTL window must NOT be touched — this is a live run.
    c, db, _task = client
    fresh = int(time.time()) - 60
    db.set_project_setting("default", "synthesis:running_since", str(fresh))
    r = c.get("/api/corpus/synthesis/status")
    body = r.json()
    assert body["running_since"] == fresh
    assert body["last_error"] is None
    assert db.get_project_setting("default", "synthesis:running_since") == str(fresh)


def test_synthesis_status_self_heals_abandoned_marker(client):
    from mslearn.opsdb import SYNTHESIS_RUNNING_TTL_S

    c, db, _task = client
    stale = int(time.time()) - int(2.5 * SYNTHESIS_RUNNING_TTL_S)
    db.set_project_setting("default", "synthesis:running_since", str(stale))
    r = c.get("/api/corpus/synthesis/status")
    body = r.json()
    assert body["running_since"] is None
    assert body["last_error"]["error"] == "course build was interrupted — press Build to restart"
    # cleared in the DB too, not just the response
    assert not db.get_project_setting("default", "synthesis:running_since")


def test_synthesis_status_self_heal_skips_synthetic_error_when_run_completed_since(client):
    from mslearn.opsdb import SYNTHESIS_RUNNING_TTL_S

    c, db, _task = client
    stale = int(time.time()) - int(2.5 * SYNTHESIS_RUNNING_TTL_S)
    db.set_project_setting("default", "synthesis:running_since", str(stale))
    # last_run's ts is fresher than the abandoned marker — a run actually
    # completed after this one started, so it isn't a wedged build; the
    # marker just failed to clear. Don't invent an interruption error.
    db.set_project_setting(
        "default", "synthesis:last_run",
        json.dumps({"ts": stale + 10, "dirty_concepts": 0, "processed_concepts": 0, "curriculum_len": 0}),
    )
    r = c.get("/api/corpus/synthesis/status")
    body = r.json()
    assert body["running_since"] is None
    assert body["last_error"] is None


def test_synthesis_status_surfaces_last_error(client):
    c, db, _task = client
    db.set_project_setting(
        "default", "synthesis:last_error",
        json.dumps({"ts": 5, "error": "boom"}),
    )
    r = c.get("/api/corpus/synthesis/status")
    assert r.json()["last_error"] == {"ts": 5, "error": "boom"}


def test_synthesis_status_surfaces_progress(client):
    c, db, _task = client
    db.set_project_setting(
        "default", "synthesis:progress",
        json.dumps({"phase": "analyzing", "done": 12, "total": 29, "ts": 5}),
    )
    r = c.get("/api/corpus/synthesis/status")
    assert r.json()["progress"] == {"phase": "analyzing", "done": 12, "total": 29, "ts": 5}

    db.set_project_setting("default", "synthesis:progress", "")
    r = c.get("/api/corpus/synthesis/status")
    assert r.json()["progress"] is None


def test_synthesis_status_self_heal_clears_progress_too(client):
    from mslearn.opsdb import SYNTHESIS_RUNNING_TTL_S

    c, db, _task = client
    stale = int(time.time()) - int(2.5 * SYNTHESIS_RUNNING_TTL_S)
    db.set_project_setting("default", "synthesis:running_since", str(stale))
    db.set_project_setting(
        "default", "synthesis:progress",
        json.dumps({"phase": "analyzing", "done": 3, "total": 10, "ts": stale}),
    )
    r = c.get("/api/corpus/synthesis/status")
    assert r.json()["progress"] is None
    assert not db.get_project_setting("default", "synthesis:progress")

    db.set_project_setting("default", "synthesis:last_error", "")
    r = c.get("/api/corpus/synthesis/status")
    assert r.json()["last_error"] is None


def test_upload_unsupported_suffix_rejected(client):
    c, _db, _task = client
    r = c.post(
        "/api/corpus/upload",
        files={"file": ("notes.docx", b"zzz", "application/octet-stream")},
        data={"role": "supplement"},
    )
    assert r.status_code == 422
    assert "unsupported file type" in r.json()["detail"]


def test_delete_source_removes_rows_and_dirties_concepts(client, tiny_pdf, monkeypatch):
    from mslearn.graph.records import ConceptRecord

    c, db, _task = client
    synth = NoDelaySynthesisTask()
    monkeypatch.setattr("mslearn.worker.tasks.synthesize_task", synth)

    source_id = c.post(
        "/api/corpus/sources", json={"ref": str(tiny_pdf), "role": "spine"}
    ).json()["source_id"]
    graph = c.app.state.context.graph
    graph.add_claim("cl1", "t", "neutral", source_id, [1.0, 0.0])
    graph.add_claim("cl2", "t2", "neutral", "other-source", [1.0, 0.0])
    graph.upsert_concept(ConceptRecord(concept_id="k1", name="n", summary="s"))
    graph.assign_claim("cl1", "k1")
    graph.assign_claim("cl2", "k1")

    r = c.delete(f"/api/corpus/sources/{source_id}")
    assert r.status_code == 200
    assert r.json() == {"source_id": source_id, "deleted": True, "affected_concepts": 1}
    assert db.source_row(source_id) is None
    assert "cl1" not in graph.claims and "cl2" in graph.claims
    assert graph.concepts["k1"]["dirty"] is True and graph.concepts["k1"]["teach_md"] == ""
    assert synth.delayed == ["default"]


def test_delete_source_unknown_404(client):
    c, _db, _task = client
    r = c.delete("/api/corpus/sources/nope")
    assert r.status_code == 404


def test_delete_while_chunking_prevents_resurrection(tmp_path, monkeypatch, tiny_pdf):
    # A source can be deleted while it's still "chunking" (background load
    # hasn't run yet). The DELETE endpoint doesn't special-case status, and
    # chunk_source_task's own guard (status != "chunking" -> no-op) must see
    # the row gone and do nothing rather than resurrecting it.
    from mslearn.worker import tasks as worker_tasks
    from mslearn.worker.app import app as celery_app

    db = OpsDB(tmp_path / "ops.db")
    graph = InMemoryGraphStore()
    ctx = PipelineContext(
        settings=Settings(profiles_path=Path("profiles.yaml")),
        db=db,
        router=ScriptedRouter([]),
        graph=graph,
    )
    monkeypatch.setattr(
        "mslearn.pipeline.orchestrator.chunk_source_task",
        type("F", (), {"delay": lambda self, *a: None})(),
    )
    app = create_app(context=ctx)
    with TestClient(app) as c:
        source_id = c.post(
            "/api/corpus/sources", json={"ref": str(tiny_pdf), "role": "spine"}
        ).json()["source_id"]
        assert db.source_row(source_id)["status"] == "chunking"

        r = c.delete(f"/api/corpus/sources/{source_id}")
        assert r.status_code == 200
        assert db.source_row(source_id) is None

        # A redelivered/late chunk_source_task run for the now-deleted source
        # must be a silent no-op, not resurrect the row.
        celery_app.conf.task_always_eager = True
        try:
            worker_tasks.chunk_source_task.delay(
                "default", source_id, str(tiny_pdf), "spine", None
            ).get()
        finally:
            celery_app.conf.task_always_eager = False
        assert db.source_row(source_id) is None
        assert graph.sources == {}


def test_upload_image_file_accepted_and_ingests(client, monkeypatch):
    c, db, _fake_task = client
    monkeypatch.setattr(
        "mslearn.worker.tasks.image_describe_via_router",
        lambda router, opsdb: (lambda b, mt: "Revenue grew 20% in Q4.\n\n[image: line chart]"),
    )
    r = c.post(
        "/api/corpus/upload",
        files={"file": ("screenshot.png", b"\x89PNG\r\n\x1a\nfakebytes", "image/png")},
        data={"role": "supplement", "local": "false"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["stored_path"].endswith(".png")
    assert db.source_row(body["source_id"])["status"] == "running"
