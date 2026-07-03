from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mslearn.graph.records import ConceptRecord
from mslearn.opsdb import OpsDB
from mslearn.providers.base import ModelResponse, ProviderBadOutputError
from mslearn.server.app import create_app
from mslearn.settings import Settings
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore, InMemoryLearnerMemory


class CapturingScriptedRouter:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []
        self.requests = []

    def complete(self, role, request):
        self.calls.append(role)
        self.requests.append(request)
        out = self.outputs.pop(0)
        if isinstance(out, Exception):
            raise out
        return ModelResponse(
            text="",
            parsed=out,
            input_tokens=1,
            output_tokens=1,
            latency_ms=1.0,
            provider="fake",
            model="m",
        )


def make_ctx(tmp_path, router, *, memory=None):
    graph = InMemoryGraphStore()
    graph.upsert_concept(
        ConceptRecord("k1", "Cache invalidation", "Know when cached values become stale.")
    )
    graph.set_concept_meta("k1", order_index=0)
    graph.add_claim(
        "c1",
        "A TTL bounds how long stale cached data can survive.",
        "neutral",
        "s1",
        [1.0, 0.0],
        quote="TTL bounds stale cached data",
        chunk_id="ch1",
    )
    graph.assign_claim("c1", "k1")
    graph.upsert_concept(
        ConceptRecord("k2", "Write through caching", "Persist writes before cache updates.")
    )
    graph.set_concept_meta("k2", order_index=1)
    graph.add_claim(
        "c2",
        "Write-through caching persists data before updating cached values.",
        "neutral",
        "s2",
        [0.0, 1.0],
        quote="persists data before updating cached values",
        chunk_id="ch2",
    )
    graph.assign_claim("c2", "k2")
    return PipelineContext(
        settings=Settings(profiles_path=Path("profiles.yaml")),
        db=OpsDB(tmp_path / "ops.db"),
        router=router,
        graph=graph,
        memory=memory,
    )


def make_client(ctx):
    app = create_app(context=ctx)
    return TestClient(app)


def test_quiz_next_generates_reasoning_question_and_caches_pending(tmp_path):
    router = CapturingScriptedRouter(
        [
            {
                "question": "Why does a TTL reduce stale-cache risk?",
                "expected_points": ["TTL bounds how long stale cached data can survive [claim:c1]."],
            }
        ]
    )
    ctx = make_ctx(tmp_path, router)

    with make_client(ctx) as client:
        response = client.get("/api/quiz/next", params={"session_id": "sess-1"})

    assert response.status_code == 200
    assert response.json() == {
        "concept_id": "k1",
        "question": "Why does a TTL reduce stale-cache risk?",
    }
    assert router.calls == ["synthesis"]
    assert router.requests[0].json_schema is not None
    prompt = router.requests[0].messages[0].content
    assert "reasoning" in prompt.lower()
    assert "[claim:c1]" in prompt
    pending = json.loads(ctx.db.get_setting("quiz:pending:sess-1:k1"))
    assert pending["question"] == "Why does a TTL reduce stale-cache risk?"
    assert pending["expected_points"] == [
        "TTL bounds how long stale cached data can survive [claim:c1]."
    ]


def test_pending_question_is_session_scoped_and_deleted_after_grading(tmp_path):
    router = CapturingScriptedRouter(
        [
            {
                "question": "Session A's question",
                "expected_points": ["point a [claim:c1]."],
            },
            {
                "question": "Session B's question",
                "expected_points": ["point b [claim:c1]."],
            },
            {
                "correct": True,
                "score_0_100": 90,
                "explanation": "Nicely done [claim:c1].",
            },
        ]
    )
    ctx = make_ctx(tmp_path, router)

    with make_client(ctx) as client:
        r_a = client.get("/api/quiz/next", params={"session_id": "sess-a"})
        r_b = client.get("/api/quiz/next", params={"session_id": "sess-b"})
        assert r_a.json()["question"] == "Session A's question"
        assert r_b.json()["question"] == "Session B's question"

        # Session A's pending question is untouched by session B's /next call.
        pending_a = json.loads(ctx.db.get_setting("quiz:pending:sess-a:k1"))
        assert pending_a["question"] == "Session A's question"

        grade = client.post(
            "/api/quiz/answer",
            json={"concept_id": "k1", "answer": "answer", "session_id": "sess-a"},
        )
        assert grade.status_code == 200

    # Graded slot is deleted...
    assert ctx.db.get_setting("quiz:pending:sess-a:k1") is None
    # ...and a replay of the same answer against the now-deleted slot fails
    # instead of re-grading against a stale cached question.
    with make_client(ctx) as client:
        replay = client.post(
            "/api/quiz/answer",
            json={"concept_id": "k1", "answer": "answer again", "session_id": "sess-a"},
        )
    assert replay.status_code == 404
    # Session B's pending question survived session A's grading.
    assert ctx.db.get_setting("quiz:pending:sess-b:k1") is not None


def test_quiz_question_excludes_rejected_claims(tmp_path):
    router = CapturingScriptedRouter(
        [{"question": "Why TTL?", "expected_points": ["TTL bounds stale data [claim:c1]."]}]
    )
    ctx = make_ctx(tmp_path, router)
    ctx.graph.add_claim(
        "c3",
        "Rejected bogus quiz fact.",
        "neutral",
        "s3",
        [0.5, 0.5],
        trust="rejected",
        quote="bogus",
        chunk_id="ch3",
    )
    ctx.graph.assign_claim("c3", "k1")

    with make_client(ctx) as client:
        response = client.get("/api/quiz/next", params={"session_id": "sess-1"})

    assert response.status_code == 200
    prompt = router.requests[0].messages[0].content
    assert "Rejected bogus quiz fact" not in prompt
    assert "[claim:c3]" not in prompt
    assert "[claim:c1]" in prompt


def test_quiz_next_prefers_recent_failures_before_unquizzed_concepts(tmp_path):
    router = CapturingScriptedRouter(
        [
            {
                "question": "Why should writes persist before cache updates?",
                "expected_points": [
                    "Write-through persists data before updating cached values [claim:c2]."
                ],
            }
        ]
    )
    ctx = make_ctx(tmp_path, router)
    ctx.db.record_quiz_result("k2", correct=False, score=35)

    with make_client(ctx) as client:
        response = client.get("/api/quiz/next", params={"session_id": "sess-1"})

    assert response.status_code == 200
    assert response.json()["concept_id"] == "k2"


def test_quiz_answer_records_failure_and_stores_struggle_not_answer(tmp_path):
    memory = InMemoryLearnerMemory()
    router = CapturingScriptedRouter(
        [
            {
                "correct": False,
                "score_0_100": 40,
                "explanation": "Missed the TTL stale-data bound [claim:c1].",
            }
        ]
    )
    ctx = make_ctx(tmp_path, router, memory=memory)
    ctx.db.set_setting(
        "quiz:pending:sess-1:k1",
        json.dumps(
            {
                "question": "Why does a TTL reduce stale-cache risk?",
                "expected_points": [
                    "TTL bounds how long stale cached data can survive [claim:c1]."
                ],
            }
        ),
    )

    with make_client(ctx) as client:
        response = client.post(
            "/api/quiz/answer",
            json={"concept_id": "k1", "answer": "my private wrong answer", "session_id": "sess-1"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "correct": False,
        "score_0_100": 40,
        "explanation": "Missed the TTL stale-data bound [claim:c1].",
    }
    assert router.calls == ["synthesis"]
    assert router.requests[0].json_schema is not None
    stats = ctx.db.quiz_stats("k1")
    assert stats["attempts"] == 1
    assert stats["incorrect"] == 1
    assert stats["avg_score"] == pytest.approx(40.0)
    items = memory.all()
    assert len(items) == 1
    assert items[0].category == "struggle"
    assert items[0].text == (
        "struggled with Cache invalidation: "
        "TTL bounds how long stale cached data can survive [claim:c1]."
    )
    assert "my private wrong answer" not in items[0].text


def test_quiz_stats_endpoint_returns_per_concept_aggregates(tmp_path):
    ctx = make_ctx(tmp_path, CapturingScriptedRouter([]))
    ctx.db.record_quiz_result("k1", correct=True, score=100)
    ctx.db.record_quiz_result("k1", correct=False, score=25)
    ctx.db.record_quiz_result("k2", correct=True, score=80)

    with make_client(ctx) as client:
        response = client.get("/api/quiz/stats")

    assert response.status_code == 200
    assert response.json() == [
        {
            "concept_id": "k1",
            "attempts": 2,
            "correct": 1,
            "incorrect": 1,
            "avg_score": 62.5,
            "last_score": 25,
            "last_correct": False,
        },
        {
            "concept_id": "k2",
            "attempts": 1,
            "correct": 1,
            "incorrect": 0,
            "avg_score": 80.0,
            "last_score": 80,
            "last_correct": True,
        },
    ]


def test_bad_quiz_judge_output_surfaces_as_502(tmp_path):
    router = CapturingScriptedRouter(
        [ProviderBadOutputError("invalid quiz_grade schema: score_0_100 is required")]
    )
    ctx = make_ctx(tmp_path, router)
    ctx.db.set_setting(
        "quiz:pending:sess-1:k1",
        json.dumps(
            {
                "question": "Why does a TTL reduce stale-cache risk?",
                "expected_points": [
                    "TTL bounds how long stale cached data can survive [claim:c1]."
                ],
            }
        ),
    )

    with make_client(ctx) as client:
        response = client.post(
            "/api/quiz/answer",
            json={"concept_id": "k1", "answer": "Because it expires entries.", "session_id": "sess-1"},
        )

    assert response.status_code == 502
    assert "score_0_100 is required" in response.json()["detail"]
