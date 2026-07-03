from mslearn.evals.gates import GATES, evaluate_all, evaluate_gate


def test_gate_thresholds():
    _, passed = evaluate_gate("extraction.precision", 0.90)
    assert passed
    _, failed = evaluate_gate("extraction.precision", 0.89)
    assert not failed


def test_evaluate_all_fails_missing():
    results = evaluate_all({"extraction.precision": 0.95})
    assert results["extraction.recall"] is False
    assert len(results) == len(GATES)
