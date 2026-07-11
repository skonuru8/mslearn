from mslearn.evals.patterns import mine_patterns
from mslearn.opsdb import OpsDB
from mslearn.providers.base import ProviderBadOutputError
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore, ScriptedRouter


def test_mine_patterns_returns_parsed_list(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    db.add_note_feedback(
        "default", "k1", helpful=False, tags=["too_shallow"], comment="not enough depth", guide_hash="h1"
    )
    router = ScriptedRouter(
        [
            {
                "patterns": [
                    {
                        "name": "shallow explanations",
                        "symptom": "notes restate claims instead of explaining them",
                        "evidence": "k1 flagged too_shallow",
                        "suggested_target_metric": "guide.depth",
                    }
                ]
            }
        ]
    )
    ctx = PipelineContext(settings=None, db=db, router=router, graph=InMemoryGraphStore())

    patterns = mine_patterns(ctx)

    assert len(patterns) == 1
    assert patterns[0]["name"] == "shallow explanations"
    assert patterns[0]["suggested_target_metric"] == "guide.depth"


class _RaisingRouter:
    def complete(self, role, request):
        raise ProviderBadOutputError("bad output")


def test_mine_patterns_degrades_to_empty_on_bad_output(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    db.add_note_feedback("default", "k1", helpful=False, tags=["wrong"], comment="", guide_hash=None)
    ctx = PipelineContext(settings=None, db=db, router=_RaisingRouter(), graph=InMemoryGraphStore())

    assert mine_patterns(ctx) == []


def test_mine_patterns_returns_empty_without_signal_and_skips_model_call(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    router = ScriptedRouter([])  # would raise IndexError if the router were called
    ctx = PipelineContext(settings=None, db=db, router=router, graph=InMemoryGraphStore())

    assert mine_patterns(ctx) == []
