from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from mslearn.graph.records import ConceptRecord
from mslearn.opsdb import OpsDB
from mslearn.server.app import create_app
from mslearn.settings import Settings
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore, InMemoryLearnerMemory, ScriptedRouter


def make_chat_ctx(tmp_path, router, *, memory=None):
    graph = InMemoryGraphStore()
    graph.upsert_concept(ConceptRecord("k1", "Cache TTLs", "TTL tradeoffs."))
    graph.add_claim(
        "c1",
        "A TTL bounds how long stale cached data can survive.",
        "neutral",
        "s1",
        [1.0, 0.0],
        quote="TTL bounds stale cached data",
        chunk_id="ch1",
    )
    graph.add_claim(
        "c2",
        "Very short TTLs can increase backend load.",
        "warns_against",
        "s2",
        [0.9, 0.0],
        quote="short TTLs can increase backend load",
        chunk_id="ch2",
    )
    graph.add_claim(
        "c3",
        "Rejected TTL advice.",
        "neutral",
        "s3",
        [0.95, 0.0],
        trust="rejected",
        chunk_id="ch3",
    )
    for claim_id in ("c1", "c2", "c3"):
        graph.assign_claim(claim_id, "k1")
    graph.add_conflict("c1", "c2", "context_dependent", "Freshness and load trade off.")
    graph.chunks["ch1"] = {
        "chunk_id": "ch1",
        "source_id": "s1",
        "seq": 1,
        "unit_index": 0,
        "text": "A TTL bounds how long stale cached data can survive.",
        "kind": "blog",
        "page": None,
        "href": None,
        "url": "https://example.com/ttl",
        "para_index": 2,
        "start_s": None,
        "end_s": None,
        "embedding": [1.0, 0.0],
    }
    graph.chunks["ch2"] = {
        "chunk_id": "ch2",
        "source_id": "s2",
        "seq": 2,
        "unit_index": 0,
        "text": "Very short TTLs can increase backend load.",
        "kind": "pdf",
        "page": 7,
        "href": None,
        "url": None,
        "para_index": None,
        "start_s": None,
        "end_s": None,
        "embedding": [0.9, 0.0],
    }
    return PipelineContext(
        settings=Settings(profiles_path=Path("profiles.yaml")),
        db=OpsDB(tmp_path / "ops.db"),
        router=router,
        graph=graph,
        memory=memory,
    )


def sse_payloads(text: str) -> list[dict]:
    return [
        json.loads(frame.removeprefix("data: "))
        for frame in text.strip().split("\n\n")
        if frame.startswith("data: ")
    ]


def test_chat_streams_deltas_done_citations_and_records_interaction(tmp_path):
    memory = InMemoryLearnerMemory()
    router = ScriptedRouter(
        embeddings=[[1.0, 0.0]],
        stream_chunks=[
            "A TTL bounds staleness [claim:c1]",
            ", while short TTLs can raise load [claim:c2].",
        ],
    )
    ctx = make_chat_ctx(tmp_path, router, memory=memory)
    app = create_app(context=ctx)

    with TestClient(app) as client:
        response = client.post(
            "/api/chat",
            json={"question": "How should I choose a cache TTL?", "session_id": "s-chat"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert sse_payloads(response.text) == [
        {"delta": "A TTL bounds staleness [claim:c1]"},
        {"delta": ", while short TTLs can raise load [claim:c2]."},
        {"done": True, "citations": ["c1", "c2"]},
    ]
    assert router.calls == ["interactive"]
    prompt = "\n".join(message.content for message in router.requests[0].messages)
    assert "answer ONLY from provided material" in prompt
    assert "never blend into one voice" in prompt
    assert "[claim:c1]" in prompt and "[claim:c2]" in prompt
    assert "[claim:c3]" not in prompt
    assert "PERSONALIZATION ONLY" in prompt
    items = memory.all()
    assert len(items) == 1
    assert items[0].category == "interaction"
    assert "How should I choose a cache TTL?" in items[0].text
    assert "A TTL bounds staleness" not in items[0].text


def test_chat_session_endpoint_returns_last_ten_turns(tmp_path):
    router = ScriptedRouter(
        embeddings=[[1.0, 0.0] for _ in range(12)],
        stream_chunks=["ok"],
    )
    ctx = make_chat_ctx(tmp_path, router)
    app = create_app(context=ctx)

    with TestClient(app) as client:
        for index in range(12):
            response = client.post(
                "/api/chat",
                json={"question": f"question {index}", "session_id": "trim-me"},
            )
            assert response.status_code == 200
        history = client.get("/api/chat/sessions/trim-me")

    assert history.status_code == 200
    turns = history.json()["turns"]
    assert len(turns) == 10
    assert [turn["question"] for turn in turns] == [f"question {index}" for index in range(2, 12)]
    assert turns[-1]["answer"] == "ok"


def test_mid_stream_provider_error_emits_error_frame(tmp_path):
    from mslearn.providers.base import ProviderError

    class ExplodingStreamRouter(ScriptedRouter):
        def stream(self, role, request):
            yield "partial "
            raise ProviderError("backend fell over")

    router = ExplodingStreamRouter([])
    ctx = make_chat_ctx(tmp_path, router)
    app = create_app(context=ctx)
    with TestClient(app) as client:
        response = client.post(
            "/api/chat", json={"question": "What is a TTL?", "session_id": "s-err"}
        )
        assert response.status_code == 200
        frames = [
            json.loads(line[len("data: "):])
            for line in response.text.split("\n\n")
            if line.startswith("data: ")
        ]
    assert {"delta": "partial "} in frames
    assert any("error" in frame and "backend fell over" in frame["error"] for frame in frames)
    assert not any(frame.get("done") for frame in frames)
