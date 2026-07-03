import pytest

from mslearn.opsdb import OpsDB
from mslearn.prompts import (
    PROMPTS,
    domain_guidance,
    get_domain_profile,
    get_prompt,
)


def test_builtin_extraction_prompt_mentions_verbatim_quote():
    assert "verbatim" in PROMPTS["extraction"].lower()


def test_db_override_wins(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    assert get_prompt(db, "extraction") == PROMPTS["extraction"]
    db.set_setting("prompt:extraction", "OVERRIDDEN")
    assert get_prompt(db, "extraction") == "OVERRIDDEN"
    with pytest.raises(KeyError):
        get_prompt(db, "unknown_prompt")


def test_synthesis_prompt_keys_and_placeholder():
    assert "concept_match" in PROMPTS
    assert "conflict_scan" in PROMPTS and "{domain_guidance}" in PROMPTS["conflict_scan"]
    assert "concept_name" in PROMPTS
    assert "concept_deps" in PROMPTS


def test_domain_guidance_and_profile_default(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    assert get_domain_profile(db) == "technical"
    db.set_setting("corpus.domain_profile", "interpretive")
    assert get_domain_profile(db) == "interpretive"
    assert "context_dependent" in domain_guidance("technical")
    assert "genuine_debate" in domain_guidance("interpretive")
    with pytest.raises(KeyError):
        domain_guidance("unknown")
