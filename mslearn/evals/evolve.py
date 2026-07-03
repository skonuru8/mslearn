from __future__ import annotations

import json
import re
from dataclasses import replace

from mslearn.evals.gates import GATES
from mslearn.evals.judged import provenance_violation_count
from mslearn.evals.metrics import compute_component_metrics
from mslearn.evals.runner import run_eval
from mslearn.prompts import PROMPTS, get_prompt
from mslearn.providers.base import ModelMessage, ModelRequest

_PROVENANCE_PREFIX = "provenance."


def _is_provenance_adjacent(metric: str | None) -> bool:
    return bool(metric) and str(metric).startswith(_PROVENANCE_PREFIX)

TUNABLE_BOUNDS: dict[str, tuple[float, float]] = {
    "trust.quote_threshold": (70.0, 98.0),
    "trust.embed_sim_threshold": (0.1, 0.8),
    "extract.max_attempts": (1.0, 4.0),
    "synth.candidate_k": (3.0, 20.0),
    "synth.similarity_floor": (0.5, 0.95),
    "monitor.failure_rate_threshold": (0.2, 0.9),
    "monitor.min_chunks": (5.0, 50.0),
}

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


class OverlayOpsDB:
    """Shadow-eval overlay: tunable/prompt overrides without mutating live store."""

    def __init__(
        self,
        base,
        *,
        tunables: dict[str, float] | None = None,
        prompts: dict[str, str] | None = None,
    ):
        self._base = base
        self._tunables = dict(tunables or {})
        self._prompts = dict(prompts or {})

    def get_tunable(self, key: str) -> float:
        if key in self._tunables:
            return float(self._tunables[key])
        return self._base.get_tunable(key)

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        if key in self._prompts:
            return self._prompts[key]
        return self._base.get_setting(key, default)

    def __getattr__(self, name: str):
        return getattr(self._base, name)


def _lower_is_better(metric: str) -> bool:
    op, _threshold = GATES.get(metric, (">=", 0.0))
    return op in ("<=", "==")


def _improved(metric: str, after: float, before: float) -> bool:
    if _lower_is_better(metric):
        return after < before
    return after > before


def _not_regressed(metric: str, after: float, before: float) -> bool:
    if _lower_is_better(metric):
        return after <= before
    return after >= before


def required_placeholders(name: str) -> set[str]:
    base = PROMPTS.get(name, "")
    return set(_PLACEHOLDER_RE.findall(base))


def ctx_with_overrides(ctx, *, tunables: dict[str, float] | None = None, prompts: dict[str, str] | None = None):
    overlay_db = OverlayOpsDB(ctx.db, tunables=tunables, prompts=prompts)
    return replace(ctx, db=overlay_db)


def validate_proposal(proposal: dict) -> str | None:
    kind = proposal.get("kind")
    key = proposal.get("key")
    if kind == "tunable":
        if key not in TUNABLE_BOUNDS:
            return f"unknown tunable {key!r}"
        low, high = TUNABLE_BOUNDS[key]
        try:
            value = float(proposal.get("value"))
        except (TypeError, ValueError):
            return f"invalid or missing value {proposal.get('value')!r}"
        if not (low <= value <= high):
            return f"value {value} outside bounds [{low}, {high}]"
        return None
    if kind == "prompt":
        prompt_name = str(key).removeprefix("prompt:")
        if prompt_name not in PROMPTS:
            return f"unknown prompt {prompt_name!r}"
        new_prompt = str(proposal.get("new_prompt", ""))
        required = required_placeholders(prompt_name)
        missing = required - set(_PLACEHOLDER_RE.findall(new_prompt))
        if missing:
            return f"missing placeholders {sorted(missing)}"
        return None
    return f"unknown proposal kind {kind!r}"


def evolve_once(ctx) -> dict:
    latest = ctx.db.latest_eval_run()
    if latest is None:
        run_eval(ctx, offline=True)

    baseline = compute_component_metrics(ctx)
    # Real judged provenance count, not a hardcoded 0.0 — otherwise the
    # provenance gate below is vacuously true and a proposal that increases
    # provenance violations would auto-apply. Judged runs cost an LLM call
    # per concept, so we measure it once for the baseline; see below for why
    # we don't re-measure it per shadow proposal.
    baseline["provenance.violations"] = float(provenance_violation_count(ctx))

    prompt = get_prompt(ctx.db, "evolve_propose")
    tunable_snapshot = {key: ctx.db.get_tunable(key) for key in TUNABLE_BOUNDS}
    audit_rows = ctx.db.tunable_history(next(iter(TUNABLE_BOUNDS)))
    response = ctx.router.complete(
        "evals",
        ModelRequest(
            messages=[
                ModelMessage(
                    role="user",
                    content=prompt.format(
                        metrics=json.dumps(baseline, indent=2),
                        tunables=json.dumps(tunable_snapshot, indent=2),
                        audit=json.dumps(audit_rows[:5], indent=2),
                    ),
                )
            ],
            json_schema={
                "type": "object",
                "required": ["proposals"],
                "properties": {
                    "proposals": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["kind", "key", "targets_metric", "why"],
                        },
                    }
                },
            },
        ),
    )
    parsed = response.parsed if isinstance(response.parsed, dict) else {}
    proposals = parsed.get("proposals", [])[:3]

    accepted = []
    rejected = []
    for proposal in proposals:
        if not isinstance(proposal, dict):
            continue
        error = validate_proposal(proposal)
        if error:
            rejected.append({"proposal": proposal, "reason": error})
            continue
        target = proposal.get("targets_metric")
        if _is_provenance_adjacent(target):
            # Re-measuring judged provenance per proposal costs an LLM call
            # per concept per shadow run — too expensive to do for every
            # proposal. Rather than fake a shadow value (which is exactly
            # the vacuous-pass bug this replaces), refuse to auto-apply and
            # record the gate as not evaluated.
            run_id = ctx.db.create_evolution_run(
                proposal_json=json.dumps(proposal),
                shadow_before_json=json.dumps(baseline),
                shadow_after_json=json.dumps({"provenance.violations": "not evaluated"}),
                accepted=False,
                reason=str(proposal.get("why", "")),
            )
            rejected.append(
                {
                    "proposal": proposal,
                    "run_id": run_id,
                    "reason": "provenance gate not evaluated for provenance-adjacent"
                              " proposals — refusing to auto-apply",
                }
            )
            continue
        if proposal.get("kind") == "tunable":
            shadow_ctx = ctx_with_overrides(
                ctx, tunables={proposal["key"]: float(proposal["value"])}
            )
        else:
            prompt_name = str(proposal["key"]).removeprefix("prompt:")
            shadow_ctx = ctx_with_overrides(
                ctx, prompts={f"prompt:{prompt_name}": str(proposal["new_prompt"])}
            )
        shadow_metrics = compute_component_metrics(shadow_ctx)
        # Not re-measured for this (non-provenance-targeting) proposal;
        # carry the real baseline forward instead of a fabricated 0.0 so the
        # shadow-vs-baseline comparison below is an honest "assumed
        # unchanged", not a silently-always-passing placeholder.
        shadow_metrics["provenance.violations"] = baseline["provenance.violations"]
        target_improved = _improved(
            target, shadow_metrics.get(target, 0.0), baseline.get(target, 0.0)
        )
        gates_ok = all(
            _not_regressed(metric, shadow_metrics.get(metric, 0.0), baseline[metric])
            for metric in GATES
            if metric in baseline
        )
        run_id = ctx.db.create_evolution_run(
            proposal_json=json.dumps(proposal),
            shadow_before_json=json.dumps(baseline),
            shadow_after_json=json.dumps(shadow_metrics),
            accepted=False,
            reason=str(proposal.get("why", "")),
        )
        if target_improved and gates_ok:
            if proposal.get("kind") == "tunable":
                ctx.db.set_tunable(
                    proposal["key"],
                    float(proposal["value"]),
                    reason=f"evolve run {run_id}: {proposal.get('why', '')}",
                )
            else:
                prompt_name = str(proposal["key"]).removeprefix("prompt:")
                ctx.db.set_setting(f"prompt:{prompt_name}", str(proposal["new_prompt"]))
            ctx.db.set_evolution_run_accepted(run_id, True)
            accepted.append({"proposal": proposal, "run_id": run_id})
        else:
            rejected.append(
                {
                    "proposal": proposal,
                    "run_id": run_id,
                    "reason": "shadow regression or no improvement",
                }
            )
    return {"accepted": accepted, "rejected": rejected, "baseline": baseline}


def rollback_tunable(db, key: str) -> float:
    history = db.tunable_history(key)
    if len(history) < 2:
        raise ValueError(f"no prior value to rollback for {key!r}")
    previous = history[1]["value"]
    db.set_tunable(key, float(previous), reason="rollback via api")
    return float(previous)
