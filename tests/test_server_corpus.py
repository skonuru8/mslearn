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

    def delay(self, chunk_id):
        self.delayed.append(chunk_id)


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


def test_upload_unsupported_suffix_rejected(client):
    c, _db, _task = client
    r = c.post(
        "/api/corpus/upload",
        files={"file": ("notes.docx", b"zzz", "application/octet-stream")},
        data={"role": "supplement"},
    )
    assert r.status_code == 422
    assert "unsupported file type" in r.json()["detail"]
