import json
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
    db = OpsDB(tmp_path / "ops.db")
    settings = Settings(profiles_path=Path("profiles.yaml"))
    ctx = PipelineContext(
        settings=settings,
        db=db,
        router=ScriptedRouter([]),
        graph=InMemoryGraphStore(),
    )
    fake_task = NoDelayTask()
    monkeypatch.setattr("mslearn.pipeline.orchestrator.extract_chunk_task", fake_task)
    app = create_app(context=ctx)
    with TestClient(app) as c:
        yield c, db, fake_task


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


def test_ingest_failure_422_and_marks_failed(client, tmp_path):
    c, db, _ = client
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"not a pdf")
    r = c.post(
        "/api/corpus/sources",
        json={"ref": str(bad), "role": "supplement"},
    )
    assert r.status_code == 422
    assert "failed to load" in r.json()["detail"]
    sources = db.all_sources()
    assert len(sources) == 1
    assert sources[0]["status"] == "failed"


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
    c, _db, _task = client
    monkeypatch.setattr("mslearn.server.routers.corpus.synthesize_task", NoDelaySynthesisTask())
    monkeypatch.setattr("mslearn.server.routers.corpus.worker_online", lambda: True)
    r = c.post("/api/corpus/synthesize")
    assert r.status_code == 200
    assert r.json() == {"enqueued": True, "worker_online": True}

    monkeypatch.setattr("mslearn.server.routers.corpus.worker_online", lambda: False)
    r = c.post("/api/corpus/synthesize")
    assert r.json() == {"enqueued": True, "worker_online": False}


def test_synthesis_status_reflects_last_run_setting(client):
    c, db, _task = client
    r = c.get("/api/corpus/synthesis/status")
    assert r.status_code == 200
    assert r.json() == {"last_run": None, "last_error": None}

    db.set_project_setting(
        "default", "synthesis:last_run",
        json.dumps({"ts": 123, "dirty_concepts": 2, "processed_concepts": 2, "curriculum_len": 5}),
    )
    r = c.get("/api/corpus/synthesis/status")
    assert r.json() == {
        "last_run": {"ts": 123, "dirty_concepts": 2, "processed_concepts": 2, "curriculum_len": 5},
        "last_error": None,
    }


def test_synthesis_status_surfaces_last_error(client):
    c, db, _task = client
    db.set_project_setting(
        "default", "synthesis:last_error",
        json.dumps({"ts": 5, "error": "boom"}),
    )
    r = c.get("/api/corpus/synthesis/status")
    assert r.json()["last_error"] == {"ts": 5, "error": "boom"}

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
