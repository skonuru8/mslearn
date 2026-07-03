from mslearn.evals.judged import provenance_citations
from mslearn.evals.metrics import _quote_match_rate, extraction_pr, grounding_rates
from mslearn.opsdb import OpsDB
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore, ScriptedRouter


def test_grounding_false_accept(tmp_path):
    router = ScriptedRouter([])
    db = OpsDB(tmp_path / "ops.db")
    ctx = PipelineContext(settings=None, db=db, router=router, graph=InMemoryGraphStore())
    rates = grounding_rates(ctx)
    assert "false_accept" in rates


def test_extraction_pr_with_scripted_router(tmp_path, monkeypatch):
    monkeypatch.setattr("mslearn.evals.golden.GOLDEN_DIR", tmp_path)
    (tmp_path / "extraction.jsonl").write_text(
        '{"chunk_text":"Cache invalidation is hard.","expected_claims":'
        '[{"text":"Cache invalidation is hard.","stance":"neutral"}],'
        '"source_type":"blog","review":"approved"}\n'
    )
    good = {
        "claims": [
            {
                "text": "Cache invalidation is hard.",
                "stance": "neutral",
                "quote": "Cache invalidation is hard.",
            }
        ]
    }
    router = ScriptedRouter([good])
    db = OpsDB(tmp_path / "ops.db")
    ctx = PipelineContext(settings=None, db=db, router=router, graph=InMemoryGraphStore())
    metrics = extraction_pr(ctx)
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 1.0


def test_quote_match_rate_applies_embedding_similarity_axis(tmp_path):
    # Quote fuzzy-matches the chunk perfectly, but claim/quote embeddings are
    # orthogonal — without passing the router's embedder into check_claim,
    # schema.quote_match_rate would skip this axis entirely and inflate to
    # 1.0. With the embedder wired in, the low similarity fails the claim.
    graph = InMemoryGraphStore()
    graph.chunks["ch1"] = {"chunk_id": "ch1", "text": "the exact quote text appears here"}
    graph.add_claim(
        "c1", "Some claim text", "neutral", "s1", [1.0, 0.0],
        quote="the exact quote text", chunk_id="ch1",
    )
    router = ScriptedRouter([], embeddings=[[1.0, 0.0], [0.0, 1.0]])
    db = OpsDB(tmp_path / "ops.db")
    ctx = PipelineContext(settings=None, db=db, router=router, graph=graph)
    rate = _quote_match_rate(ctx)
    assert rate == 0.0
    assert router.embed_texts == ["Some claim text", "the exact quote text"]


def test_provenance_unknown_claim():
    graph = InMemoryGraphStore()
    graph.add_claim("c1", "text", "neutral", "s1", [1.0, 0.0], chunk_id="ch1")
    ctx = PipelineContext(settings=None, db=None, router=None, graph=graph)
    violations = provenance_citations("Fact [claim:missing].", ctx, concept_id="k1")
    assert any("unknown claim" in v for v in violations)


def test_provenance_uncited_paragraph():
    graph = InMemoryGraphStore()
    ctx = PipelineContext(settings=None, db=None, router=None, graph=graph)
    md = "## Explanation\n\nNo citation here.\n\n## Worked example\n\nExample without gate."
    violations = provenance_citations(md, ctx)
    assert any("uncited paragraph" in v for v in violations)
