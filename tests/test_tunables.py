import pytest

from mslearn.opsdb import TUNABLE_DEFAULTS, OpsDB


def test_defaults_registry_exact():
    assert TUNABLE_DEFAULTS == {
        "trust.quote_threshold": 90.0,
        "trust.embed_sim_threshold": 0.35,
        "extract.max_attempts": 2.0,
        "extract.max_claims": 15.0,
        "extract.max_tokens": 4096.0,
        "monitor.failure_rate_threshold": 0.5,
        "monitor.min_chunks": 10.0,
        "synth.candidate_k": 8.0,
        "synth.similarity_floor": 0.75,
        "synth.concurrency": 8.0,
        "synth.max_tokens": 8192.0,
        "chat.max_tokens": 8192.0,
        "quiz.max_tokens": 8192.0,
        "teach.max_tokens": 8192.0,
        "guide.max_tokens": 8192.0,
        "evolve.max_tokens": 8192.0,
        "image.max_tokens": 4096.0,
    }


def test_get_falls_back_to_default(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    assert db.get_tunable("trust.quote_threshold") == 90.0
    with pytest.raises(KeyError):
        db.get_tunable("nope.unknown")


def test_set_overrides_and_audits(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    db.set_tunable("trust.quote_threshold", 85.0, reason="eval run 7 improved recall")
    db.set_tunable("trust.quote_threshold", 88.0, reason="eval run 9")
    assert db.get_tunable("trust.quote_threshold") == 88.0
    history = db.tunable_history("trust.quote_threshold")
    assert len(history) == 2
    assert history[0]["value"] == 88.0 and "run 9" in history[0]["reason"]
