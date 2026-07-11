import threading
import time

import pytest

from mslearn.graph.records import ConceptRecord
from mslearn.opsdb import OpsDB
from mslearn.pipeline.synthesis import cluster_new_claims, concept_match_claim_ids
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore
from tests.test_extraction_graph import ScriptedRouter


def make_ctx(tmp_path, graph, outputs):
    db = OpsDB(tmp_path / "ops.db")
    router = ScriptedRouter(outputs)
    return PipelineContext(settings=None, db=db, router=router, graph=graph), router


def test_cluster_singleton_when_no_candidates(tmp_path):
    graph = InMemoryGraphStore()
    graph.add_claim("cl1", "cache ttl", "neutral", "s1", [1.0, 0.0, 0.0])
    ctx, router = make_ctx(tmp_path, graph, [])
    dirty = cluster_new_claims(ctx)
    assert dirty == {"k-cl1"}
    assert graph.concept_id_of_claim("cl1") == "k-cl1"
    assert router.calls == []


def test_cluster_joins_existing_concept(tmp_path):
    graph = InMemoryGraphStore()
    graph.add_claim("cl0", "cache ttl", "neutral", "s1", [1.0, 0.0, 0.0])
    graph.add_claim("cl1", "cache expiry", "neutral", "s1", [0.99, 0.01, 0.0])
    graph.upsert_concept(ConceptRecord(concept_id="k-existing", name=""))
    graph.assign_claim("cl0", "k-existing")
    ctx, router = make_ctx(tmp_path, graph, [{"matches": ["cl0"]}])
    dirty = cluster_new_claims(ctx)
    assert dirty == {"k-existing"}
    assert graph.concept_id_of_claim("cl1") == "k-existing"
    assert router.calls == ["synthesis"]


def test_cluster_forms_new_concept_from_unassigned_matches(tmp_path):
    graph = InMemoryGraphStore()
    graph.add_claim("cl1", "cache ttl", "neutral", "s1", [1.0, 0.0, 0.0])
    graph.add_claim("cl2", "cache expiry", "neutral", "s1", [0.99, 0.01, 0.0])
    # cl1 and cl2 are mutual candidates of each other, so both are anchors
    # with non-empty candidate sets. Phase A fires the concept_match model
    # call for EVERY anchor concurrently (it has no way to know ahead of
    # time that cl2 will be swept into cl1's cluster -- that depends on
    # cl1's own model result), so both calls happen here, not just one as
    # in the old strictly-serial loop (which skipped cl2's call because by
    # the time the loop reached cl2, cl1's processing had already assigned
    # it). cl2's own precomputed result is simply discarded in Phase B once
    # it's found already-assigned. Both scripted outputs carry the same
    # content so the (racy, thread-scheduling-dependent) order in which the
    # two concurrent calls consume them doesn't change the outcome: for
    # whichever anchor's own id isn't a valid candidate for itself, it gets
    # filtered out as a hallucinated match (logged, doesn't affect the
    # asserted final state below).
    ctx, router = make_ctx(tmp_path, graph, [{"matches": ["cl1", "cl2"]}, {"matches": ["cl1", "cl2"]}])
    dirty = cluster_new_claims(ctx)
    assert dirty == {"k-cl1"}
    assert graph.concept_id_of_claim("cl1") == "k-cl1"
    assert graph.concept_id_of_claim("cl2") == "k-cl1"
    assert router.calls == ["synthesis", "synthesis"]


def test_cluster_drops_hallucinated_match_ids(tmp_path):
    graph = InMemoryGraphStore()
    graph.add_claim("cl1", "cache ttl", "neutral", "s1", [1.0, 0.0, 0.0])
    graph.add_claim("cl2", "cache expiry", "neutral", "s1", [0.99, 0.01, 0.0])
    ctx, router = make_ctx(tmp_path, graph, [{"matches": ["fake-id"]}, {"matches": []}])
    dirty = cluster_new_claims(ctx)
    assert dirty == {"k-cl1", "k-cl2"}
    assert graph.concept_id_of_claim("cl1") == "k-cl1"
    assert graph.concept_id_of_claim("cl2") == "k-cl2"
    assert router.calls == ["synthesis", "synthesis"]


def test_mint_or_reuse_concept_is_sticky_under_races(tmp_path):
    # A claim in the about-to-be-clustered set was assigned a concept by a
    # concurrent synthesis run in the tiny window between the caller's
    # unassigned-check and the mint. The old code minted a brand-new
    # f"k-{min(...)}" id anyway, silently re-homing the claim and breaking
    # sticky ids (and the Anki/markdown exports keyed on them). It must
    # reuse the concept that already claimed it instead.
    from mslearn.pipeline.synthesis import _mint_or_reuse_concept

    graph = InMemoryGraphStore()
    graph.add_claim("a1", "x", "neutral", "s1", [1.0, 0.0])
    graph.add_claim("b1", "y", "neutral", "s1", [1.0, 0.0])
    graph.upsert_concept(ConceptRecord(concept_id="k-b1", name=""))
    graph.assign_claim("b1", "k-b1")  # simulates a concurrent run's write

    # a1 < b1 alphabetically, so a naive mint would produce "k-a1" and
    # silently move b1's cluster identity out from under any export that
    # already used "k-b1".
    concept_id = _mint_or_reuse_concept(graph, set(), ["a1", "b1"])
    assert concept_id == "k-b1"


def test_cluster_skips_rejected_claims(tmp_path):
    graph = InMemoryGraphStore()
    graph.add_claim("cl_bad", "bad", "neutral", "s1", [1.0, 0.0, 0.0], trust="rejected")
    graph.add_claim("cl_ok", "good", "neutral", "s1", [1.0, 0.0, 0.0], trust="trusted")
    ctx, router = make_ctx(tmp_path, graph, [])
    dirty = cluster_new_claims(ctx)
    assert dirty == {"k-cl_ok"}
    assert graph.concept_id_of_claim("cl_bad") is None
    assert router.calls == []


def test_cluster_new_claims_parallel_same_assignments(tmp_path):
    # c1 and c2 are near-duplicate embeddings (mutual candidates); c3 is
    # orthogonal to both and has no candidates at all, so it never triggers
    # a model call and must land in its own concept.
    graph = InMemoryGraphStore()
    graph.add_claim("c1", "cache ttl short", "neutral", "s1", [1.0, 0.0, 0.0])
    graph.add_claim("c2", "cache ttl similar", "neutral", "s1", [0.99, 0.01, 0.0])
    graph.add_claim("c3", "unrelated topic", "neutral", "s1", [0.0, 1.0, 0.0])
    # Both c1 and c2 fire their own concept_match call in Phase A (each is a
    # candidate of the other, and Phase A cannot know ahead of time that one
    # will be swept into the other's cluster -- see
    # test_cluster_forms_new_concept_from_unassigned_matches for why). Both
    # scripted outputs carry identical content so the race between which
    # concurrent call consumes which output doesn't change the outcome.
    ctx, router = make_ctx(tmp_path, graph, [{"matches": ["c1", "c2"]}, {"matches": ["c1", "c2"]}])

    cluster_new_claims(ctx)

    assert graph.concept_id_of_claim("c1") == graph.concept_id_of_claim("c2")
    assert graph.concept_id_of_claim("c3") is not None
    assert graph.concept_id_of_claim("c3") != graph.concept_id_of_claim("c1")


def _make_match_ctx(tmp_path, outputs):
    db = OpsDB(tmp_path / "ops.db")
    router = ScriptedRouter(outputs)
    return PipelineContext(settings=None, db=db, router=router, graph=None), router


def test_concept_match_maps_positional_ids(tmp_path):
    # The concept_match model frequently returns a candidate's 1-based list
    # position ("2") instead of its real claim_id. That position must be
    # resolved back to the claim_id of the candidate presented at that spot
    # in _concept_match_prompt's numbered list -- not dropped as a stray id.
    anchor = {"claim_id": "anchor", "text": "anchor text"}
    candidates = [
        {"claim_id": "c-a", "text": "candidate a", "stance": "neutral"},
        {"claim_id": "c-b", "text": "candidate b", "stance": "neutral"},
        {"claim_id": "c-c", "text": "candidate c", "stance": "neutral"},
    ]
    ctx, router = _make_match_ctx(tmp_path, [{"matches": ["2"]}])
    matches = concept_match_claim_ids(ctx, anchor, candidates)
    assert matches == ["c-b"]
    assert router.calls == ["synthesis"]


def test_concept_match_drops_out_of_range(tmp_path):
    # "99" is neither a real claim id nor a valid 1-based index into a
    # 3-candidate list -- it must still be dropped, not resolved.
    anchor = {"claim_id": "anchor", "text": "anchor text"}
    candidates = [
        {"claim_id": "c-a", "text": "candidate a", "stance": "neutral"},
        {"claim_id": "c-b", "text": "candidate b", "stance": "neutral"},
        {"claim_id": "c-c", "text": "candidate c", "stance": "neutral"},
    ]
    ctx, router = _make_match_ctx(tmp_path, [{"matches": ["99"]}])
    matches = concept_match_claim_ids(ctx, anchor, candidates)
    assert matches == []
    assert router.calls == ["synthesis"]


def test_concept_match_exact_id_still_works(tmp_path):
    # Regression guard: a model that already returns the real claim_id
    # (the common, correct case) must keep working unchanged.
    anchor = {"claim_id": "anchor", "text": "anchor text"}
    candidates = [
        {"claim_id": "c-a", "text": "candidate a", "stance": "neutral"},
        {"claim_id": "c-b", "text": "candidate b", "stance": "neutral"},
        {"claim_id": "c-c", "text": "candidate c", "stance": "neutral"},
    ]
    ctx, router = _make_match_ctx(tmp_path, [{"matches": ["c-c"]}])
    matches = concept_match_claim_ids(ctx, anchor, candidates)
    assert matches == ["c-c"]
    assert router.calls == ["synthesis"]


class _TrackingRouter:
    """Fake router that sleeps ~40ms per call (standing in for real model
    latency) and records the maximum number of calls that were ever
    in-flight at once, proving cluster_new_claims actually overlaps its
    concept_match calls instead of running them one at a time. Unlike
    ScriptedRouter (which just pops a fixed queue in call order -- fine for
    serial code but meaningless once calls race each other), this fake
    reads the anchor claim id out of the prompt so each concurrent call
    gets the response that belongs to it, regardless of firing order."""

    def __init__(self, match_map: dict[str, list[str]]):
        self.match_map = match_map
        self.calls: list[str] = []
        self._lock = threading.Lock()
        self._in_flight = 0
        self.max_in_flight = 0

    def complete(self, role, request):
        with self._lock:
            self.calls.append(role)
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
        try:
            time.sleep(0.04)
            content = request.messages[0].content
            anchor_id = None
            for line in content.splitlines():
                if line.startswith("Anchor claim: "):
                    anchor_id = line[len("Anchor claim: ") :].split(" | ", 1)[0]
                    break
            from mslearn.providers.base import ModelResponse

            return ModelResponse(
                text="",
                parsed={"matches": self.match_map.get(anchor_id, [])},
                input_tokens=1,
                output_tokens=1,
                latency_ms=1.0,
                provider="fake",
                model="m",
            )
        finally:
            with self._lock:
                self._in_flight -= 1


def test_cluster_survives_bad_match_response(tmp_path):
    # A truncated/malformed concept_match response (e.g. deepseek-v4-flash
    # overflowing max_tokens on a large corpus) must not crash clustering --
    # the anchor should just fall back to getting its own concept instead of
    # taking down the whole synthesis run.
    from mslearn.providers.base import ProviderBadOutputError

    graph = InMemoryGraphStore()
    graph.add_claim("cl1", "cache ttl", "neutral", "s1", [1.0, 0.0, 0.0])
    graph.add_claim("cl2", "cache expiry", "neutral", "s1", [0.99, 0.01, 0.0])
    ctx, router = make_ctx(
        tmp_path,
        graph,
        [ProviderBadOutputError("truncated"), ProviderBadOutputError("truncated")],
    )

    dirty = cluster_new_claims(ctx)

    assert graph.concept_id_of_claim("cl1") is not None
    assert graph.concept_id_of_claim("cl2") is not None
    assert dirty


def test_cluster_propagates_transient_error(tmp_path):
    # ProviderTransientError (retryable 429/5xx) must NOT be swallowed as a
    # degradation case like ProviderBadOutputError -- it needs to propagate
    # out of cluster_new_claims so synthesize_task's autoretry_for kicks in
    # and Celery retries the whole synthesis run, instead of silently
    # degrading to "no match" and permanently over-splitting the curriculum.
    from mslearn.providers.base import ProviderTransientError

    graph = InMemoryGraphStore()
    graph.add_claim("cl1", "cache ttl", "neutral", "s1", [1.0, 0.0, 0.0])
    graph.add_claim("cl2", "cache expiry", "neutral", "s1", [0.99, 0.01, 0.0])
    ctx, router = make_ctx(
        tmp_path,
        graph,
        [ProviderTransientError("rate limited"), ProviderTransientError("rate limited")],
    )

    with pytest.raises(ProviderTransientError):
        cluster_new_claims(ctx)


def test_cluster_new_claims_runs_calls_in_parallel(tmp_path):
    # Two independent near-duplicate pairs -- (c1, c2) and (c3, c4) -- each
    # only a mutual candidate within its own pair, so all 4 anchors get a
    # non-empty candidate set and each fires its own concept_match call.
    graph = InMemoryGraphStore()
    graph.add_claim("c1", "topic a variant 1", "neutral", "s1", [1.0, 0.0, 0.0, 0.0])
    graph.add_claim("c2", "topic a variant 2", "neutral", "s1", [0.99, 0.01, 0.0, 0.0])
    graph.add_claim("c3", "topic b variant 1", "neutral", "s1", [0.0, 0.0, 1.0, 0.0])
    graph.add_claim("c4", "topic b variant 2", "neutral", "s1", [0.0, 0.0, 0.99, 0.01])

    router = _TrackingRouter({"c1": ["c2"], "c2": ["c1"], "c3": ["c4"], "c4": ["c3"]})
    db = OpsDB(tmp_path / "ops.db")
    db.set_tunable("synth.concurrency", 4.0, reason="test")
    ctx = PipelineContext(settings=None, db=db, router=router, graph=graph)

    cluster_new_claims(ctx)

    # With 4 anchors each sleeping ~40ms and a concurrency width of 4, a
    # serial implementation could only ever have 1 call in flight; this
    # proves Phase A actually overlaps them.
    assert router.max_in_flight >= 2
    assert len(router.calls) == 4
    assert graph.concept_id_of_claim("c1") == graph.concept_id_of_claim("c2")
    assert graph.concept_id_of_claim("c3") == graph.concept_id_of_claim("c4")
    assert graph.concept_id_of_claim("c1") != graph.concept_id_of_claim("c3")
