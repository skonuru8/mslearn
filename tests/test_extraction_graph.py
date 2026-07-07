from mslearn.opsdb import OpsDB
from mslearn.pipeline.extraction_graph import build_extraction_graph, run_extraction
from mslearn.prompts import get_prompt
from mslearn.providers.base import ModelResponse, ProviderBadOutputError, ProviderError

CHUNK = "Cache invalidation is one of the two hard problems in computer science."
GOOD = {"claims": [{"text": "Cache invalidation is hard.", "stance": "neutral",
                    "quote": "Cache invalidation is one of the two hard problems"}]}
BAD = {"claims": [{"text": "Bananas are yellow.", "stance": "neutral",
                   "quote": "this text does not appear anywhere at all"}]}
MALFORMED = {"nope": "not the claims key"}


class ScriptedRouter:
    """Returns queued responses; records the roles used."""

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def complete(self, role, request):
        self.calls.append(role)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return ModelResponse(text="", parsed=out, input_tokens=1, output_tokens=1,
                             latency_ms=1.0, provider="fake", model="m")

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


def db(tmp_path):
    return OpsDB(tmp_path / "ops.db")


def test_clean_pass_single_attempt(tmp_path):
    router = ScriptedRouter([GOOD])
    state = run_extraction(build_extraction_graph(router, db(tmp_path)), "c1", CHUNK)
    assert [d.text for d in state["accepted"]] == ["Cache invalidation is hard."]
    assert state["rejected"] == [] and state["error"] is None
    assert router.calls == ["extraction"]


def test_retry_then_pass(tmp_path):
    router = ScriptedRouter([BAD, GOOD])
    state = run_extraction(build_extraction_graph(router, db(tmp_path)), "c1", CHUNK)
    assert len(state["accepted"]) == 1
    assert router.calls == ["extraction", "extraction"]


def test_escalation_after_exhausted_retries(tmp_path):
    router = ScriptedRouter([BAD, BAD, GOOD])
    state = run_extraction(build_extraction_graph(router, db(tmp_path)), "c1", CHUNK)
    assert len(state["accepted"]) == 1 and state["escalated"] is True
    assert router.calls == ["extraction", "extraction", "synthesis"]


def test_escalated_failure_ends_with_rejects(tmp_path):
    router = ScriptedRouter([BAD, BAD, BAD, BAD])
    state = run_extraction(build_extraction_graph(router, db(tmp_path)), "c1", CHUNK)
    assert state["accepted"] == [] and len(state["rejected"]) == 1
    assert state["escalated"] is True


def test_provider_error_sets_error(tmp_path):
    router = ScriptedRouter([ProviderError("down")])
    state = run_extraction(build_extraction_graph(router, db(tmp_path)), "c1", CHUNK)
    assert state["error"] == "down" and state["accepted"] == []


def test_bad_output_retries_then_passes(tmp_path):
    # The user's exact symptom: a truncated-JSON ProviderBadOutputError (e.g.
    # finish_reason=length) must not fail the chunk permanently — it gets a
    # retry within extract.max_attempts, same as a parse failure.
    router = ScriptedRouter([ProviderBadOutputError("invalid JSON from openrouter"), GOOD])
    state = run_extraction(build_extraction_graph(router, db(tmp_path)), "c1", CHUNK)
    assert len(state["accepted"]) == 1
    assert state["error"] is None
    assert router.calls == ["extraction", "extraction"]


def test_bad_output_exhausted_escalates_with_rejects(tmp_path):
    router = ScriptedRouter([ProviderBadOutputError("bad")] * 4)
    state = run_extraction(build_extraction_graph(router, db(tmp_path)), "c1", CHUNK)
    assert state["escalated"] is True
    assert state["accepted"] == []
    assert state["error"] is None


def test_empty_claims_is_valid_end(tmp_path):
    router = ScriptedRouter([{"claims": []}])
    state = run_extraction(build_extraction_graph(router, db(tmp_path)), "c1", CHUNK)
    assert state["accepted"] == [] and state["rejected"] == [] and state["error"] is None


def test_parse_error_retry_then_pass(tmp_path):
    router = ScriptedRouter([MALFORMED, GOOD])
    state = run_extraction(build_extraction_graph(router, db(tmp_path)), "c1", CHUNK)
    assert len(state["accepted"]) == 1
    assert [d.text for d in state["accepted"]] == ["Cache invalidation is hard."]
    assert router.calls == ["extraction", "extraction"]


def test_parse_error_exhausted_escalates_with_rejects(tmp_path):
    router = ScriptedRouter([MALFORMED, MALFORMED, MALFORMED, MALFORMED])
    state = run_extraction(build_extraction_graph(router, db(tmp_path)), "c1", CHUNK)
    assert state["escalated"] is True
    assert state["accepted"] == []
    assert len(state["rejected"]) >= 1
    assert state["error"] is None


def test_accepted_claims_not_duplicated_across_retries(tmp_path):
    # Attempt 1: one good claim + one bad (gets rejected, triggers a retry).
    # Attempt 2: the model re-emits the same good claim (common LLM behaviour
    # on a retry prompt). It must not be re-validated/duplicated in `accepted`.
    one_good_one_bad = {"claims": [
        {"text": "Cache invalidation is hard.", "stance": "neutral",
         "quote": "Cache invalidation is one of the two hard problems"},
        {"text": "Bananas are yellow.", "stance": "neutral",
         "quote": "this text does not appear anywhere at all"},
    ]}
    retry_repeats_the_good_one = {"claims": [
        {"text": "Cache invalidation is hard.", "stance": "neutral",
         "quote": "Cache invalidation is one of the two hard problems"},
    ]}
    router = ScriptedRouter([one_good_one_bad, retry_repeats_the_good_one])
    state = run_extraction(build_extraction_graph(router, db(tmp_path)), "c1", CHUNK)
    assert [d.text for d in state["accepted"]] == ["Cache invalidation is hard."]
    assert state["rejected"] == []
    assert router.calls == ["extraction", "extraction"]


def test_extraction_request_uses_max_tokens_tunable(tmp_path):
    class RecordingRouter(ScriptedRouter):
        def __init__(self, outputs):
            super().__init__(outputs)
            self.requests = []

        def complete(self, role, request):
            self.requests.append(request)
            return super().complete(role, request)

    router = RecordingRouter([GOOD])
    d = db(tmp_path)
    d.set_tunable("extract.max_tokens", 4096.0, "test")
    run_extraction(build_extraction_graph(router, d), "c1", CHUNK)
    assert router.requests[0].max_tokens == 4096


def test_extraction_prompt_mentions_kind(tmp_path):
    p = get_prompt(db(tmp_path), "extraction")
    assert "kind" in p and "mechanism" in p and "caveat" in p


def test_run_extraction_reuses_prebuilt_graph(tmp_path, monkeypatch):
    # run_extraction takes a prebuilt graph and must NOT rebuild it internally
    # — building recompiles the StateGraph and re-reads every tunable, which
    # extract_chunk_task previously paid on every single chunk.
    import mslearn.pipeline.extraction_graph as eg

    calls = {"n": 0}
    orig = eg.build_extraction_graph

    def counting(router, db):
        calls["n"] += 1
        return orig(router, db)

    monkeypatch.setattr(eg, "build_extraction_graph", counting)
    router = ScriptedRouter([GOOD, GOOD])
    graph = eg.build_extraction_graph(router, db(tmp_path))
    eg.run_extraction(graph, "s:1", CHUNK)
    eg.run_extraction(graph, "s:2", CHUNK)
    assert calls["n"] == 1  # built once, not per chunk


def test_extraction_prompt_injects_max_claims_tunable(tmp_path):
    class RecordingRouter(ScriptedRouter):
        def __init__(self, outputs):
            super().__init__(outputs)
            self.requests = []

        def complete(self, role, request):
            self.requests.append(request)
            return super().complete(role, request)

    router = RecordingRouter([GOOD])
    d = db(tmp_path)
    d.set_tunable("extract.max_claims", 5.0, "test")
    run_extraction(build_extraction_graph(router, d), "c1", CHUNK)
    prompt_text = router.requests[0].messages[0].content
    assert "at most 5 claims" in prompt_text
    assert "{max_claims}" not in prompt_text
    assert '{"claims": []}' in prompt_text
