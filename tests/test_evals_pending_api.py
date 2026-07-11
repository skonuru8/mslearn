import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mslearn.opsdb import OpsDB
from mslearn.server.app import create_app
from mslearn.settings import Settings
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore


@pytest.fixture()
def app_ctx(tmp_path):
    ctx = PipelineContext(
        settings=Settings(profiles_path=Path("profiles.yaml")),
        db=OpsDB(tmp_path / "ops.db"),
        router=None,
        graph=InMemoryGraphStore(),
    )
    return ctx


@pytest.fixture()
def client(app_ctx):
    app = create_app(context=app_ctx)
    with TestClient(app) as test_client:
        yield test_client


def _seed_pending_prompt_run(db) -> int:
    proposal = {
        "kind": "prompt",
        "key": "prompt:rubric_teach",
        "new_prompt": "Score {concept_name} using {markdown}.",
        "targets_metric": "extraction.recall",
        "why": "clarify rubric",
    }
    return db.create_evolution_run(
        proposal_json=json.dumps(proposal),
        shadow_before_json=json.dumps({"extraction.recall": 0.85}),
        shadow_after_json=json.dumps({"extraction.recall": 0.90}),
        accepted=False,
        reason="clarify rubric",
        status="pending",
    )


def test_get_pending_lists_seeded_run(client, app_ctx):
    run_id = _seed_pending_prompt_run(app_ctx.db)

    response = client.get("/api/evals/pending")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["run_id"] == run_id
    assert body[0]["proposal"]["kind"] == "prompt"
    assert body[0]["shadow_before"]["extraction.recall"] == 0.85
    assert body[0]["shadow_after"]["extraction.recall"] == 0.90
    assert body[0]["why"]


def test_approve_pending_applies_prompt_and_clears_pending(client, app_ctx):
    run_id = _seed_pending_prompt_run(app_ctx.db)

    response = client.post(f"/api/evals/pending/{run_id}/approve")

    assert response.status_code == 200
    assert app_ctx.db.get_setting("prompt:rubric_teach") == "Score {concept_name} using {markdown}."
    assert app_ctx.db.pending_evolution_runs() == []
    history = app_ctx.db.evolution_history()
    row = next(r for r in history if r["id"] == run_id)
    assert row["status"] == "applied"


def test_reject_pending_clears_without_applying(client, app_ctx):
    run_id = _seed_pending_prompt_run(app_ctx.db)

    response = client.post(f"/api/evals/pending/{run_id}/reject")

    assert response.status_code == 200
    assert app_ctx.db.get_setting("prompt:rubric_teach") is None
    assert app_ctx.db.pending_evolution_runs() == []
    history = app_ctx.db.evolution_history()
    row = next(r for r in history if r["id"] == run_id)
    assert row["status"] == "rejected"


def test_approve_unknown_run_404s(client):
    response = client.post("/api/evals/pending/999/approve")
    assert response.status_code == 404


def test_reject_unknown_run_404s(client):
    response = client.post("/api/evals/pending/999/reject")
    assert response.status_code == 404
