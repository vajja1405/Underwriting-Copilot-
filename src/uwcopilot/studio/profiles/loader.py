"""Load business-type profiles from config/studio/business_types/*.yaml.

    profile = load_profile("car_wash")                      # the base profile
    profile = load_profile("car_wash", metro_id="houston_tx")  # with that metro's overrides applied
    ids = list_profiles()

A metro override replaces one Quantity or Curve (named by a dotted path such as "labor.hourly_wage" or
"cost_ratios.utilities") with a value that carries its own provenance, for example a BLS wage for that metro.
"""

from pathlib import Path
from typing import Any

import yaml

from uwcopilot.common.paths import CONFIG_DIR
from uwcopilot.studio.profiles.models import Curve, Profile, Quantity

PROFILES_DIR: Path = CONFIG_DIR / "studio" / "business_types"


class ProfileNotFound(LookupError):
    """No YAML file exists for the requested profile id."""


def profile_path(profile_id: str, profiles_dir: Path = PROFILES_DIR) -> Path:
    """The YAML file for a profile id (the file name is the id)."""
    return profiles_dir / f"{profile_id}.yaml"


def list_profiles(profiles_dir: Path = PROFILES_DIR) -> list[str]:
    """Every profile id with a YAML file, sorted."""
    return sorted(path.stem for path in profiles_dir.glob("*.yaml"))


def read_yaml(path: Path) -> dict[str, Any]:
    """Parse a YAML file that must hold a mapping."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must hold a YAML mapping")
    return data


def load_profile(profile_id: str, metro_id: str | None = None, profiles_dir: Path = PROFILES_DIR) -> Profile:
    """Load and validate one profile; with metro_id, apply that metro's overrides (if the profile has any)."""
    path = profile_path(profile_id, profiles_dir)
    if not path.is_file():
        raise ProfileNotFound(f"no profile {profile_id!r}; known: {list_profiles(profiles_dir)}")
    profile = Profile.model_validate(read_yaml(path))
    if profile.id != profile_id:
        raise ValueError(f"{path.name} declares id {profile.id!r}; the id must match the file name")
    if metro_id is None:
        return profile
    return apply_metro_overrides(profile, metro_id)


def load_all_profiles(profiles_dir: Path = PROFILES_DIR) -> dict[str, Profile]:
    """{profile id: Profile} for every YAML file."""
    return {
        profile_id: load_profile(profile_id, profiles_dir=profiles_dir) for profile_id in list_profiles(profiles_dir)
    }


def apply_metro_overrides(profile: Profile, metro_id: str) -> Profile:
    """Return a copy of the profile with metro_overrides[metro_id] applied (unchanged if there are none).

    Each override path must point to an existing Quantity (replaced by a Quantity) or Curve (replaced by a Curve).
    The result is validated again, so an override can never break a profile rule.
    """
    overrides = profile.metro_overrides.get(metro_id, {})
    if not overrides:
        return profile
    data = profile.model_dump()
    for dotted_path, replacement in overrides.items():
        parent, key = walk_to_parent(data, dotted_path)
        current = parent[key]
        expected = Curve if isinstance(current, dict) and "values" in current else Quantity
        if not isinstance(replacement, expected):
            raise ValueError(f"override {dotted_path!r} must be a {expected.__name__}")
        parent[key] = replacement.model_dump()
    return Profile.model_validate(data)


def walk_to_parent(data: dict[str, Any], dotted_path: str) -> tuple[dict[str, Any], str]:
    """Follow 'a.b.c' through nested dicts; return (the dict holding 'c', 'c'). Unknown paths raise KeyError."""
    parts = dotted_path.split(".")
    node: Any = data
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"override path {dotted_path!r}: no field {part!r}")
        node = node[part]
    last = parts[-1]
    if not isinstance(node, dict) or last not in node:
        raise KeyError(f"override path {dotted_path!r}: no field {last!r}")
    if not isinstance(node[last], dict) or "status" not in node[last]:
        raise KeyError(f"override path {dotted_path!r} does not name a Quantity or Curve")
    return node, last
