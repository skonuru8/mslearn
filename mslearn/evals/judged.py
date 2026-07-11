from __future__ import annotations

import json
import re

from mslearn.pipeline.guide_gen import generate_guide
from mslearn.pipeline.teaching import generate_teaching
from mslearn.prompts import get_prompt
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


def judge_guide(ctx, n: int = 5) -> dict[str, float]:
    """Judges the guide JSON path the user actually sees (generate_guide), not
    the legacy teach_concept markdown path judge_teaching scores.

    Degrades to a neutral all-zero result on ProviderBadOutputError from
    either guide generation or the rubric judge call — never crashes a run.
    """
    concepts = ctx.graph.curriculum() or ctx.graph.all_concepts()
    sample = concepts[:n]
    neutral = {"depth": 0.0, "non_redundancy": 0.0, "category_fit": 0.0, "grounding": 0.0}
    if not sample:
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
            violations = guide_grounding_violations(guide, concept_claims)
            response = ctx.router.complete(
                "evals",
                ModelRequest(
                    messages=[
                        ModelMessage(
                            role="user",
                            content=prompt.format(
                                concept_name=concept.get("name", ""),
                                concept_summary=concept.get("summary", ""),
                                guide=json.dumps(guide),
                            ),
                        )
                    ],
                    json_schema=_GUIDE_RUBRIC_SCHEMA,
                ),
            )
        except ProviderBadOutputError:
            continue
        parsed = response.parsed if isinstance(response.parsed, dict) else {}
        depth_scores.append(float(parsed.get("depth_1_5", 0)) / 5.0)
        redundancy_scores.append(float(parsed.get("redundancy_1_5", 0)) / 5.0)
        category_scores.append(float(parsed.get("category_fit_1_5", 0)) / 5.0)
        grounding = float(parsed.get("grounding_1_5", 0)) / 5.0
        if violations:
            grounding = max(0.0, grounding - 0.2 * len(violations))
        grounding_scores.append(grounding)

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
