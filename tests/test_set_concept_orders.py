from mslearn.graph.store import GraphStore


def test_set_concept_orders_single_unwind(monkeypatch):
    store = GraphStore.__new__(GraphStore)          # bypass __init__/driver
    calls = []
    # patch whichever write helper your implementation calls:
    monkeypatch.setattr(store, "run_write", lambda q, **kw: calls.append((q, kw)), raising=False)
    store.set_concept_orders([("a", 0), ("b", 1)], project_id="p")
    assert len(calls) == 1
    q, kw = calls[0]
    assert "UNWIND" in q
    assert kw["rows"] == [{"concept_id": "a", "order_index": 0},
                          {"concept_id": "b", "order_index": 1}]


def test_set_concept_orders_empty_noop(monkeypatch):
    store = GraphStore.__new__(GraphStore)
    calls = []
    monkeypatch.setattr(store, "run_write", lambda q, **kw: calls.append(q), raising=False)
    store.set_concept_orders([], project_id="p")
    assert calls == []
