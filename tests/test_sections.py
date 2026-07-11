import json
from pathlib import Path

from mslearn.graph.records import ConceptRecord
from mslearn.opsdb import OpsDB
from mslearn.pipeline.synthesis import assign_sections
from mslearn.settings import Settings
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore, ScriptedRouter


def _add_chunk(graph, chunk_id, *, source_id="s1", seq, section_path):
    graph.chunks[chunk_id] = {
        "chunk_id": chunk_id,
        "source_id": source_id,
        "seq": seq,
        "section_path": json.dumps(list(section_path)),
        "project_id": "default",
    }


def _ctx(tmp_path):
    return PipelineContext(
        settings=Settings(profiles_path=Path("profiles.yaml")),
        db=OpsDB(tmp_path / "o.db"), router=ScriptedRouter(outputs=[]),
        graph=InMemoryGraphStore(), memory=None,
    )


def test_assign_sections_picks_dominant_path_and_reconciles_category(tmp_path):
    ctx = _ctx(tmp_path)
    g = ctx.graph
    g.upsert_concept(ConceptRecord(concept_id="k1", name="N"))

    _add_chunk(g, "c1", seq=2, section_path=["Ch1", "1.1"])
    _add_chunk(g, "c2", seq=5, section_path=["Ch1", "1.1"])
    _add_chunk(g, "c3", seq=9, section_path=["Ch2"])

    for i, chunk_id in enumerate(["c1", "c2", "c3"], start=1):
        g.add_claim(f"cl{i}", "text", "neutral", "s1", [0.1], chunk_id=chunk_id)
        g.assign_claim(f"cl{i}", "k1")

    n = assign_sections(ctx)

    assert n == 1
    assert g.get_concept("k1")["section_path"] == ["Ch1", "1.1"]
    assert g.get_concept("k1")["category"] == "Ch1"


def test_assign_sections_empty_paths_leaves_category_untouched(tmp_path):
    ctx = _ctx(tmp_path)
    g = ctx.graph
    g.upsert_concept(ConceptRecord(concept_id="k1", name="N"))
    g.set_concept_meta("k1", category="Manual")

    _add_chunk(g, "c1", seq=0, section_path=[])
    g.add_claim("cl1", "text", "neutral", "s1", [0.1], chunk_id="c1")
    g.assign_claim("cl1", "k1")

    n = assign_sections(ctx)

    assert n == 0
    assert g.get_concept("k1")["section_path"] == []
    assert g.get_concept("k1")["category"] == "Manual"
