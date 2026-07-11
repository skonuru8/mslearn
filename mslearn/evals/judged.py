from __future__ import annotations

import json
import re

from mslearn.evals.golden import load_golden
from mslearn.pipeline.guide import GUIDE_SCHEMA, drop_ungrounded, parse_guide
from mslearn.pipeline.guide_gen import _format_claims, generate_guide
from mslearn.pipeline.teaching import generate_teaching
from mslearn.prompts import domain_guidance, get_domain_profile, get_prompt
from mslearn.providers.base import ModelMessage, ModelRequest, ProviderBadOutputError

_CLAIM_RE = re.compile(r"\[claim:([^\]\s]+)\]")
_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")


def provenance_citations(markdown: str, ctx, *, concept_id: str | None = None) -> list[str]:
    violations: list[str] = []
    known_claims = set(getattr(ctx.graph, "claims", {}).keys())
    if concept_id and hasattr(ctx.graph, "claims_in_concept"):
        concept_claims = {c["claim_id"] for c in ctx.graph.claims_in_concept(concept_id)}
    else:
        concept_claims = None

    for claim_id in _CLAIM_RE.findall(markdown):
        if claim_id not in known_claims:
            violations.append(f"unknown claim id {claim_id!r}")
        elif concept_claims is not None and claim_id not in concept_claims:
            violations.append(f"claim {claim_id!r} not in concept {concept_id!r}")

    in_worked_example = False
    for block in _PARAGRAPH_SPLIT.split(markdown):
        stripped = block.strip()
        if not stripped or stripped.startswith("#"):
            if stripped.startswith("## Worked example"):
                in_worked_example = True
            elif stripped.startswith("## "):
                in_worked_example = False
            continue
        if in_worked_example:
            continue
        if not _CLAIM_RE.search(stripped):
            violations.append(f"uncited paragraph: {stripped[:80]!r}")
    return violations


def judge_teaching(ctx, n: int = 5) -> dict[str, float]:
    concepts = ctx.graph.curriculum() or ctx.graph.all_concepts()
    sample = concepts[:n]
    if not sample:
        return {"clarity": 0.0, "grounding": 0.0, "tension_handled_rate": 0.0}
    clarity_scores: list[float] = []
    grounding_scores: list[float] = []
    tension_ok = 0
    tension_total = 0
    prompt = get_prompt(ctx.db, "rubric_teach")
    schema = {
        "type": "object",
        "required": ["clarity_1_5", "grounding_1_5", "tension_handled"],
        "properties": {
            "clarity_1_5": {"type": "integer", "minimum": 1, "maximum": 5},
            "grounding_1_5": {"type": "integer", "minimum": 1, "maximum": 5},
            "tension_handled": {"type": "boolean"},
        },
        "additionalProperties": False,
    }
    for concept in sample:
        concept_id = concept["concept_id"]
        markdown = generate_teaching(ctx, concept_id)
        conflicts = ctx.graph.conflicts_in_concept(concept_id)
        if conflicts:
            tension_total += 1
        response = ctx.router.complete(
            "evals",
            ModelRequest(
                messages=[
                    ModelMessage(
                        role="user",
                        content=prompt.format(markdown=markdown, concept_name=concept.get("name", "")),
                    )
                ],
                json_schema=schema,
            ),
        )
        parsed = response.parsed if isinstance(response.parsed, dict) else {}
        clarity_scores.append(float(parsed.get("clarity_1_5", 0)))
        grounding_scores.append(float(parsed.get("grounding_1_5", 0)))
        if conflicts and parsed.get("tension_handled"):
            tension_ok += 1
    return {
        "clarity": sum(clarity_scores) / len(clarity_scores),
        "grounding": sum(grounding_scores) / len(grounding_scores),
        "tension_handled_rate": (tension_ok / tension_total) if tension_total else 1.0,
    }


def guide_grounding_violations(guide: dict, concept_claim_ids: set[str]) -> list[str]:
    """Structural grounding check for guide JSON: flag section items that cite
    a claim id outside the concept, or that cite no claims at all."""
    violations: list[str] = []
    for section in guide.get("sections", []):
        for item in section.get("items", []):
            claims = item.get("claims") or []
            if not claims:
                violations.append(
                    f"empty claims on item {item.get('text', '')[:60]!r} in section"
                    f" {section.get('id', '')!r}"
                )
                continue
            for claim_id in claims:
                if claim_id not in concept_claim_ids:
                    violations.append(
                        f"claim {claim_id!r} not in concept (item {item.get('text', '')[:60]!r})"
                    )
    return violations


_GUIDE_RUBRIC_SCHEMA = {
    "type": "object",
    "required": ["depth_1_5", "redundancy_1_5", "category_fit_1_5", "grounding_1_5"],
    "properties": {
        "depth_1_5": {"type": "integer", "minimum": 1, "maximum": 5},
        "redundancy_1_5": {"type": "integer", "minimum": 1, "maximum": 5},
        "category_fit_1_5": {"type": "integer", "minimum": 1, "maximum": 5},
        "grounding_1_5": {"type": "integer", "minimum": 1, "maximum": 5},
    },
    "additionalProperties": False,
}


def _rubric_score_guide(
    ctx, prompt: str, concept_name: str, concept_summary: str, guide: dict, concept_claim_ids: set[str]
) -> dict[str, float]:
    """Scores one guide dict against the rubric_guide judge, folding in the
    structural grounding penalty from guide_grounding_violations."""
    violations = guide_grounding_violations(guide, concept_claim_ids)
    response = ctx.router.complete(
        "evals",
        ModelRequest(
            messages=[
                ModelMessage(
                    role="user",
                    content=prompt.format(
                        concept_name=concept_name,
                        concept_summary=concept_summary,
                        guide=json.dumps(guide),
                    ),
                )
            ],
            json_schema=_GUIDE_RUBRIC_SCHEMA,
        ),
    )
    parsed = response.parsed if isinstance(response.parsed, dict) else {}
    grounding = float(parsed.get("grounding_1_5", 0)) / 5.0
    if violations:
        grounding = max(0.0, grounding - 0.2 * len(violations))
    return {
        "depth": float(parsed.get("depth_1_5", 0)) / 5.0,
        "non_redundancy": float(parsed.get("redundancy_1_5", 0)) / 5.0,
        "category_fit": float(parsed.get("category_fit_1_5", 0)) / 5.0,
        "grounding": grounding,
    }


def _generate_guide_for_fixture(ctx, fixture) -> dict:
    """Generates a fresh guide from a frozen `guide` golden fixture's claims,
    bypassing generate_guide's live-graph lookup and cache (the fixture's
    concept_id need not exist in the current graph)."""
    profile = get_domain_profile(ctx.db)
    prompt = get_prompt(ctx.db, "guide").format(
        domain_guidance=domain_guidance(profile),
        concept_name=fixture.concept_name,
        concept_summary=fixture.concept_summary,
        claims=_format_claims(fixture.claims),
        memory_hints="(none)",
    )
    resp = ctx.router.complete(
        "interactive",
        ModelRequest(
            messages=[ModelMessage(role="user", content=prompt)],
            json_schema=GUIDE_SCHEMA,
            max_tokens=int(ctx.db.get_tunable("guide.max_tokens")),
        ),
    )
    guide = drop_ungrounded(
        parse_guide(
            {**(resp.parsed or {}), "concept_id": fixture.concept_id, "title": fixture.concept_name}
        )
    )
    return guide.model_dump()


def judge_guide(ctx, n: int = 5) -> dict[str, float]:
    """Judges the guide JSON path the user actually sees (generate_guide), not
    the legacy teach_concept markdown path judge_teaching scores. Also scores
    every active `guide` golden fixture (concepts ratcheted in from negative
    feedback via promote_feedback_to_golden) so a fixed regression stays
    caught instead of drifting back once the live sample rotates past it.

    Degrades to a neutral all-zero result on ProviderBadOutputError from
    either guide generation or the rubric judge call — never crashes a run.
    """
    concepts = ctx.graph.curriculum() or ctx.graph.all_concepts()
    sample = concepts[: n]
    fixtures = load_golden("guide", active_only=True)
    neutral = {"depth": 0.0, "non_redundancy": 0.0, "category_fit": 0.0, "grounding": 0.0}
    if not sample and not fixtures:
        return neutral

    depth_scores: list[float] = []
    redundancy_scores: list[float] = []
    category_scores: list[float] = []
    grounding_scores: list[float] = []
    prompt = get_prompt(ctx.db, "rubric_guide")

    for concept in sample:
        concept_id = concept["concept_id"]
        try:
            guide, _cached = generate_guide(ctx, concept_id)
            concept_claims = {
                c["claim_id"] for c in ctx.graph.claims_in_concept(concept_id)
            }
            scores = _rubric_score_guide(
                ctx, prompt, concept.get("name", ""), concept.get("summary", ""), guide, concept_claims
            )
        except ProviderBadOutputError:
            continue
        depth_scores.append(scores["depth"])
        redundancy_scores.append(scores["non_redundancy"])
        category_scores.append(scores["category_fit"])
        grounding_scores.append(scores["grounding"])

    for fixture in fixtures:
        try:
            guide = _generate_guide_for_fixture(ctx, fixture)
            concept_claims = {c["claim_id"] for c in fixture.claims}
            scores = _rubric_score_guide(
                ctx, prompt, fixture.concept_name, fixture.concept_summary, guide, concept_claims
            )
        except ProviderBadOutputError:
            continue
        depth_scores.append(scores["depth"])
        redundancy_scores.append(scores["non_redundancy"])
        category_scores.append(scores["category_fit"])
        grounding_scores.append(scores["grounding"])

    if not depth_scores:
        return neutral
    return {
        "depth": sum(depth_scores) / len(depth_scores),
        "non_redundancy": sum(redundancy_scores) / len(redundancy_scores),
        "category_fit": sum(category_scores) / len(category_scores),
        "grounding": sum(grounding_scores) / len(grounding_scores),
    }


def judge_provenance_spot(ctx, markdown: str, claims_text: str) -> dict:
    prompt = get_prompt(ctx.db, "provenance_check")
    schema = {
        "type": "object",
        "required": ["unsupported_fact", "offending_sentence"],
        "properties": {
            "unsupported_fact": {"type": "boolean"},
            "offending_sentence": {"type": "string"},
        },
        "additionalProperties": False,
    }
    response = ctx.router.complete(
        "evals",
        ModelRequest(
            messages=[
                ModelMessage(
                    role="user",
                    content=prompt.format(markdown=markdown, claims=claims_text),
                )
            ],
            json_schema=schema,
        ),
    )
    return response.parsed if isinstance(response.parsed, dict) else {}


def provenance_violation_count(ctx, *, n: int = 5) -> int:
    concepts = ctx.graph.curriculum() or ctx.graph.all_concepts()
    total = 0
    for concept in concepts[:n]:
        concept_id = concept["concept_id"]
        try:
            markdown = generate_teaching(ctx, concept_id)
        except Exception:
            continue
        total += len(provenance_citations(markdown, ctx, concept_id=concept_id))
        claims = ctx.graph.claims_in_concept(concept_id)
        claims_text = "\n".join(f"- {c['claim_id']}: {c['text']}" for c in claims)
        spot = judge_provenance_spot(ctx, markdown, claims_text)
        if spot.get("unsupported_fact"):
            total += 1
    return total
