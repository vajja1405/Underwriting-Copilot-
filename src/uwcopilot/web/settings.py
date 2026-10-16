"""Web app settings, read once at startup and checked loudly.

Project variables (UWC_* and HF_TOKEN) follow the repo rule in uwcopilot.common.settings: a non-empty value in the
repo .env file wins over the process environment. Variables that describe WHERE the process runs (SPACE_ID,
SPACE_HOST and the OAuth variables a Hugging Face Space injects) are read from the process environment only.

| Variable | Meaning | Default |
|---|---|---|
| SPACE_ID | set by Hugging Face inside a Space; turns on "Space mode" | unset |
| SPACE_HOST | the Space's own host name, e.g. astra6-fundproof.hf.space | unset |
| OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, OPENID_PROVIDER_URL | injected by a Space with `hf_oauth: true` | unset |
| UWC_PUBLIC_URL | public base URL for the OAuth callback outside a Space (local OAuth testing) | unset |
| UWC_DEV_AUTH | "1" turns on the dev login; REFUSED when SPACE_ID is set | off |
| UWC_ROLE_ALLOWLIST | "hfuser@id:role,..." or "hfuser:role,..." (see parse_role_allowlist) | empty |
| UWC_SESSION_SECRET | signs session cookies; at least 32 characters; REQUIRED in a Space | random per process |
| UWC_SESSION_MAX_AGE_S | session lifetime in seconds | 28800 (8 hours) |
| UWC_FERNET_KEY | Fernet key(s) for data at rest; REQUIRED with UWC_STORE=hf_dataset | dev fallback |
| UWC_STORE | local or hf_dataset | local |
| UWC_STORE_DIR | folder of the local store (and the local mirror of the dataset) | data/web/store |
| UWC_HF_DATASET_REPO | private dataset repo id for UWC_STORE=hf_dataset, e.g. astra6/fundproof-data | unset |
| HF_TOKEN | Hugging Face token with write access to that dataset | unset |
| UWC_HF_FLUSH_EVERY_S | seconds between batched dataset commits | 300 |
| UWC_RETENTION_DAYS | default retention for stored objects | 30 |
| UWC_PURGE_EVERY_S | seconds between retention purges (expired objects are deleted; also once at startup) | 3600 |
| UWC_RATE_LIMIT_IP_PER_MIN / UWC_RATE_LIMIT_IP_BURST | per-IP token bucket on /api | 120 / 60 |
| UWC_RATE_LIMIT_USER_PER_MIN / UWC_RATE_LIMIT_USER_BURST | per-user token bucket on /api | 120 / 60 |
| UWC_TRUSTED_PROXY_HOPS | proxies in front of the app that append to X-Forwarded-For | 1 in a Space, else 0 |
| UWC_MAX_BODY_BYTES | largest accepted request body | 10485760 (10 MiB) |
| UWC_STATIC_DIR | the Next.js static export folder | frontend/out |
| UWC_GIT_SHA | commit shown by /api/health (set by the Docker build) | unset |

Every default above is a design choice for a small prototype, not a measured value. The 8-hour session matches the
default Hugging Face OAuth token lifetime (480 minutes, huggingface.co/docs/hub/spaces-oauth, read 2026-10-12).
"""

import logging
import os
import re
import secrets
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from uwcopilot.common.paths import DATA_DIR, ENV_FILE, REPO_ROOT
from uwcopilot.common.settings import SettingReader, env_reader, is_http_url
from uwcopilot.web.crypto import CryptoError, parse_fernet_keys

logger = logging.getLogger(__name__)

# Rename the product here (and in frontend/lib/config.ts); nothing else needs to change.
PRODUCT_NAME = "Fundproof"
# Shown on every page and results view (Studio addendum rule C).
DISCLAIMER = "Educational planning tool. Not financial, legal or lending advice. Lenders make their own decisions."

ROLES: tuple[str, ...] = ("founder", "lender_reviewer", "lender_senior", "admin")
# founder is given to every signed-in user; the other roles come only from UWC_ROLE_ALLOWLIST.
ALLOWLIST_ROLES: tuple[str, ...] = ("lender_reviewer", "lender_senior", "admin")
StoreBackend = Literal["local", "hf_dataset"]

DEFAULT_STORE_DIR = DATA_DIR / "web" / "store"
DEFAULT_STATIC_DIR = REPO_ROOT / "frontend" / "out"
DEFAULT_SESSION_MAX_AGE_S = 8 * 60 * 60
DEFAULT_MAX_BODY_BYTES = 10 * 1024 * 1024
MIN_SESSION_SECRET_CHARS = 32
# The only extra origin allowed by CORS, and only when the dev login is on (next dev runs on port 3000).
DEV_FRONTEND_ORIGIN = "http://localhost:3000"
# Hugging Face usernames: letters, digits, '-', '_' and '.'; we also use this shape for dev usernames.
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
# The account id ("sub" from Hugging Face sign-in) that an allowlist entry may be pinned to.
SUBJECT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
# Data minimization: only these scopes are requested, whatever the Space allows.
OAUTH_SCOPES = "openid profile"


class WebSettingsError(ValueError):
    """A web setting is missing, malformed, or unsafe in the environment the app runs in."""


class OAuthSettings(BaseModel):
    """What the OpenID Connect sign-in needs; present only when every value is known."""

    model_config = ConfigDict(frozen=True)

    client_id: str
    client_secret: SecretStr
    provider_url: str
    redirect_uri: str
    scopes: str = OAUTH_SCOPES


class WebSettings(BaseModel):
    """Every web setting, already validated. Build it with load_web_settings()."""

    model_config = ConfigDict(frozen=True)

    product_name: str = PRODUCT_NAME
    disclaimer: str = DISCLAIMER
    app_version: str = "unknown"
    git_sha: str | None = None
    is_space: bool = False
    space_id: str | None = None
    space_host: str | None = None
    dev_auth: bool = False
    oauth: OAuthSettings | None = None
    # {allowlist_key(username, subject or None): allowlisted roles}
    role_allowlist: dict[str, tuple[str, ...]] = Field(default_factory=dict)
    session_secret: SecretStr
    session_secret_generated: bool = False
    session_max_age_s: int = Field(default=DEFAULT_SESSION_MAX_AGE_S, gt=0)
    fernet_keys: tuple[SecretStr, ...] = ()
    store_backend: StoreBackend = "local"
    store_dir: Path = DEFAULT_STORE_DIR
    hf_dataset_repo: str | None = None
    hf_token: SecretStr | None = None
    hf_flush_every_s: int = Field(default=300, gt=0)
    retention_days: int = Field(default=30, gt=0)
    purge_every_s: int = Field(default=3600, gt=0)
    rate_limit_ip_per_minute: int = Field(default=120, gt=0)
    rate_limit_ip_burst: int = Field(default=60, gt=0)
    rate_limit_user_per_minute: int = Field(default=120, gt=0)
    rate_limit_user_burst: int = Field(default=60, gt=0)
    trusted_proxy_hops: int = Field(default=0, ge=0)
    max_body_bytes: int = Field(default=DEFAULT_MAX_BODY_BYTES, gt=0)
    static_dir: Path = DEFAULT_STATIC_DIR

    @property
    def space_url(self) -> str | None:
        """https://<SPACE_HOST> inside a Space (the direct, non-iframe address), else None."""
        return f"https://{self.space_host}" if self.is_space and self.space_host else None

    @property
    def fernet_key_bytes(self) -> list[bytes]:
        """The configured Fernet keys as bytes (empty when none is configured)."""
        return [key.get_secret_value().encode("ascii") for key in self.fernet_keys]


def package_version() -> str:
    """The installed uwcopilot version, or 'unknown' when the package metadata is missing."""
    try:
        return version("uwcopilot")
    except PackageNotFoundError:
        return "unknown"


def allowlist_key(username: str, subject: str | None = None) -> str:
    """The role_allowlist key: "alice" for a username-only entry, "alice@<subject>" for a pinned entry."""
    return username.casefold() if subject is None else f"{username.casefold()}@{subject}"


def parse_role_allowlist(raw: str | None) -> dict[str, tuple[str, ...]]:
    """Parse "alice@6123abc:lender_reviewer, bob:admin" into {"alice@6123abc": ("lender_reviewer",), "bob": ...}.

    Two entry forms:
    - "hfuser@id:role" (recommended in a Space): the role holds only for the account whose Hugging Face sign-in id
      ("sub", shown as `subject` by GET /api/me after signing in) is `id`. A username can be renamed and then
      registered by someone else; the id cannot, so a pinned entry does not pass to the new holder of the name.
    - "hfuser:role": the role holds for whoever currently has that username (fine for local development).
    A user may be listed more than once to get several roles. Usernames are compared case-insensitively; ids exactly.
    """
    allowlist: dict[str, list[str]] = {}
    if not raw:
        return {}
    for entry in raw.split(","):
        if not entry.strip():
            continue
        account, separator, role = entry.strip().partition(":")
        username, pin, subject = account.strip().partition("@")
        role = role.strip()
        if not separator or not USERNAME_PATTERN.match(username) or (pin and not SUBJECT_PATTERN.match(subject)):
            raise WebSettingsError(
                f"UWC_ROLE_ALLOWLIST entry {entry.strip()!r} must look like 'hfuser@id:role' or 'hfuser:role'"
            )
        if role not in ALLOWLIST_ROLES:
            raise WebSettingsError(f"UWC_ROLE_ALLOWLIST role {role!r} must be one of {list(ALLOWLIST_ROLES)}")
        roles = allowlist.setdefault(allowlist_key(username, subject if pin else None), [])
        if role not in roles:
            roles.append(role)
    return {key: tuple(roles) for key, roles in allowlist.items()}


def warn_about_unpinned_entries(allowlist: dict[str, tuple[str, ...]], is_space: bool) -> None:
    """In a Space, warn when an allowlist entry is bound to a username only (see parse_role_allowlist)."""
    unpinned = sorted(key for key in allowlist if "@" not in key)
    if is_space and unpinned:
        logger.warning(
            "UWC_ROLE_ALLOWLIST has %d entr(y/ies) without an account id: a renamed username can be registered by "
            "someone else, who would get the role. Pin them as 'hfuser@id:role' (id = `subject` in GET /api/me).",
            len(unpinned),
        )


def read_int(read: SettingReader, name: str, default: int) -> int:
    """An integer setting, or default when unset; a non-integer value is an error."""
    raw = read(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise WebSettingsError(f"{name} must be an integer (got {raw!r})") from error


def read_dev_auth(read: SettingReader, is_space: bool) -> bool:
    """UWC_DEV_AUTH: "1" on, "0"/unset off. Refused inside a Space, whatever else is configured."""
    raw = read("UWC_DEV_AUTH")
    if raw is None or raw == "0":
        return False
    if raw != "1":
        raise WebSettingsError(f"UWC_DEV_AUTH must be 1 or 0 (got {raw!r})")
    if is_space:
        raise WebSettingsError(
            "UWC_DEV_AUTH=1 is refused inside a Hugging Face Space (SPACE_ID is set). The dev login lets anyone "
            "pick any role; remove UWC_DEV_AUTH from the Space settings."
        )
    return True


def read_session_secret(read: SettingReader, is_space: bool) -> tuple[SecretStr, bool]:
    """(secret, generated). Required in a Space; generated per process (with a warning) elsewhere."""
    raw = read("UWC_SESSION_SECRET")
    if raw is None:
        if is_space:
            raise WebSettingsError("UWC_SESSION_SECRET is required in a Space (add it as a Space secret).")
        logger.warning("UWC_SESSION_SECRET is not set: using a random secret; sessions end when the process stops.")
        return SecretStr(secrets.token_urlsafe(48)), True
    if len(raw) < MIN_SESSION_SECRET_CHARS:
        raise WebSettingsError(f"UWC_SESSION_SECRET must have at least {MIN_SESSION_SECRET_CHARS} characters.")
    return SecretStr(raw), False


def read_oauth(environ: Mapping[str, str], read: SettingReader, space_host: str | None) -> OAuthSettings | None:
    """OAuth settings when the client id, secret, provider URL and a public base URL are all known, else None."""
    client_id = (environ.get("OAUTH_CLIENT_ID") or "").strip()
    client_secret = (environ.get("OAUTH_CLIENT_SECRET") or "").strip()
    provider_url = (environ.get("OPENID_PROVIDER_URL") or "").strip().rstrip("/")
    base_url = f"https://{space_host}" if space_host else (read("UWC_PUBLIC_URL") or "").rstrip("/")
    if not (client_id and client_secret and provider_url and base_url):
        return None
    for name, url in (("OPENID_PROVIDER_URL", provider_url), ("public base URL", base_url)):
        if not is_http_url(url):
            raise WebSettingsError(f"{name} must be an http(s) URL (got {url!r})")
    return OAuthSettings(
        client_id=client_id,
        client_secret=SecretStr(client_secret),
        provider_url=provider_url,
        redirect_uri=f"{base_url}/api/auth/callback",
    )


def read_store(read: SettingReader) -> tuple[StoreBackend, str | None, SecretStr | None]:
    """(backend, dataset repo id, HF token); the dataset store needs both the repo id and the token."""
    backend = read("UWC_STORE") or "local"
    if backend == "local":
        return "local", None, None
    if backend != "hf_dataset":
        raise WebSettingsError(f"UWC_STORE must be local or hf_dataset (got {backend!r})")
    repo = read("UWC_HF_DATASET_REPO")
    token = read("HF_TOKEN")
    if not repo or "/" not in repo:
        raise WebSettingsError("UWC_STORE=hf_dataset needs UWC_HF_DATASET_REPO as 'owner/name'.")
    if not token:
        raise WebSettingsError("UWC_STORE=hf_dataset needs HF_TOKEN (a token with write access to that dataset).")
    return "hf_dataset", repo, SecretStr(token)


def load_web_settings(env_file: Path | None = ENV_FILE, environ: Mapping[str, str] | None = None) -> WebSettings:
    """Read and check every web setting. Pass env_file=None to skip the repo .env file (tests do)."""
    process_env = os.environ if environ is None else environ
    read = env_reader(env_file, process_env)
    space_id = (process_env.get("SPACE_ID") or "").strip() or None
    is_space = space_id is not None
    space_host = (process_env.get("SPACE_HOST") or "").strip() or None
    dev_auth = read_dev_auth(read, is_space)
    session_secret, generated = read_session_secret(read, is_space)
    backend, dataset_repo, hf_token = read_store(read)

    raw_keys = read("UWC_FERNET_KEY")
    try:
        fernet_keys = tuple(SecretStr(key.decode("ascii")) for key in parse_fernet_keys(raw_keys)) if raw_keys else ()
    except CryptoError as error:
        raise WebSettingsError(str(error)) from error
    if backend == "hf_dataset" and not fernet_keys:
        raise WebSettingsError("UWC_STORE=hf_dataset needs UWC_FERNET_KEY: stored data is always encrypted.")

    role_allowlist = parse_role_allowlist(read("UWC_ROLE_ALLOWLIST"))
    warn_about_unpinned_entries(role_allowlist, is_space)
    static_dir = read("UWC_STATIC_DIR")
    store_dir = read("UWC_STORE_DIR")
    values = {
        "app_version": package_version(),
        "git_sha": read("UWC_GIT_SHA"),
        "is_space": is_space,
        "space_id": space_id,
        "space_host": space_host,
        "dev_auth": dev_auth,
        "oauth": read_oauth(process_env, read, space_host),
        "role_allowlist": role_allowlist,
        "session_secret": session_secret,
        "session_secret_generated": generated,
        "session_max_age_s": read_int(read, "UWC_SESSION_MAX_AGE_S", DEFAULT_SESSION_MAX_AGE_S),
        "fernet_keys": fernet_keys,
        "store_backend": backend,
        "store_dir": Path(store_dir) if store_dir else DEFAULT_STORE_DIR,
        "hf_dataset_repo": dataset_repo,
        "hf_token": hf_token,
        "hf_flush_every_s": read_int(read, "UWC_HF_FLUSH_EVERY_S", 300),
        "retention_days": read_int(read, "UWC_RETENTION_DAYS", 30),
        "purge_every_s": read_int(read, "UWC_PURGE_EVERY_S", 3600),
        "rate_limit_ip_per_minute": read_int(read, "UWC_RATE_LIMIT_IP_PER_MIN", 120),
        "rate_limit_ip_burst": read_int(read, "UWC_RATE_LIMIT_IP_BURST", 60),
        "rate_limit_user_per_minute": read_int(read, "UWC_RATE_LIMIT_USER_PER_MIN", 120),
        "rate_limit_user_burst": read_int(read, "UWC_RATE_LIMIT_USER_BURST", 60),
        "trusted_proxy_hops": read_int(read, "UWC_TRUSTED_PROXY_HOPS", 1 if is_space else 0),
        "max_body_bytes": read_int(read, "UWC_MAX_BODY_BYTES", DEFAULT_MAX_BODY_BYTES),
        "static_dir": Path(static_dir) if static_dir else DEFAULT_STATIC_DIR,
    }
    try:
        return WebSettings(**values)
    except ValidationError as error:
        raise WebSettingsError(f"invalid web setting: {error}") from error
