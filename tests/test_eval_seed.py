from mslearn.evals.evolve import OverlayOpsDB, required_placeholders, validate_proposal
from mslearn.evals.golden import ExtractionGolden, append_golden, load_golden
from mslearn.evals.seed import seed_extraction
from mslearn.opsdb import OpsDB
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore, ScriptedRouter


def test_seed_extraction_appends_pending(tmp_path, monkeypatch):
    monkeypatch.setattr("mslearn.evals.golden.GOLDEN_DIR", tmp_path)
    graph = InMemoryGraphStore()
    graph.chunks["ch1"] = {
        "chunk_id": "ch1",
        "text": "Caching is hard.",
        "source_id": "s1",
        "kind": "blog",
    }
    graph.sources["s1"] = {"source_id": "s1", "source_type": "blog"}
    router = ScriptedRouter(
        [{"claims": [{"text": "Caching is hard.", "stance": "neutral", "quote": "Caching is hard."}]}]
    )
    db = OpsDB(tmp_path / "ops.db")
    ctx = PipelineContext(settings=None, db=db, router=router, graph=graph)
    added = seed_extraction(ctx, n_chunks=1)
    assert added == 1
    rows = load_golden("extraction")
    assert rows[0].review == "pending"


def test_review_approve_excludes_from_active_only(tmp_path, monkeypatch):
    monkeypatch.setattr("mslearn.evals.golden.GOLDEN_DIR", tmp_path)
    append_golden(
        "extraction",
        ExtractionGolden(
            chunk_text="x",
            expected_claims=[{"text": "a", "stance": "neutral"}],
            source_type="blog",
            review="pending",
        ),
    )
    assert len(load_golden("extraction", active_only=True)) == 0
    rows = load_golden("extraction")
    rows[0] = ExtractionGolden(
        chunk_text="x",
        expected_claims=[{"text": "a", "stance": "neutral"}],
        source_type="blog",
        review="approved",
    )
    from mslearn.evals.golden import save_golden

    save_golden("extraction", rows)
    assert len(load_golden("extraction", active_only=True)) == 1


def test_validate_proposal_bounds():
    err = validate_proposal({"kind": "tunable", "key": "trust.quote_threshold", "value": 10.0})
    assert err is not None


def test_overlay_does_not_mutate_base(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    base_value = db.get_tunable("trust.quote_threshold")
    overlay = OverlayOpsDB(db, tunables={"trust.quote_threshold": base_value + 1})
    assert overlay.get_tunable("trust.quote_threshold") == base_value + 1
    assert db.get_tunable("trust.quote_threshold") == base_value


def test_required_placeholders():
    assert "domain_guidance" in required_placeholders("conflict_scan")
