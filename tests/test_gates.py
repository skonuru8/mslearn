from mslearn.evals.gates import GATES, MIN_FEEDBACK_SAMPLES, gate_enforced


def test_gate_enforced_below_sample_floor_not_enforced():
    metrics = {"feedback.total_rated": MIN_FEEDBACK_SAMPLES - 1}
    assert gate_enforced("feedback.helpful_rate", metrics) is False
    assert gate_enforced("feedback.wrong_rate", metrics) is False


def test_gate_enforced_at_or_above_sample_floor_is_enforced():
    metrics = {"feedback.total_rated": MIN_FEEDBACK_SAMPLES}
    assert gate_enforced("feedback.helpful_rate", metrics) is True
    assert gate_enforced("feedback.wrong_rate", metrics) is True


def test_gate_enforced_non_sample_gated_metric_always_enforced():
    assert gate_enforced("extraction.precision", {}) is True
    assert gate_enforced("guide.grounding", {}) is True


def test_new_gate_thresholds_present():
    assert GATES["guide.grounding"] == (">=", 0.98)
    assert GATES["feedback.wrong_rate"] == ("<=", 0.05)
    assert GATES["feedback.helpful_rate"] == (">=", 0.70)
