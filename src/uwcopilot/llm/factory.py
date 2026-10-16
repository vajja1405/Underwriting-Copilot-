"""One place to build a ready-to-use LLM client from settings.

Real providers are wrapped as Cached(Budgeted(provider client)):
- the cache answers repeated requests for free, before the budget is even consulted;
- on a cache miss, the budget check runs before any money is spent.
The fake provider is wrapped only in the budget layer (it is free) and is never cached, so tests always see
exactly the scripted responses.
The ollama provider (local open-weight models) needs no API key. It still goes through Cached(Budgeted(...)): its
price list entries are 0.0, and the cache key holds the model digest and every model option.
The hf_inference provider (Hugging Face Inference Providers, hf_inference_client.py) is DISABLED by default: it is
built only when asked for by name AND UWC_HF_INFERENCE_ENABLED, HF_TOKEN and a model are set, and it goes through
Cached(Budgeted(...)) like every paid provider, so a model without a verified price is refused before any request.
It is not in settings.PROVIDERS, so UWC_LLM_PROVIDER cannot select it.
"""

from collections.abc import Sequence
from typing import Any

from uwcopilot.llm.anthropic_client import AnthropicClient
from uwcopilot.llm.base import LLMClient
from uwcopilot.llm.budget import BudgetedLLMClient, CostTracker, load_prices
from uwcopilot.llm.cache import CachedLLMClient, LLMCache
from uwcopilot.llm.fake_client import FakeLLM, FakeResponder
from uwcopilot.llm.hf_inference_client import (
    HF_INFERENCE_PROVIDER,
    HFInferenceSettings,
    build_hf_inference_client,
    load_hf_inference_settings,
)
from uwcopilot.llm.ollama_client import OllamaClient, OllamaOptions
from uwcopilot.llm.openai_client import OpenAIClient
from uwcopilot.llm.settings import LLMSettings, load_llm_settings, validate_provider

API_KEY_NAMES = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


class MissingAPIKeyError(RuntimeError):
    """Raised when a real provider is requested but its API key is not configured."""


def cost_tracker_from_settings(settings: LLMSettings) -> CostTracker:
    """A fresh CostTracker with the run's cap and the price list from settings."""
    return CostTracker(settings.max_run_cost_usd, load_prices(settings.pricing_file))


def get_llm_client(
    provider: str | None = None,
    *,
    settings: LLMSettings | None = None,
    model: str | None = None,
    cost_tracker: CostTracker | None = None,
    use_cache: bool = True,
    fake_responses: Sequence[str] | FakeResponder | None = None,
    http_client: Any | None = None,
    ollama_options: OllamaOptions | None = None,
    hf_settings: HFInferenceSettings | None = None,
) -> LLMClient:
    """Build an LLM client.

    provider: "anthropic", "openai", "ollama", "fake" (default: settings.provider, i.e. UWC_LLM_PROVIDER) or
        "hf_inference" (only by name; disabled by default).
    settings: defaults to load_llm_settings() (repo .env plus project variables).
    model: overrides the model from settings.
    cost_tracker: share one tracker across clients so the cap covers the whole run.
    use_cache: real providers only; set False to always call the API.
    fake_responses: scripted strings or a responder function for the fake provider.
    http_client: test hook passed to the SDK (an httpx2.Client with a mock transport); for ollama, an
        httpx.Client with a MockTransport.
    ollama_options: num_ctx, seed, optional JSON-schema format and expected digest for the ollama provider.
    hf_settings: settings for provider "hf_inference" (default: load_hf_inference_settings(), from .env).

    Raises MissingAPIKeyError when a paid provider has no API key; no request is attempted in that case.
    Provider "hf_inference" raises HFInferenceDisabled / HFInferenceTokenMissing / HFInferenceModelMissing (no request)
    unless it was explicitly enabled; see hf_inference_client.py.
    The ollama client contacts the local server when it is built (version and model digest).
    """
    settings = settings or load_llm_settings()
    if provider == HF_INFERENCE_PROVIDER:
        tracker = cost_tracker or cost_tracker_from_settings(settings)
        hf_client = build_hf_inference_client(hf_settings or load_hf_inference_settings(), model, http_client)
        return wrap_provider_client(hf_client, tracker, settings, use_cache)
    chosen = validate_provider(provider or settings.provider)
    chosen_model = model or settings.model_for(chosen)
    tracker = cost_tracker or cost_tracker_from_settings(settings)

    if chosen == "fake":
        fake = FakeLLM(fake_responses if fake_responses is not None else [], model=chosen_model)
        return BudgetedLLMClient(fake, tracker)

    provider_client: LLMClient
    if chosen == "ollama":
        provider_client = OllamaClient(
            settings.ollama_base_url, chosen_model, options=ollama_options, http_client=http_client
        )
        return wrap_provider_client(provider_client, tracker, settings, use_cache)

    api_key = settings.api_key_for(chosen)
    if not api_key:
        raise MissingAPIKeyError(
            f"{API_KEY_NAMES[chosen]} is not set. Add it to the repo .env file (see .env.example). "
            "No LLM call was made; metrics that need an LLM stay 'not measured'."
        )
    base_url = settings.base_url_for(chosen) or ""
    if chosen == "anthropic":
        provider_client = AnthropicClient(
            api_key, base_url, chosen_model, http_client=http_client, effort=settings.anthropic_effort
        )
    else:
        provider_client = OpenAIClient(api_key, base_url, chosen_model, http_client=http_client)
    return wrap_provider_client(provider_client, tracker, settings, use_cache)


def wrap_provider_client(
    provider_client: LLMClient, tracker: CostTracker, settings: LLMSettings, use_cache: bool
) -> LLMClient:
    """Wrap a real provider client as Cached(Budgeted(client)), or only Budgeted(client) when use_cache is False."""
    budgeted = BudgetedLLMClient(provider_client, tracker)
    if not use_cache:
        return budgeted
    return CachedLLMClient(budgeted, LLMCache(settings.cache_dir))
