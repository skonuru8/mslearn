import pytest

from mslearn.pipeline.contracts import (
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
    assert set(props) == {"text", "stance", "quote"}
    assert props["stance"]["enum"] == list(STANCES)
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
