from pathlib import Path

import pytest

from mslearn.graph.records import ConceptRecord
from mslearn.opsdb import OpsDB
from mslearn.pipeline.teaching import TeachingError, generate_teaching
from mslearn.providers.base import ModelResponse
from mslearn.settings import Settings
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore, InMemoryLearnerMemory


class TeachingRouter:
    def __init__(self, texts: list[str]):
        self.texts = list(texts)
        self.calls = []
        self.requests = []

    def complete(self, role, request):
        self.calls.append(role)
        self.requests.append(request)
        text = self.texts.pop(0)
        return ModelResponse(
            text=text,
            parsed=None,
            input_tokens=1,
            output_tokens=1,
            latency_ms=1.0,
            provider="fake",
            model="m",
        )


def make_ctx(tmp_path, router, *, memory=None):
    return PipelineContext(
        settings=Settings(profiles_path=Path("profiles.yaml")),
        db=OpsDB(tmp_path / "ops.db"),
        router=router,
        graph=InMemoryGraphStore(),
        memory=memory,
    )


def seed_concept(graph: InMemoryGraphStore, *, dirty: bool = False) -> None:
    graph.upsert_concept(
        ConceptRecord(
            concept_id="k1",
            name="Cache invalidation",
            summary="Know when cached values become stale.",
        )
    )
    graph.add_claim(
        "c1",
        "Cache invalidation is difficult.",
        "neutral",
        "s1",
        [1.0, 0.0],
        quote="Cache invalidation is one of the two hard problems",
        chunk_id="ch1",
    )
    graph.assign_claim("c1", "k1")
    graph.mark_concept_dirty("k1", dirty)


def good_markdown() -> str:
    return "\n".join(
        [
            "## Explanation",
            "Cache invalidation is difficult. [claim:c1]",
            "## Worked example",
            "Expire stale cache entries. [claim:c1]",
            "## Common misconception",
            "Caching never removes freshness work. [claim:c1]",
        ]
    )


def test_generate_teaching_caches_after_first_call(tmp_path):
    router = TeachingRouter([good_markdown()])
    ctx = make_ctx(tmp_path, router)
    seed_concept(ctx.graph)

    first = generate_teaching(ctx, "k1")
    second = generate_teaching(ctx, "k1")

    assert first == second == good_markdown()
    assert router.calls == ["synthesis"]
    assert ctx.graph.get_concept("k1")["teach_md"] == good_markdown()
    assert router.requests[0].max_tokens == int(ctx.db.get_tunable("teach.max_tokens"))


def test_generate_teaching_force_regenerates_cached_teaching(tmp_path):
    router = TeachingRouter([good_markdown()])
    ctx = make_ctx(tmp_path, router)
    seed_concept(ctx.graph)
    ctx.graph.set_concept_teaching("k1", "old cached lesson")

    result = generate_teaching(ctx, "k1", force=True)

    assert result == good_markdown()
    assert router.calls == ["synthesis"]
    assert ctx.graph.get_concept("k1")["teach_md"] == good_markdown()


def test_generate_teaching_retries_once_when_conflict_section_missing(tmp_path):
    corrected = good_markdown() + "\n## Where sources disagree\nOne side: [claim:c1]. Other: [claim:c2]."
    router = TeachingRouter([good_markdown(), corrected])
    ctx = make_ctx(tmp_path, router)
    seed_concept(ctx.graph)
    ctx.graph.add_claim(
        "c2",
        "Cache invalidation can be simple with short TTLs.",
        "neutral",
        "s2",
        [0.0, 1.0],
        quote="simple with short TTLs",
        chunk_id="ch2",
    )
    ctx.graph.assign_claim("c2", "k1")
    ctx.graph.add_conflict("c1", "c2", "context_dependent", "Different operating conditions.")

    result = generate_teaching(ctx, "k1")

    assert result == corrected
    assert router.calls == ["synthesis", "synthesis"]
    retry_prompt = router.requests[-1].messages[-1].content
    assert "must include `## Where sources disagree`" in retry_prompt


def test_generate_teaching_errors_after_retry_missing_conflict_section(tmp_path):
    router = TeachingRouter([good_markdown(), good_markdown()])
    ctx = make_ctx(tmp_path, router)
    seed_concept(ctx.graph)
    ctx.graph.add_claim(
        "c2",
        "Cache invalidation can be simple with short TTLs.",
        "neutral",
        "s2",
        [0.0, 1.0],
        quote="simple with short TTLs",
        chunk_id="ch2",
    )
    ctx.graph.assign_claim("c2", "k1")
    ctx.graph.add_conflict("c1", "c2", "context_dependent", "Different operating conditions.")

    with pytest.raises(TeachingError):
        generate_teaching(ctx, "k1")

    assert router.calls == ["synthesis", "synthesis"]


def test_generate_teaching_includes_memory_hints_as_personalization_only(tmp_path):
    memory = InMemoryLearnerMemory()
    memory.add("Cache invalidation: learner likes database examples", "preference")
    router = TeachingRouter([good_markdown()])
    ctx = make_ctx(tmp_path, router, memory=memory)
    seed_concept(ctx.graph)

    generate_teaching(ctx, "k1")

    prompt = router.requests[0].messages[0].content
    assert "PERSONALIZATION ONLY:" in prompt
    assert "learner likes database examples" in prompt
    assert router.requests[0].json_schema is None


def test_generate_teaching_excludes_rejected_claims(tmp_path):
    router = TeachingRouter([good_markdown()])
    ctx = make_ctx(tmp_path, router)
    seed_concept(ctx.graph)
    ctx.graph.add_claim(
        "c2",
        "Rejected bogus fact.",
        "neutral",
        "s2",
        [0.0, 1.0],
        trust="rejected",
        quote="bogus",
        chunk_id="ch2",
    )
    ctx.graph.assign_claim("c2", "k1")

    generate_teaching(ctx, "k1")

    prompt = router.requests[0].messages[0].content
    assert "[claim:c1]" in prompt
    assert "Rejected bogus fact" not in prompt
    assert "[claim:c2]" not in prompt
