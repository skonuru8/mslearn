from mslearn.pipeline.study_outline import build_outline


def _row(concept_id, name, order_index, section_path, conflict_count=0):
    return {
        "concept_id": concept_id,
        "name": name,
        "order_index": order_index,
        "section_path": section_path,
        "conflict_count": conflict_count,
    }


def test_build_outline_nests_by_section_path_and_orders():
    rows = [
        _row("k3", "Loops", 2, ["Ch1", "1.2"]),
        _row("k1", "Numbers", 0, ["Ch1", "1.1"]),
        _row("k4", "History", 3, ["Ch2"]),
        _row("k2", "Preface", 1, []),
    ]

    outline = build_outline(rows)

    assert outline["has_structure"] is True
    tree = outline["tree"]
    assert [n["title"] for n in tree] == ["Ch1", "Ch2"]

    ch1 = tree[0]
    assert [c["title"] for c in ch1["children"]] == ["1.1", "1.2"]
    assert ch1["concepts"] == []
    assert ch1["children"][0]["concepts"] == [
        {"concept_id": "k1", "name": "Numbers", "conflict_count": 0}
    ]
    assert ch1["children"][1]["concepts"] == [
        {"concept_id": "k3", "name": "Loops", "conflict_count": 0}
    ]

    ch2 = tree[1]
    assert ch2["children"] == []
    assert ch2["concepts"] == [{"concept_id": "k4", "name": "History", "conflict_count": 0}]

    assert outline["flat"] == [{"concept_id": "k2", "name": "Preface", "conflict_count": 0}]


def test_build_outline_all_empty_paths_is_unstructured():
    rows = [
        _row("k1", "Numbers", 0, []),
        _row("k2", "Loops", 1, []),
    ]

    outline = build_outline(rows)

    assert outline["has_structure"] is False
    assert outline["tree"] == []
    assert [c["concept_id"] for c in outline["flat"]] == ["k1", "k2"]
