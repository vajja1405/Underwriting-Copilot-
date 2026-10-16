"""Tests for the hf_inference provider: disabled by default, no request without every opt-in, mocked HTTP only.

No test sends a request to Hugging Face: every HTTP call goes to an httpx.MockTransport, and the tests that check
"no request was made" use a transport that fails the test if it is called.
"""

import json
from pathlib import Path

import httpx
import pytest

from uwcopilot.common.paths import CONFIG_DIR
from uwcopilot.llm.base import IncompleteLLMResponse, LLMMessage
from uwcopilot.llm.budget import CostTracker, ModelPrice, PriceNotVerified, load_prices
from uwcopilot.llm.cache import CachedLLMClient
from uwcopilot.llm.factory import get_llm_client
from uwcopilot.llm.hf_inference_client import (
    DEFAULT_HF_BASE_URL,
    PROVIDER,
    HFInferenceClient,
    HFInferenceDisabled,
    HFInferenceError,
    HFInferenceModelMissing,
    HFInferenceSettings,
    HFInferenceTokenMissing,
    build_chat_body,
    load_hf_inference_settings,
)
from uwcopilot.llm.settings import PROVIDERS, LLMSettings, SettingsError, load_llm_settings, validate_provider

TOKEN = "hf_test_token_not_real"
MODEL = "test-org/test-model"
USER = [LLMMessage(role="user", content="Write one sentence.")]
PRICING_FILE = CONFIG_DIR / "llm_pricing.yaml"


def answer_json(content: str = "A sentence.", finish_reason: str = "stop", usage: bool = True) -> dict:
    """An OpenAI-style chat-completions answer."""
    body: dict = {
        "id": "chatcmpl-test",
        "model": MODEL,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": finish_reason}],
    }
    if usage:
        body["usage"] = {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
    return body


class Recorder:
    """A MockTransport handler that records requests and answers with fixed JSON."""

    def __init__(self, status: int = 200, body: dict | None = None) -> None:
        self.status = status
        self.body = body if body is not None else answer_json()
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status, json=self.body)


def must_not_be_called(request: httpx.Request) -> httpx.Response:
    """A handler for checks that no request is sent at all."""
    raise AssertionError(f"no request may be sent, got {request.method} {request.url}")


def enabled_settings(**changes: object) -> HFInferenceSettings:
    """Settings with every opt-in present (test token, test model)."""
    fields: dict = {"enabled": True, "token": TOKEN, "model": MODEL}
    fields.update(changes)
    return HFInferenceSettings(**fields)


def llm_settings(tmp_path: Path) -> LLMSettings:
    """Project LLM settings with a temporary cache folder and the real price list."""
    return LLMSettings(provider="fake", cache_dir=tmp_path / "cache", pricing_file=PRICING_FILE)


def priced_tracker() -> CostTracker:
    """A cost tracker that knows a TEST price for the test model (not a real price)."""
    prices = load_prices(PRICING_FILE)
    prices[MODEL] = ModelPrice(
        model=MODEL,
        provider=PROVIDER,
        input_usd_per_mtok=1.0,
        output_usd_per_mtok=1.0,
        source="test price for a mocked endpoint, not a real price",
        checked_on="2026-10-14",
    )
    return CostTracker(10.0, prices)


# --------------------------------------------------------------------------------------------------------------
# Settings: off unless explicitly enabled
# --------------------------------------------------------------------------------------------------------------


def test_settings_are_disabled_by_default_and_hide_the_token() -> None:
    settings = load_hf_inference_settings(env_file=None, environ={"HF_TOKEN": TOKEN})
    assert settings.enabled is False
    assert settings.token == TOKEN and TOKEN not in repr(settings)
    assert settings.base_url == DEFAULT_HF_BASE_URL == "https://router.huggingface.co/v1"
    assert settings.model is None


@pytest.mark.parametrize(
    ("flag", "expected"), [("1", True), ("true", True), ("YES", True), ("0", False), ("no", False)]
)
def test_only_an_explicit_flag_enables_the_provider(flag: str, expected: bool) -> None:
    settings = load_hf_inference_settings(env_file=None, environ={"UWC_HF_INFERENCE_ENABLED": flag})
    assert settings.enabled is expected


def test_the_env_file_wins_and_the_base_url_is_validated(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("UWC_HF_INFERENCE_MODEL=file-org/file-model\n", encoding="utf-8")
    settings = load_hf_inference_settings(env_file=env_file, environ={"UWC_HF_INFERENCE_MODEL": "env-org/env-model"})
    assert settings.model == "file-org/file-model"
    with pytest.raises(SettingsError):
        load_hf_inference_settings(env_file=None, environ={"UWC_HF_INFERENCE_BASE_URL": "http://router.example/v1"})


def test_uwc_llm_provider_cannot_select_hf_inference() -> None:
    assert PROVIDER not in PROVIDERS
    with pytest.raises(SettingsError):
        validate_provider(PROVIDER)
    with pytest.raises(SettingsError):
        load_llm_settings(env_file=None, environ={"UWC_LLM_PROVIDER": PROVIDER})


# --------------------------------------------------------------------------------------------------------------
# Factory: no request unless every opt-in is present
# --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("settings", "error"),
    [
        (HFInferenceSettings(token=TOKEN, model=MODEL), HFInferenceDisabled),
        (enabled_settings(token=None), HFInferenceTokenMissing),
        (enabled_settings(model=None), HFInferenceModelMissing),
    ],
)
def test_the_factory_refuses_without_every_opt_in(
    tmp_path: Path, settings: HFInferenceSettings, error: type[Exception]
) -> None:
    http = httpx.Client(transport=httpx.MockTransport(must_not_be_called))
    with pytest.raises(error):
        get_llm_client(PROVIDER, settings=llm_settings(tmp_path), hf_settings=settings, http_client=http)


def test_the_factory_loads_settings_itself_and_stays_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("UWC_HF_INFERENCE_ENABLED", "HF_TOKEN", "UWC_HF_INFERENCE_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        "uwcopilot.llm.factory.load_hf_inference_settings",
        lambda: load_hf_inference_settings(env_file=None, environ={}),
    )
    http = httpx.Client(transport=httpx.MockTransport(must_not_be_called))
    with pytest.raises(HFInferenceDisabled):
        get_llm_client(PROVIDER, settings=llm_settings(tmp_path), http_client=http)


def test_an_enabled_client_without_a_verified_price_is_refused_before_any_request(tmp_path: Path) -> None:
    http = httpx.Client(transport=httpx.MockTransport(must_not_be_called))
    client = get_llm_client(PROVIDER, settings=llm_settings(tmp_path), hf_settings=enabled_settings(), http_client=http)
    assert isinstance(client, CachedLLMClient) and client.provider == PROVIDER
    with pytest.raises(PriceNotVerified):
        client.complete("system", USER, max_tokens=50)


def test_an_enabled_priced_client_calls_the_mock_router_once_then_uses_the_cache(tmp_path: Path) -> None:
    recorder = Recorder()
    http = httpx.Client(transport=httpx.MockTransport(recorder))
    tracker = priced_tracker()
    client = get_llm_client(
        PROVIDER,
        settings=llm_settings(tmp_path),
        hf_settings=enabled_settings(),
        http_client=http,
        cost_tracker=tracker,
    )
    first = client.complete("system", USER, max_tokens=50)
    second = client.complete("system", USER, max_tokens=50)
    assert first.text == "A sentence." and first.cost_usd is not None and first.cost_usd > 0
    assert second.cached is True and len(recorder.requests) == 1
    assert tracker.paid_calls == 1


# --------------------------------------------------------------------------------------------------------------
# The client against a mocked router
# --------------------------------------------------------------------------------------------------------------


def test_request_shape_and_answer_mapping() -> None:
    recorder = Recorder()
    client = HFInferenceClient(TOKEN, MODEL, http_client=httpx.Client(transport=httpx.MockTransport(recorder)))
    response = client.complete("Be brief.", USER, max_tokens=64, temperature=0.0)
    request = recorder.requests[0]
    assert request.method == "POST" and str(request.url) == "https://router.huggingface.co/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    body = json.loads(request.content)
    assert body == build_chat_body(MODEL, "Be brief.", USER, 64, 0.0)
    assert body["messages"][0] == {"role": "system", "content": "Be brief."} and body["stream"] is False
    assert (response.text, response.provider, response.model) == ("A sentence.", PROVIDER, MODEL)
    assert (response.input_tokens, response.output_tokens, response.stop_reason) == (12, 3, "stop")
    assert response.cost_usd is None and not response.is_fake


def test_an_empty_system_prompt_is_not_sent() -> None:
    assert [item["role"] for item in build_chat_body(MODEL, "", USER, 10, 0.0)["messages"]] == ["user"]


def test_a_cut_off_answer_raises_incomplete() -> None:
    recorder = Recorder(body=answer_json(finish_reason="length"))
    client = HFInferenceClient(TOKEN, MODEL, http_client=httpx.Client(transport=httpx.MockTransport(recorder)))
    with pytest.raises(IncompleteLLMResponse):
        client.complete("s", USER, max_tokens=5)


def test_an_answer_without_usage_is_not_used() -> None:
    recorder = Recorder(body=answer_json(usage=False))
    client = HFInferenceClient(TOKEN, MODEL, http_client=httpx.Client(transport=httpx.MockTransport(recorder)))
    with pytest.raises(HFInferenceError, match="usage"):
        client.complete("s", USER, max_tokens=5)


def test_an_http_error_names_the_status_but_never_the_token() -> None:
    recorder = Recorder(status=401, body={"error": "Invalid credentials in Authorization header"})
    client = HFInferenceClient(TOKEN, MODEL, http_client=httpx.Client(transport=httpx.MockTransport(recorder)))
    with pytest.raises(HFInferenceError) as raised:
        client.complete("s", USER, max_tokens=5)
    assert "HTTP 401" in str(raised.value) and TOKEN not in str(raised.value)


def test_a_transport_error_becomes_an_hf_inference_error() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = HFInferenceClient(TOKEN, MODEL, http_client=httpx.Client(transport=httpx.MockTransport(refuse)))
    with pytest.raises(HFInferenceError, match="could not reach"):
        client.complete("s", USER, max_tokens=5)


def test_a_body_without_choices_is_not_used() -> None:
    recorder = Recorder(body={"usage": {"prompt_tokens": 1, "completion_tokens": 1}})
    client = HFInferenceClient(TOKEN, MODEL, http_client=httpx.Client(transport=httpx.MockTransport(recorder)))
    with pytest.raises(HFInferenceError, match="no choices"):
        client.complete("s", USER, max_tokens=5)


def test_the_client_needs_a_token_and_a_model() -> None:
    with pytest.raises(HFInferenceTokenMissing):
        HFInferenceClient("", MODEL)
    with pytest.raises(HFInferenceModelMissing):
        HFInferenceClient(TOKEN, "")
