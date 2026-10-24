"""Tests for the bedrock provider: disabled by default, no request without every opt-in, stubbed bedrock-runtime only.

No test sends a request to AWS. The boto3 clients are built with the test credentials "testing" in an environment
without any real AWS configuration (no AWS_* variables, missing config files, instance metadata disabled), and every
Converse call is answered by botocore's Stubber, which also checks each request against the bedrock-runtime service
model, or by a botocore "before-send" hook (the retry test), or by a small fake object (answers the service model
would not allow). A Stubber with nothing queued fails the test if a request is attempted.
"""

import ast
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from uwcopilot.common.paths import CONFIG_DIR
from uwcopilot.llm import bedrock_client
from uwcopilot.llm.base import IncompleteLLMResponse, LLMMessage
from uwcopilot.llm.bedrock_client import (
    PROVIDER,
    STRUCTURED_OUTPUT_TOOL,
    BedrockAccessDenied,
    BedrockClient,
    BedrockCredentialsMissing,
    BedrockDisabled,
    BedrockError,
    BedrockModelMissing,
    BedrockOptions,
    BedrockRegionMissing,
    BedrockSDKMissing,
    BedrockSettings,
    BedrockThrottled,
    build_bedrock_client,
    build_converse_request,
    load_bedrock_settings,
    load_sdk_module,
    make_runtime_client,
)
from uwcopilot.llm.budget import CostTracker, ModelPrice, PriceNotVerified, load_prices
from uwcopilot.llm.cache import CachedLLMClient, cache_key
from uwcopilot.llm.factory import get_llm_client
from uwcopilot.llm.settings import (
    PROVIDERS,
    ImplicitSDKConfigError,
    LLMSettings,
    SettingsError,
    load_llm_settings,
    validate_provider,
)

# botocore comes only with the optional "bedrock" extra (`uv sync --extra bedrock`). CI installs no extras
# (`uv sync --locked`), so without it this module is skipped instead of failing to import.
botocore_exceptions = pytest.importorskip("botocore.exceptions", reason="needs the optional 'bedrock' extra")
AWSResponse = pytest.importorskip("botocore.awsrequest").AWSResponse
Stubber = pytest.importorskip("botocore.stub").Stubber
EndpointConnectionError = botocore_exceptions.EndpointConnectionError
NoCredentialsError = botocore_exceptions.NoCredentialsError

MODEL = "test-provider.test-model-v1:0"
REGION = "us-east-1"
USER = [LLMMessage(role="user", content="Write one sentence.")]
PRICING_FILE = CONFIG_DIR / "llm_pricing.yaml"
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}


def converse_answer(
    *blocks: dict[str, Any], stop_reason: str = "end_turn", input_tokens: int = 12, output_tokens: int = 3
) -> dict[str, Any]:
    """A Converse answer in the shape of the bedrock-runtime service model (default: one text block)."""
    content = list(blocks) or [{"text": "A sentence."}]
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": stop_reason,
        "usage": {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "totalTokens": input_tokens + output_tokens,
        },
        "metrics": {"latencyMs": 250},
        "ResponseMetadata": {"RequestId": "req-test-1", "HTTPStatusCode": 200},
    }


class FakeRuntime:
    """A stand-in for a bedrock-runtime client: returns a fixed answer or raises a fixed error, and records calls."""

    def __init__(self, answer: Any = None, error: Exception | None = None) -> None:
        self.answer = answer
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def converse(self, **request: Any) -> Any:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.answer


@pytest.fixture
def aws_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> pytest.MonkeyPatch:
    """No real AWS configuration: every AWS_* variable removed, config files not yet written, no instance metadata."""
    for name in list(os.environ):
        if name.startswith("AWS_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "aws-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "aws-credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    return monkeypatch


@pytest.fixture
def test_credentials(aws_env: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """The aws_env environment plus obviously fake static credentials (enough to build and sign, never sent)."""
    aws_env.setenv("AWS_ACCESS_KEY_ID", "testing")
    aws_env.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    return aws_env


def enabled_settings(**changes: Any) -> BedrockSettings:
    """Settings with every opt-in present (test region, test model id)."""
    fields: dict[str, Any] = {"enabled": True, "region": REGION, "model": MODEL}
    fields.update(changes)
    return BedrockSettings(**fields)


def stubbed_client(
    options: BedrockOptions | None = None, region: str = REGION, send_temperature: bool = False
) -> tuple[BedrockClient, Stubber]:
    """A BedrockClient over a real boto3 bedrock-runtime client whose calls are answered by a Stubber."""
    runtime = make_runtime_client(enabled_settings(region=region))
    client = BedrockClient(runtime, MODEL, region, options=options, send_temperature=send_temperature)
    return client, Stubber(runtime)


def llm_settings(tmp_path: Path) -> LLMSettings:
    """Project LLM settings with a temporary cache folder and the real price list."""
    return LLMSettings(provider="fake", cache_dir=tmp_path / "cache", pricing_file=PRICING_FILE)


def priced_tracker() -> CostTracker:
    """A cost tracker that knows a TEST price for the test model id (not a real Bedrock price)."""
    prices = load_prices(PRICING_FILE)
    prices[MODEL] = ModelPrice(
        model=MODEL,
        provider=PROVIDER,
        input_usd_per_mtok=1.0,
        output_usd_per_mtok=1.0,
        source="test price for a stubbed endpoint, not a real price",
        checked_on="2026-10-23",
    )
    return CostTracker(10.0, prices)


# --------------------------------------------------------------------------------------------------------------
# Settings: off unless explicitly enabled
# --------------------------------------------------------------------------------------------------------------


def test_settings_are_disabled_by_default() -> None:
    assert load_bedrock_settings(env_file=None, environ={}) == BedrockSettings()
    assert BedrockSettings() == BedrockSettings(
        enabled=False, region=None, profile=None, model=None, send_temperature=False
    )


@pytest.mark.parametrize(
    ("flag", "expected"), [("1", True), ("true", True), ("YES", True), ("0", False), ("no", False)]
)
def test_only_an_explicit_flag_enables_the_provider(flag: str, expected: bool) -> None:
    assert load_bedrock_settings(env_file=None, environ={"UWC_BEDROCK_ENABLED": flag}).enabled is expected


def test_the_env_file_wins_and_the_region_is_validated(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("UWC_BEDROCK_MODEL=file.model-v1:0\nUWC_BEDROCK_REGION=eu-west-1\n", encoding="utf-8")
    environ = {"UWC_BEDROCK_MODEL": "env.model-v1:0", "UWC_BEDROCK_PROFILE": "uw-dev", "AWS_REGION": "us-west-2"}
    settings = load_bedrock_settings(env_file=env_file, environ=environ)
    assert (settings.model, settings.region, settings.profile) == ("file.model-v1:0", "eu-west-1", "uw-dev")
    for bad_region in ("us-east-1.evil.example", "US-EAST-1", "localhost"):
        with pytest.raises(SettingsError):
            load_bedrock_settings(env_file=None, environ={"UWC_BEDROCK_REGION": bad_region})


def test_aws_region_variables_are_never_used_as_the_region() -> None:
    settings = load_bedrock_settings(env_file=None, environ={"AWS_REGION": "us-west-2", "AWS_DEFAULT_REGION": "eu-1"})
    assert settings.region is None


def test_uwc_llm_provider_cannot_select_bedrock() -> None:
    assert PROVIDER not in PROVIDERS
    with pytest.raises(SettingsError):
        validate_provider(PROVIDER)
    with pytest.raises(SettingsError):
        load_llm_settings(env_file=None, environ={"UWC_LLM_PROVIDER": PROVIDER})


def test_no_bedrock_price_is_marked_verified() -> None:
    prices = load_prices(PRICING_FILE)
    assert not [price for price in prices.values() if price.provider == PROVIDER and price.is_verified]


# --------------------------------------------------------------------------------------------------------------
# Factory: no request unless every opt-in is present
# --------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("settings", "error"),
    [
        (BedrockSettings(region=REGION, model=MODEL), BedrockDisabled),
        (enabled_settings(region=None), BedrockRegionMissing),
        (enabled_settings(model=None), BedrockModelMissing),
    ],
)
def test_the_factory_refuses_without_every_opt_in(
    tmp_path: Path, test_credentials: pytest.MonkeyPatch, settings: BedrockSettings, error: type[Exception]
) -> None:
    runtime = make_runtime_client(enabled_settings())
    with Stubber(runtime), pytest.raises(error):
        get_llm_client(PROVIDER, settings=llm_settings(tmp_path), bedrock_settings=settings, bedrock_runtime=runtime)


def test_the_factory_loads_settings_itself_and_stays_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "uwcopilot.llm.factory.load_bedrock_settings", lambda: load_bedrock_settings(env_file=None, environ={})
    )
    with pytest.raises(BedrockDisabled):
        get_llm_client(PROVIDER, settings=llm_settings(tmp_path))


def test_missing_credentials_are_reported_when_the_client_is_built(tmp_path: Path, aws_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(BedrockCredentialsMissing, match="No AWS credentials"):
        get_llm_client(PROVIDER, settings=llm_settings(tmp_path), bedrock_settings=enabled_settings())


def test_an_unknown_profile_is_reported_as_missing_credentials(aws_env: pytest.MonkeyPatch) -> None:
    with pytest.raises(BedrockCredentialsMissing, match="no-such-profile"):
        make_runtime_client(enabled_settings(profile="no-such-profile"))


@pytest.mark.parametrize("name", ["AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_BEDROCK_RUNTIME", "AWS_BEARER_TOKEN_BEDROCK"])
def test_implicit_sdk_variables_are_refused(test_credentials: pytest.MonkeyPatch, name: str) -> None:
    test_credentials.setenv(name, "https://somewhere-else.example" if "URL" in name else "not-a-real-key")
    with pytest.raises(ImplicitSDKConfigError, match=name):
        make_runtime_client(enabled_settings())


def test_the_built_client_uses_the_chosen_region_retries_and_timeout(test_credentials: pytest.MonkeyPatch) -> None:
    runtime = make_runtime_client(enabled_settings(region="eu-west-1"))
    assert runtime.meta.endpoint_url == "https://bedrock-runtime.eu-west-1.amazonaws.com"
    assert runtime.meta.config.retries == {"mode": "standard", "total_max_attempts": 3}
    assert runtime.meta.config.read_timeout == 120.0


def test_an_enabled_client_without_a_verified_price_is_refused_before_any_request(
    tmp_path: Path, test_credentials: pytest.MonkeyPatch
) -> None:
    client = get_llm_client(PROVIDER, settings=llm_settings(tmp_path), bedrock_settings=enabled_settings())
    assert isinstance(client, CachedLLMClient) and client.provider == PROVIDER and client.model == MODEL
    runtime = client.inner.inner._runtime
    with Stubber(runtime), pytest.raises(PriceNotVerified):
        client.complete("system", USER, max_tokens=50)


def test_an_enabled_priced_client_calls_the_stub_once_then_uses_the_cache(
    tmp_path: Path, test_credentials: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime_client(enabled_settings())
    tracker = priced_tracker()
    client = get_llm_client(
        PROVIDER,
        settings=llm_settings(tmp_path),
        bedrock_settings=enabled_settings(),
        bedrock_runtime=runtime,
        cost_tracker=tracker,
    )
    with Stubber(runtime) as stubber:
        stubber.add_response("converse", converse_answer())
        first = client.complete("system", USER, max_tokens=50)
        second = client.complete("system", USER, max_tokens=50)
        stubber.assert_no_pending_responses()
    assert first.text == "A sentence." and first.cost_usd == pytest.approx((12 + 3) / 1_000_000)
    assert second.cached is True and second.cost_usd == 0.0 and tracker.paid_calls == 1


def test_a_cached_guardrail_stop_is_still_refused_on_a_cache_hit(
    tmp_path: Path, test_credentials: pytest.MonkeyPatch
) -> None:
    runtime = make_runtime_client(enabled_settings())
    tracker = priced_tracker()
    client = get_llm_client(
        PROVIDER,
        settings=llm_settings(tmp_path),
        bedrock_settings=enabled_settings(),
        bedrock_runtime=runtime,
        cost_tracker=tracker,
    )
    with Stubber(runtime) as stubber:
        stubber.add_response("converse", converse_answer(stop_reason="guardrail_intervened"))
        for _ in range(2):
            with pytest.raises(IncompleteLLMResponse):
                client.complete("system", USER, max_tokens=50)
        stubber.assert_no_pending_responses()
    assert tracker.paid_calls == 1


# --------------------------------------------------------------------------------------------------------------
# The client against a stubbed bedrock-runtime
# --------------------------------------------------------------------------------------------------------------


def test_request_shape_and_answer_mapping(test_credentials: pytest.MonkeyPatch) -> None:
    client, stubber = stubbed_client()
    answer = converse_answer(
        {"reasoningContent": {"reasoningText": {"text": "thinking that must be dropped"}}},
        {"text": "A sen"},
        {"text": "tence."},
    )
    expected_request = {
        "modelId": MODEL,
        "messages": [{"role": "user", "content": [{"text": "Write one sentence."}]}],
        "inferenceConfig": {"maxTokens": 64},
        "system": [{"text": "Be brief."}],
    }
    with stubber:
        stubber.add_response("converse", answer, expected_params=expected_request)
        response = client.complete("Be brief.", USER, max_tokens=64, temperature=0.0)
        stubber.assert_no_pending_responses()
    assert (response.text, response.provider, response.model) == ("A sentence.", PROVIDER, MODEL)
    assert (response.input_tokens, response.output_tokens, response.stop_reason) == (12, 3, "end_turn")
    assert response.cost_usd is None and not response.is_fake and not response.cached
    assert response.metadata["request_id"] == "req-test-1" and response.metadata["bedrock_latency_ms"] == 250
    assert response.metadata["region"] == REGION and response.metadata["bedrock_stop_reason"] == "end_turn"


def test_an_empty_system_prompt_is_not_sent_and_temperature_needs_the_flag() -> None:
    without = build_converse_request(MODEL, "", USER, 10, 0.3, send_temperature=False, output_schema=None)
    assert "system" not in without and without["inferenceConfig"] == {"maxTokens": 10}
    with_temperature = build_converse_request(MODEL, "s", USER, 10, 0.3, send_temperature=True, output_schema=None)
    assert with_temperature["inferenceConfig"] == {"maxTokens": 10, "temperature": 0.3}
    with pytest.raises(ValueError, match="from 0 to 1"):
        build_converse_request(MODEL, "s", USER, 10, 1.5, send_temperature=True, output_schema=None)


def test_a_multi_turn_conversation_keeps_roles_and_order(test_credentials: pytest.MonkeyPatch) -> None:
    client, stubber = stubbed_client(send_temperature=True)
    messages = [
        LLMMessage(role="user", content="first"),
        LLMMessage(role="assistant", content="reply"),
        LLMMessage(role="user", content="second"),
    ]
    expected_request = {
        "modelId": MODEL,
        "messages": [
            {"role": "user", "content": [{"text": "first"}]},
            {"role": "assistant", "content": [{"text": "reply"}]},
            {"role": "user", "content": [{"text": "second"}]},
        ],
        "inferenceConfig": {"maxTokens": 20, "temperature": 0.5},
        "system": [{"text": "s"}],
    }
    with stubber:
        stubber.add_response("converse", converse_answer(), expected_params=expected_request)
        client.complete("s", messages, max_tokens=20, temperature=0.5)
        stubber.assert_no_pending_responses()


@pytest.mark.parametrize(
    ("bedrock_reason", "shared_reason"),
    [
        ("max_tokens", "max_tokens"),
        ("model_context_window_exceeded", "max_tokens"),
        ("content_filtered", "content_filter"),
        ("guardrail_intervened", "content_filter"),
        ("malformed_model_output", "refusal"),
        ("malformed_tool_use", "refusal"),
    ],
)
def test_unfinished_answers_raise_incomplete_with_the_original_reason(
    test_credentials: pytest.MonkeyPatch, bedrock_reason: str, shared_reason: str
) -> None:
    client, stubber = stubbed_client()
    with stubber:
        stubber.add_response("converse", converse_answer(stop_reason=bedrock_reason))
        with pytest.raises(IncompleteLLMResponse) as raised:
            client.complete("s", USER, max_tokens=5)
    assert raised.value.response.stop_reason == shared_reason
    assert raised.value.response.metadata["bedrock_stop_reason"] == bedrock_reason
    assert raised.value.response.output_tokens == 3


@pytest.mark.parametrize("bedrock_reason", ["end_turn", "stop_sequence"])
def test_finished_answers_keep_their_stop_reason(test_credentials: pytest.MonkeyPatch, bedrock_reason: str) -> None:
    client, stubber = stubbed_client()
    with stubber:
        stubber.add_response("converse", converse_answer(stop_reason=bedrock_reason))
        assert client.complete("s", USER, max_tokens=5).stop_reason == bedrock_reason


def test_an_unknown_stop_reason_fails_closed() -> None:
    client = BedrockClient(FakeRuntime(answer=converse_answer(stop_reason="some_new_reason")), MODEL, REGION)
    with pytest.raises(IncompleteLLMResponse) as raised:
        client.complete("s", USER, max_tokens=5)
    assert raised.value.response.stop_reason == "refusal"
    assert raised.value.response.metadata["bedrock_stop_reason"] == "some_new_reason"


@pytest.mark.parametrize(
    ("code", "status", "error"),
    [
        ("ThrottlingException", 429, BedrockThrottled),
        ("AccessDeniedException", 403, BedrockAccessDenied),
        ("ValidationException", 400, BedrockError),
        ("ResourceNotFoundException", 404, BedrockError),
    ],
)
def test_service_errors_name_the_error_code(
    test_credentials: pytest.MonkeyPatch, code: str, status: int, error: type[Exception]
) -> None:
    client, stubber = stubbed_client()
    with stubber:
        stubber.add_client_error(
            "converse", service_error_code=code, service_message="details", http_status_code=status
        )
        with pytest.raises(error) as raised:
            client.complete("s", USER, max_tokens=5)
    assert code in str(raised.value) and "testing" not in str(raised.value)


def test_throttling_is_retried_twice_then_reported(test_credentials: pytest.MonkeyPatch) -> None:
    test_credentials.setattr("botocore.endpoint.time.sleep", lambda seconds: None)
    runtime = make_runtime_client(enabled_settings())
    attempts: list[str] = []

    class Body:
        def stream(self) -> Any:
            yield b'{"message": "Too many requests"}'

    def throttle(request: Any, **kwargs: Any) -> AWSResponse:
        attempts.append(request.url)
        headers = {"x-amzn-ErrorType": "ThrottlingException", "Content-Type": "application/json"}
        return AWSResponse(request.url, 429, headers, Body())

    runtime.meta.events.register("before-send.bedrock-runtime.Converse", throttle)
    client = BedrockClient(runtime, MODEL, REGION)
    with pytest.raises(BedrockThrottled, match="after 2 retries"):
        client.complete("s", USER, max_tokens=5)
    assert len(attempts) == 3
    assert attempts[0].startswith("https://bedrock-runtime.us-east-1.amazonaws.com/model/")


def test_sdk_failures_become_bedrock_errors() -> None:
    no_credentials = BedrockClient(FakeRuntime(error=NoCredentialsError()), MODEL, REGION)
    with pytest.raises(BedrockCredentialsMissing):
        no_credentials.complete("s", USER, max_tokens=5)
    unreachable = BedrockClient(FakeRuntime(error=EndpointConnectionError(endpoint_url="https://x")), MODEL, REGION)
    with pytest.raises(BedrockError, match="failed before an answer arrived"):
        unreachable.complete("s", USER, max_tokens=5)


def test_an_answer_without_usage_or_message_is_not_used() -> None:
    without_usage = converse_answer()
    del without_usage["usage"]
    with pytest.raises(BedrockError, match="usage"):
        BedrockClient(FakeRuntime(answer=without_usage), MODEL, REGION).complete("s", USER, max_tokens=5)
    without_message = converse_answer()
    without_message["output"] = {}
    with pytest.raises(BedrockError, match="no output message"):
        BedrockClient(FakeRuntime(answer=without_message), MODEL, REGION).complete("s", USER, max_tokens=5)


def test_the_client_needs_a_model_and_a_valid_region() -> None:
    with pytest.raises(BedrockModelMissing):
        BedrockClient(FakeRuntime(), "", REGION)
    with pytest.raises(SettingsError):
        BedrockClient(FakeRuntime(), MODEL, "not a region")
    with pytest.raises(BedrockModelMissing):
        build_bedrock_client(enabled_settings(model=None), runtime_client=FakeRuntime())
    assert build_bedrock_client(enabled_settings(), model="other.model-v1:0", runtime_client=FakeRuntime()).model == (
        "other.model-v1:0"
    )


# --------------------------------------------------------------------------------------------------------------
# Structured output (forced tool use)
# --------------------------------------------------------------------------------------------------------------


def test_structured_output_forces_one_tool_and_returns_its_input_as_json(test_credentials: pytest.MonkeyPatch) -> None:
    client, stubber = stubbed_client(options=BedrockOptions(output_schema=SCHEMA))
    tool_call = {"toolUse": {"toolUseId": "tool-1", "name": STRUCTURED_OUTPUT_TOOL, "input": {"answer": "yes"}}}
    with stubber:
        stubber.add_response("converse", converse_answer(tool_call, stop_reason="tool_use"))
        response = client.complete("s", USER, max_tokens=50)
        stubber.assert_no_pending_responses()
    assert response.text == '{"answer": "yes"}' and response.stop_reason == "tool_use"
    request = build_converse_request(MODEL, "s", USER, 50, 0, send_temperature=False, output_schema=SCHEMA)
    assert request["toolConfig"]["toolChoice"] == {"tool": {"name": STRUCTURED_OUTPUT_TOOL}}
    assert request["toolConfig"]["tools"][0]["toolSpec"]["inputSchema"] == {"json": SCHEMA}


def test_structured_output_without_the_tool_call_is_not_used(test_credentials: pytest.MonkeyPatch) -> None:
    client, stubber = stubbed_client(options=BedrockOptions(output_schema=SCHEMA))
    with stubber:
        stubber.add_response("converse", converse_answer({"text": "plain text instead"}))
        with pytest.raises(BedrockError, match="tool call"):
            client.complete("s", USER, max_tokens=50)


# --------------------------------------------------------------------------------------------------------------
# Cache key: stable for the same settings, different when anything that changes the answer changes
# --------------------------------------------------------------------------------------------------------------


def key_for(client: BedrockClient) -> str:
    """The LLM cache key of one fixed request through this client (the same params CachedLLMClient builds)."""
    params = {"max_tokens": 50, "temperature": 0, "client": dict(client.request_fingerprint)}
    return cache_key(client.provider, client.model, "system", USER, params)


def test_the_cache_key_is_stable_and_tracks_every_answer_changing_setting(
    tmp_path: Path, test_credentials: pytest.MonkeyPatch
) -> None:
    first, _ = stubbed_client()
    second, _ = stubbed_client()
    assert first.request_fingerprint == {
        "region": "us-east-1",
        "endpoint_url": "https://bedrock-runtime.us-east-1.amazonaws.com",
        "send_temperature": "False",
        "output_schema_sha256": "",
    }
    assert key_for(first) == key_for(second)
    changed = [
        stubbed_client(region="eu-west-1")[0],
        stubbed_client(send_temperature=True)[0],
        stubbed_client(options=BedrockOptions(output_schema=SCHEMA))[0],
    ]
    keys = {key_for(first)} | {key_for(client) for client in changed}
    assert len(keys) == 4
    # A named profile chooses credentials, not the answer, so it does not change the key.
    (tmp_path / "aws-credentials").write_text(
        "[uw-dev]\naws_access_key_id = testing\naws_secret_access_key = testing\n", encoding="utf-8"
    )
    profiled = BedrockClient(make_runtime_client(enabled_settings(profile="uw-dev")), MODEL, REGION)
    assert key_for(profiled) == key_for(first)


# --------------------------------------------------------------------------------------------------------------
# boto3 stays optional
# --------------------------------------------------------------------------------------------------------------


def test_the_module_does_not_import_boto3_at_import_time() -> None:
    tree = ast.parse(Path(bedrock_client.__file__).read_text(encoding="utf-8"))
    top_level: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            top_level.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_level.append(node.module)
    assert not [name for name in top_level if name.split(".")[0] in {"boto3", "botocore"}]


def test_a_missing_boto3_names_the_install_command(aws_env: pytest.MonkeyPatch) -> None:
    aws_env.setitem(sys.modules, "boto3", None)
    with pytest.raises(BedrockSDKMissing, match="uv sync --extra bedrock"):
        load_sdk_module("boto3")
    with pytest.raises(BedrockSDKMissing):
        make_runtime_client(enabled_settings())
