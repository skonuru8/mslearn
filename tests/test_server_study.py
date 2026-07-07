import contextlib
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

GUIDE_JSON = {
    "concept_id": "k1",
    "title": "Cache invalidation",
    "tl_dr": {"text": "Cache invalidation is difficult.", "claims": ["c1"]},
    "skeleton": ["Explanation"],
    "sections": [
        {
            "id": "s1",
            "title": "Explanation",
            "items": [
                {"kind": "claim", "text": "Cache invalidation is difficult.", "claims": ["c1"]},
            ],
        }
    ],
    "disagreements": [],
    "open_questions": [],
}

FLASHCARDS_JSON = {
    "cards": [
        {"front": "Why is cache invalidation hard?", "back": "Cache invalidation is difficult.", "claims": ["c1"]},
        {"front": "Unsupported filler", "back": "no source", "claims": []},
    ]
}

SELFCHECK_JSON = {
    "checks": [
        {"question": "Why is cache invalidation hard?", "answer": "Cache invalidation is difficult.", "claims": ["c1"]},
    ]
}


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


class JsonRouter:
    """Serves queued JSON (`parsed`) responses in order — the study endpoints
    (teach/flashcards/selfcheck) all make schema-constrained calls."""

    def __init__(self, outputs: list[dict]):
        self.outputs = list(outputs)
        self.calls = []
        self.requests = []

    def complete(self, role, request):
        self.calls.append(role)
        self.requests.append(request)
        out = self.outputs.pop(0)
        return ModelResponse(
            text="",
            parsed=out,
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


def _seed_graph() -> InMemoryGraphStore:
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
    return graph


@pytest.fixture()
def study_client_factory(tmp_path, monkeypatch):
    """Factory fixture so individual tests can queue whatever JSON responses
    the endpoints under test will consume (teach/flashcards/selfcheck each
    make one schema-constrained model call)."""

    with contextlib.ExitStack() as stack:

        def _make(outputs: list[dict] | None = None):
            graph = _seed_graph()
            router = JsonRouter(outputs if outputs is not None else [dict(GUIDE_JSON)])
            ctx = PipelineContext(
                settings=Settings(profiles_path=Path("profiles.yaml")),
                db=OpsDB(tmp_path / "ops.db"),
                router=router,
                graph=graph,
            )
            task = NoDelayTask()
            monkeypatch.setattr("mslearn.server.routers.study.synthesize_task", task)
            app = create_app(context=ctx)
            client = stack.enter_context(TestClient(app))
            return client, graph, router, task

        yield _make


@pytest.fixture()
def study_client(study_client_factory):
    yield study_client_factory()


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
            "conflict_count": 0,
        }
    ]


def test_curriculum_carries_conflict_counts(study_client):
    # The conflict badge feeds straight from the curriculum rows — no
    # per-concept detail fan-out from the UI.
    client, graph, _router, _task = study_client
    graph.add_claim("c2", "Cache invalidation is easy.", "neutral", "s1", [0.9, 0.1])
    graph.assign_claim("c2", "k1")
    graph.add_conflict("c1", "c2", "genuine_debate", "hardness dispute")

    response = client.get("/api/study/curriculum")

    assert response.status_code == 200
    assert response.json()[0]["conflict_count"] == 1


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


def test_teach_returns_guide_and_progress(study_client):
    client, graph, router, _task = study_client

    response = client.get("/api/study/concepts/k1/teach")

    assert response.status_code == 200
    body = response.json()
    assert "guide" in body and "sections" in body["guide"]
    assert body["guide"]["title"] == "Cache invalidation"
    assert body["cached"] is False
    assert body["progress"] == {}
    assert router.calls == ["interactive"]
    assert graph.get_concept("k1")["teach_md"]

    # Second call hits the cache — no second model call, and the response
    # says so, so the UI can show a fast-path indicator instead of the
    # "writing your lesson" state.
    cached_response = client.get("/api/study/concepts/k1/teach")
    assert cached_response.status_code == 200
    assert cached_response.json()["cached"] is True
    assert router.calls == ["interactive"]


def test_progress_toggle_persists(study_client):
    client, _graph, _router, _task = study_client
    client.get("/api/study/concepts/k1/teach")

    response = client.post(
        "/api/study/concepts/k1/progress", json={"section_id": "s1", "reviewed": True}
    )
    assert response.status_code == 200
    assert response.json() == {"progress": {"s1": True}}

    body = client.get("/api/study/concepts/k1/teach").json()
    assert body["progress"].get("s1") is True


def test_flashcards_count_and_grounding(study_client_factory):
    client, _graph, router, _task = study_client_factory(
        outputs=[dict(GUIDE_JSON), dict(FLASHCARDS_JSON)]
    )
    client.get("/api/study/concepts/k1/teach")

    response = client.post("/api/study/concepts/k1/flashcards", json={"count": 3})

    assert response.status_code == 200
    cards = response.json()["cards"]
    assert len(cards) <= 3
    assert all(c["claims"] for c in cards)
    assert router.calls == ["interactive", "interactive"]


def test_selfcheck_count_and_grounding(study_client_factory):
    client, _graph, _router, _task = study_client_factory(
        outputs=[dict(GUIDE_JSON), dict(SELFCHECK_JSON)]
    )
    client.get("/api/study/concepts/k1/teach")

    response = client.post("/api/study/concepts/k1/selfcheck", json={"count": 5})

    assert response.status_code == 200
    checks = response.json()["checks"]
    assert len(checks) <= 5
    assert all(c["claims"] for c in checks)


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


def test_cross_project_concept_404(study_client):
    client, _graph, _router, _task = study_client

    assert client.get("/api/study/concepts/nope/teach").status_code == 404
    assert client.post(
        "/api/study/concepts/nope/progress", json={"section_id": "s1", "reviewed": True}
    ).status_code == 404
    assert client.post(
        "/api/study/concepts/nope/flashcards", json={"count": 3}
    ).status_code == 404
    assert client.post(
        "/api/study/concepts/nope/selfcheck", json={"count": 3}
    ).status_code == 404
