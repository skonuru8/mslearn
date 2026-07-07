from __future__ import annotations
import json
from mslearn.pipeline.guide import GUIDE_SCHEMA, drop_uncited, parse_guide
from mslearn.pipeline.teaching import _format_memory_hints, _trusted_claims
from mslearn.prompts import domain_guidance, get_domain_profile, get_prompt
from mslearn.providers.base import ModelMessage, ModelRequest


def _format_claims(claims):
    return "\n".join(
        f"- id={c['claim_id']} kind={c.get('kind','claim')} stance={c.get('stance','')}: {c['text']}"
        for c in claims) or "(none)"


def _disagreements(graph, concept_id, project_id):
    out = []
    for r in graph.conflicts_in_concept(concept_id, project_id=project_id):
        out.append({
            "summary": r.get("rationale", ""),
            "classification": r.get("classification", ""),
            "a": {"label": f"claim {r.get('claim_a','')}", "text": r.get("text_a", ""), "claims": [r.get("claim_a","")]},
            "b": {"label": f"claim {r.get('claim_b','')}", "text": r.get("text_b", ""), "claims": [r.get("claim_b","")]},
        })
    return out


def generate_guide(ctx, concept_id, force=False, project_id="default") -> tuple[dict, bool]:
    concept = ctx.graph.get_concept(concept_id, project_id=project_id)
    if concept is None:
        raise KeyError(f"unknown concept {concept_id!r}")
    cached = concept.get("teach_md") or ""
    if cached and not force and not concept.get("dirty", False):
        try:
            return json.loads(cached), True
        except json.JSONDecodeError:
            pass  # stale markdown from before the guide migration → regenerate
    claims = _trusted_claims(ctx.graph.claims_in_concept(concept_id, project_id=project_id))
    profile = get_domain_profile(ctx.db, project_id)
    prompt = get_prompt(ctx.db, "guide").format(
        domain_guidance=domain_guidance(profile),
        concept_name=concept.get("name", ""),
        concept_summary=concept.get("summary", ""),
        claims=_format_claims(claims),
        memory_hints=_format_memory_hints(ctx.memory, concept.get("name", ""), project_id),
    )
    resp = ctx.router.complete("interactive", ModelRequest(
        messages=[ModelMessage(role="user", content=prompt)],
        json_schema=GUIDE_SCHEMA,
        max_tokens=int(ctx.db.get_tunable("guide.max_tokens")),
    ))
    guide = drop_uncited(parse_guide({**resp.parsed, "concept_id": concept_id,
                                      "title": concept.get("name", "")}))
    data = guide.model_dump()
    data["disagreements"] = _disagreements(ctx.graph, concept_id, project_id)
    ctx.graph.set_concept_teaching(concept_id, json.dumps(data), project_id=project_id)
    ctx.graph.mark_concept_dirty(concept_id, False, project_id=project_id)
    return data, False
