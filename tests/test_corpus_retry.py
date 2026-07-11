from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mslearn.opsdb import OpsDB
from mslearn.server.app import create_app
from mslearn.settings import Settings
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore
from tests.test_extraction_graph import ScriptedRouter


class NoDelayChunkSourceTask:
    def __init__(self):
        self.delayed = []

    def delay(self, *args):
        self.delayed.append(args)


class NoDelayExtractTask:
    def __init__(self):
        self.delayed = []

    def delay(self, project_id, chunk_id):
        self.delayed.append((project_id, chunk_id))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from mslearn.worker.app import app as celery_app

    db = OpsDB(tmp_path / "ops.db")
    settings = Settings(profiles_path=Path("profiles.yaml"))
    ctx = PipelineContext(
        settings=settings,
        db=db,
        router=ScriptedRouter([]),
        graph=InMemoryGraphStore(),
    )
    fake_chunk_task = NoDelayChunkSourceTask()
    fake_extract_task = NoDelayExtractTask()
    monkeypatch.setattr("mslearn.server.routers.corpus.chunk_source_task", fake_chunk_task)
    monkeypatch.setattr("mslearn.worker.tasks.extract_chunk_task", fake_extract_task)
    monkeypatch.setattr("mslearn.pipeline.orchestrator.extract_chunk_task", fake_extract_task)
    celery_app.conf.task_always_eager = True
    app = create_app(context=ctx)
    with TestClient(app) as c:
        yield c, db, fake_chunk_task, fake_extract_task
    celery_app.conf.task_always_eager = False


def test_retry_reload_when_no_chunks(client):
    c, db, fake_chunk_task, _fake_extract_task = client
    db.register_source("s1", ref="http://example.com/x", role="spine", total_chunks=0)
    db.set_source_status("s1", "failed", error="ssl error")

    r = c.post("/api/corpus/sources/s1/retry")
    assert r.status_code == 200
    body = r.json()
    assert body["source_id"] == "s1"
    assert body["mode"] == "reload"

    row = db.source_row("s1")
    assert row["status"] == "chunking"
    assert row["error"] is None
    assert fake_chunk_task.delayed == [
        ("default", "s1", "http://example.com/x", "spine", None, True)
    ]


def test_retry_chunks_when_chunks_exist(client):
    c, db, _fake_chunk_task, fake_extract_task = client
    db.register_source("s2", ref="ref2", role="spine", total_chunks=3)
    db.register_chunk_jobs("s2", ["c1", "c2", "c3"])
    db.mark_chunk("c1", "failed", error="boom")
    db.set_source_status("s2", "failed", error="failure rate too high")

    r = c.post("/api/corpus/sources/s2/retry")
    assert r.status_code == 200
    body = r.json()
    assert body["source_id"] == "s2"
    assert body["mode"] == "chunks"

    row = db.source_row("s2")
    assert row["status"] == "running"
    assert row["error"] is None
    assert row["failed_chunks"] == 0
    # resume_pending re-enqueues every still-pending chunk for the source,
    # not just the one that was reset from "failed" — c2/c3 were already
    # pending (never completed), so all three go out.
    assert sorted(fake_extract_task.delayed) == [
        ("default", "c1"), ("default", "c2"), ("default", "c3"),
    ]


def test_retry_unknown_source_404(client):
    c, _db, _fake_chunk_task, _fake_extract_task = client
    r = c.post("/api/corpus/sources/nope/retry")
    assert r.status_code == 404
