from pathlib import Path

from fastapi.testclient import TestClient

from mslearn.graph.records import ConceptRecord
from mslearn.opsdb import OpsDB
from mslearn.server.app import create_app
from mslearn.settings import Settings
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore, ScriptedRouter


def _client(graph: InMemoryGraphStore, tmp_path: Path) -> TestClient:
    ctx = PipelineContext(
        settings=Settings(profiles_path=Path("profiles.yaml")),
        db=OpsDB(tmp_path / "ops.db"),
        router=ScriptedRouter(outputs=[]),
        graph=graph,
    )
    app = create_app(context=ctx)
    return TestClient(app)


def test_outline_endpoint_returns_nested_tree_for_structured_project(tmp_path):
    graph = InMemoryGraphStore()
    graph.upsert_concept(ConceptRecord("k1", "Numbers", "About numbers."))
    graph.set_concept_meta("k1", order_index=0)
    graph.concepts["k1"]["section_path"] = ["Ch1", "1.1"]
    graph.upsert_concept(ConceptRecord("k2", "History", "About history."))
    graph.set_concept_meta("k2", order_index=1)
    graph.concepts["k2"]["section_path"] = ["Ch2"]

    with _client(graph, tmp_path) as client:
        response = client.get("/api/study/outline")

    assert response.status_code == 200
    body = response.json()
    assert body["has_structure"] is True
    assert [n["title"] for n in body["tree"]] == ["Ch1", "Ch2"]
    assert body["tree"][0]["children"][0]["title"] == "1.1"
    assert body["tree"][0]["children"][0]["concepts"] == [
        {"concept_id": "k1", "name": "Numbers", "conflict_count": 0}
    ]
    assert body["flat"] == []


def test_outline_endpoint_flat_for_unstructured_project(tmp_path):
    graph = InMemoryGraphStore()
    graph.upsert_concept(ConceptRecord("k1", "Numbers", "About numbers."))
    graph.set_concept_meta("k1", order_index=0)

    with _client(graph, tmp_path) as client:
        response = client.get("/api/study/outline")

    assert response.status_code == 200
    body = response.json()
    assert body["has_structure"] is False
    assert body["tree"] == []
    assert body["flat"] == [{"concept_id": "k1", "name": "Numbers", "conflict_count": 0}]
