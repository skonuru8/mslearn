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
        "guide.grounding": 1.0,
        "feedback.wrong_rate": 0.0,
        "feedback.helpful_rate": 1.0,
    }

    result = run_eval(ctx, offline=True, metric_fn=lambda _ctx: fake_metrics)
    assert result["passed"] is True
    latest = db.latest_eval_run()
    assert latest is not None
    assert latest["passed"] == 1
    rows = db.eval_metrics_for_run(latest["id"])
    assert any(r["metric"] == "extraction.precision" for r in rows)


def test_component_filter_scopes_gates_not_just_metrics(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    ctx = PipelineContext(
        settings=None, db=db, router=ScriptedRouter([]), graph=InMemoryGraphStore()
    )
    # extraction gates pass; clustering.f1 is far below its 0.80 gate. Before
    # the fix, `--component extraction` still failed the whole run over the
    # unrelated clustering regression because the gate loop ignored the
    # component filter entirely.
    fake_metrics = {
        "extraction.precision": 0.95,
        "extraction.recall": 0.90,
        "clustering.f1": 0.10,
    }
    result = run_eval(ctx, component="extraction", offline=True, metric_fn=lambda _ctx: fake_metrics)
    assert result["passed"] is True
    assert result["gates"] == {"extraction.precision": True, "extraction.recall": True}
    assert result["metrics"] == {"extraction.precision": 0.95, "extraction.recall": 0.90}

    full_result = run_eval(ctx, offline=True, metric_fn=lambda _ctx: fake_metrics)
    assert full_result["passed"] is False  # clustering.f1 still fails the full run


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
    assert router.requests[-1].max_tokens == int(db.get_tunable("evolve.max_tokens"))


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


def test_evolve_refuses_provenance_adjacent_proposal(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    db.create_eval_run("full", "abc", True)
    router = ScriptedRouter(
        [
            {
                "proposals": [
                    {
                        "kind": "tunable",
                        "key": "trust.quote_threshold",
                        "value": 95.0,
                        "targets_metric": "provenance.violations",
                        "why": "reduce fabrication",
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
    }
    with patch("mslearn.evals.evolve.compute_component_metrics", return_value=baseline):
        summary = evolve_once(ctx)
    assert summary["accepted"] == []
    assert "not evaluated" in summary["rejected"][0]["reason"]
    assert db.get_tunable("trust.quote_threshold") == 90.0  # never applied


def test_evolve_uses_real_provenance_baseline_not_hardcoded_zero(tmp_path, monkeypatch):
    db = OpsDB(tmp_path / "ops.db")
    db.create_eval_run("full", "abc", True)
    router = ScriptedRouter([{"proposals": []}])
    ctx = PipelineContext(settings=None, db=db, router=router, graph=InMemoryGraphStore())
    baseline = {
        "extraction.precision": 0.9,
        "extraction.recall": 0.85,
        "grounding.false_accept": 0.01,
        "clustering.f1": 0.8,
        "tension.accuracy": 0.75,
        "schema.validity": 0.99,
    }
    monkeypatch.setattr("mslearn.evals.evolve.provenance_violation_count", lambda _ctx: 3)
    with patch("mslearn.evals.evolve.compute_component_metrics", return_value=baseline):
        summary = evolve_once(ctx)
    assert summary["baseline"]["provenance.violations"] == 3.0


def test_evolve_updates_accepted_flag_in_history(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    db.create_eval_run("full", "abc", True)
    router = ScriptedRouter(
        [
            {
                "proposals": [
                    {
                        "kind": "tunable",
                        "key": "trust.embed_sim_threshold",
                        "value": 0.2,
                        "targets_metric": "extraction.recall",
                        "why": "loosen matching",
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
    }
    shadow = dict(baseline)
    shadow["extraction.recall"] = 0.9  # improved; nothing else regresses
    with patch("mslearn.evals.evolve.compute_component_metrics", side_effect=[baseline, shadow]):
        summary = evolve_once(ctx)
    assert summary["accepted"], summary
    run_id = summary["accepted"][0]["run_id"]
    history = db.evolution_history()
    row = next(r for r in history if r["id"] == run_id)
    assert row["accepted"] == 1


def test_validate_proposal_missing_value_rejected_not_crash():
    from mslearn.evals.evolve import validate_proposal

    error = validate_proposal({"kind": "tunable", "key": "trust.quote_threshold"})
    assert error is not None and "invalid or missing value" in error


def test_evolve_tunable_proposal_passing_gates_auto_applies(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    db.create_eval_run("full", "abc", True)
    router = ScriptedRouter(
        [
            {
                "proposals": [
                    {
                        "kind": "tunable",
                        "key": "trust.embed_sim_threshold",
                        "value": 0.2,
                        "targets_metric": "extraction.recall",
                        "why": "loosen matching",
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
    }
    shadow = dict(baseline)
    shadow["extraction.recall"] = 0.9
    with patch("mslearn.evals.evolve.compute_component_metrics", side_effect=[baseline, shadow]):
        summary = evolve_once(ctx)
    assert summary["accepted"], summary
    assert summary["pending"] == []
    assert db.get_tunable("trust.embed_sim_threshold") == 0.2
    assert db.pending_evolution_runs() == []


def test_evolve_prompt_proposal_passing_gates_goes_pending_not_applied(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    db.create_eval_run("full", "abc", True)
    new_rubric_teach = (
        "Score teaching markdown thoroughly for concept {concept_name}.\n"
        "Markdown:\n{markdown}\n"
        "Return JSON: clarity_1_5, grounding_1_5, tension_handled (bool)."
    )
    router = ScriptedRouter(
        [
            {
                "proposals": [
                    {
                        "kind": "prompt",
                        "key": "prompt:rubric_teach",
                        "new_prompt": new_rubric_teach,
                        "targets_metric": "extraction.recall",
                        "why": "clarify rubric",
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
    }
    shadow = dict(baseline)
    shadow["extraction.recall"] = 0.9
    with patch("mslearn.evals.evolve.compute_component_metrics", side_effect=[baseline, shadow]):
        summary = evolve_once(ctx)
    assert summary["accepted"] == []
    assert len(summary["pending"]) == 1
    assert db.get_setting("prompt:rubric_teach") is None  # never auto-applied
    pending_rows = db.pending_evolution_runs()
    assert len(pending_rows) == 1
    assert pending_rows[0]["id"] == summary["pending"][0]["run_id"]


def test_evolve_sample_gated_feedback_regression_not_enforced_below_floor(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    db.create_eval_run("full", "abc", True)
    router = ScriptedRouter(
        [
            {
                "proposals": [
                    {
                        "kind": "tunable",
                        "key": "synth.similarity_floor",
                        "value": 0.8,
                        "targets_metric": "extraction.recall",
                        "why": "tighten matching",
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
        "feedback.wrong_rate": 0.5,
        "feedback.total_rated": 3,  # below MIN_FEEDBACK_SAMPLES
    }
    shadow = dict(baseline)
    shadow["extraction.recall"] = 0.9  # improved
    shadow["feedback.wrong_rate"] = 0.8  # would regress the gate if enforced
    with patch("mslearn.evals.evolve.compute_component_metrics", side_effect=[baseline, shadow]):
        summary = evolve_once(ctx)
    assert summary["accepted"], summary
    assert db.get_tunable("synth.similarity_floor") == 0.8


def test_evolve_sample_gated_feedback_regression_enforced_at_floor(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    db.create_eval_run("full", "abc", True)
    router = ScriptedRouter(
        [
            {
                "proposals": [
                    {
                        "kind": "tunable",
                        "key": "synth.similarity_floor",
                        "value": 0.8,
                        "targets_metric": "extraction.recall",
                        "why": "tighten matching",
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
        "feedback.wrong_rate": 0.5,
        "feedback.total_rated": 10,  # at MIN_FEEDBACK_SAMPLES, gate is live
    }
    shadow = dict(baseline)
    shadow["extraction.recall"] = 0.9  # improved
    shadow["feedback.wrong_rate"] = 0.8  # regresses — should block acceptance
    with patch("mslearn.evals.evolve.compute_component_metrics", side_effect=[baseline, shadow]):
        summary = evolve_once(ctx)
    assert summary["accepted"] == []
    assert db.get_tunable("synth.similarity_floor") == 0.75  # unchanged
