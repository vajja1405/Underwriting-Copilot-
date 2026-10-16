"""Tests for the ollama provider in settings, factory and pricing: no API key, loopback-only http, cost 0.0."""

from pathlib import Path

import pytest
from ollama_test_helpers import FakeOllamaServer, tags_json

from uwcopilot.common.paths import CONFIG_DIR
from uwcopilot.llm.base import LLMMessage
from uwcopilot.llm.budget import BudgetedLLMClient, CostTracker, PriceNotVerified, load_prices
from uwcopilot.llm.cache import CachedLLMClient
from uwcopilot.llm.factory import get_llm_client
from uwcopilot.llm.ollama_client import OllamaClient, OllamaOptions
from uwcopilot.llm.settings import (
    DEFAULT_MODELS,
    DEFAULT_OLLAMA_BASE_URL,
    PROVIDERS,
    LLMSettings,
    SettingsError,
    load_llm_settings,
)

PRICING_FILE = CONFIG_DIR / "llm_pricing.yaml"
LOCAL_MODELS = ("qwen2.5-3b-instruct-q4km", "qwen2.5-1.5b-instruct-f16")
USER = [LLMMessage(role="user", content="hello")]


def settings_without_keys(tmp_path: Path) -> LLMSettings:
    """Ollama settings with no API key of any kind."""
    return LLMSettings(provider="ollama", cache_dir=tmp_path / "cache", pricing_file=PRICING_FILE)


def test_ollama_is_a_known_provider_with_a_local_default() -> None:
    assert "ollama" in PROVIDERS
    assert DEFAULT_MODELS["ollama"] == "qwen2.5-3b-instruct-q4km"
    settings = load_llm_settings(env_file=None, environ={"UWC_LLM_PROVIDER": "ollama"})
    assert settings.provider == "ollama"
    assert settings.ollama_base_url == DEFAULT_OLLAMA_BASE_URL == "http://127.0.0.1:11434"
    assert settings.base_url_for("ollama") == DEFAULT_OLLAMA_BASE_URL
    assert settings.api_key_for("ollama") is None
    assert settings.model_for("ollama") == "qwen2.5-3b-instruct-q4km"


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:11434/", "http://localhost:11434"),
        ("http://[::1]:11434", "http://[::1]:11434"),
        ("https://gpu-box.example:11434", "https://gpu-box.example:11434"),
    ],
)
def test_ollama_base_url_loopback_or_https(url: str, expected: str) -> None:
    settings = load_llm_settings(env_file=None, environ={"UWC_OLLAMA_BASE_URL": url})
    assert settings.ollama_base_url == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.5:11434",
        "http://ollama:11434",
        "http://localhost.evil.example",
        "http://127.0.0.1.evil.com",
        "http://localhost@evil.com",
        # Loopback URLs that would send every request to the wrong path or fail inside httpx.
        "http://127.0.0.1:11434#@evil.com",
        "http://127.0.0.1:11434?x=1",
        "http://127.0.0.1:11434\\evil.com",
        "http://127.0.0.1:80:11434",
    ],
)
def test_ollama_base_url_plain_http_to_another_host_is_refused(url: str) -> None:
    with pytest.raises(SettingsError):
        load_llm_settings(env_file=None, environ={"UWC_OLLAMA_BASE_URL": url})


def test_factory_builds_ollama_without_any_key(tmp_path: Path) -> None:
    server = FakeOllamaServer(tags=tags_json(model="qwen2.5-3b-instruct-q4km"))
    client = get_llm_client("ollama", settings=settings_without_keys(tmp_path), http_client=server.client())
    assert isinstance(client, CachedLLMClient)
    assert isinstance(client.inner, BudgetedLLMClient)
    assert isinstance(client.inner.inner, OllamaClient)
    assert client.provider == "ollama"
    assert client.model == "qwen2.5-3b-instruct-q4km"


def test_factory_without_cache_is_only_budgeted(tmp_path: Path) -> None:
    server = FakeOllamaServer(tags=tags_json(model="qwen2.5-3b-instruct-q4km"))
    client = get_llm_client(
        "ollama",
        settings=settings_without_keys(tmp_path),
        http_client=server.client(),
        use_cache=False,
        ollama_options=OllamaOptions(num_ctx=2048),
    )
    assert isinstance(client, BudgetedLLMClient)
    assert client.inner.options.num_ctx == 2048


def test_pricing_gives_cost_zero_through_the_budget_layer(tmp_path: Path) -> None:
    server = FakeOllamaServer(tags=tags_json(model="qwen2.5-3b-instruct-q4km"))
    tracker = CostTracker(10.0, load_prices(PRICING_FILE))
    client = get_llm_client(
        "ollama", settings=settings_without_keys(tmp_path), http_client=server.client(), cost_tracker=tracker
    )
    response = client.complete("sys", USER, max_tokens=10)
    assert response.cost_usd == 0.0
    assert tracker.spent_usd == 0.0


@pytest.mark.parametrize("model", LOCAL_MODELS)
def test_local_model_price_entries_are_zero_and_verified(model: str) -> None:
    price = load_prices(PRICING_FILE)[model]
    assert price.provider == "ollama"
    assert price.is_verified
    assert price.cost_usd(1_000_000, 1_000_000) == 0.0
    assert "no API charge" in price.source
    assert price.checked_on == "2026-10-12"


def test_local_model_without_a_price_entry_is_refused_before_the_request(tmp_path: Path) -> None:
    server = FakeOllamaServer(tags=tags_json(model="unpriced-local-model"))
    client = get_llm_client(
        "ollama", settings=settings_without_keys(tmp_path), model="unpriced-local-model", http_client=server.client()
    )
    with pytest.raises(PriceNotVerified):
        client.complete("sys", USER, max_tokens=10)
    assert server.chat_bodies() == []
