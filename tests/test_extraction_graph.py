from mslearn.opsdb import OpsDB
from mslearn.pipeline.extraction_graph import run_extraction
from mslearn.providers.base import ModelResponse, ProviderError

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
    state = run_extraction(router, db(tmp_path), "c1", CHUNK)
    assert [d.text for d in state["accepted"]] == ["Cache invalidation is hard."]
    assert state["rejected"] == [] and state["error"] is None
    assert router.calls == ["extraction"]


def test_retry_then_pass(tmp_path):
    router = ScriptedRouter([BAD, GOOD])
    state = run_extraction(router, db(tmp_path), "c1", CHUNK)
    assert len(state["accepted"]) == 1
    assert router.calls == ["extraction", "extraction"]


def test_escalation_after_exhausted_retries(tmp_path):
    router = ScriptedRouter([BAD, BAD, GOOD])
    state = run_extraction(router, db(tmp_path), "c1", CHUNK)
    assert len(state["accepted"]) == 1 and state["escalated"] is True
    assert router.calls == ["extraction", "extraction", "synthesis"]


def test_escalated_failure_ends_with_rejects(tmp_path):
    router = ScriptedRouter([BAD, BAD, BAD, BAD])
    state = run_extraction(router, db(tmp_path), "c1", CHUNK)
    assert state["accepted"] == [] and len(state["rejected"]) == 1
    assert state["escalated"] is True


def test_provider_error_sets_error(tmp_path):
    router = ScriptedRouter([ProviderError("down")])
    state = run_extraction(router, db(tmp_path), "c1", CHUNK)
    assert state["error"] == "down" and state["accepted"] == []


def test_empty_claims_is_valid_end(tmp_path):
    router = ScriptedRouter([{"claims": []}])
    state = run_extraction(router, db(tmp_path), "c1", CHUNK)
    assert state["accepted"] == [] and state["rejected"] == [] and state["error"] is None


def test_parse_error_retry_then_pass(tmp_path):
    router = ScriptedRouter([MALFORMED, GOOD])
    state = run_extraction(router, db(tmp_path), "c1", CHUNK)
    assert len(state["accepted"]) == 1
    assert [d.text for d in state["accepted"]] == ["Cache invalidation is hard."]
    assert router.calls == ["extraction", "extraction"]


def test_parse_error_exhausted_escalates_with_rejects(tmp_path):
    router = ScriptedRouter([MALFORMED, MALFORMED, MALFORMED, MALFORMED])
    state = run_extraction(router, db(tmp_path), "c1", CHUNK)
    assert state["escalated"] is True
    assert state["accepted"] == []
    assert len(state["rejected"]) >= 1
    assert state["error"] is None
