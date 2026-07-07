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


def test_tl_dr_without_claims_backfilled_from_surviving_section():
    raw = {**RAW, "tl_dr": {"text": "Sorts fast.", "claims": []}}
    g = drop_uncited(parse_guide(raw))
    assert g.tl_dr.text == "Sorts fast."
    assert g.tl_dr.claims == ["c3"]  # backed by the surviving cited item


def test_tl_dr_blanked_when_no_claims_and_all_sections_uncited():
    raw = {**RAW, "tl_dr": {"text": "Sorts fast.", "claims": []},
           "sections": [{"id": "s1", "title": "x", "items": [
               {"kind": "claim", "text": "t", "claims": []}]}]}
    g = drop_uncited(parse_guide(raw))
    assert g.sections == []
    assert g.tl_dr.claims == []
    assert g.tl_dr.text == ""


def test_item_with_only_blank_claim_ids_dropped_like_empty_claims():
    raw = {**RAW, "sections": [{"id": "s1", "title": "x", "items": [
        {"kind": "claim", "text": "t", "claims": [""]},
        {"kind": "claim", "text": "u", "claims": ["", "  "]},
        {"kind": "claim", "text": "kept", "claims": ["c3"]},
    ]}]}
    g = drop_uncited(parse_guide(raw))
    assert len(g.sections) == 1
    texts = [i.text for i in g.sections[0].items]
    assert texts == ["kept"]  # blank-only claim ids dropped, same as []


def test_item_with_mixed_blank_and_real_claim_ids_strips_blanks():
    raw = {**RAW, "sections": [{"id": "s1", "title": "x", "items": [
        {"kind": "claim", "text": "t", "claims": ["", "c3", " "]},
    ]}]}
    g = drop_uncited(parse_guide(raw))
    assert len(g.sections) == 1
    assert g.sections[0].items[0].claims == ["c3"]  # blank ids stripped, real id kept
