from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from mslearn.opsdb import OpsDB

ROLES = ("extraction", "synthesis", "interactive", "evals", "embedding")
_ACTIVE_KEY = "active_profile"


class RoleConfig(BaseModel):
    provider: str
    model: str
    params: dict = Field(default_factory=dict)


class Profile(BaseModel):
    roles: dict[str, RoleConfig]


class ProfilesConfig(BaseModel):
    default_profile: str
    profiles: dict[str, Profile]


def load_profiles(path: Path | str) -> ProfilesConfig:
    data = yaml.safe_load(Path(path).read_text())
    cfg = ProfilesConfig.model_validate(data)
    for name, profile in cfg.profiles.items():
        missing = set(ROLES) - set(profile.roles)
        if missing:
            raise ValueError(f"profile {name!r} missing roles: {sorted(missing)}")
    if cfg.default_profile not in cfg.profiles:
        raise ValueError(f"unknown profile {cfg.default_profile!r} as default_profile")
    return cfg


def get_active_profile_name(db: OpsDB, cfg: ProfilesConfig) -> str:
    name = db.get_setting(_ACTIVE_KEY, cfg.default_profile)
    return name if name in cfg.profiles else cfg.default_profile


def set_active_profile_name(db: OpsDB, cfg: ProfilesConfig, name: str) -> None:
    if name not in cfg.profiles:
        raise ValueError(f"unknown profile {name!r}")
    db.set_setting(_ACTIVE_KEY, name)
