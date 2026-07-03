from __future__ import annotations

GATES: dict[str, tuple[str, float]] = {
    "extraction.precision": (">=", 0.90),
    "extraction.recall": (">=", 0.85),
    "grounding.false_accept": ("<=", 0.02),
    "clustering.f1": (">=", 0.80),
    "tension.accuracy": (">=", 0.75),
    "schema.validity": (">=", 0.99),
    "provenance.violations": ("==", 0.0),
}


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
