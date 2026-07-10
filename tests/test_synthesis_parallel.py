import threading
import time

from mslearn.graph.records import ConceptRecord
from mslearn.opsdb import OpsDB
from mslearn.pipeline.synthesis import process_dirty_concepts
from mslearn.providers.base import ModelResponse
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore


class ConcurrencyTrackingRouter:
    """Fake router that sleeps per call and records the max number of
    concurrently in-flight `complete()` calls, proving (or disproving)
    that callers actually run work in parallel rather than serially."""

    def __init__(self):
        self._lock = threading.Lock()
        self._in_flight = 0
        self.max_in_flight = 0
        self.calls = []

    def complete(self, role, request):
        with self._lock:
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
            self.calls.append(role)
        try:
            time.sleep(0.05)
            content = request.messages[0].content
            if "conflict" in content.lower():
                parsed = {"conflicts": []}
            else:
                parsed = {"name": f"Name for {content[-4:]}", "summary": "A summary."}
            return ModelResponse(
                text="",
                parsed=parsed,
                input_tokens=1,
                output_tokens=1,
                latency_ms=1.0,
                provider="fake",
                model="m",
            )
        finally:
            with self._lock:
                self._in_flight -= 1


def test_process_dirty_concepts_runs_concurrently(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    db.set_tunable("synth.concurrency", 4, reason="test")

    graph = InMemoryGraphStore()
    for i in range(4):
        concept_id = f"k{i}"
        graph.upsert_concept(ConceptRecord(concept_id=concept_id, name=""))
        graph.add_claim(f"c{i}a", f"claim {i}a", "neutral", f"s{i}", [1.0, 0.0])
        graph.add_claim(f"c{i}b", f"claim {i}b", "neutral", f"s{i}", [0.9, 0.0])
        graph.assign_claim(f"c{i}a", concept_id)
        graph.assign_claim(f"c{i}b", concept_id)
        graph.mark_concept_dirty(concept_id, True)

    router = ConcurrencyTrackingRouter()
    ctx = PipelineContext(settings=None, db=db, router=router, graph=graph)

    result = process_dirty_concepts(ctx)

    assert result == 4
    for i in range(4):
        concept = graph.get_concept(f"k{i}")
        assert concept["name"] != ""

    assert router.max_in_flight >= 2, (
        f"expected parallel model calls, got max_in_flight={router.max_in_flight}"
    )
