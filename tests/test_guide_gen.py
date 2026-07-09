import json
from pathlib import Path

import pytest

from mslearn.graph.records import ConceptRecord
from mslearn.opsdb import OpsDB
from mslearn.pipeline.guide import GuideParseError
from mslearn.pipeline.guide_gen import generate_guide
from mslearn.settings import Settings
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore, RaisingLearnerMemory, ScriptedRouter

GUIDE_OUTPUT = {
    "concept_id": "con1",
    "title": "Merge sort",
    "tl_dr": {"text": "Merge sort runs in O(n log n).", "claims": ["c3"]},
    "skeleton": ["Cost"],
    "sections": [
        {
            "id": "s1",
            "title": "Cost",
            "items": [
                {"kind": "claim", "text": "Merge sort runs in O(n log n).", "claims": ["c3"]},
                {"kind": "example", "text": "hallucinated extra fact", "claims": []},
            ],
        }
    ],
    "disagreements": [],
    "open_questions": [],
}


def seed_concept(graph: InMemoryGraphStore) -> None:
    graph.upsert_concept(
        ConceptRecord(
            concept_id="con1", name="Merge sort", summary="Sorts via divide and conquer."
        )
    )
    graph.add_claim(
        "c3", "Merge sort runs in O(n log n).", "neutral", "s1", [1.0, 0.0],
        quote="O(n log n) time", chunk_id="ch1",
    )
    graph.assign_claim("c3", "con1")
    graph.add_claim(
        "c4", "Merge sort is O(n^2) in the worst case.", "neutral", "s2", [0.0, 1.0],
        quote="worst case quadratic", chunk_id="ch2",
    )
    graph.assign_claim("c4", "con1")
    graph.add_conflict("c3", "c4", "evidence_mismatch", "Sources disagree on worst-case complexity.")


def make_ctx(tmp_path, router, *, memory=None):
    graph = InMemoryGraphStore()
    seed_concept(graph)
    return PipelineContext(
        settings=Settings(profiles_path=Path("profiles.yaml")),
        db=OpsDB(tmp_path / "ops.db"),
        router=router,
        graph=graph,
        memory=memory,
    )


@pytest.fixture()
def fake_ctx(tmp_path):
    router = ScriptedRouter(outputs=[dict(GUIDE_OUTPUT)])
    return make_ctx(tmp_path, router)


@pytest.fixture()
def fake_ctx_raising_memory(tmp_path):
    router = ScriptedRouter(outputs=[dict(GUIDE_OUTPUT)])
    return make_ctx(tmp_path, router, memory=RaisingLearnerMemory())


def test_generate_guide_drops_uncited_and_adds_disagreements(fake_ctx):
    out, cached = generate_guide(fake_ctx, "con1", force=True, project_id="default")
    assert out["title"]
    assert cached is False
    assert all(item["claims"] for sec in out["sections"] for item in sec["items"])
    assert out["disagreements"][0]["classification"] in {
        "context_dependent", "outdated", "genuine_debate", "evidence_mismatch",
    }
    disagreement = out["disagreements"][0]
    assert disagreement["a"]["label"] == "Position A"
    assert disagreement["b"]["label"] == "Position B"
    assert "c3" not in disagreement["a"]["label"]
    assert "c4" not in disagreement["b"]["label"]


def test_generate_guide_memory_failure_degrades(fake_ctx_raising_memory):
    out, _cached = generate_guide(fake_ctx_raising_memory, "con1", force=True)
    assert out["title"]  # no raise


def test_generate_guide_caches_json_after_first_call(tmp_path):
    router = ScriptedRouter(outputs=[dict(GUIDE_OUTPUT)])
    ctx = make_ctx(tmp_path, router)

    first, first_cached = generate_guide(ctx, "con1")
    second, second_cached = generate_guide(ctx, "con1")

    assert first == second
    assert first_cached is False
    assert second_cached is True
    assert router.calls == ["interactive"]
    cached = json.loads(ctx.graph.get_concept("con1")["teach_md"])
    assert cached["title"] == "Merge sort"


def test_generate_guide_regenerates_when_cached_content_is_stale_markdown(tmp_path):
    router = ScriptedRouter(outputs=[dict(GUIDE_OUTPUT)])
    ctx = make_ctx(tmp_path, router)
    ctx.graph.set_concept_teaching("con1", "## Explanation\nOld markdown lesson.")

    out, cached = generate_guide(ctx, "con1")

    assert out["title"] == "Merge sort"
    assert cached is False
    assert router.calls == ["interactive"]


def test_generate_guide_unknown_concept_raises_keyerror(tmp_path):
    router = ScriptedRouter(outputs=[])
    ctx = make_ctx(tmp_path, router)

    with pytest.raises(KeyError):
        generate_guide(ctx, "nope")


def test_generate_guide_none_parsed_does_not_raise_typeerror(tmp_path):
    # A provider that returns parsed=None (e.g. a malformed/empty completion)
    # must not blow up generate_guide with `TypeError: argument of type
    # 'NoneType' is not a mapping` from `{**resp.parsed, ...}`. It should
    # instead fail the schema validation that already exists, surfacing as
    # a GuideParseError the caller can handle.
    router = ScriptedRouter(outputs=[None])
    ctx = make_ctx(tmp_path, router)

    with pytest.raises(GuideParseError):
        generate_guide(ctx, "con1", force=True)
