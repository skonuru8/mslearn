from mslearn.pipeline.guide import parse_guide, drop_uncited

RAW = {
  "concept_id": "con1", "title": "Merge sort",
  "tl_dr": {"text": "Sorts in O(n log n).", "claims": ["c3"]},
  "skeleton": ["Cost"],
  "sections": [{"id": "s1", "title": "Cost", "items": [
      {"kind": "claim", "text": "O(n log n).", "claims": ["c3"]},
      {"kind": "example", "text": "hallucinated", "claims": []}]}],
  "disagreements": [], "open_questions": [],
}


def test_parse_and_drop_uncited():
    g = drop_uncited(parse_guide(RAW))
    assert g.title == "Merge sort"
    kinds = [i.kind for i in g.sections[0].items]
    assert kinds == ["claim"]  # uncited example dropped


def test_empty_section_dropped_when_all_items_uncited():
    raw = {**RAW, "sections": [{"id":"s1","title":"x","items":[
        {"kind":"claim","text":"t","claims":[]}]}]}
    g = drop_uncited(parse_guide(raw))
    assert g.sections == []
