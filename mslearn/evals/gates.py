from __future__ import annotations

GATES: dict[str, tuple[str, float]] = {
    "extraction.precision": (">=", 0.90),
    "extraction.recall": (">=", 0.85),
    "grounding.false_accept": ("<=", 0.02),
    "clustering.f1": (">=", 0.80),
    "tension.accuracy": (">=", 0.75),
    "schema.validity": (">=", 0.99),
    "provenance.violations": ("==", 0.0),
    "guide.grounding": (">=", 0.98),
    "feedback.wrong_rate": ("<=", 0.05),
    "feedback.helpful_rate": (">=", 0.70),
}

# Feedback gates are noisy on a handful of ratings — they bind only once
# there's enough signal to trust them (mirrors the provenance gate's
# "not evaluated" pattern for proposals that can't be cheaply re-measured).
MIN_FEEDBACK_SAMPLES = 10
SAMPLE_GATED = {"feedback.helpful_rate", "feedback.wrong_rate"}


def gate_enforced(metric: str, metrics: dict) -> bool:
    if metric in SAMPLE_GATED:
        return metrics.get("feedback.total_rated", 0) >= MIN_FEEDBACK_SAMPLES
    return True


def evaluate_gate(metric: str, value: float) -> tuple[float | None, bool]:
    if metric not in GATES:
        return None, True
    op, threshold = GATES[metric]
    if op == ">=":
        passed = value >= threshold
    elif op == "<=":
        passed = value <= threshold
    elif op == "==":
        passed = value == threshold
    else:
        raise ValueError(f"unknown gate operator {op!r}")
    return threshold, passed


def evaluate_all(metrics: dict[str, float]) -> dict[str, bool]:
    results: dict[str, bool] = {}
    for metric, (op, threshold) in GATES.items():
        if metric not in metrics:
            results[metric] = False
            continue
        value = metrics[metric]
        if op == ">=":
            results[metric] = value >= threshold
        elif op == "<=":
            results[metric] = value <= threshold
        else:
            results[metric] = value == threshold
    return results
