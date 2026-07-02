import pytest

from mslearn.opsdb import OpsDB
from mslearn.profiles import (
    ROLES,
    get_active_profile_name,
    load_profiles,
    set_active_profile_name,
)


def test_shipped_profiles_yaml_is_valid_and_complete():
    cfg = load_profiles("profiles.yaml")
    assert cfg.default_profile == "openrouter"
    assert set(cfg.profiles) == {"openrouter", "claude-code", "offline"}
    for profile in cfg.profiles.values():
        assert set(profile.roles) == set(ROLES)
    # offline must not depend on any remote provider
    assert all(rc.provider == "ollama" for rc in cfg.profiles["offline"].roles.values())


def test_missing_role_rejected(tmp_path):
    bad = tmp_path / "p.yaml"
    bad.write_text(
        "default_profile: x\n"
        "profiles:\n"
        "  x:\n"
        "    roles:\n"
        "      extraction: {provider: ollama, model: m}\n"
    )
    with pytest.raises(ValueError, match="missing roles"):
        load_profiles(bad)


def test_active_profile_defaults_and_switches(tmp_path):
    cfg = load_profiles("profiles.yaml")
    db = OpsDB(tmp_path / "ops.db")
    assert get_active_profile_name(db, cfg) == "openrouter"
    set_active_profile_name(db, cfg, "offline")
    assert get_active_profile_name(db, cfg) == "offline"
    with pytest.raises(ValueError, match="unknown profile"):
        set_active_profile_name(db, cfg, "nope")
