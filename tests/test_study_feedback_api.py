from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mslearn.graph.records import ConceptRecord
from mslearn.opsdb import OpsDB
from mslearn.server.app import create_app
from mslearn.settings import Settings
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore


def _seed_graph() -> InMemoryGraphStore:
    graph = InMemoryGraphStore()
    graph.upsert_concept(
        ConceptRecord("k1", "Cache invalidation", "Know when cached values become stale.")
    )
    graph.set_concept_meta("k1", order_index=0)
    return graph


@pytest.fixture()
def client(tmp_path):
    graph = _seed_graph()
    ctx = PipelineContext(
        settings=Settings(profiles_path=Path("profiles.yaml")),
        db=OpsDB(tmp_path / "ops.db"),
        router=None,
        graph=graph,
    )
    app = create_app(context=ctx)
    with TestClient(app) as test_client:
        yield test_client


def test_post_feedback_valid(client):
    response = client.post(
        "/api/study/concepts/k1/feedback",
        json={"helpful": True, "tags": ["too_shallow"], "comment": "needs more", "guide_hash": "h1"},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_post_feedback_unknown_tag_rejected(client):
    response = client.post(
        "/api/study/concepts/k1/feedback",
        json={"helpful": False, "tags": ["bogus_tag"]},
    )
    assert response.status_code == 422


def test_get_feedback_returns_latest(client):
    client.post(
        "/api/study/concepts/k1/feedback",
        json={"helpful": True, "tags": ["too_shallow"], "comment": "first"},
    )
    client.post(
        "/api/study/concepts/k1/feedback",
        json={"helpful": False, "tags": ["wrong"], "comment": "second"},
    )
    response = client.get("/api/study/concepts/k1/feedback")
    assert response.status_code == 200
    body = response.json()
    assert body["helpful"] is False
    assert body["tags"] == ["wrong"]
    assert body["comment"] == "second"


def test_get_feedback_empty_when_none(client):
    response = client.get("/api/study/concepts/k1/feedback")
    assert response.status_code == 200
    assert response.json() == {}
