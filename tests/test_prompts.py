import pytest

from mslearn.opsdb import OpsDB
from mslearn.prompts import PROMPTS, get_prompt


def test_builtin_extraction_prompt_mentions_verbatim_quote():
    assert "verbatim" in PROMPTS["extraction"].lower()


def test_db_override_wins(tmp_path):
    db = OpsDB(tmp_path / "ops.db")
    assert get_prompt(db, "extraction") == PROMPTS["extraction"]
    db.set_setting("prompt:extraction", "OVERRIDDEN")
    assert get_prompt(db, "extraction") == "OVERRIDDEN"
    with pytest.raises(KeyError):
        get_prompt(db, "unknown_prompt")
