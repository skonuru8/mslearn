import json
import xml.etree.ElementTree as ET

import pytest

from mslearn.graph.export import write_graphml, write_json

NODES = [
    {"id": "n0", "labels": ["Source"], "properties": {"source_id": "srcA", "title": "Book"}},
    {"id": "n1", "labels": ["Claim"], "properties": {"claim_id": "cl1", "text": "x"}},
]
RELS = [
    {"start": "n1", "end": "n0", "type": "EXTRACTED_FROM", "properties": {}},
    {"start": "n1", "end": "n1", "type": "CONFLICTS_WITH",
     "properties": {"classification": "outdated", "rationale": "r"}},
]


def test_write_json_roundtrips(tmp_path):
    path = tmp_path / "graph.json"
    write_json(NODES, RELS, path)
    data = json.loads(path.read_text())
    assert data["nodes"] == NODES and data["relationships"] == RELS


def test_write_graphml_valid_xml(tmp_path):
    path = tmp_path / "graph.graphml"
    write_graphml(NODES, RELS, path)
    root = ET.parse(path).getroot()
    ns = {"g": "http://graphml.graphdrawing.org/xmlns"}
    graph = root.find("g:graph", ns)
    assert len(graph.findall("g:node", ns)) == 2
    edges = graph.findall("g:edge", ns)
    assert len(edges) == 2
    assert {e.get("source") for e in edges} == {"n1"}


@pytest.mark.neo4j
def test_export_all_excludes_embeddings(clean_graph):
    from mslearn.chunking import chunk_source
    from tests.test_graph_ingest import embed_stub, make_doc

    doc = make_doc()
    chunks = chunk_source(doc)
    clean_graph.upsert_source(doc)
    clean_graph.upsert_chunks(chunks, embed_stub(chunks))
    nodes, rels = clean_graph.export_all()
    assert len(nodes) == 1 + len(chunks)
    assert all("embedding" not in n["properties"] for n in nodes)
    assert any(r["type"] == "HAS_CHUNK" for r in rels)
