"""LLM settings, read explicitly from the repo's .env file and a small set of project variables.

Why not let the SDKs read the environment themselves? The anthropic and openai SDKs silently pick up variables
such as ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN or OPENAI_BASE_URL. A hosting platform can set those for its
own purposes, and then a script would send requests (and documents) to a server nobody chose. So:

- base URLs come only from UWC_ANTHROPIC_BASE_URL / UWC_OPENAI_BASE_URL (defaults: the public APIs) and
  UWC_OLLAMA_BASE_URL (default: a local Ollama server on the loopback interface);
- API keys come only from ANTHROPIC_API_KEY / OPENAI_API_KEY;
- a value in the repo .env file wins over the same name in the process environment.

The clients then pass these values to the SDK explicitly (see anthropic_client.py and openai_client.py).
The "ollama" provider runs open-weight models on this machine and needs no API key (see ollama_client.py).
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import SplitResult, urlsplit

from uwcopilot.common.paths import CACHE_DIR, CONFIG_DIR, ENV_FILE
from uwcopilot.common.settings import env_reader, is_http_url

Provider = Literal["anthropic", "openai", "ollama", "fake"]
PROVIDERS: tuple[str, ...] = ("anthropic", "openai", "ollama", "fake")

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
# Ollama's default port on the loopback interface. Plain http is accepted only for loopback hosts (see
# validate_base_url), so a remote Ollama server must be reached over https.
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"

# Low-cost current models, checked on 2026-10-10:
# - claude-haiku-5-5: https://platform.claude.com/docs/en/about-claude/models (models overview, "Claude API ID")
# - gpt-6-luna: https://developers.openai.com/api/docs/models ("Our most efficient model")
# - qwen2.5-3b-instruct-q4km: the local name given by scripts/fetch_local_model.py to Qwen2.5 3B Instruct (Q4_K_M).
# Override with UWC_LLM_MODEL (for example claude-opus-5-5 for harder tasks).
DEFAULT_MODELS: dict[str, str] = {
    "anthropic": "claude-haiku-5-5",
    "openai": "gpt-6-luna",
    "ollama": "qwen2.5-3b-instruct-q4km",
    "fake": "fake-llm",
}
DEFAULT_MAX_RUN_COST_USD = 10.0
DEFAULT_PRICING_FILE = CONFIG_DIR / "llm_pricing.yaml"
DEFAULT_LLM_CACHE_DIR = CACHE_DIR / "llm"
# Effort for Anthropic requests. Claude Haiku 5.5 thinks adaptively by default with effort "medium", and thinking
# tokens count toward max_tokens; "low" keeps short, structured tasks (memos, cited answers) inside their budget.
ANTHROPIC_EFFORTS: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")
DEFAULT_ANTHROPIC_EFFORT = "low"
# Plain http is allowed only to these hosts (a local test server or gateway); everything else needs https.
LOCAL_HOSTS: frozenset[str] = frozenset({"localhost", "127.0.0.1", "::1"})


class SettingsError(ValueError):
    """Raised when a setting has an invalid value."""


class ImplicitSDKConfigError(RuntimeError):
    """Raised when an SDK environment variable is set that the SDK would apply even with explicit arguments."""


def check_no_implicit_sdk_env(names: tuple[str, ...], environ: Mapping[str, str] | None = None) -> None:
    """Refuse to build a client while any of these SDK variables is set.

    Some SDK variables (custom headers, organization or project ids, admin keys) are read by the SDK even when
    the API key and base URL are passed explicitly, and there is no constructor argument that switches them
    off. Failing loudly is safer than letting them change requests without anyone noticing.
    """
    process_env = os.environ if environ is None else environ
    present = [name for name in names if process_env.get(name)]
    if present:
        raise ImplicitSDKConfigError(
            f"Environment variable(s) {', '.join(present)} would be applied implicitly by the SDK. "
            "This project configures LLM clients only from explicit settings; unset them and try again."
        )


@dataclass(frozen=True)
class LLMSettings:
    """Everything the LLM layer needs. API keys are excluded from repr so they never end up in logs."""

    provider: str = "anthropic"
    model: str | None = None
    anthropic_api_key: str | None = field(default=None, repr=False)
    openai_api_key: str | None = field(default=None, repr=False)
    anthropic_base_url: str = DEFAULT_ANTHROPIC_BASE_URL
    openai_base_url: str = DEFAULT_OPENAI_BASE_URL
    ollama_base_url: str = DEFAULT_OLLAMA_BASE_URL
    max_run_cost_usd: float = DEFAULT_MAX_RUN_COST_USD
    cache_dir: Path = DEFAULT_LLM_CACHE_DIR
    pricing_file: Path = DEFAULT_PRICING_FILE
    anthropic_effort: str = DEFAULT_ANTHROPIC_EFFORT

    def model_for(self, provider: str) -> str:
        """The model to use for a provider: UWC_LLM_MODEL if it was set for this provider, else the default."""
        if self.model and provider == self.provider:
            return self.model
        return DEFAULT_MODELS[provider]

    def api_key_for(self, provider: str) -> str | None:
        """The API key for a provider, or None if it is not configured (the fake and ollama providers need none)."""
        if provider == "anthropic":
            return self.anthropic_api_key
        if provider == "openai":
            return self.openai_api_key
        return None

    def base_url_for(self, provider: str) -> str | None:
        """The explicit base URL for a provider (None for the fake provider)."""
        if provider == "anthropic":
            return self.anthropic_base_url
        if provider == "openai":
            return self.openai_base_url
        if provider == "ollama":
            return self.ollama_base_url
        return None


def validate_provider(provider: str) -> str:
    """Return the provider name if it is supported, else raise SettingsError."""
    if provider not in PROVIDERS:
        raise SettingsError(f"Unknown LLM provider {provider!r}. Use one of: {', '.join(PROVIDERS)}.")
    return provider


def validate_effort(effort: str) -> str:
    """Return the Anthropic effort level if it is one of ANTHROPIC_EFFORTS, else raise SettingsError."""
    if effort not in ANTHROPIC_EFFORTS:
        raise SettingsError(f"Unknown effort {effort!r}. Use one of: {', '.join(ANTHROPIC_EFFORTS)}.")
    return effort


def has_valid_port(parts: SplitResult) -> bool:
    """True when the URL has no port, or a port that parses as a number (urlsplit raises ValueError otherwise)."""
    try:
        return parts.port is None or parts.port >= 0
    except ValueError:
        return False


def validate_base_url(url: str) -> str:
    """Require https, except plain http to a loopback host for a local test server. Trailing slashes are removed.

    The URL is parsed rather than prefix-matched, so "http://localhost.evil.example" or
    "http://localhost@evil.example" cannot pass as local. User info (user:password@) is always refused: the API key
    travels in a header, and a URL is not a place for credentials. A query or fragment is refused because the
    clients append paths to the base URL ("http://127.0.0.1:11434#x" + "/api/chat" would request "/"), and so is
    a port that does not parse ("http://127.0.0.1:80:11434").
    """
    cleaned = url.strip().rstrip("/")
    if not is_http_url(cleaned):
        raise SettingsError(f"LLM base URL must be an absolute https URL (got {url!r}).")
    parts = urlsplit(cleaned)
    if not has_valid_port(parts):
        raise SettingsError(f"LLM base URL has an invalid port (got {url!r}).")
    if "?" in cleaned or "#" in cleaned:
        raise SettingsError(f"LLM base URL must not contain a query or fragment (got {url!r}).")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise SettingsError("LLM base URL must not contain a user name or password.")
    if parts.scheme == "http" and parts.hostname not in LOCAL_HOSTS:
        raise SettingsError(f"LLM base URL must use https unless it points at localhost (got {url!r}).")
    return cleaned


def load_llm_settings(
    env_file: Path | None = ENV_FILE,
    environ: Mapping[str, str] | None = None,
) -> LLMSettings:
    """Build LLMSettings from the .env file and the process environment (the .env file wins).

    Only these names are read: UWC_LLM_PROVIDER, UWC_LLM_MODEL, ANTHROPIC_API_KEY, OPENAI_API_KEY,
    UWC_ANTHROPIC_BASE_URL, UWC_OPENAI_BASE_URL, UWC_OLLAMA_BASE_URL, UWC_ANTHROPIC_EFFORT, MAX_RUN_COST_USD,
    UWC_LLM_CACHE_DIR, UWC_LLM_PRICING_FILE.
    Pass env_file=None to ignore the .env file (tests do this).
    """
    read = env_reader(env_file, environ)

    max_cost_text = read("MAX_RUN_COST_USD")
    try:
        max_cost = float(max_cost_text) if max_cost_text else DEFAULT_MAX_RUN_COST_USD
    except ValueError as error:
        raise SettingsError(f"MAX_RUN_COST_USD must be a number (got {max_cost_text!r}).") from error
    if max_cost <= 0:
        raise SettingsError("MAX_RUN_COST_USD must be greater than zero.")

    cache_dir_text = read("UWC_LLM_CACHE_DIR")
    pricing_text = read("UWC_LLM_PRICING_FILE")
    return LLMSettings(
        provider=validate_provider(read("UWC_LLM_PROVIDER") or "anthropic"),
        model=read("UWC_LLM_MODEL"),
        anthropic_api_key=read("ANTHROPIC_API_KEY"),
        openai_api_key=read("OPENAI_API_KEY"),
        anthropic_base_url=validate_base_url(read("UWC_ANTHROPIC_BASE_URL") or DEFAULT_ANTHROPIC_BASE_URL),
        openai_base_url=validate_base_url(read("UWC_OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL),
        ollama_base_url=validate_base_url(read("UWC_OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL),
        max_run_cost_usd=max_cost,
        cache_dir=Path(cache_dir_text) if cache_dir_text else DEFAULT_LLM_CACHE_DIR,
        pricing_file=Path(pricing_text) if pricing_text else DEFAULT_PRICING_FILE,
        anthropic_effort=validate_effort(read("UWC_ANTHROPIC_EFFORT") or DEFAULT_ANTHROPIC_EFFORT),
    )
