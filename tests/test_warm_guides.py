import json
import threading
import time

from mslearn.graph.records import ConceptRecord
from mslearn.opsdb import OpsDB
from mslearn.worker import tasks as worker_tasks
from mslearn.worker.context import PipelineContext, set_context
from tests.fakes import InMemoryGraphStore


def make_ctx(tmp_path, concept_ids):
    db = OpsDB(tmp_path / "ops.db")
    graph = InMemoryGraphStore()
    for cid in concept_ids:
        graph.upsert_concept(ConceptRecord(concept_id=cid, name=cid))
    ctx = PipelineContext(settings=None, db=db, router=None, graph=graph)
    set_context(ctx)
    return ctx


def test_warm_guides_generates_all(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path, ["k1", "k2", "k3"])
    lock = threading.Lock()
    called = []

    def fake_generate_guide(ctx_arg, concept_id, force=False, project_id="default"):
        with lock:
            called.append(concept_id)
        return {}, False

    monkeypatch.setattr(worker_tasks, "generate_guide", fake_generate_guide)

    result = worker_tasks.warm_guides_task(project_id="default")

    assert sorted(called) == ["k1", "k2", "k3"]
    assert result == 3
    raw = ctx.db.get_project_setting("default", "guides:progress")
    assert raw is not None
    progress = json.loads(raw)
    assert progress["done"] == progress["total"] == 3


def test_warm_guides_best_effort(tmp_path, monkeypatch):
    ctx = make_ctx(tmp_path, ["k1", "k2", "k3"])
    called = []

    def fake_generate_guide(ctx_arg, concept_id, force=False, project_id="default"):
        called.append(concept_id)
        if concept_id == "k2":
            raise RuntimeError("boom")
        return {}, False

    monkeypatch.setattr(worker_tasks, "generate_guide", fake_generate_guide)

    result = worker_tasks.warm_guides_task(project_id="default")  # must not raise

    assert sorted(called) == ["k1", "k2", "k3"]
    assert result == 3
    raw = ctx.db.get_project_setting("default", "guides:progress")
    progress = json.loads(raw)
    assert progress["done"] == progress["total"] == 3


def test_warm_guides_parallel(tmp_path, monkeypatch):
    concept_ids = [f"k{i}" for i in range(6)]
    ctx = make_ctx(tmp_path, concept_ids)
    ctx.db.set_tunable("synth.concurrency", 4.0, reason="test")

    lock = threading.Lock()
    state = {"in_flight": 0, "max_in_flight": 0}

    def fake_generate_guide(ctx_arg, concept_id, force=False, project_id="default"):
        with lock:
            state["in_flight"] += 1
            state["max_in_flight"] = max(state["max_in_flight"], state["in_flight"])
        time.sleep(0.04)
        with lock:
            state["in_flight"] -= 1
        return {}, False

    monkeypatch.setattr(worker_tasks, "generate_guide", fake_generate_guide)

    worker_tasks.warm_guides_task(project_id="default")

    assert state["max_in_flight"] >= 2
