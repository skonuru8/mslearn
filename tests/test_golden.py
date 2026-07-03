import pytest

from mslearn.evals.golden import (
    GoldenFormatError,
    GroundingGolden,
    load_golden,
    save_golden,
)


def test_load_extraction_fixtures(tmp_path, monkeypatch):
    monkeypatch.setattr("mslearn.evals.golden.GOLDEN_DIR", tmp_path)
    rows = load_golden("extraction")
    assert len(rows) == 0

    (tmp_path / "extraction.jsonl").write_text(
        '{"chunk_text":"x","expected_claims":[{"text":"a","stance":"neutral"}],'
        '"source_type":"blog","review":"approved"}\n'
    )
    rows = load_golden("extraction")
    assert len(rows) == 1
    assert rows[0].expected_claims[0]["text"] == "a"


def test_active_only_excludes_pending(tmp_path, monkeypatch):
    monkeypatch.setattr("mslearn.evals.golden.GOLDEN_DIR", tmp_path)
    (tmp_path / "grounding.jsonl").write_text(
        '{"chunk_text":"c","claim_text":"t","quote":"q","valid":true,"review":"pending"}\n'
        '{"chunk_text":"c","claim_text":"t","quote":"q","valid":true,"review":"approved"}\n'
    )
    assert len(load_golden("grounding")) == 2
    assert len(load_golden("grounding", active_only=True)) == 1


def test_invalid_tension_classification_raises(tmp_path, monkeypatch):
    monkeypatch.setattr("mslearn.evals.golden.GOLDEN_DIR", tmp_path)
    (tmp_path / "tension.jsonl").write_text(
        '{"claim_a":"a","claim_b":"b","domain_profile":"technical",'
        '"classification":"not_real","review":"approved"}\n'
    )
    with pytest.raises(GoldenFormatError):
        load_golden("tension")


def test_save_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr("mslearn.evals.golden.GOLDEN_DIR", tmp_path)
    row = GroundingGolden(
        chunk_text="chunk",
        claim_text="claim",
        quote="quote",
        valid=True,
        review="approved",
    )
    save_golden("grounding", [row])
    loaded = load_golden("grounding")
    assert loaded[0].claim_text == "claim"


def test_repo_fixtures_load():
    rows = load_golden("extraction", active_only=True)
    assert 5 <= len(rows) <= 8
