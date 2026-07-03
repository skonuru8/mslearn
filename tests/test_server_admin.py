from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mslearn.opsdb import OpsDB, TUNABLE_DEFAULTS
from mslearn.profiles import get_active_profile_name, load_profiles
from mslearn.server.app import create_app
from mslearn.settings import Settings
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore
from tests.test_extraction_graph import ScriptedRouter


@pytest.fixture()
def client(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    settings = Settings(profiles_path=Path("profiles.yaml"))
    ctx = PipelineContext(
        settings=settings,
        db=db,
        router=ScriptedRouter([]),
        graph=InMemoryGraphStore(),
    )
    app = create_app(context=ctx)
    with TestClient(app) as c:
        yield c, db


def test_health(client):
    c, _ = client
    r = c.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_profiles_list_and_switch(client):
    c, db = client
    cfg = load_profiles("profiles.yaml")

    r = c.get("/api/admin/profiles")
    assert r.status_code == 200
    body = r.json()
    assert body["active"] == get_active_profile_name(db, cfg)
    assert set(body["available"]) == set(cfg.profiles)

    r = c.post("/api/admin/profiles/offline")
    assert r.status_code == 200
    assert r.json()["active"] == "offline"
    assert get_active_profile_name(db, cfg) == "offline"


def test_profiles_unknown_404(client):
    c, _ = client
    r = c.post("/api/admin/profiles/nope")
    assert r.status_code == 404


def test_tunables_list_includes_defaults(client):
    c, _ = client
    r = c.get("/api/admin/tunables")
    assert r.status_code == 200
    items = {t["key"]: t for t in r.json()}
    assert set(items) == set(TUNABLE_DEFAULTS)
    for key, default in TUNABLE_DEFAULTS.items():
        assert items[key]["default"] == default
        assert items[key]["value"] == default


def test_tunables_set_and_history(client):
    c, db = client
    r = c.post(
        "/api/admin/tunables/trust.quote_threshold",
        json={"value": 85.0, "reason": "eval run 7"},
    )
    assert r.status_code == 200
    assert r.json()["value"] == 85.0
    assert db.get_tunable("trust.quote_threshold") == 85.0

    r = c.get("/api/admin/tunables/trust.quote_threshold/history")
    assert r.status_code == 200
    history = r.json()
    assert len(history) == 1
    assert history[0]["value"] == 85.0
    assert "run 7" in history[0]["reason"]


def test_tunables_unknown_422(client):
    c, _ = client
    r = c.post(
        "/api/admin/tunables/nope.unknown",
        json={"value": 1.0, "reason": "bad"},
    )
    assert r.status_code == 422


def test_spend_aggregates_roles_and_cost(client):
    c, db = client
    db.log_model_call(
        role="extraction",
        provider="ollama",
        model="m1",
        cost_usd=0.01,
        outcome="ok",
    )
    db.log_model_call(
        role="synthesis",
        provider="openrouter",
        model="m2",
        cost_usd=0.02,
        outcome="ok",
    )
    db.log_model_call(
        role="synthesis",
        provider="openrouter",
        model="m2",
        cost_usd=None,
        outcome="ok",
    )

    r = c.get("/api/admin/spend?limit=100")
    assert r.status_code == 200
    body = r.json()
    assert body["total_calls"] == 3
    assert body["total_cost_usd"] == pytest.approx(0.03)
    assert body["by_role"] == {"extraction": 1, "synthesis": 2}
    assert len(body["recent_calls"]) == 3
