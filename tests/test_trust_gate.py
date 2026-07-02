from mslearn.pipeline.contracts import ClaimDraft
from mslearn.pipeline.trust import TrustVerdict, check_claim, cosine

CHUNK = "Cache invalidation is one of the two hard problems in computer science."


def draft(quote, text="Cache invalidation is hard."):
    return ClaimDraft(text=text, stance="neutral", quote=quote)


def test_cosine():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0  # zero vector safe


def test_verbatim_quote_passes():
    verdict = check_claim(CHUNK, draft("Cache invalidation is one of the two hard problems"),
                          quote_threshold=90.0, embed_sim_threshold=0.35)
    assert verdict.ok and verdict.reasons == []
    assert verdict.quote_score >= 90.0 and verdict.embed_sim is None


def test_fabricated_quote_fails():
    verdict = check_claim(CHUNK, draft("Naming things is easy and fun for everyone"),
                          quote_threshold=90.0, embed_sim_threshold=0.35)
    assert not verdict.ok
    assert any("quote" in r for r in verdict.reasons)


def test_empty_quote_fails():
    verdict = check_claim(CHUNK, draft("   "), quote_threshold=90.0, embed_sim_threshold=0.35)
    assert not verdict.ok


def test_embedding_sanity_check():
    def far_embedder(texts):
        return [[1.0, 0.0] if "invalidation" in t.lower() else [0.0, 1.0] for t in texts]

    verdict = check_claim(
        CHUNK, draft("Cache invalidation is one of the two hard problems",
                     text="Bananas are yellow."),
        quote_threshold=90.0, embed_sim_threshold=0.35, embedder=far_embedder,
    )
    assert not verdict.ok and verdict.embed_sim == 0.0
    assert any("similarity" in r for r in verdict.reasons)


def test_verdict_frozen():
    import pytest

    verdict = TrustVerdict(ok=True, reasons=[], quote_score=100.0, embed_sim=None)
    with pytest.raises(AttributeError):
        verdict.ok = False
