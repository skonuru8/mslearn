import pytest

pytestmark = pytest.mark.neo4j


def test_ping_and_schema(clean_graph):
    indexes = clean_graph.list_index_names()
    assert "claim_embedding" in indexes and "chunk_embedding" in indexes


def test_wipe_empties_graph(clean_graph):
    clean_graph.run_write("CREATE (:Source {source_id: 'tmp'})")
    assert clean_graph.node_count() == 1
    clean_graph.wipe()
    assert clean_graph.node_count() == 0
