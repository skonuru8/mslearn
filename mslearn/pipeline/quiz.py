from __future__ import annotations

import json
from typing import Any

from mslearn.prompts import get_prompt
from mslearn.providers.base import ModelMessage, ModelRequest, ProviderBadOutputError

_TRUSTED = frozenset({"trusted", "escalated", "image_observed"})

_QUESTION_SCHEMA = {
    "type": "object",
    "required": ["question", "expected_points"],
    "properties": {
        "question": {"type": "string"},
        "expected_points": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}

_GRADE_SCHEMA = {
    "type": "object",
    "required": ["correct", "score_0_100", "explanation"],
    "properties": {
        "correct": {"type": "boolean"},
        "score_0_100": {"type": "integer", "minimum": 0, "maximum": 100},
        "explanation": {"type": "string"},
    },
    "additionalProperties": False,
}


def next_concept(ctx, project_id: str = "default") -> str | None:
    concepts = ctx.graph.curriculum(project_id=project_id)
    if not concepts:
        concepts = ctx.graph.all_concepts(project_id=project_id)
    if not concepts:
        return None

    by_id = {row["concept_id"]: row for row in concepts}
    stats = {row["concept_id"]: row for row in ctx.db.quiz_stats(project_id=project_id)}
    failures = [
        row
        for row in stats.values()
        if row["concept_id"] in by_id and row.get("last_correct") is False
    ]
    if failures:
        struggle_text = _struggle_text(ctx.memory, project_id)
        failures.sort(
            key=lambda row: (
                _mentions_concept(struggle_text, by_id[row["concept_id"]]),
                row.get("last_ts") or 0.0,
            ),
            reverse=True,
        )
        return failures[0]["concept_id"]

    for concept in concepts:
        if stats.get(concept["concept_id"], {}).get("attempts", 0) == 0:
            return concept["concept_id"]
    return None


def _pending_key(session_id: str, concept_id: str) -> str:
    # Keyed by (session_id, concept_id): a single global `quiz:pending:{concept_id}`
    # slot would let concurrent /quiz/next calls for the same concept from
    # different sessions clobber each other's pending question.
    return f"quiz:pending:{session_id}:{concept_id}"


def generate_question(ctx, concept_id: str, session_id: str, project_id: str = "default") -> dict:
    concept = ctx.graph.get_concept(concept_id, project_id=project_id)
    if concept is None:
        raise KeyError(f"unknown concept {concept_id!r}")
    claims = _trusted_claims(ctx.graph.claims_in_concept(concept_id, project_id=project_id))
    response = ctx.router.complete(
        "interactive",
        ModelRequest(
            messages=[
                ModelMessage(
                    role="user",
                    content=_question_prompt(get_prompt(ctx.db, "quiz_question"), concept, claims),
                )
            ],
            json_schema=_QUESTION_SCHEMA,
            max_tokens=int(ctx.db.get_tunable("quiz.max_tokens")),
        ),
    )
    parsed = _require_dict(response.parsed, "quiz_question")
    question = str(parsed.get("question", "")).strip()
    expected_points = parsed.get("expected_points")
    if not question or not isinstance(expected_points, list) or not expected_points:
        raise ProviderBadOutputError("invalid quiz_question schema: question and expected_points required")
    result = {
        "question": question,
        "expected_points": [str(point) for point in expected_points],
    }
    ctx.db.set_setting(_pending_key(session_id, concept_id), json.dumps(result))
    return result


def grade_answer(
    ctx, concept_id: str, answer: str, session_id: str, project_id: str = "default"
) -> dict:
    concept = ctx.graph.get_concept(concept_id, project_id=project_id)
    if concept is None:
        raise KeyError(f"unknown concept {concept_id!r}")
    pending = _pending_question(ctx, session_id, concept_id)
    response = ctx.router.complete(
        "interactive",
        ModelRequest(
            messages=[
                ModelMessage(
                    role="user",
                    content=_grade_prompt(get_prompt(ctx.db, "quiz_grade"), pending, answer),
                )
            ],
            json_schema=_GRADE_SCHEMA,
            max_tokens=int(ctx.db.get_tunable("quiz.max_tokens")),
        ),
    )
    parsed = _require_dict(response.parsed, "quiz_grade")
    if not isinstance(parsed.get("correct"), bool):
        raise ProviderBadOutputError("invalid quiz_grade schema: correct must be boolean")
    score = parsed.get("score_0_100")
    if not isinstance(score, int) or score < 0 or score > 100:
        raise ProviderBadOutputError("invalid quiz_grade schema: score_0_100 must be 0..100")
    explanation = str(parsed.get("explanation", "")).strip()
    if not explanation:
        raise ProviderBadOutputError("invalid quiz_grade schema: explanation required")

    result = {"correct": parsed["correct"], "score_0_100": score, "explanation": explanation}
    ctx.db.record_quiz_result(concept_id, correct=result["correct"], score=score, project_id=project_id)
    # Delete the pending slot so a replayed /quiz/answer can't be graded
    # again against the same cached question (which would inflate quiz_stats).
    ctx.db.delete_setting(_pending_key(session_id, concept_id))
    if not result["correct"]:
        _record_struggle(ctx.memory, concept, pending, project_id)
    return result


def public_quiz_stats(ctx, project_id: str = "default") -> list[dict]:
    return [
        {key: value for key, value in row.items() if key != "last_ts"}
        for row in ctx.db.quiz_stats(project_id=project_id)
    ]


def _pending_question(ctx, session_id: str, concept_id: str) -> dict:
    raw = ctx.db.get_setting(_pending_key(session_id, concept_id))
    if raw is None:
        raise KeyError(f"no pending quiz question for {concept_id!r}")
    try:
        pending = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProviderBadOutputError(f"invalid pending quiz cache: {exc}") from exc
    if not isinstance(pending, dict):
        raise ProviderBadOutputError("invalid pending quiz cache: object required")
    return pending


def _question_prompt(base: str, concept: dict, claims: list[dict]) -> str:
    return base.format(
        concept_name=concept.get("name", ""),
        concept_summary=concept.get("summary", ""),
        claims=_format_claims(claims),
    )


def _grade_prompt(base: str, pending: dict, answer: str) -> str:
    return base.format(
        question=pending.get("question", ""),
        expected_points="\n".join(f"- {point}" for point in pending.get("expected_points", [])),
        answer=answer,
    )


def _trusted_claims(claims: list[dict]) -> list[dict]:
    return [c for c in claims if c.get("trust", "trusted") in _TRUSTED]


def _format_claims(claims: list[dict]) -> str:
    if not claims:
        return "(none)"
    return "\n".join(
        f"- [claim:{row['claim_id']}] ({row.get('stance', '')}) {row.get('text', '')}"
        for row in claims
    )


def _require_dict(parsed: Any, prompt_name: str) -> dict:
    if not isinstance(parsed, dict):
        raise ProviderBadOutputError(f"invalid {prompt_name} schema: object required")
    return parsed


def _record_struggle(memory, concept: dict, pending: dict, project_id: str = "default") -> None:
    if memory is None:
        return
    expected_points = pending.get("expected_points")
    missed = str(expected_points[0]) if expected_points else "expected reasoning point"
    memory.add(f"struggled with {concept.get('name', '')}: {missed}", "struggle", project_id=project_id)


def _struggle_text(memory, project_id: str = "default") -> str:
    if memory is None:
        return ""
    hits = memory.search("struggles", k=20, project_id=project_id)
    return "\n".join(_memory_text(item) for item in hits).lower()


def _memory_text(item) -> str:
    if isinstance(item, dict):
        return str(item.get("text", ""))
    return str(getattr(item, "text", ""))


def _mentions_concept(text: str, concept: dict) -> bool:
    return concept.get("name", "").lower() in text
