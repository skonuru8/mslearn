from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mslearn.graph.records import ConceptRecord
from mslearn.opsdb import OpsDB
from mslearn.providers.base import ModelResponse
from mslearn.server.app import create_app
from mslearn.settings import Settings
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore


class TextRouter:
    def __init__(self, text: str):
        self.text = text
        self.calls = []

    def complete(self, role, request):
        self.calls.append(role)
        return ModelResponse(
            text=self.text,
            parsed=None,
            input_tokens=1,
            output_tokens=1,
            latency_ms=1.0,
            provider="fake",
            model="m",
        )


class NoDelayTask:
    def __init__(self):
        self.count = 0

    def delay(self, project_id="default"):
        self.count += 1


@pytest.fixture()
def study_client(tmp_path, monkeypatch):
    graph = InMemoryGraphStore()
    graph.upsert_concept(
        ConceptRecord("k1", "Cache invalidation", "Know when cached values become stale.")
    )
    graph.set_concept_meta("k1", order_index=0)
    graph.add_claim(
        "c1",
        "Cache invalidation is difficult.",
        "neutral",
        "s1",
        [1.0, 0.0],
        quote="Cache invalidation is one of the two hard problems",
        chunk_id="ch1",
    )
    graph.assign_claim("c1", "k1")
    graph.chunks["ch1"] = {
        "chunk_id": "ch1",
        "source_id": "s1",
        "seq": 3,
        "unit_index": 1,
        "text": "Cache invalidation is one of the two hard problems.",
        "kind": "pdf",
        "page": 12,
        "href": None,
        "url": None,
        "para_index": None,
        "start_s": None,
        "end_s": None,
    }
    router = TextRouter(
        "\n".join(
            [
                "## Explanation",
                "Cache invalidation is difficult. [claim:c1]",
                "## Worked example",
                "Expire stale cache entries. [claim:c1]",
                "## Common misconception",
                "Caching still has freshness costs. [claim:c1]",
            ]
        )
    )
    ctx = PipelineContext(
        settings=Settings(profiles_path=Path("profiles.yaml")),
        db=OpsDB(tmp_path / "ops.db"),
        router=router,
        graph=graph,
    )
    task = NoDelayTask()
    monkeypatch.setattr("mslearn.server.routers.study.synthesize_task", task)
    app = create_app(context=ctx)
    with TestClient(app) as client:
        yield client, graph, router, task


def test_curriculum_endpoint_returns_store_curriculum(study_client):
    client, _graph, _router, _task = study_client

    response = client.get("/api/study/curriculum")

    assert response.status_code == 200
    assert response.json() == [
        {
            "concept_id": "k1",
            "name": "Cache invalidation",
            "summary": "Know when cached values become stale.",
            "order_index": 0,
        }
    ]


def test_concept_endpoint_returns_meta_claims_conflicts_and_citations(study_client):
    client, _graph, _router, _task = study_client

    response = client.get("/api/study/concepts/k1")

    assert response.status_code == 200
    body = response.json()
    assert body["concept"]["concept_id"] == "k1"
    assert body["claims"][0]["claim_id"] == "c1"
    assert body["conflicts"] == []
    assert body["citations"][0]["claim_id"] == "c1"
    assert body["citations"][0]["page"] == 12


def test_teach_endpoint_returns_generated_markdown(study_client):
    client, graph, router, _task = study_client

    response = client.get("/api/study/concepts/k1/teach")

    assert response.status_code == 200
    assert response.json()["markdown"].startswith("## Explanation")
    assert response.json()["cached"] is False
    assert router.calls == ["synthesis"]
    assert graph.get_concept("k1")["teach_md"].startswith("## Explanation")

    # Second call hits the cache — no second model call, and the response
    # says so, so the UI can show a fast-path indicator instead of the
    # "writing your lesson" state.
    cached_response = client.get("/api/study/concepts/k1/teach")
    assert cached_response.status_code == 200
    assert cached_response.json()["cached"] is True
    assert router.calls == ["synthesis"]


def test_flag_claim_rejects_dirties_clears_cache_and_enqueues(study_client):
    client, graph, _router, task = study_client
    graph.set_concept_teaching("k1", "cached lesson")

    response = client.post("/api/study/claims/c1/flag", json={"reason": "bad citation"})

    assert response.status_code == 200
    assert response.json() == {"claim_id": "c1", "concept_id": "k1", "status": "flagged"}
    assert graph.claims["c1"]["trust"] == "rejected"
    assert graph.get_concept("k1")["dirty"] is True
    assert graph.get_concept("k1")["teach_md"] == ""
    assert task.count == 1


def test_study_unknown_ids_404(study_client):
    client, _graph, _router, _task = study_client

    assert client.get("/api/study/concepts/nope").status_code == 404
    assert client.get("/api/study/concepts/nope/teach").status_code == 404
    assert client.post("/api/study/claims/nope/flag", json={"reason": "bad"}).status_code == 404
