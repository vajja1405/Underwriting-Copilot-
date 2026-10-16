"""Free API keys for the Studio data connectors, read from .env or the process environment.

Rules (CLAUDE.md rule 4 and the Studio addendum F):
- a key is read only when a connector needs it, by its exact variable name;
- a key's value is never logged, never put in a cache key, a fixture, a SourceRecord or a results file;
- a missing key raises MissingApiKey naming the variable, so `make studio-data` fails with a clear message.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from uwcopilot.common.paths import ENV_FILE
from uwcopilot.common.settings import env_reader
from uwcopilot.studio.data.errors import MissingApiKey

# Every key a Studio connector may read, and what it is for.
KEY_PURPOSES: dict[str, str] = {
    "CENSUS_API_KEY": "the Census Data API (ACS 5-year tract tables and County Business Patterns)",
    "BLS_API_KEY": "the BLS Public Data API v2 (time series such as OEWS wages)",
    "FRED_API_KEY": "the FRED API (the keyless fredgraph.csv download is used when it is not set)",
    "HUD_USER_TOKEN": "the HUD USER API (Fair Market Rents, residential reference only)",
    "ORS_API_KEY": "OpenRouteService drive-time areas (optional, Studio S2)",
}


EnvFile = Path | None | Literal["default"]


def resolve_env_file(env_file: EnvFile) -> Path | None:
    """'default' means the repo .env file (looked up at call time, so tests can replace ENV_FILE); None skips it."""
    return ENV_FILE if env_file == "default" else env_file


def read_key(name: str, env_file: EnvFile = "default", environ: Mapping[str, str] | None = None) -> str | None:
    """The key's value, or None when it is not set. Only names listed in KEY_PURPOSES can be read."""
    if name not in KEY_PURPOSES:
        raise ValueError(f"unknown Studio key {name!r}; known: {sorted(KEY_PURPOSES)}")
    return env_reader(resolve_env_file(env_file), environ)(name)


def require_key(name: str, env_file: EnvFile = "default", environ: Mapping[str, str] | None = None) -> str:
    """The key's value; raises MissingApiKey (naming the variable, never a value) when it is not set."""
    value = read_key(name, env_file, environ)
    if value is None:
        raise MissingApiKey(name, KEY_PURPOSES[name])
    return value


def key_status(env_file: EnvFile = "default", environ: Mapping[str, str] | None = None) -> dict[str, bool]:
    """{variable: is it set?} for every Studio key. Used by the coverage report; it never returns a value."""
    read = env_reader(resolve_env_file(env_file), environ)
    return {name: read(name) is not None for name in KEY_PURPOSES}
