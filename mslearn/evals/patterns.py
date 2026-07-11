from __future__ import annotations

import json

from mslearn.opsdb import DEFAULT_PROJECT_ID
from mslearn.prompts import get_prompt
from mslearn.providers.base import ModelMessage, ModelRequest, ProviderBadOutputError

_PATTERNS_SCHEMA = {
    "type": "object",
    "required": ["patterns"],
    "properties": {
        "patterns": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name", "symptom", "evidence", "suggested_target_metric"],
                "properties": {
                    "name": {"type": "string"},
                    "symptom": {"type": "string"},
                    "evidence": {"type": "string"},
                    "suggested_target_metric": {"type": "string"},
                },
            },
        }
    },
}


def mine_patterns(ctx, *, feedback_limit: int = 20, history_limit: int = 20) -> list[dict]:
    """Clusters recurring signal (recent negative feedback + rejected
    evolution proposals) into a small set of named failure patterns the
    proposer (evolve_propose) can target directly, instead of guessing from
    aggregate metrics alone.

    Best-effort: skips the model call entirely when there's no signal to
    mine, and degrades to [] on ProviderBadOutputError — pattern mining
    must never crash the evolve loop.
    """
    negative_feedback = ctx.db.recent_negative_feedback(DEFAULT_PROJECT_ID, limit=feedback_limit)
    rejected_history = [
        row for row in ctx.db.evolution_history(limit=history_limit) if not row.get("accepted")
    ]
    if not negative_feedback and not rejected_history:
        return []

    prompt = get_prompt(ctx.db, "patterns_summarize")
    try:
        response = ctx.router.complete(
            "evals",
            ModelRequest(
                messages=[
                    ModelMessage(
                        role="user",
                        content=prompt.format(
                            feedback=json.dumps(negative_feedback, indent=2),
                            rejected_history=json.dumps(rejected_history, indent=2),
                        ),
                    )
                ],
                json_schema=_PATTERNS_SCHEMA,
            ),
        )
    except ProviderBadOutputError:
        return []

    parsed = response.parsed if isinstance(response.parsed, dict) else {}
    patterns = parsed.get("patterns", [])
    if not isinstance(patterns, list):
        return []
    return [p for p in patterns if isinstance(p, dict)]
