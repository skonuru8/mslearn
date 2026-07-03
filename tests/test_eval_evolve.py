from unittest.mock import patch

from mslearn.evals.evolve import evolve_once
from mslearn.evals.runner import run_eval
from mslearn.opsdb import OpsDB
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore, ScriptedRouter


def test_runner_persists_metrics(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    ctx = PipelineContext(
        settings=None,
        db=db,
        router=ScriptedRouter([]),
        graph=InMemoryGraphStore(),
    )

    fake_metrics = {
        "extraction.precision": 0.95,
        "extraction.recall": 0.90,
        "grounding.false_accept": 0.0,
        "clustering.f1": 0.85,
        "tension.accuracy": 0.80,
        "schema.validity": 1.0,
        "provenance.violations": 0.0,
    }

    result = run_eval(ctx, offline=True, metric_fn=lambda _ctx: fake_metrics)
    assert result["passed"] is True
    latest = db.latest_eval_run()
    assert latest is not None
    assert latest["passed"] == 1
    rows = db.eval_metrics_for_run(latest["id"])
    assert any(r["metric"] == "extraction.precision" for r in rows)


def test_evolve_rejects_invalid_proposal(tmp_path, monkeypatch):
    db = OpsDB(tmp_path / "ops.db")
    db.create_eval_run("full", "abc", True)
    router = ScriptedRouter(
        [
            {
                "proposals": [
                    {
                        "kind": "tunable",
                        "key": "trust.quote_threshold",
                        "value": 5.0,
                        "targets_metric": "grounding.false_accept",
                        "why": "too low",
                    }
                ]
            }
        ]
    )
    ctx = PipelineContext(settings=None, db=db, router=router, graph=InMemoryGraphStore())
    baseline = {
        "extraction.precision": 0.9,
        "extraction.recall": 0.85,
        "grounding.false_accept": 0.01,
        "clustering.f1": 0.8,
        "tension.accuracy": 0.75,
        "schema.validity": 0.99,
        "provenance.violations": 0.0,
    }
    with patch("mslearn.evals.evolve.compute_component_metrics", return_value=baseline):
        summary = evolve_once(ctx)
    assert summary["accepted"] == []
    assert summary["rejected"]


def test_direction_aware_improvement_and_regression():
    from mslearn.evals.evolve import _improved, _not_regressed

    # higher-is-better metric
    assert _improved("extraction.recall", 0.9, 0.8)
    assert not _improved("extraction.recall", 0.8, 0.9)
    # lower-is-better metric: a DECREASE is the improvement
    assert _improved("grounding.false_accept", 0.01, 0.05)
    assert not _improved("grounding.false_accept", 0.05, 0.01)
    # regression checks respect gate direction
    assert _not_regressed("grounding.false_accept", 0.01, 0.02)
    assert not _not_regressed("grounding.false_accept", 0.05, 0.02)
    assert _not_regressed("clustering.f1", 0.85, 0.85)


def test_validate_proposal_missing_value_rejected_not_crash():
    from mslearn.evals.evolve import validate_proposal

    error = validate_proposal({"kind": "tunable", "key": "trust.quote_threshold"})
    assert error is not None and "invalid or missing value" in error
