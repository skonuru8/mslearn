from __future__ import annotations

from mslearn.pipeline.teaching import _format_memory_hints, _trusted_claims
from mslearn.prompts import domain_guidance, get_domain_profile, get_prompt
from mslearn.providers.base import ModelMessage, ModelRequest

FLASHCARD_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "front": {"type": "string"},
                    "back": {"type": "string"},
                    "claims": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["front", "back", "claims"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["cards"],
    "additionalProperties": False,
}

SELFCHECK_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "answer": {"type": "string"},
                    "claims": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["question", "answer", "claims"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["checks"],
    "additionalProperties": False,
}


def _format_claims(claims: list[dict]) -> str:
    return "\n".join(
        f"- id={c['claim_id']} kind={c.get('kind', 'claim')} stance={c.get('stance', '')}: {c['text']}"
        for c in claims
    ) or "(none)"


def _generate(ctx, concept_id, prompt_name, schema, response_key, count, project_id) -> list[dict]:
    concept = ctx.graph.get_concept(concept_id, project_id=project_id)
    if concept is None:
        raise KeyError(f"unknown concept {concept_id!r}")
    claims = _trusted_claims(ctx.graph.claims_in_concept(concept_id, project_id=project_id))
    profile = get_domain_profile(ctx.db, project_id)
    prompt = get_prompt(ctx.db, prompt_name).format(
        domain_guidance=domain_guidance(profile),
        concept_name=concept.get("name", ""),
        concept_summary=concept.get("summary", ""),
        claims=_format_claims(claims),
        memory_hints=_format_memory_hints(ctx.memory, concept.get("name", ""), project_id),
    )
    resp = ctx.router.complete("interactive", ModelRequest(
        messages=[ModelMessage(role="user", content=prompt)],
        json_schema=schema,
        max_tokens=int(ctx.db.get_tunable("guide.max_tokens")),
    ))
    items = (resp.parsed or {}).get(response_key, [])
    grounded = [item for item in items if item.get("claims")]
    return grounded[:count]


def make_flashcards(ctx, concept_id: str, count: int, project_id: str = "default") -> list[dict]:
    return _generate(ctx, concept_id, "flashcards", FLASHCARD_SCHEMA, "cards", count, project_id)


def make_selfcheck(ctx, concept_id: str, count: int, project_id: str = "default") -> list[dict]:
    return _generate(ctx, concept_id, "selfcheck", SELFCHECK_SCHEMA, "checks", count, project_id)
