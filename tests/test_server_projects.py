from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mslearn.opsdb import OpsDB
from mslearn.server.app import create_app
from mslearn.settings import Settings
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore, ScriptedRouter


@pytest.fixture()
def client(tmp_path):
    graph = InMemoryGraphStore()
    ctx = PipelineContext(
        settings=Settings(profiles_path=Path("profiles.yaml")),
        db=OpsDB(tmp_path / "ops.db"),
        router=ScriptedRouter([]),
        graph=graph,
    )
    app = create_app(context=ctx)
    with TestClient(app) as c:
        yield c, ctx.db, graph


def test_project_crud_api(client):
    c, db, graph = client

    response = c.get("/api/projects")
    assert response.status_code == 200
    assert [row["project_id"] for row in response.json()] == ["default"]

    response = c.post("/api/projects", json={"name": "Project Alpha", "project_id": "alpha"})
    assert response.status_code == 200
    assert response.json()["project_id"] == "alpha"
    assert db.project_exists("alpha")

    response = c.delete("/api/projects/alpha")
    assert response.status_code == 200
    assert response.json() == {"deleted": "alpha"}
    assert not db.project_exists("alpha")
    assert "alpha" in graph.deleted_projects


def test_project_api_rejects_duplicates_invalid_ids_and_default_delete(client):
    c, _db, _graph = client
    assert c.post("/api/projects", json={"name": "Alpha", "project_id": "alpha"}).status_code == 200
    assert c.post("/api/projects", json={"name": "Alpha 2", "project_id": "alpha"}).status_code == 409
    assert c.post("/api/projects", json={"name": "Bad", "project_id": "Bad Id"}).status_code == 422
    assert c.delete("/api/projects/default").status_code == 422
    assert c.delete("/api/projects/missing").status_code == 404
