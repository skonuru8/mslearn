from mslearn.evals.metrics import feedback_rates
from mslearn.opsdb import OpsDB
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore, ScriptedRouter


def test_feedback_rates_from_seeded_rows(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    db.add_note_feedback("default", "k1", helpful=True, tags=[], comment="", guide_hash=None)
    db.add_note_feedback("default", "k2", helpful=False, tags=["wrong"], comment="", guide_hash=None)
    db.add_note_feedback("default", "k3", helpful=False, tags=["too_shallow", "repetitive"], comment="", guide_hash=None)
    db.add_note_feedback("default", "k4", helpful=True, tags=["off_topic"], comment="", guide_hash=None)

    ctx = PipelineContext(settings=None, db=db, router=ScriptedRouter([]), graph=InMemoryGraphStore())

    rates = feedback_rates(ctx)

    assert rates["total_rated"] == 4
    assert rates["helpful_rate"] == 0.5
    assert rates["wrong_rate"] == 0.25
    assert rates["shallow_rate"] == 0.25
    assert rates["repetitive_rate"] == 0.25
    assert rates["offtopic_rate"] == 0.25


def test_feedback_rates_zero_when_no_feedback(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    ctx = PipelineContext(settings=None, db=db, router=ScriptedRouter([]), graph=InMemoryGraphStore())

    rates = feedback_rates(ctx)

    assert rates == {
        "helpful_rate": 0.0,
        "shallow_rate": 0.0,
        "repetitive_rate": 0.0,
        "wrong_rate": 0.0,
        "offtopic_rate": 0.0,
        "total_rated": 0,
    }
