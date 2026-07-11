from pathlib import Path

from mslearn.graph.records import ConceptRecord
from mslearn.opsdb import OpsDB
from mslearn.pipeline.synthesis import assign_categories
from mslearn.prompts import PROMPTS
from mslearn.settings import Settings
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore, ScriptedRouter


def test_category_prompt_bans_catch_all_and_covers_every_concept():
    prompt = PROMPTS["concept_categories"].lower()
    assert "other" in prompt  # it explicitly forbids the word
    assert "never create a catch-all" in prompt
    assert "every provided concept id must appear" in prompt


def _ctx(tmp_path, outputs):
    g = InMemoryGraphStore()
    for cid in ("k1", "k2", "k3"):
        g.upsert_concept(ConceptRecord(concept_id=cid, name=f"Name {cid}"))
        g.set_concept_meta(cid, order_index=int(cid[-1]))
    return PipelineContext(
        settings=Settings(profiles_path=Path("profiles.yaml")),
        db=OpsDB(tmp_path / "o.db"), router=ScriptedRouter(outputs=outputs),
        graph=g, memory=None,
    )


def test_assign_categories_persists(tmp_path):
    ctx = _ctx(tmp_path, [{"categories": [
        {"name": "Alpha", "concept_ids": ["k1", "k2"]},
        {"name": "Beta", "concept_ids": ["k3"]},
    ]}])
    n = assign_categories(ctx)
    assert n == 3
    cats = {c["concept_id"]: c["category"] for c in ctx.graph.all_concepts()}
    assert cats == {"k1": "Alpha", "k2": "Alpha", "k3": "Beta"}


def test_assign_categories_bad_output_leaves_empty(tmp_path):
    from mslearn.providers.base import ProviderBadOutputError

    class Boom(ScriptedRouter):
        def complete(self, role, request):
            raise ProviderBadOutputError("truncated")

    ctx = _ctx(tmp_path, [])
    ctx = ctx.__class__(**{**ctx.__dict__, "router": Boom(outputs=[])})
    assert assign_categories(ctx) == 0
    assert all(c["category"] == "" for c in ctx.graph.all_concepts())
