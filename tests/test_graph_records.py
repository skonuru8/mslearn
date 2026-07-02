import pytest

from mslearn.graph.records import (
    CONFLICT_CLASSIFICATIONS,
    ClaimRecord,
    ConceptRecord,
    validate_classification,
)


def test_conflict_taxonomy_exact():
    assert CONFLICT_CLASSIFICATIONS == (
        "context_dependent", "outdated", "genuine_debate", "evidence_mismatch",
    )


def test_validate_classification():
    assert validate_classification("outdated") == "outdated"
    with pytest.raises(ValueError, match="unknown conflict classification"):
        validate_classification("vibes")


def test_records_frozen():
    claim = ClaimRecord(claim_id="c1", chunk_id="ch1", source_id="s1",
                        text="t", stance="neutral", quote="q", trust="trusted")
    with pytest.raises(AttributeError):
        claim.text = "other"
    concept = ConceptRecord(concept_id="k1", name="Caching")
    assert concept.summary == ""
