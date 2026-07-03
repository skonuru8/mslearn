from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from mslearn.graph.records import ConceptRecord
from mslearn.opsdb import OpsDB
from mslearn.pipeline.exports import export_anki, export_graph, export_markdown
from mslearn.server.app import create_app
from mslearn.settings import Settings
from mslearn.worker.context import PipelineContext
from tests.fakes import InMemoryGraphStore, ScriptedRouter


def make_export_ctx(tmp_path, router=None):
    graph = InMemoryGraphStore()
    graph.upsert_concept(ConceptRecord("k1", "Cache invalidation", "Know stale-cache risk."))
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
    graph.add_claim(
        "c2",
        "Very short TTLs can increase backend load.",
        "warns_against",
        "s2",
        [0.9, 0.0],
        quote="short TTLs increase backend load",
        chunk_id="ch2",
    )
    graph.assign_claim("c1", "k1")
    graph.assign_claim("c2", "k1")
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
        "url": "https://example.com/cache",
        "para_index": 3,
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
    graph.upsert_concept(ConceptRecord("k2", "Write through caching", "Persist before cache."))
    graph.set_concept_meta("k2", order_index=1)
    graph.set_concept_teaching("k2", "## Cached Lesson\n\nUse the cached teaching markdown.")
    return PipelineContext(
        settings=Settings(profiles_path=Path("profiles.yaml")),
        db=OpsDB(tmp_path / "ops.db"),
        router=router or ScriptedRouter([]),
        graph=graph,
    )


def test_export_markdown_is_deterministic_without_model_calls(tmp_path):
    router = ScriptedRouter(["should not be used"])
    ctx = make_export_ctx(tmp_path, router)

    paths = export_markdown(ctx, tmp_path / "markdown")

    assert router.calls == []
    assert [path.name for path in paths] == [
        "000-cache-invalidation.md",
        "001-write-through-caching.md",
        "_index.md",
    ]
    markdown = (tmp_path / "markdown" / "000-cache-invalidation.md").read_text()
    assert "# Cache invalidation" in markdown
    assert "A TTL bounds how long stale cached data can survive. [^1]" in markdown
    assert "> TTL bounds stale cached data" in markdown
    assert "## Conflicts" in markdown
    assert "Freshness and load trade off." in markdown
    assert "[^1]: s1, blog, seq 1, paragraph 3, https://example.com/cache" in markdown
    cached = (tmp_path / "markdown" / "001-write-through-caching.md").read_text()
    assert cached == "## Cached Lesson\n\nUse the cached teaching markdown.\n"


def test_export_anki_writes_readable_package_with_stable_note_content(tmp_path):
    ctx = make_export_ctx(tmp_path)

    out_path = export_anki(ctx, tmp_path / "mslearn.apkg")

    assert out_path == tmp_path / "mslearn.apkg"
    assert out_path.exists()
    with zipfile.ZipFile(out_path) as package:
        assert "collection.anki2" in package.namelist()
        package.extract("collection.anki2", tmp_path)
    db = sqlite3.connect(tmp_path / "collection.anki2")
    try:
        rows = [row[0].split("\x1f") for row in db.execute("SELECT flds FROM notes")]
    finally:
        db.close()
    fields = "\n".join("\n".join(row) for row in rows)
    assert "Explain: Cache invalidation" in fields
    assert "Where do sources disagree on Cache invalidation?" in fields
    assert "Freshness and load trade off." in fields


def test_export_graph_writes_graphml_and_json(tmp_path):
    ctx = make_export_ctx(tmp_path)

    paths = export_graph(ctx, tmp_path / "graph")

    assert [path.name for path in paths] == ["graph.graphml", "graph.json"]
    graph_json = json.loads((tmp_path / "graph" / "graph.json").read_text())
    assert any(node["id"] == "concept:k1" for node in graph_json["nodes"])
    assert any(rel["type"] == "IN_CONCEPT" for rel in graph_json["relationships"])
    assert (tmp_path / "graph" / "graph.graphml").exists()


def test_exports_endpoint_writes_selected_kinds_under_timestamp(tmp_path, monkeypatch):
    ctx = make_export_ctx(tmp_path)
    monkeypatch.chdir(tmp_path)
    app = create_app(context=ctx)

    with TestClient(app) as client:
        response = client.post("/api/exports", json={"kinds": ["markdown", "graph"]})

    assert response.status_code == 200
    files = response.json()["files"]
    assert set(files) == {"markdown", "graph"}
    for paths in files.values():
        assert paths
        for exported in paths:
            assert exported.startswith("data/exports/")
            assert Path(exported).exists()
