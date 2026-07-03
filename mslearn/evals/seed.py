from __future__ import annotations

import random

from mslearn.evals.golden import (
    ClusteringGolden,
    ExtractionGolden,
    GroundingGolden,
    TensionGolden,
    append_golden,
    load_golden,
)
from mslearn.pipeline.contracts import ClaimDraft
from mslearn.pipeline.trust import check_claim
from mslearn.prompts import get_prompt
from mslearn.providers.base import ModelMessage, ModelRequest

_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "stance": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["text", "stance", "quote"],
            },
        }
    },
    "required": ["claims"],
}


def seed_extraction(ctx, n_chunks: int = 50) -> int:
    chunks = ctx.graph.sample_chunks(n_chunks)
    added = 0
    prompt = get_prompt(ctx.db, "extraction")
    for chunk in chunks:
        response = ctx.router.complete(
            "evals",
            ModelRequest(
                messages=[
                    ModelMessage(
                        role="user",
                        content=f"{prompt}\n\nCHUNK:\n{chunk['text']}",
                    )
                ],
                json_schema=_EXTRACTION_SCHEMA,
            ),
        )
        parsed = response.parsed if isinstance(response.parsed, dict) else {}
        claims = parsed.get("claims", [])
        append_golden(
            "extraction",
            ExtractionGolden(
                chunk_text=chunk["text"],
                expected_claims=[
                    {"text": str(row.get("text", "")), "stance": str(row.get("stance", "neutral"))}
                    for row in claims
                    if isinstance(row, dict)
                ],
                source_type=str(chunk.get("source_type") or chunk.get("kind") or "pdf"),
                review="pending",
            ),
        )
        added += 1
    return added


def seed_grounding(ctx, n_claims: int = 50) -> int:
    claims = list(getattr(ctx.graph, "claims", {}).values())[:n_claims]
    if not claims and hasattr(ctx.graph, "claims_in_concept"):
        for concept in ctx.graph.all_concepts():
            claims.extend(ctx.graph.claims_in_concept(concept["concept_id"]))
    added = 0
    quote_threshold = ctx.db.get_tunable("trust.quote_threshold")
    embed_threshold = ctx.db.get_tunable("trust.embed_sim_threshold")
    for claim in claims[:n_claims]:
        chunk = ctx.graph.get_chunk(claim.get("chunk_id", ""))
        if not chunk:
            continue
        chunk_text = chunk["text"]
        quote = claim.get("quote", claim.get("text", ""))
        append_golden(
            "grounding",
            GroundingGolden(
                chunk_text=chunk_text,
                claim_text=claim.get("text", ""),
                quote=quote,
                valid=True,
                review="pending",
            ),
        )
        perturbed = quote[: max(1, len(quote) // 2)] + " NOT IN CHUNK"
        append_golden(
            "grounding",
            GroundingGolden(
                chunk_text=chunk_text,
                claim_text=claim.get("text", ""),
                quote=perturbed,
                valid=False,
                review="pending",
            ),
        )
        added += 2
        _ = check_claim(
            chunk_text,
            ClaimDraft(text=claim.get("text", ""), stance="neutral", quote=quote),
            quote_threshold=quote_threshold,
            embed_sim_threshold=embed_threshold,
        )
    return added


def seed_clustering(ctx, n_pairs: int = 50) -> int:
    claims = list(getattr(ctx.graph, "claims", {}).values())
    if len(claims) < 2:
        return 0
    added = 0
    for _ in range(n_pairs):
        a, b = random.sample(claims, 2)
        append_golden(
            "clustering",
            ClusteringGolden(
                text_a=a.get("text", ""),
                text_b=b.get("text", ""),
                same_concept=False,
                review="pending",
            ),
        )
        added += 1
    return added


def seed_tension(ctx, n_pairs: int = 50) -> int:
    conflicts = getattr(ctx.graph, "conflicts", {})
    claims = getattr(ctx.graph, "claims", {})

    def claim_text(cid: str) -> str:
        return claims.get(cid, {}).get("text", cid)

    added = 0
    items = list(conflicts.values()) if isinstance(conflicts, dict) else []
    for row in items[:n_pairs]:
        append_golden(
            "tension",
            TensionGolden(
                claim_a=claim_text(row.get("claim_a", "")),
                claim_b=claim_text(row.get("claim_b", "")),
                domain_profile="technical",
                classification=row.get("classification", "genuine_debate"),
                review="pending",
            ),
        )
        added += 1
    return added


def pending_golden(kind: str) -> list[dict]:
    rows = load_golden(kind)
    return [
        {"index": index, **row.__dict__}
        for index, row in enumerate(rows)
        if row.review == "pending"
    ]
