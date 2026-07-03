from __future__ import annotations

import subprocess
import sys
from typing import Callable

from mslearn.evals.gates import GATES, evaluate_gate
from mslearn.evals.judged import provenance_violation_count
from mslearn.evals.metrics import compute_component_metrics


def _git_sha() -> str | None:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True)
            .strip()
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def run_eval(
    ctx,
    *,
    component: str | None = None,
    offline: bool = False,
    metric_fn: Callable | None = None,
) -> dict:
    compute = metric_fn or compute_component_metrics
    metrics = compute(ctx)
    if not offline:
        metrics["provenance.violations"] = float(provenance_violation_count(ctx))
    else:
        metrics.setdefault("provenance.violations", 0.0)

    relevant_gates = GATES
    if component:
        # Previously this only trimmed `metrics` for display, while `k in GATES`
        # let every gated metric back in and the gate loop below iterated all
        # of GATES unconditionally — so `--component extraction` still failed
        # the run over an unrelated clustering/tension regression. Scope both
        # the retained metrics AND the gates actually evaluated to this
        # component's prefix.
        prefix = f"{component}."
        metrics = {k: v for k, v in metrics.items() if k.startswith(prefix)}
        relevant_gates = {k: v for k, v in GATES.items() if k.startswith(prefix)}

    gate_results = {}
    all_passed = True
    for metric in relevant_gates:
        if metric not in metrics:
            gate_results[metric] = False
            all_passed = False
            continue
        threshold, passed = evaluate_gate(metric, metrics[metric])
        gate_results[metric] = passed
        if not passed:
            all_passed = False

    run_id = ctx.db.create_eval_run(
        kind=component or "full",
        git_sha=_git_sha(),
        passed=all_passed,
    )
    for metric, value in metrics.items():
        threshold = GATES.get(metric, (None, None))[1] if metric in GATES else None
        _, passed = evaluate_gate(metric, value) if metric in GATES else (None, True)
        ctx.db.add_eval_metric(run_id, metric, value, threshold, passed)

    _print_table(metrics, gate_results)
    return {
        "run_id": run_id,
        "passed": all_passed,
        "metrics": metrics,
        "gates": gate_results,
    }


def _print_table(metrics: dict[str, float], gate_results: dict[str, bool]) -> None:
    print(f"{'metric':<28} {'value':>10} {'gate':>10} {'pass':>6}")
    for metric in sorted(set(metrics) | set(GATES)):
        value = metrics.get(metric)
        if value is None:
            continue
        op, threshold = GATES.get(metric, ("", None))
        gate_str = f"{op} {threshold}" if threshold is not None else "-"
        passed = gate_results.get(metric, True)
        print(f"{metric:<28} {value:10.4f} {gate_str:>10} {str(passed):>6}")


def main(argv: list[str] | None = None) -> int:
    from mslearn.worker.context import build_default_context

    argv = list(sys.argv[1:] if argv is None else argv)
    component = None
    offline = False
    if "--component" in argv:
        component = argv[argv.index("--component") + 1]
    if "--offline" in argv:
        offline = True
    ctx = build_default_context()
    result = run_eval(ctx, component=component, offline=offline)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
