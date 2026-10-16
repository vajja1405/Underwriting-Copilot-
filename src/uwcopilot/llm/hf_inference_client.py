"""Hugging Face Inference Providers client (the OpenAI-compatible chat completions router). DISABLED by default.

What it talks to: POST <base URL>/chat/completions with "Authorization: Bearer <HF_TOKEN>", the request and
answer in the OpenAI chat-completions shape (messages in; choices[0].message.content, finish_reason and usage out).
The base URL default, https://router.huggingface.co/v1, the /chat/completions path, the "Authorization: Bearer" header
and the HF_TOKEN name are taken from the Hugging Face documentation page
https://huggingface.co/docs/inference-providers/index, section "OpenAI-Compatible Chat Completions Endpoint (Chat
Only)" (anchor #alternative-openai-compatible-chat-completions-endpoint-chat-only), retrieved 2026-10-14T10:25Z
(HTML SHA-256 f6c2b8287c08e9b16cb8cf80dfff28f8cd48fcc7cd66fbed076cd45e136e7417; the page is not committed). Only the
documentation page was fetched. This repository has NEVER sent a request to the inference endpoint: the client is
tested only against httpx.MockTransport (tests/llm/test_hf_inference_client.py). The same page describes Inference
Providers as pay-as-you-go, and the owners decided on no extra spending, so nothing here may be described as having
run.

Why it is off unless three things are true (any one missing means no request can be sent):
1. UWC_HF_INFERENCE_ENABLED is "1", "true" or "yes" in the repo .env file or the environment. Otherwise
   build_hf_inference_client raises HFInferenceDisabled. The flag exists because the Space already holds an
   HF_TOKEN secret for deployment, and that token alone must never start paid inference.
2. HF_TOKEN is set (HFInferenceTokenMissing otherwise) and a model is chosen: UWC_HF_INFERENCE_MODEL or the model
   argument (HFInferenceModelMissing otherwise; there is no default model, so none is claimed or guessed).
3. The model has a verified price in config/llm_pricing.yaml. The factory wraps this client as
   Cached(Budgeted(client)); without a verified price the budget layer raises PriceNotVerified before the call, so
   the run's cost cap (MAX_RUN_COST_USD) is always enforced.
The provider is also not one of llm.settings.PROVIDERS, so UWC_LLM_PROVIDER cannot select it: a caller must ask for
"hf_inference" by name (factory.get_llm_client(provider="hf_inference")).

Settings are read with the project's env_reader (only the names below; a non-empty .env value wins over the process
environment). The base URL is checked with llm.settings.validate_base_url (https, or http to a loopback host only).
The token is never logged, cached or put in an error message; HFInferenceSettings excludes it from repr.

Answer handling, like the other clients: finish_reason "length" or "content_filter" raises IncompleteLLMResponse (the
partial text is never used); an answer without a usage block raises HFInferenceError, because its cost could not be
accounted for. There are no automatic retries.
"""

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from uwcopilot.common.paths import ENV_FILE
from uwcopilot.common.settings import env_reader
from uwcopilot.llm.base import LLMMessage, LLMResponse, raise_if_incomplete
from uwcopilot.llm.settings import validate_base_url

PROVIDER = "hf_inference"
HF_INFERENCE_PROVIDER = PROVIDER
DEFAULT_HF_BASE_URL = "https://router.huggingface.co/v1"
CHAT_PATH = "/chat/completions"
ENABLE_FLAG_NAME = "UWC_HF_INFERENCE_ENABLED"
TOKEN_NAME = "HF_TOKEN"
BASE_URL_NAME = "UWC_HF_INFERENCE_BASE_URL"
MODEL_NAME = "UWC_HF_INFERENCE_MODEL"
TRUE_VALUES: frozenset[str] = frozenset({"1", "true", "yes"})
DEFAULT_TIMEOUT_S = 120.0
MAX_ERROR_TEXT_CHARS = 300


class HFInferenceDisabled(RuntimeError):
    """The hf_inference provider was requested while UWC_HF_INFERENCE_ENABLED is not set. No request was made."""


class HFInferenceTokenMissing(RuntimeError):
    """The provider is enabled but HF_TOKEN is not set. No request was made."""


class HFInferenceModelMissing(RuntimeError):
    """No model was chosen (UWC_HF_INFERENCE_MODEL or the model argument). No request was made."""


class HFInferenceError(RuntimeError):
    """The router could not be reached, answered with an error, or returned an unusable body."""


@dataclass(frozen=True)
class HFInferenceSettings:
    """What the hf_inference client needs. The token is excluded from repr so it never ends up in logs."""

    enabled: bool = False
    token: str | None = field(default=None, repr=False)
    base_url: str = DEFAULT_HF_BASE_URL
    model: str | None = None


def is_enabled(flag: str | None) -> bool:
    """True only for an explicit "1", "true" or "yes" (case-insensitive); anything else keeps the provider off."""
    return flag is not None and flag.strip().lower() in TRUE_VALUES


def load_hf_inference_settings(
    env_file: Path | None = ENV_FILE, environ: Mapping[str, str] | None = None
) -> HFInferenceSettings:
    """Read UWC_HF_INFERENCE_ENABLED, HF_TOKEN, UWC_HF_INFERENCE_BASE_URL and UWC_HF_INFERENCE_MODEL.

    Pass env_file=None to ignore the .env file (tests do this). The base URL is validated.
    """
    read = env_reader(env_file, environ)
    return HFInferenceSettings(
        enabled=is_enabled(read(ENABLE_FLAG_NAME)),
        token=read(TOKEN_NAME),
        base_url=validate_base_url(read(BASE_URL_NAME) or DEFAULT_HF_BASE_URL),
        model=read(MODEL_NAME),
    )


def build_chat_body(
    model: str, system: str, messages: list[LLMMessage], max_tokens: int, temperature: float
) -> dict[str, Any]:
    """The JSON body of one chat-completions request. An empty system prompt is not sent."""
    chat_messages: list[dict[str, str]] = []
    if system:
        chat_messages.append({"role": "system", "content": system})
    for message in messages:
        chat_messages.append({"role": message.role, "content": message.content})
    return {
        "model": model,
        "messages": chat_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }


def error_text(reply: httpx.Response) -> str:
    """A short description of an error reply: its "error" field when it has one, else the start of the body."""
    try:
        payload = reply.json()
    except ValueError:
        return reply.text[:MAX_ERROR_TEXT_CHARS]
    if isinstance(payload, dict) and "error" in payload:
        return str(payload["error"])[:MAX_ERROR_TEXT_CHARS]
    return reply.text[:MAX_ERROR_TEXT_CHARS]


def first_choice(data: dict[str, Any]) -> tuple[str, str | None]:
    """(message content, finish_reason) of the first choice, or HFInferenceError when the body has none."""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise HFInferenceError("the chat-completions answer has no choices; it cannot be used")
    message = choices[0].get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str):
        raise HFInferenceError("the chat-completions answer has no message content; it cannot be used")
    finish = choices[0].get("finish_reason")
    return message["content"], finish if isinstance(finish, str) else None


def token_counts(data: dict[str, Any]) -> tuple[int, int]:
    """(prompt_tokens, completion_tokens) from the usage block, or HFInferenceError when it is missing."""
    usage = data.get("usage")
    if not isinstance(usage, dict):
        raise HFInferenceError("the answer has no usage block; its cost cannot be accounted for, so it is not used")
    prompt_tokens, completion_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if not isinstance(prompt_tokens, int) or not isinstance(completion_tokens, int):
        raise HFInferenceError("the usage block has no integer token counts; the cost cannot be accounted for")
    return prompt_tokens, completion_tokens


class HFInferenceClient:
    """LLMClient for the Hugging Face Inference Providers router. Build it with build_hf_inference_client."""

    provider = PROVIDER

    def __init__(
        self,
        token: str,
        model: str,
        base_url: str = DEFAULT_HF_BASE_URL,
        http_client: httpx.Client | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        """Keep the token and settings. No request is sent here. http_client is for tests (httpx.MockTransport)."""
        if not token:
            raise HFInferenceTokenMissing(f"{TOKEN_NAME} is not set; no request was made.")
        if not model:
            raise HFInferenceModelMissing(f"choose a model ({MODEL_NAME} or the model argument); none is assumed.")
        self.model = model
        self.base_url = validate_base_url(base_url)
        self._token = token
        self._http = http_client or httpx.Client(timeout=timeout_s)

    @property
    def request_fingerprint(self) -> dict[str, str]:
        """Settings that change the answer without being part of the prompt (part of the LLM cache key)."""
        return {"base_url": self.base_url}

    def complete(
        self,
        system: str,
        messages: list[LLMMessage],
        max_tokens: int,
        temperature: float = 0,
    ) -> LLMResponse:
        """Send one chat-completions request and return the answer (IncompleteLLMResponse when it was cut off)."""
        body = build_chat_body(self.model, system, messages, max_tokens, temperature)
        headers = {"Authorization": f"Bearer {self._token}"}
        started = time.perf_counter()
        try:
            reply = self._http.post(f"{self.base_url}{CHAT_PATH}", json=body, headers=headers)
        except httpx.TransportError as error:
            raise HFInferenceError(f"could not reach {self.base_url} ({type(error).__name__})") from error
        latency = time.perf_counter() - started
        if reply.status_code != 200:
            raise HFInferenceError(f"POST {CHAT_PATH} returned HTTP {reply.status_code}: {error_text(reply)}")
        try:
            data = reply.json()
        except ValueError as error:
            raise HFInferenceError("the chat-completions answer is not JSON; it cannot be used") from error
        if not isinstance(data, dict):
            raise HFInferenceError("the chat-completions answer is not a JSON object; it cannot be used")
        text, finish_reason = first_choice(data)
        input_tokens, output_tokens = token_counts(data)
        response = LLMResponse(
            text=text,
            model=str(data.get("model") or self.model),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_s=latency,
            cost_usd=None,
            cached=False,
            provider=PROVIDER,
            stop_reason=finish_reason,
            metadata={"base_url": self.base_url, "response_id": str(data.get("id") or "")},
        )
        return raise_if_incomplete(response)

    def close(self) -> None:
        """Close the HTTP connection pool."""
        self._http.close()


def build_hf_inference_client(
    settings: HFInferenceSettings, model: str | None = None, http_client: httpx.Client | None = None
) -> HFInferenceClient:
    """The client, or an error naming the missing opt-in (see the module docstring). Sends no request."""
    if not settings.enabled:
        raise HFInferenceDisabled(
            f"the hf_inference provider is disabled: set {ENABLE_FLAG_NAME}=1 to opt in (it can cost money). "
            "No request was made."
        )
    if not settings.token:
        raise HFInferenceTokenMissing(f"{TOKEN_NAME} is not set; no request was made.")
    chosen = model or settings.model
    if not chosen:
        raise HFInferenceModelMissing(f"choose a model ({MODEL_NAME} or the model argument); none is assumed.")
    return HFInferenceClient(settings.token, chosen, settings.base_url, http_client=http_client)
