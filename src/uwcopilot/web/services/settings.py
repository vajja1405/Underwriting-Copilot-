"""Settings of the web services layer (heavy-work threads, timeouts, caches, the lender demo folders).

They are read like every project setting (uwcopilot.common.settings.env_reader: a non-empty value in the repo .env
file wins over the process environment). They live here, not in web/settings.py, so the feature services can be
tuned without touching the app shell's settings.

| Variable | Meaning | Default |
|---|---|---|
| UWC_COMPUTE_TIMEOUT_S | seconds a request waits for a heavy job (simulation, path search, agent run) | 60 |
| UWC_COMPUTE_WORKERS | worker threads for heavy jobs | 2 |
| UWC_COMPUTE_MAX_PENDING | heavy jobs running or queued at once; more get 503 "server busy" | 8 |
| UWC_COMPUTE_CACHE_ENTRIES | finished results kept in memory (per process) | 256 |

Every default is a design choice for a small single-container prototype, not a measured value.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from uwcopilot.common.paths import ENV_FILE, REPO_ROOT
from uwcopilot.common.settings import SettingReader, env_reader

DEFAULT_TIMEOUT_S = 60.0
DEFAULT_WORKERS = 2
DEFAULT_MAX_PENDING = 8
DEFAULT_CACHE_ENTRIES = 256
# The SYNTHETIC structured applications the Lender Desk demo shows (credit + insurance, startup credit).
DEFAULT_DEMO_DIRS: tuple[Path, ...] = (
    REPO_ROOT / "eval" / "applications",
    REPO_ROOT / "eval" / "startup_applications",
)


class ServiceSettingsError(ValueError):
    """A services setting is malformed."""


@dataclass(frozen=True)
class ServiceSettings:
    """Every services setting, already checked."""

    timeout_s: float = DEFAULT_TIMEOUT_S
    workers: int = DEFAULT_WORKERS
    max_pending: int = DEFAULT_MAX_PENDING
    cache_entries: int = DEFAULT_CACHE_ENTRIES
    demo_dirs: tuple[Path, ...] = field(default_factory=lambda: DEFAULT_DEMO_DIRS)


def read_positive(read: SettingReader, name: str, default: float, as_int: bool) -> float:
    """A positive number from the environment, or default when unset."""
    raw = read(name)
    if raw is None:
        return default
    try:
        value = int(raw) if as_int else float(raw)
    except ValueError as error:
        raise ServiceSettingsError(f"{name} must be a number (got {raw!r})") from error
    if value <= 0:
        raise ServiceSettingsError(f"{name} must be positive (got {raw!r})")
    return value


def load_service_settings(
    env_file: Path | None = ENV_FILE, environ: Mapping[str, str] | None = None
) -> ServiceSettings:
    """Read the services settings. env_file=None skips the repo .env file (tests do)."""
    read = env_reader(env_file, environ)
    return ServiceSettings(
        timeout_s=read_positive(read, "UWC_COMPUTE_TIMEOUT_S", DEFAULT_TIMEOUT_S, as_int=False),
        workers=int(read_positive(read, "UWC_COMPUTE_WORKERS", DEFAULT_WORKERS, as_int=True)),
        max_pending=int(read_positive(read, "UWC_COMPUTE_MAX_PENDING", DEFAULT_MAX_PENDING, as_int=True)),
        cache_entries=int(read_positive(read, "UWC_COMPUTE_CACHE_ENTRIES", DEFAULT_CACHE_ENTRIES, as_int=True)),
    )
