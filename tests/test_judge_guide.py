from mslearn.evals.golden import GuideGolden, append_golden
from mslearn.evals.judged import guide_grounding_violations, judge_guide
from mslearn.graph.records import ConceptRecord
from mslearn.opsdb import OpsDB
from mslearn.providers.base import ProviderBadOutputError
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore, ScriptedRouter


def test_grounding_violations_flags_out_of_concept_and_empty():
    guide = {"tl_dr": {"text": "t", "claims": ["c1"]}, "sections": [
        {"id": "s1", "title": "S", "items": [
            {"kind": "claim", "text": "a", "claims": ["c1"]},
            {"kind": "claim", "text": "b", "claims": ["c9"]},
            {"kind": "claim", "text": "c", "claims": []},
        ]},
    ]}
    v = guide_grounding_violations(guide, {"c1"})
    assert any("c9" in x for x in v)
    assert any("empty" in x.lower() for x in v)


def _seed_graph() -> InMemoryGraphStore:
    graph = InMemoryGraphStore()
    graph.upsert_concept(ConceptRecord("k1", "Cache invalidation", "Summary"))
    graph.set_concept_meta("k1", order_index=0)
    graph.add_claim("c1", "text", "neutral", "s1", [1.0, 0.0], quote="q", chunk_id="ch1")
    graph.assign_claim("c1", "k1")
    return graph


def test_judge_guide_scores_four_axes(tmp_path):
    graph = _seed_graph()
    guide_json = {
        "concept_id": "k1",
        "title": "Cache invalidation",
        "tl_dr": {"text": "t", "claims": ["c1"]},
        "skeleton": ["S"],
        "sections": [
            {"id": "s1", "title": "S", "items": [{"kind": "claim", "text": "x", "claims": ["c1"]}]}
        ],
    }
    rubric_json = {"depth_1_5": 4, "redundancy_1_5": 5, "category_fit_1_5": 3, "grounding_1_5": 5}
    router = ScriptedRouter([guide_json, rubric_json])
    db = OpsDB(tmp_path / "o.db")
    ctx = PipelineContext(settings=None, db=db, router=router, graph=graph)

    result = judge_guide(ctx, n=1)

    assert result["depth"] == 4 / 5
    assert result["non_redundancy"] == 1.0
    assert result["category_fit"] == 3 / 5
    assert result["grounding"] == 1.0


class _RaisingRouter:
    def complete(self, role, request):
        raise ProviderBadOutputError("bad output")


def test_judge_guide_degrades_on_bad_output_instead_of_crashing(tmp_path):
    graph = _seed_graph()
    db = OpsDB(tmp_path / "o.db")
    ctx = PipelineContext(settings=None, db=db, router=_RaisingRouter(), graph=graph)

    result = judge_guide(ctx, n=1)

    assert result == {"depth": 0.0, "non_redundancy": 0.0, "category_fit": 0.0, "grounding": 0.0}


def test_judge_guide_also_scores_active_golden_fixtures(tmp_path, monkeypatch):
    # No live concepts in the graph at all — the only signal judge_guide has
    # is the ratcheted golden fixture, so this proves the fixture path is
    # wired in on its own, not just riding along with live sampling.
    monkeypatch.setattr("mslearn.evals.golden.GOLDEN_DIR", tmp_path)
    append_golden(
        "guide",
        GuideGolden(
            concept_id="k1",
            concept_name="Cache invalidation",
            concept_summary="Summary",
            claims=[{"claim_id": "c1", "text": "text", "stance": "neutral", "kind": "claim"}],
            failing_axis="depth",
            tag="too_shallow",
            review="approved",
        ),
    )
    guide_json = {
        "concept_id": "k1",
        "title": "Cache invalidation",
        "tl_dr": {"text": "t", "claims": ["c1"]},
        "skeleton": ["S"],
        "sections": [
            {"id": "s1", "title": "S", "items": [{"kind": "claim", "text": "x", "claims": ["c1"]}]}
        ],
    }
    rubric_json = {"depth_1_5": 5, "redundancy_1_5": 5, "category_fit_1_5": 5, "grounding_1_5": 5}
    router = ScriptedRouter([guide_json, rubric_json])
    db = OpsDB(tmp_path / "o.db")
    ctx = PipelineContext(settings=None, db=db, router=router, graph=InMemoryGraphStore())

    result = judge_guide(ctx, n=5)

    assert result == {"depth": 1.0, "non_redundancy": 1.0, "category_fit": 1.0, "grounding": 1.0}
