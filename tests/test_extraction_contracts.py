import pytest

from mslearn.pipeline.contracts import (
    CLAIM_KINDS,
    EXTRACTION_SCHEMA,
    STANCES,
    ClaimDraft,
    ExtractionParseError,
    derive_claim_id,
    parse_extraction,
    to_claim_record,
)


def test_schema_shape():
    props = EXTRACTION_SCHEMA["properties"]["claims"]["items"]["properties"]
    assert set(props) == {"text", "stance", "quote", "kind"}
    assert props["stance"]["enum"] == list(STANCES)
    assert props["kind"]["enum"] == list(CLAIM_KINDS)
    assert EXTRACTION_SCHEMA["properties"]["claims"]["items"]["additionalProperties"] is False


def test_parse_valid():
    drafts = parse_extraction(
        {"claims": [{"text": "Caching helps.", "stance": "recommends", "quote": "cache it"}]}
    )
    assert drafts == [ClaimDraft(text="Caching helps.", stance="recommends", quote="cache it")]
    assert parse_extraction({"claims": []}) == []


def test_parse_rejects_bad_shapes():
    with pytest.raises(ExtractionParseError):
        parse_extraction({"nope": []})
    with pytest.raises(ExtractionParseError):
        parse_extraction({"claims": [{"text": "x", "stance": "loves_it", "quote": "q"}]})
    with pytest.raises(ExtractionParseError):
        parse_extraction(None)


def test_claim_id_stable_and_distinct():
    a = derive_claim_id("src:0", "Caching helps.")
    assert a == derive_claim_id("src:0", "Caching helps.")
    assert a != derive_claim_id("src:1", "Caching helps.")
    assert len(a) == 16


def test_to_claim_record():
    draft = ClaimDraft(text="t", stance="neutral", quote="q")
    rec = to_claim_record(draft, chunk_id="src:0", source_id="src", trust="trusted")
    assert rec.claim_id == derive_claim_id("src:0", "t")
    assert rec.chunk_id == "src:0" and rec.trust == "trusted" and rec.quote == "q"


def test_claim_draft_carries_kind():
    d = ClaimDraft(text="t", stance="neutral", quote="q", kind="mechanism")
    assert d.kind == "mechanism"


def test_parse_extraction_reads_kind():
    drafts = parse_extraction({"claims": [
        {"text": "t", "stance": "neutral", "quote": "q", "kind": "caveat"}]})
    assert drafts[0].kind == "caveat"


def test_unknown_kind_rejected():
    with pytest.raises(Exception):
        ClaimDraft(text="t", stance="neutral", quote="q", kind="bogus")


def test_to_claim_record_copies_kind():
    d = ClaimDraft(text="t", stance="neutral", quote="q", kind="example")
    rec = to_claim_record(d, chunk_id="s:1", source_id="s", trust="trusted")
    assert rec.kind == "example"


def test_claim_kinds_membership():
    assert set(CLAIM_KINDS) == {"definition", "claim", "mechanism", "example", "caveat", "actionable"}
