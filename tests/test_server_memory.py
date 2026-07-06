from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mslearn.opsdb import OpsDB
from mslearn.server.app import create_app
from mslearn.settings import Settings
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore, InMemoryLearnerMemory, ScriptedRouter


def make_ctx(tmp_path, *, memory):
    return PipelineContext(
        settings=Settings(profiles_path=Path("profiles.yaml")),
        db=OpsDB(tmp_path / "ops.db"),
        router=ScriptedRouter([]),
        graph=InMemoryGraphStore(),
        memory=memory,
    )


def test_memory_endpoint_lists_items_and_deletes_by_id(tmp_path):
    memory = InMemoryLearnerMemory()
    struggle_id = memory.add("Struggles with cache invalidation.", "struggle")
    memory.add("Prefers concrete examples.", "preference")
    app = create_app(context=make_ctx(tmp_path, memory=memory))

    with TestClient(app) as client:
        list_response = client.get("/api/memory")
        delete_response = client.delete(f"/api/memory/{struggle_id}")
        after_delete = client.get("/api/memory")

    assert list_response.status_code == 200
    assert list_response.json()["items"] == [
        {
            "memory_id": struggle_id,
            "text": "Struggles with cache invalidation.",
            "category": "struggle",
            "created_at": list_response.json()["items"][0]["created_at"],
        },
        {
            "memory_id": list_response.json()["items"][1]["memory_id"],
            "text": "Prefers concrete examples.",
            "category": "preference",
            "created_at": list_response.json()["items"][1]["created_at"],
        },
    ]
    assert delete_response.status_code == 200
    assert delete_response.json() == {"memory_id": struggle_id, "deleted": True}
    assert struggle_id not in [item["memory_id"] for item in after_delete.json()["items"]]


def test_memory_endpoint_returns_503_when_memory_unavailable(tmp_path):
    app = create_app(context=make_ctx(tmp_path, memory=None))

    with TestClient(app) as client:
        response = client.get("/api/memory")
        delete_response = client.delete("/api/memory/missing")

    assert response.status_code == 503
    assert response.json()["detail"] == "learner memory unavailable"
    assert delete_response.status_code == 503


class BrokenMemory:
    """A backend can fail on real use (e.g. an unreachable embedder) rather
    than at ctx.memory construction time. The memory endpoints must turn
    that into the same honest 503 the UI already handles, not an unhandled
    500."""

    def all(self, project_id: str = "default"):
        raise RuntimeError("embedder connection refused")

    def delete(self, memory_id: str) -> None:
        raise RuntimeError("embedder connection refused")


def test_memory_endpoint_returns_503_with_reason_when_client_init_fails(tmp_path):
    app = create_app(context=make_ctx(tmp_path, memory=BrokenMemory()))

    with TestClient(app) as client:
        list_response = client.get("/api/memory")
        delete_response = client.delete("/api/memory/missing")

    assert list_response.status_code == 503
    assert "embedder connection refused" in list_response.json()["detail"]
    assert delete_response.status_code == 503
    assert "embedder connection refused" in delete_response.json()["detail"]


def test_static_dist_absent_does_not_shadow_api(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = create_app(context=make_ctx(tmp_path, memory=None))

    with TestClient(app) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
