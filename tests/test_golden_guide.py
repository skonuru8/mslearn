from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mslearn.evals.golden import load_golden
from mslearn.evals.seed import promote_feedback_to_golden
from mslearn.graph.records import ConceptRecord
from mslearn.opsdb import OpsDB
from mslearn.server.app import create_app
from mslearn.settings import Settings
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore


def _seed_graph() -> InMemoryGraphStore:
    graph = InMemoryGraphStore()
    graph.upsert_concept(ConceptRecord("k1", "Cache invalidation", "Summary"))
    graph.set_concept_meta("k1", order_index=0)
    graph.add_claim("c1", "text", "neutral", "s1", [1.0, 0.0], quote="q", chunk_id="ch1")
    graph.assign_claim("c1", "k1")
    return graph


def test_promote_feedback_creates_active_guide_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr("mslearn.evals.golden.GOLDEN_DIR", tmp_path)
    graph = _seed_graph()
    db = OpsDB(tmp_path / "ops.db")
    db.add_note_feedback(
        "default", "k1", helpful=False, tags=["too_shallow"], comment="needs depth", guide_hash="h1"
    )
    ctx = PipelineContext(settings=None, db=db, router=None, graph=graph)

    record = promote_feedback_to_golden(ctx, "k1")

    assert record.concept_id == "k1"
    assert record.failing_axis == "depth"
    assert record.tag == "too_shallow"

    fixtures = load_golden("guide", active_only=True)
    assert len(fixtures) == 1
    assert fixtures[0].concept_id == "k1"
    assert fixtures[0].claims == [{"claim_id": "c1", "text": "text", "stance": "neutral", "kind": "claim"}]


def test_promote_feedback_maps_each_tag_to_its_axis(tmp_path, monkeypatch):
    monkeypatch.setattr("mslearn.evals.golden.GOLDEN_DIR", tmp_path)
    expectations = {
        "too_shallow": "depth",
        "repetitive": "non_redundancy",
        "wrong": "grounding",
        "off_topic": "category_fit",
    }
    for tag, axis in expectations.items():
        graph = _seed_graph()
        db = OpsDB(tmp_path / f"ops-{tag}.db")
        db.add_note_feedback("default", "k1", helpful=False, tags=[tag], comment="", guide_hash=None)
        ctx = PipelineContext(settings=None, db=db, router=None, graph=graph)
        record = promote_feedback_to_golden(ctx, "k1")
        assert record.failing_axis == axis


def test_promote_feedback_unknown_concept_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("mslearn.evals.golden.GOLDEN_DIR", tmp_path)
    db = OpsDB(tmp_path / "ops.db")
    ctx = PipelineContext(settings=None, db=db, router=None, graph=InMemoryGraphStore())
    with pytest.raises(KeyError):
        promote_feedback_to_golden(ctx, "nope")


def test_promote_feedback_no_feedback_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("mslearn.evals.golden.GOLDEN_DIR", tmp_path)
    graph = _seed_graph()
    db = OpsDB(tmp_path / "ops.db")
    ctx = PipelineContext(settings=None, db=db, router=None, graph=graph)
    with pytest.raises(KeyError):
        promote_feedback_to_golden(ctx, "k1")


def test_golden_guide_from_feedback_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr("mslearn.evals.golden.GOLDEN_DIR", tmp_path)
    graph = _seed_graph()
    db = OpsDB(tmp_path / "ops.db")
    db.add_note_feedback(
        "default", "k1", helpful=False, tags=["repetitive"], comment="", guide_hash=None
    )
    ctx = PipelineContext(
        settings=Settings(profiles_path=Path("profiles.yaml")), db=db, router=None, graph=graph
    )
    app = create_app(context=ctx)
    with TestClient(app) as client:
        response = client.post("/api/evals/golden/guide/from-feedback", json={"concept_id": "k1"})

        assert response.status_code == 200
        body = response.json()
        assert body["concept_id"] == "k1"
        assert body["failing_axis"] == "non_redundancy"

        fixtures = load_golden("guide", active_only=True)
        assert len(fixtures) == 1


def test_golden_guide_from_feedback_unknown_concept_404s(tmp_path, monkeypatch):
    monkeypatch.setattr("mslearn.evals.golden.GOLDEN_DIR", tmp_path)
    db = OpsDB(tmp_path / "ops.db")
    ctx = PipelineContext(
        settings=Settings(profiles_path=Path("profiles.yaml")),
        db=db,
        router=None,
        graph=InMemoryGraphStore(),
    )
    app = create_app(context=ctx)
    with TestClient(app) as client:
        response = client.post("/api/evals/golden/guide/from-feedback", json={"concept_id": "nope"})
        assert response.status_code == 404


def test_golden_kind_index_route_still_reachable_after_from_feedback_route(tmp_path, monkeypatch):
    # Regression guard: /golden/guide/from-feedback must be registered before
    # /golden/{kind}/{index} or Starlette would try to route "from-feedback"
    # through {index}: int and 422 instead of ever reaching the literal route.
    monkeypatch.setattr("mslearn.evals.golden.GOLDEN_DIR", tmp_path)
    from mslearn.evals.golden import ClusteringGolden, append_golden

    append_golden(
        "clustering",
        ClusteringGolden(text_a="a", text_b="b", same_concept=True, review="pending"),
    )
    ctx = PipelineContext(
        settings=Settings(profiles_path=Path("profiles.yaml")),
        db=OpsDB(tmp_path / "ops.db"),
        router=None,
        graph=InMemoryGraphStore(),
    )
    app = create_app(context=ctx)
    with TestClient(app) as client:
        response = client.post(
            "/api/evals/golden/clustering/0", json={"action": "approve"}
        )
        assert response.status_code == 200
        assert response.json()["status"] == "approved"
