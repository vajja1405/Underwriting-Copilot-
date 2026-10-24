"""AWS Bedrock client using boto3's bedrock-runtime Converse API. DISABLED by default and never run in this repo.

What it talks to: one bedrock-runtime Converse request per call (boto3 `client.converse(...)`). The system prompt is
sent as system=[{"text": ...}] (an empty one is not sent), each message as {"role", "content": [{"text": ...}]}, and
max_tokens as inferenceConfig.maxTokens. temperature is sent as inferenceConfig.temperature only when
send_temperature is on (UWC_BEDROCK_SEND_TEMPERATURE=1), because some models reject it (the Anthropic client omits it
for current Claude models for the same reason). From the answer, the text blocks of output.message.content are
joined (reasoning blocks are dropped, like thinking blocks in anthropic_client.py), usage.inputTokens and
usage.outputTokens become the token counts, and stopReason becomes the stop reason (table below). Converse does not
echo the model id, so the response's model is the id that was requested.
The request and answer shapes are those of the bedrock-runtime service model shipped with botocore 1.43.118 (API
version 2023-09-30). The tests check every request against that model with botocore's Stubber. This repository has
NEVER sent a request to Bedrock: no AWS credentials were configured where it was written, and the client is tested
only with a stubbed bedrock-runtime client (tests/llm/test_bedrock_client.py). See docs/llm_bedrock.md.

Why it is off unless every one of these is true (any one missing means no request can be sent):
1. UWC_BEDROCK_ENABLED is "1", "true" or "yes" (BedrockDisabled otherwise). Bedrock is pay-per-token.
2. UWC_BEDROCK_REGION is set (BedrockRegionMissing). AWS_REGION and AWS_DEFAULT_REGION are never used: the region
   decides where prompts are processed, so it is chosen on purpose.
3. A model id is chosen: UWC_BEDROCK_MODEL or the model argument (BedrockModelMissing). There is no default model.
4. AWS credentials resolve (BedrockCredentialsMissing): the named profile UWC_BEDROCK_PROFILE, or boto3's default
   credential chain when no profile is named. They are checked when the client is built, before any request.
5. The model id has a verified price in config/llm_pricing.yaml. The factory wraps this client as
   Cached(Budgeted(client)); without a verified price the budget layer raises PriceNotVerified before the call.
   No Bedrock price is in the price list yet, so today every Bedrock call is refused before any request.
The provider is also not one of llm.settings.PROVIDERS, so UWC_LLM_PROVIDER cannot select it: a caller must ask for
"bedrock" by name (factory.get_llm_client(provider="bedrock")).

SDK variables that botocore applies on its own are refused, as the other clients do (check_no_implicit_sdk_env):
AWS_ENDPOINT_URL and AWS_ENDPOINT_URL_BEDROCK_RUNTIME would send prompts to another endpoint, and
AWS_BEARER_TOKEN_BEDROCK makes botocore switch to that Bedrock API key instead of the chosen credentials. Endpoint
URLs in the shared AWS config file are ignored (ignore_configured_endpoint_urls=True). Credentials are never logged,
cached or put in an error message.

Stop reasons: the shared layers (budget, cache) treat a response as unfinished when its stop_reason is one of
base.INCOMPLETE_STOP_REASONS, so Bedrock's reasons are mapped onto those names. The original reason is kept in
metadata["bedrock_stop_reason"].
    end_turn, stop_sequence, tool_use              -> unchanged (finished)
    max_tokens, model_context_window_exceeded      -> "max_tokens" (cut off)
    content_filtered, guardrail_intervened         -> "content_filter" (withheld)
    malformed_model_output, malformed_tool_use,
    and any reason this client does not know       -> "refusal" (no usable answer; unknown reasons fail closed)
An unfinished answer raises IncompleteLLMResponse (its cost is still recorded by the budget layer).

Retries, matching the anthropic and openai clients (max_retries=2): botocore's "standard" retry mode with 3 attempts
in total, which backs off on throttling and server errors. When those are used up, a ThrottlingException raises
BedrockThrottled, an AccessDeniedException raises BedrockAccessDenied (IAM permission or model access) and any other
service error raises BedrockError with its error code.

Structured output: BedrockOptions.output_schema (a JSON schema) is sent as one tool whose inputSchema is that schema,
with toolChoice forcing the model to call it. The tool call's input is returned as JSON text, so callers parse
response.text exactly as they do with Ollama's `format` schema. The schema's SHA-256 is part of the cache key.

boto3 is an optional dependency (`uv sync --extra bedrock`). It is imported only when a client is built, so the rest
of the LLM layer works without it; BedrockSDKMissing names the install command when it is absent.
"""

import importlib
import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from uwcopilot.common.paths import ENV_FILE
from uwcopilot.common.settings import env_reader
from uwcopilot.llm.base import INCOMPLETE_STOP_REASONS, LLMMessage, LLMResponse, raise_if_incomplete
from uwcopilot.llm.hf_inference_client import is_enabled
from uwcopilot.llm.ollama_client import canonical_json_sha256
from uwcopilot.llm.settings import SettingsError, check_no_implicit_sdk_env

PROVIDER = "bedrock"
BEDROCK_PROVIDER = PROVIDER
SERVICE_NAME = "bedrock-runtime"
ENABLE_FLAG_NAME = "UWC_BEDROCK_ENABLED"
REGION_NAME = "UWC_BEDROCK_REGION"
PROFILE_NAME = "UWC_BEDROCK_PROFILE"
MODEL_NAME = "UWC_BEDROCK_MODEL"
SEND_TEMPERATURE_NAME = "UWC_BEDROCK_SEND_TEMPERATURE"
# Read by botocore even when the region and credentials are passed explicitly (see the module docstring).
IMPLICIT_SDK_ENV_VARS: tuple[str, ...] = (
    "AWS_ENDPOINT_URL",
    "AWS_ENDPOINT_URL_BEDROCK_RUNTIME",
    "AWS_BEARER_TOKEN_BEDROCK",
)
# AWS region names such as us-east-1, eu-central-2 or us-gov-west-1. The region becomes part of the endpoint host.
REGION_PATTERN = re.compile(r"[a-z]{2}(-[a-z]+)+-\d+")
DEFAULT_TIMEOUT_S = 120.0
DEFAULT_MAX_RETRIES = 2
MAX_ERROR_TEXT_CHARS = 300
# Converse accepts inferenceConfig.temperature from 0 to 1.
MAX_TEMPERATURE = 1.0
STRUCTURED_OUTPUT_TOOL = "structured_output"
# Bedrock stopReason -> the shared stop reason the budget and cache layers understand (see the module docstring).
SHARED_STOP_REASONS: dict[str, str] = {
    "end_turn": "end_turn",
    "stop_sequence": "stop_sequence",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "model_context_window_exceeded": "max_tokens",
    "content_filtered": "content_filter",
    "guardrail_intervened": "content_filter",
    "malformed_model_output": "refusal",
    "malformed_tool_use": "refusal",
}
UNKNOWN_STOP_REASON = "refusal"


class BedrockSDKMissing(RuntimeError):
    """boto3 is not installed (it is the optional "bedrock" extra). No request was made."""


class BedrockDisabled(RuntimeError):
    """The bedrock provider was requested while UWC_BEDROCK_ENABLED is not set. No request was made."""


class BedrockRegionMissing(RuntimeError):
    """The provider is enabled but UWC_BEDROCK_REGION is not set. No request was made."""


class BedrockModelMissing(RuntimeError):
    """No model id was chosen (UWC_BEDROCK_MODEL or the model argument). No request was made."""


class BedrockCredentialsMissing(RuntimeError):
    """No AWS credentials could be resolved for the chosen profile or the default chain. No request was made."""


class BedrockError(RuntimeError):
    """Bedrock could not be reached, answered with an error, or returned an unusable answer."""


class BedrockThrottled(BedrockError):
    """Bedrock kept answering ThrottlingException after the retries were used up."""


class BedrockAccessDenied(BedrockError):
    """Bedrock answered AccessDeniedException: an IAM permission or access to the model is missing."""


@dataclass(frozen=True)
class BedrockSettings:
    """What the bedrock client needs. There is no secret here: credentials stay in the AWS credential chain."""

    enabled: bool = False
    region: str | None = None
    profile: str | None = None
    model: str | None = None
    send_temperature: bool = False


@dataclass(frozen=True)
class BedrockOptions:
    """Settings that change the answer but are not arguments of complete().

    output_schema is an optional JSON schema; when it is set the answer is the forced tool call's input, as JSON text.
    """

    output_schema: dict[str, Any] | None = None


def validate_region(region: str) -> str:
    """Return the region if it looks like an AWS region name (us-east-1), else raise SettingsError."""
    if not REGION_PATTERN.fullmatch(region):
        raise SettingsError(f"{REGION_NAME} must be an AWS region name such as us-east-1 (got {region!r}).")
    return region


def load_bedrock_settings(
    env_file: Path | None = ENV_FILE, environ: Mapping[str, str] | None = None
) -> BedrockSettings:
    """Read the five UWC_BEDROCK_* names: ENABLED, REGION, PROFILE, MODEL and SEND_TEMPERATURE.

    Pass env_file=None to ignore the .env file (tests do this). The region is validated when it is set.
    """
    read = env_reader(env_file, environ)
    region = read(REGION_NAME)
    return BedrockSettings(
        enabled=is_enabled(read(ENABLE_FLAG_NAME)),
        region=validate_region(region) if region else None,
        profile=read(PROFILE_NAME),
        model=read(MODEL_NAME),
        send_temperature=is_enabled(read(SEND_TEMPERATURE_NAME)),
    )


def load_sdk_module(name: str) -> ModuleType:
    """Import boto3 or a botocore module only when it is needed, so the "bedrock" extra stays optional."""
    try:
        return importlib.import_module(name)
    except ImportError as error:
        raise BedrockSDKMissing(
            f"{name} is not installed. Install the optional extra with `uv sync --extra bedrock`. No request was made."
        ) from error


def build_converse_request(
    model: str,
    system: str,
    messages: list[LLMMessage],
    max_tokens: int,
    temperature: float,
    send_temperature: bool,
    output_schema: dict[str, Any] | None,
) -> dict[str, Any]:
    """The keyword arguments of one Converse call. An empty system prompt is not sent."""
    inference: dict[str, Any] = {"maxTokens": max_tokens}
    if send_temperature:
        if not 0 <= temperature <= MAX_TEMPERATURE:
            raise ValueError(f"Bedrock Converse accepts a temperature from 0 to 1 (got {temperature}).")
        inference["temperature"] = temperature
    request: dict[str, Any] = {
        "modelId": model,
        "messages": [{"role": item.role, "content": [{"text": item.content}]} for item in messages],
        "inferenceConfig": inference,
    }
    if system:
        request["system"] = [{"text": system}]
    if output_schema is not None:
        tool_spec = {
            "name": STRUCTURED_OUTPUT_TOOL,
            "description": "Return the answer as arguments that match this JSON schema.",
            "inputSchema": {"json": output_schema},
        }
        request["toolConfig"] = {
            "tools": [{"toolSpec": tool_spec}],
            "toolChoice": {"tool": {"name": STRUCTURED_OUTPUT_TOOL}},
        }
    return request


def shared_stop_reason(bedrock_reason: str | None) -> str:
    """The shared stop reason for a Bedrock stopReason; an unknown or missing reason counts as no usable answer."""
    if bedrock_reason is None:
        return UNKNOWN_STOP_REASON
    return SHARED_STOP_REASONS.get(bedrock_reason, UNKNOWN_STOP_REASON)


def content_blocks(reply: dict[str, Any]) -> list[dict[str, Any]]:
    """The content blocks of output.message, or BedrockError when the answer has none."""
    output = reply.get("output")
    message = output.get("message") if isinstance(output, dict) else None
    if not isinstance(message, dict) or not isinstance(message.get("content"), list):
        raise BedrockError("the Converse answer has no output message; it cannot be used")
    return [block for block in message["content"] if isinstance(block, dict)]


def joined_text(blocks: list[dict[str, Any]]) -> str:
    """The text blocks joined in order. Reasoning, tool and other blocks are not part of the answer text."""
    return "".join(block["text"] for block in blocks if isinstance(block.get("text"), str))


def structured_text(blocks: list[dict[str, Any]]) -> str:
    """The forced tool call's input as JSON text, or BedrockError when the model did not call the tool."""
    for block in blocks:
        tool_use = block.get("toolUse")
        if isinstance(tool_use, dict) and tool_use.get("name") == STRUCTURED_OUTPUT_TOOL:
            return json.dumps(tool_use.get("input"), ensure_ascii=False)
    raise BedrockError(f"structured output was requested but the answer has no {STRUCTURED_OUTPUT_TOOL!r} tool call")


def token_counts(reply: dict[str, Any]) -> tuple[int, int]:
    """(inputTokens, outputTokens) from the usage block, or BedrockError when it is missing."""
    usage = reply.get("usage")
    if not isinstance(usage, dict):
        raise BedrockError("the answer has no usage block; its cost cannot be accounted for, so it is not used")
    input_tokens, output_tokens = usage.get("inputTokens"), usage.get("outputTokens")
    if not isinstance(input_tokens, int) or not isinstance(output_tokens, int):
        raise BedrockError("the usage block has no integer token counts; the cost cannot be accounted for")
    return input_tokens, output_tokens


def service_error(error: Any) -> BedrockError:
    """The BedrockError for a botocore ClientError: its error code and the start of the service's message."""
    details = getattr(error, "response", {}).get("Error", {})
    code = str(details.get("Code") or "unknown")
    message = str(details.get("Message") or "")[:MAX_ERROR_TEXT_CHARS]
    if code == "ThrottlingException":
        return BedrockThrottled(
            f"Bedrock is throttling requests ({code}) after {DEFAULT_MAX_RETRIES} retries: {message}. "
            "Wait and rerun, or ask AWS for a higher quota for this model and region."
        )
    if code == "AccessDeniedException":
        return BedrockAccessDenied(
            f"Bedrock denied the request ({code}): {message}. Check that the IAM identity may call "
            "bedrock:InvokeModel on this model and that the account has access to the model in this region."
        )
    return BedrockError(f"Bedrock Converse returned {code}: {message}")


def make_runtime_client(settings: BedrockSettings) -> Any:
    """A boto3 bedrock-runtime client for the settings' region and profile. Sends no request to Bedrock.

    Raises ImplicitSDKConfigError while an implicit SDK variable is set, BedrockSDKMissing without boto3, and
    BedrockCredentialsMissing when the profile does not exist or no credentials resolve. Resolving credentials can
    contact the AWS credential sources the chain uses (for example an SSO cache or instance metadata); it never
    contacts Bedrock.
    """
    check_no_implicit_sdk_env(IMPLICIT_SDK_ENV_VARS)
    boto3 = load_sdk_module("boto3")
    botocore_config = load_sdk_module("botocore.config")
    botocore_exceptions = load_sdk_module("botocore.exceptions")
    try:
        session = boto3.Session(profile_name=settings.profile, region_name=settings.region)
    except botocore_exceptions.ProfileNotFound as error:
        raise BedrockCredentialsMissing(
            f"AWS profile {settings.profile!r} ({PROFILE_NAME}) is not configured; no request was made."
        ) from error
    if session.get_credentials() is None:
        raise BedrockCredentialsMissing(
            f"No AWS credentials were found (name a profile with {PROFILE_NAME}, or set up the default AWS "
            "credential chain, for example with `aws sso login`); no request was made."
        )
    config = botocore_config.Config(
        read_timeout=DEFAULT_TIMEOUT_S,
        retries={"mode": "standard", "total_max_attempts": DEFAULT_MAX_RETRIES + 1},
        ignore_configured_endpoint_urls=True,
    )
    return session.client(SERVICE_NAME, region_name=settings.region, config=config)


class BedrockClient:
    """LLMClient for models on AWS Bedrock (Converse API). Build it with build_bedrock_client."""

    provider = PROVIDER

    def __init__(
        self,
        runtime_client: Any,
        model: str,
        region: str,
        options: BedrockOptions | None = None,
        send_temperature: bool = False,
    ) -> None:
        """Keep the boto3 bedrock-runtime client and the settings. No request is sent here.

        runtime_client is a boto3 bedrock-runtime client (tests pass one wrapped in botocore's Stubber, or a fake
        object with a converse method).
        """
        if not model:
            raise BedrockModelMissing(f"choose a model id ({MODEL_NAME} or the model argument); none is assumed.")
        self.model = model
        self.region = validate_region(region)
        self.options = options or BedrockOptions()
        self.send_temperature = send_temperature
        self._runtime = runtime_client

    @property
    def endpoint_url(self) -> str:
        """The endpoint the boto3 client sends requests to ('' for a test object without one)."""
        return str(getattr(getattr(self._runtime, "meta", None), "endpoint_url", "") or "")

    @property
    def output_schema_sha256(self) -> str:
        """SHA-256 of the structured-output JSON schema, or '' when none is used."""
        if self.options.output_schema is None:
            return ""
        return canonical_json_sha256(self.options.output_schema)

    @property
    def request_fingerprint(self) -> dict[str, str]:
        """Settings that change the answer without being part of the prompt (part of the LLM cache key).

        The profile is left out: it chooses credentials, not the answer.
        """
        return {
            "region": self.region,
            "endpoint_url": self.endpoint_url,
            "send_temperature": str(self.send_temperature),
            "output_schema_sha256": self.output_schema_sha256,
        }

    def _converse(self, request: dict[str, Any]) -> dict[str, Any]:
        """Send one Converse request, turning botocore errors into the errors of this module."""
        botocore_exceptions = load_sdk_module("botocore.exceptions")
        try:
            reply = self._runtime.converse(**request)
        except botocore_exceptions.ClientError as error:
            raise service_error(error) from error
        except (botocore_exceptions.NoCredentialsError, botocore_exceptions.PartialCredentialsError) as error:
            raise BedrockCredentialsMissing(f"AWS credentials could not be used ({type(error).__name__}).") from error
        except botocore_exceptions.BotoCoreError as error:
            raise BedrockError(
                f"the Converse request failed before an answer arrived ({type(error).__name__}: "
                f"{str(error)[:MAX_ERROR_TEXT_CHARS]})"
            ) from error
        if not isinstance(reply, dict):
            raise BedrockError("the Converse answer is not a mapping; it cannot be used")
        return reply

    def complete(
        self,
        system: str,
        messages: list[LLMMessage],
        max_tokens: int,
        temperature: float = 0,
    ) -> LLMResponse:
        """Send one Converse request and return the answer (IncompleteLLMResponse when it is not finished)."""
        request = build_converse_request(
            self.model, system, messages, max_tokens, temperature, self.send_temperature, self.options.output_schema
        )
        started = time.perf_counter()
        reply = self._converse(request)
        latency = time.perf_counter() - started
        input_tokens, output_tokens = token_counts(reply)
        bedrock_reason = reply.get("stopReason")
        stop_reason = shared_stop_reason(bedrock_reason)
        blocks = content_blocks(reply)
        text = joined_text(blocks)
        if self.options.output_schema is not None and stop_reason not in INCOMPLETE_STOP_REASONS:
            text = structured_text(blocks)
        response = LLMResponse(
            text=text,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_s=latency,
            cost_usd=None,
            cached=False,
            provider=PROVIDER,
            stop_reason=stop_reason,
            metadata={
                "region": self.region,
                "endpoint_url": self.endpoint_url,
                "bedrock_stop_reason": bedrock_reason,
                "request_id": str((reply.get("ResponseMetadata") or {}).get("RequestId") or ""),
                "bedrock_latency_ms": (reply.get("metrics") or {}).get("latencyMs"),
                "output_schema_sha256": self.output_schema_sha256,
            },
        )
        return raise_if_incomplete(response)


def build_bedrock_client(
    settings: BedrockSettings,
    model: str | None = None,
    options: BedrockOptions | None = None,
    runtime_client: Any | None = None,
) -> BedrockClient:
    """The client, or an error naming the missing opt-in (see the module docstring). Sends no request to Bedrock.

    runtime_client is a test hook (a stubbed boto3 bedrock-runtime client); without it one is built from the settings.
    """
    if not settings.enabled:
        raise BedrockDisabled(
            f"the bedrock provider is disabled: set {ENABLE_FLAG_NAME}=1 to opt in (it can cost money). "
            "No request was made."
        )
    if not settings.region:
        raise BedrockRegionMissing(f"{REGION_NAME} is not set (for example us-east-1); no request was made.")
    chosen = model or settings.model
    if not chosen:
        raise BedrockModelMissing(f"choose a model id ({MODEL_NAME} or the model argument); none is assumed.")
    runtime = runtime_client if runtime_client is not None else make_runtime_client(settings)
    return BedrockClient(runtime, chosen, settings.region, options=options, send_temperature=settings.send_temperature)
