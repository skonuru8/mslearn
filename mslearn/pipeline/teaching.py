from __future__ import annotations

from mslearn.prompts import domain_guidance, get_domain_profile, get_prompt
from mslearn.providers.base import ModelMessage, ModelRequest

_TRUSTED = frozenset({"trusted", "escalated"})


class TeachingError(Exception):
    """Teaching generation failed its required output contract."""


def generate_teaching(ctx, concept_id: str, force: bool = False, project_id: str = "default") -> str:
    graph = ctx.graph
    concept = graph.get_concept(concept_id, project_id=project_id)
    if concept is None:
        raise TeachingError(f"unknown concept {concept_id!r}")

    cached = concept.get("teach_md") or ""
    if cached and not force and not concept.get("dirty", False):
        return cached

    prompt = _build_prompt(
        ctx,
        concept,
        _trusted_claims(graph.claims_in_concept(concept_id, project_id=project_id)),
        project_id=project_id,
    )
    markdown = _complete_teaching(ctx, prompt)
    conflicts = graph.conflicts_in_concept(concept_id, project_id=project_id)
    if conflicts and "## Where sources disagree" not in markdown:
        corrective = (
            f"{prompt}\n\n"
            "Your previous response omitted a required section. You must include "
            "`## Where sources disagree` and cite each side with [claim:<id>] citations."
        )
        markdown = _complete_teaching(ctx, corrective)
        if "## Where sources disagree" not in markdown:
            raise TeachingError("teaching omitted required conflict section")

    graph.set_concept_teaching(concept_id, markdown, project_id=project_id)
    graph.mark_concept_dirty(concept_id, False, project_id=project_id)
    return markdown


def _trusted_claims(claims: list[dict]) -> list[dict]:
    return [c for c in claims if c.get("trust", "trusted") in _TRUSTED]


def _complete_teaching(ctx, content: str) -> str:
    response = ctx.router.complete(
        "synthesis",
        ModelRequest(
            messages=[ModelMessage(role="user", content=content)],
            max_tokens=int(ctx.db.get_tunable("teach.max_tokens")),
        ),
    )
    return response.text


def _build_prompt(ctx, concept: dict, claims: list[dict], *, project_id: str = "default") -> str:
    prompt = get_prompt(ctx.db, "teach_concept")
    profile = get_domain_profile(ctx.db, project_id)
    claim_ids = [row["claim_id"] for row in claims]
    citations = ctx.graph.citations_for_claims(claim_ids, project_id=project_id)
    return prompt.format(
        domain_guidance=domain_guidance(profile),
        concept_name=concept.get("name", ""),
        concept_summary=concept.get("summary", ""),
        claims=_format_claims(claims),
        conflicts=_format_conflicts(
            ctx.graph.conflicts_in_concept(concept["concept_id"], project_id=project_id)
        ),
        memory_hints=_format_memory_hints(ctx.memory, concept.get("name", ""), project_id),
    ) + _format_citations(citations)


def _format_claims(claims: list[dict]) -> str:
    if not claims:
        return "(none)"
    lines = []
    for row in claims:
        lines.append(
            f"- [claim:{row['claim_id']}] ({row.get('stance', '')}, "
            f"source {row.get('source_id', '')}) {row.get('text', '')} "
            f'-- quote: "{row.get("quote", "")}"'
        )
    return "\n".join(lines)


def _format_conflicts(conflicts: list[dict]) -> str:
    if not conflicts:
        return "(none)"
    return "\n".join(
        "- [claim:{claim_a}] vs [claim:{claim_b}] ({classification}): {rationale}".format(
            claim_a=row.get("claim_a", ""),
            claim_b=row.get("claim_b", ""),
            classification=row.get("classification", ""),
            rationale=row.get("rationale", ""),
        )
        for row in conflicts
    )


def _format_memory_hints(memory, concept_name: str, project_id: str = "default") -> str:
    if memory is None or not concept_name:
        return "(none)"
    hits = memory.search(concept_name, k=5, project_id=project_id)
    if not hits:
        return "(none)"
    return "\n".join(f"- PERSONALIZATION ONLY: {_memory_text(item)}" for item in hits)


def _memory_text(item) -> str:
    if isinstance(item, dict):
        return str(item.get("text", ""))
    return str(getattr(item, "text", ""))


def _format_citations(citations: list[dict]) -> str:
    if not citations:
        return ""
    lines = ["", "", "Citation locators:"]
    for row in citations:
        locator = ", ".join(
            f"{key}={value}"
            for key, value in row.items()
            if key != "claim_id" and value is not None
        )
        lines.append(f"- [claim:{row['claim_id']}] {locator}")
    return "\n".join(lines)
