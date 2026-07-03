from __future__ import annotations

TRUSTED_FOR_QA = frozenset({"trusted", "escalated"})


def retrieve(ctx, question: str, k: int = 8) -> dict:
    embedding = ctx.router.embed([question])[0]
    claim_hits = ctx.graph.vector_search_claims(embedding, k=max(k * 3, k))
    trusted_claims = [
        row for row in claim_hits if row.get("trust") in TRUSTED_FOR_QA
    ][:k]
    chunk_hits = ctx.graph.vector_search_chunks(embedding, k=k)
    return {
        "claims": trusted_claims,
        "chunks": chunk_hits,
        "conflicts": _retrieved_conflicts(ctx.graph, trusted_claims),
    }


def _retrieved_conflicts(graph, claims: list[dict]) -> list[dict]:
    retrieved_ids = {row["claim_id"] for row in claims}
    concept_ids = {
        concept_id
        for row in claims
        if (concept_id := graph.concept_id_of_claim(row["claim_id"])) is not None
    }
    conflicts: dict[tuple[str, str], dict] = {}
    for concept_id in concept_ids:
        for row in graph.conflicts_in_concept(concept_id):
            pair = tuple(sorted((row.get("claim_a", ""), row.get("claim_b", ""))))
            if pair[0] in retrieved_ids and pair[1] in retrieved_ids:
                conflicts[pair] = {
                    "claim_a": pair[0],
                    "claim_b": pair[1],
                    "classification": row.get("classification", ""),
                    "rationale": row.get("rationale", ""),
                }
    return [conflicts[pair] for pair in sorted(conflicts)]
