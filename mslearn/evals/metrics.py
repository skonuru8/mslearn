from __future__ import annotations

import re

from rapidfuzz import fuzz

from mslearn.evals.golden import load_golden
from mslearn.evals.judged import judge_guide
from mslearn.opsdb import DEFAULT_PROJECT_ID
from mslearn.pipeline.contracts import ClaimDraft
from mslearn.pipeline.extraction_graph import build_extraction_graph, run_extraction
from mslearn.pipeline.synthesis import classify_conflict_pair, concept_match_claim_ids
from mslearn.pipeline.trust import check_claim

_MATCH_THRESHOLD = 80
_CLAIM_RE = re.compile(r"\[claim:([^\]\s]+)\]")


def extraction_pr(ctx) -> dict[str, float]:
    rows = load_golden("extraction", active_only=True)
    if not rows:
        return {"precision": 0.0, "recall": 0.0}
    tp = fp = fn = 0
    # Built once for the whole golden set, not per row — mirrors the worker's
    # per-process cache (see worker/context.py); the graph structure and its
    # tunables don't change between rows.
    graph = build_extraction_graph(ctx.router, ctx.db)
    for index, row in enumerate(rows):
        state = run_extraction(graph, f"eval-{index}", row.chunk_text)
        predicted = [
            {"text": draft.text, "stance": draft.stance}
            for draft in state["accepted"]
        ]
        matched_expected = set()
        for pred in predicted:
            best_idx = None
            best_score = 0.0
            for idx, expected in enumerate(row.expected_claims):
                if idx in matched_expected:
                    continue
                score = fuzz.token_set_ratio(pred["text"], expected["text"])
                if score >= _MATCH_THRESHOLD and pred["stance"] == expected["stance"]:
                    if score > best_score:
                        best_score = score
                        best_idx = idx
            if best_idx is None:
                fp += 1
            else:
                tp += 1
                matched_expected.add(best_idx)
        fn += len(row.expected_claims) - len(matched_expected)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return {"precision": precision, "recall": recall}


def grounding_rates(ctx) -> dict[str, float]:
    rows = load_golden("grounding", active_only=True)
    if not rows:
        return {"false_accept": 0.0, "false_reject": 0.0}
    quote_threshold = ctx.db.get_tunable("trust.quote_threshold")
    embed_threshold = ctx.db.get_tunable("trust.embed_sim_threshold")
    invalid_rows = [r for r in rows if not r.valid]
    valid_rows = [r for r in rows if r.valid]
    false_accept = 0.0
    if invalid_rows:
        passed = sum(
            1
            for row in invalid_rows
            if check_claim(
                row.chunk_text,
                ClaimDraft(text=row.claim_text, stance="neutral", quote=row.quote),
                quote_threshold=quote_threshold,
                embed_sim_threshold=embed_threshold,
                embedder=ctx.router.embed,
            ).ok
        )
        false_accept = passed / len(invalid_rows)
    false_reject = 0.0
    if valid_rows:
        rejected = sum(
            1
            for row in valid_rows
            if not check_claim(
                row.chunk_text,
                ClaimDraft(text=row.claim_text, stance="neutral", quote=row.quote),
                quote_threshold=quote_threshold,
                embed_sim_threshold=embed_threshold,
                embedder=ctx.router.embed,
            ).ok
        )
        false_reject = rejected / len(valid_rows)
    return {"false_accept": false_accept, "false_reject": false_reject}


def clustering_f1(ctx) -> dict[str, float]:
    rows = load_golden("clustering", active_only=True)
    if not rows:
        return {"f1": 0.0, "precision": 0.0, "recall": 0.0}
    tp = fp = fn = 0
    for index, row in enumerate(rows):
        anchor = {
            "claim_id": f"a-{index}",
            "text": row.text_a,
            "stance": "neutral",
        }
        candidate = {
            "claim_id": f"b-{index}",
            "text": row.text_b,
            "stance": "neutral",
        }
        matches = concept_match_claim_ids(ctx, anchor, [candidate])
        predicted_same = candidate["claim_id"] in matches
        if predicted_same and row.same_concept:
            tp += 1
        elif predicted_same and not row.same_concept:
            fp += 1
        elif not predicted_same and row.same_concept:
            fn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {"f1": f1, "precision": precision, "recall": recall}


def tension_accuracy(ctx) -> dict[str, float]:
    rows = load_golden("tension", active_only=True)
    if not rows:
        return {"accuracy": 0.0}
    correct = 0
    for index, row in enumerate(rows):
        claim_a = {"claim_id": f"a-{index}", "text": row.claim_a, "stance": "neutral"}
        claim_b = {"claim_id": f"b-{index}", "text": row.claim_b, "stance": "neutral"}
        predicted = classify_conflict_pair(
            ctx, claim_a=claim_a, claim_b=claim_b, domain_profile=row.domain_profile
        )
        if predicted == row.classification:
            correct += 1
    return {"accuracy": correct / len(rows)}


def schema_validity(ctx) -> dict[str, float]:
    with ctx.db._lock:
        rows = ctx.db.conn.execute(
            "SELECT role, outcome, error FROM model_calls"
        ).fetchall()
    extraction_synthesis = [
        dict(r)
        for r in rows
        if r["role"] in {"extraction", "synthesis"}
    ]
    total = len(extraction_synthesis)
    bad = sum(
        1
        for row in extraction_synthesis
        if row["outcome"] == "error"
        and row["error"]
        and "BadOutput" in row["error"]
    )
    validity = 1.0 - (bad / total) if total else 1.0
    quote_rate = _quote_match_rate(ctx)
    coverage = _chunk_coverage(ctx)
    return {
        "validity": validity,
        "quote_match_rate": quote_rate,
        "chunk_coverage": coverage,
    }


def _quote_match_rate(ctx) -> float:
    claims = getattr(ctx.graph, "claims", None)
    if not isinstance(claims, dict) or not claims:
        return 1.0
    passed = 0
    total = 0
    quote_threshold = ctx.db.get_tunable("trust.quote_threshold")
    for claim in claims.values():
        chunk = ctx.graph.get_chunk(claim.get("chunk_id", "")) if hasattr(ctx.graph, "get_chunk") else None
        chunk_text = chunk.get("text", "") if chunk else ""
        if not chunk_text:
            continue
        total += 1
        draft = ClaimDraft(
            text=claim.get("text", ""),
            stance=claim.get("stance", "neutral"),
            quote=claim.get("quote", claim.get("text", "")),
        )
        if check_claim(
            chunk_text,
            draft,
            quote_threshold=quote_threshold,
            embed_sim_threshold=ctx.db.get_tunable("trust.embed_sim_threshold"),
            embedder=ctx.router.embed,
        ).ok:
            passed += 1
    return passed / total if total else 1.0


def _chunk_coverage(ctx) -> float:
    sources = ctx.db.all_sources()
    if not sources:
        return 1.0
    done = sum(s.get("done_chunks", 0) for s in sources)
    if done == 0:
        return 0.0
    if hasattr(ctx.graph, "claims"):
        claim_chunks = {c.get("chunk_id") for c in ctx.graph.claims.values()}
        return min(1.0, len(claim_chunks) / done)
    return 1.0


def feedback_rates(ctx) -> dict[str, float]:
    agg = ctx.db.feedback_aggregate(DEFAULT_PROJECT_ID)
    total = agg.get("total_rated", 0)

    def _rate(key: str) -> float:
        return (agg.get(key, 0) / total) if total else 0.0

    return {
        "helpful_rate": _rate("helpful"),
        "shallow_rate": _rate("too_shallow"),
        "repetitive_rate": _rate("repetitive"),
        "wrong_rate": _rate("wrong"),
        "offtopic_rate": _rate("off_topic"),
        "total_rated": total,
    }


def guide_quality(ctx) -> dict[str, float]:
    return judge_guide(ctx)


def compute_component_metrics(ctx) -> dict[str, float]:
    extraction = extraction_pr(ctx)
    grounding = grounding_rates(ctx)
    clustering = clustering_f1(ctx)
    tension = tension_accuracy(ctx)
    schema = schema_validity(ctx)
    feedback = feedback_rates(ctx)
    guide = guide_quality(ctx)
    return {
        "extraction.precision": extraction["precision"],
        "extraction.recall": extraction["recall"],
        "grounding.false_accept": grounding["false_accept"],
        "grounding.false_reject": grounding["false_reject"],
        "clustering.f1": clustering["f1"],
        "tension.accuracy": tension["accuracy"],
        "schema.validity": schema["validity"],
        "schema.quote_match_rate": schema["quote_match_rate"],
        "schema.chunk_coverage": schema["chunk_coverage"],
        "feedback.helpful_rate": feedback["helpful_rate"],
        "feedback.shallow_rate": feedback["shallow_rate"],
        "feedback.repetitive_rate": feedback["repetitive_rate"],
        "feedback.wrong_rate": feedback["wrong_rate"],
        "feedback.offtopic_rate": feedback["offtopic_rate"],
        "feedback.total_rated": feedback["total_rated"],
        "guide.depth": guide["depth"],
        "guide.non_redundancy": guide["non_redundancy"],
        "guide.category_fit": guide["category_fit"],
        "guide.grounding": guide["grounding"],
    }
