from mslearn.pipeline.guide import GuideParseError, drop_ungrounded, parse_guide

RAW = {
  "concept_id": "con1", "title": "Merge sort",
  "tl_dr": {"text": "Sorts in O(n log n).", "claims": ["c3"]},
  "skeleton": ["Cost"],
  "sections": [{"id": "s1", "title": "Cost", "items": [
      {"kind": "claim", "text": "O(n log n).", "claims": ["c3"]},
      {"kind": "example", "text": "hallucinated", "claims": []}]}],
  "disagreements": [], "open_questions": [],
}


def test_parse_and_drop_ungrounded():
    g = drop_ungrounded(parse_guide(RAW))
    assert g.title == "Merge sort"
    kinds = [i.kind for i in g.sections[0].items]
    assert kinds == ["claim"]  # uncited example dropped


def test_empty_section_dropped_when_all_items_uncited():
    raw = {**RAW, "sections": [{"id":"s1","title":"x","items":[
        {"kind":"claim","text":"t","claims":[]}]}]}
    g = drop_ungrounded(parse_guide(raw))
    assert g.sections == []


def test_tl_dr_without_claims_backfilled_from_surviving_section():
    raw = {**RAW, "tl_dr": {"text": "Sorts fast.", "claims": []}}
    g = drop_ungrounded(parse_guide(raw))
    assert g.tl_dr.text == "Sorts fast."
    assert g.tl_dr.claims == ["c3"]  # backed by the surviving cited item


def test_tl_dr_blanked_when_no_claims_and_all_sections_uncited():
    raw = {**RAW, "tl_dr": {"text": "Sorts fast.", "claims": []},
           "sections": [{"id": "s1", "title": "x", "items": [
               {"kind": "claim", "text": "t", "claims": []}]}]}
    g = drop_ungrounded(parse_guide(raw))
    assert g.sections == []
    assert g.tl_dr.claims == []
    assert g.tl_dr.text == ""


def test_item_with_only_blank_claim_ids_dropped_like_empty_claims():
    raw = {**RAW, "sections": [{"id": "s1", "title": "x", "items": [
        {"kind": "claim", "text": "t", "claims": [""]},
        {"kind": "claim", "text": "u", "claims": ["", "  "]},
        {"kind": "claim", "text": "kept", "claims": ["c3"]},
    ]}]}
    g = drop_ungrounded(parse_guide(raw))
    assert len(g.sections) == 1
    texts = [i.text for i in g.sections[0].items]
    assert texts == ["kept"]  # blank-only claim ids dropped, same as []


def test_item_with_mixed_blank_and_real_claim_ids_strips_blanks():
    raw = {**RAW, "sections": [{"id": "s1", "title": "x", "items": [
        {"kind": "claim", "text": "t", "claims": ["", "c3", " "]},
    ]}]}
    g = drop_ungrounded(parse_guide(raw))
    assert len(g.sections) == 1
    assert g.sections[0].items[0].claims == ["c3"]  # blank ids stripped, real id kept


def test_drop_ungrounded_keeps_interpretation():
    raw = {**RAW, "sections": [{"id": "s1", "title": "x", "items": [
        {"kind": "claim", "text": "uncited", "claims": []},
        {"kind": "claim", "text": "cited", "claims": ["c3"]},
    ]}],
        "interpretation": [
            {"angle": "assumption", "text": "model analysis", "claims": []},
        ]}
    g = drop_ungrounded(parse_guide(raw))
    texts = [i.text for i in g.sections[0].items]
    assert texts == ["cited"]  # uncited section item dropped
    assert len(g.interpretation) == 1
    assert g.interpretation[0].text == "model analysis"  # interpretation kept despite no claims


def test_interpretation_angle_validated():
    raw = {**RAW, "interpretation": [
        {"angle": "bogus", "text": "nope", "claims": []},
    ]}
    try:
        parse_guide(raw)
    except GuideParseError:
        pass
    else:
        assert False, "expected GuideParseError for unknown interpretation angle"


def test_parse_guide_without_interpretation_ok():
    g = parse_guide(RAW)
    assert g.interpretation == []
