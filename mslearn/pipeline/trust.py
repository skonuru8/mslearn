import math
from dataclasses import dataclass, field
from typing import Callable

from rapidfuzz import fuzz

from mslearn.pipeline.contracts import ClaimDraft

Embedder = Callable[[list[str]], list[list[float]]]


@dataclass(frozen=True)
class TrustVerdict:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    quote_score: float = 0.0
    embed_sim: float | None = None


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def check_claim(
    chunk_text: str,
    draft: ClaimDraft,
    *,
    quote_threshold: float,
    embed_sim_threshold: float,
    embedder: Embedder | None = None,
) -> TrustVerdict:
    reasons: list[str] = []
    quote = draft.quote.strip()

    score = 0.0
    if not quote:
        reasons.append("quote is empty")
    else:
        score = float(fuzz.partial_ratio(quote, chunk_text))
        if score < quote_threshold:
            reasons.append(
                f"quote not found in chunk (match {score:.0f} < {quote_threshold:.0f})"
            )

    sim: float | None = None
    if embedder is not None and quote:
        vec_text, vec_quote = embedder([draft.text, quote])
        sim = cosine(vec_text, vec_quote)
        if sim < embed_sim_threshold:
            reasons.append(
                f"claim/quote similarity {sim:.2f} < {embed_sim_threshold:.2f}"
            )

    return TrustVerdict(ok=not reasons, reasons=reasons, quote_score=score, embed_sim=sim)
