"""Tests for uwcopilot.llm.ollama_client against a pretend server (httpx.MockTransport), plus two opt-in live tests.

The live tests (a one-word smoke call and a JSON-schema `format` call) use the real local Ollama server. They are
marked `integration` (excluded by default) and are also skipped unless UWC_OLLAMA_LIVE=1 was set when pytest started:

    UWC_OLLAMA_LIVE=1 uv run pytest -m integration tests/llm/test_ollama_client.py

The variable is read at import time, because the autouse conftest fixture clears every UWC_* variable before
each test runs.
"""

import json
import math
import os
from pathlib import Path

import httpx
import pytest
from ollama_test_helpers import OTHER_DIGEST, TEST_DIGEST, TEST_MODEL, FakeOllamaServer, chat_json, tags_json

from uwcopilot.common.paths import CONFIG_DIR
from uwcopilot.llm.base import IncompleteLLMResponse, LLMClient, LLMMessage
from uwcopilot.llm.cache import CachedLLMClient
from uwcopilot.llm.factory import get_llm_client
from uwcopilot.llm.ollama_client import (
    ContextBudgetExceeded,
    ContextRejectedByServer,
    OllamaClient,
    OllamaDigestMismatch,
    OllamaError,
    OllamaModelNotFound,
    OllamaOptions,
    estimate_prompt_tokens,
)
from uwcopilot.llm.ollama_queue import OllamaQueueBusy, ollama_queue_lock
from uwcopilot.llm.settings import DEFAULT_OLLAMA_BASE_URL, LLMSettings, SettingsError

LIVE = os.environ.get("UWC_OLLAMA_LIVE") == "1"
LIVE_BASE_URL = os.environ.get("UWC_OLLAMA_BASE_URL") or DEFAULT_OLLAMA_BASE_URL
LIVE_MODEL = os.environ.get("UWC_OLLAMA_LIVE_MODEL") or "qwen2.5-3b-instruct-q4km"
BASE_URL = "http://127.0.0.1:11434"
USER = [LLMMessage(role="user", content="What is the minimum?")]


def make_client(server: FakeOllamaServer, options: OllamaOptions | None = None) -> OllamaClient:
    """An OllamaClient talking to the pretend server."""
    return OllamaClient(BASE_URL, TEST_MODEL, options=options, http_client=server.client())


def test_request_body_has_model_messages_and_all_options() -> None:
    server = FakeOllamaServer()
    client = make_client(server)
    client.complete("system prompt", USER, max_tokens=50, temperature=0)
    assert server.chat_bodies() == [
        {
            "model": TEST_MODEL,
            "messages": [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "What is the minimum?"},
            ],
            "stream": False,
            "options": {"temperature": 0, "seed": 42, "num_ctx": 4096, "num_predict": 50},
        }
    ]
    assert isinstance(client, LLMClient)


def test_startup_reads_version_and_digest_then_posts_to_native_chat() -> None:
    server = FakeOllamaServer()
    client = make_client(server)
    client.complete("sys", USER, max_tokens=10)
    paths = [(request.method, request.url.path) for request in server.requests]
    assert paths == [("GET", "/api/version"), ("GET", "/api/tags"), ("POST", "/api/chat")]
    assert client.digest == TEST_DIGEST
    assert client.runtime_version == "0.40.3"
    assert client.model_details["quantization_level"] == "Q4_K_M"


def test_optional_json_schema_format_is_sent() -> None:
    schema = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
    server = FakeOllamaServer()
    client = make_client(server, OllamaOptions(format_schema=schema))
    client.complete("sys", USER, max_tokens=10)
    assert server.chat_bodies()[0]["format"] == schema
    assert len(client.request_fingerprint["format_sha256"]) == 64


def test_empty_system_prompt_is_not_sent() -> None:
    server = FakeOllamaServer()
    make_client(server).complete("", USER, max_tokens=10)
    assert [message["role"] for message in server.chat_bodies()[0]["messages"]] == ["user"]


def test_response_mapping_text_tokens_latency_stop_reason_and_metadata() -> None:
    server = FakeOllamaServer(chat=chat_json("Answer [NBC-1.1].", prompt_eval_count=20, eval_count=5))
    response = make_client(server).complete("sys", USER, max_tokens=10, temperature=0)
    assert response.text == "Answer [NBC-1.1]."
    assert (response.input_tokens, response.output_tokens) == (20, 5)
    assert response.latency_s >= 0
    assert response.provider == "ollama"
    assert response.cost_usd == 0.0
    assert response.stop_reason == "stop"
    assert not response.is_fake
    metadata = response.metadata
    assert metadata["digest"] == TEST_DIGEST
    assert metadata["ollama_version"] == "0.40.3"
    assert metadata["total_duration_ns"] == 7_000_000_000
    assert metadata["load_duration_ns"] == 6_000_000_000
    assert metadata["prompt_eval_duration_ns"] == 500_000_000
    assert metadata["eval_duration_ns"] == 100_000_000
    assert metadata["prompt_eval_count"] == 20
    assert metadata["eval_count"] == 5
    assert metadata["options"] == {"temperature": 0, "seed": 42, "num_ctx": 4096, "num_predict": 10}
    assert metadata["possible_truncation"] is False


def test_missing_counts_are_recorded_as_reported() -> None:
    server = FakeOllamaServer(chat=chat_json(prompt_eval_count=None, eval_count=3))
    response = make_client(server).complete("sys", USER, max_tokens=10)
    assert response.input_tokens == 0
    assert response.metadata["prompt_eval_count"] is None


def test_length_stop_raises_incomplete_with_the_response_kept() -> None:
    server = FakeOllamaServer(chat=chat_json("Sure, here are", done_reason="length"))
    with pytest.raises(IncompleteLLMResponse) as caught:
        make_client(server).complete("sys", USER, max_tokens=4)
    assert caught.value.response.stop_reason == "length"
    assert caught.value.response.text == "Sure, here are"


def test_non_200_reply_raises_ollama_error_with_server_message() -> None:
    server = FakeOllamaServer(chat={"error": "model runner crashed"}, chat_status=500)
    with pytest.raises(OllamaError, match="HTTP 500: model runner crashed"):
        make_client(server).complete("sys", USER, max_tokens=10)


def test_unreachable_server_raises_ollama_error() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    http_client = httpx.Client(transport=httpx.MockTransport(refuse))
    with pytest.raises(OllamaError, match="Could not reach Ollama"):
        OllamaClient(BASE_URL, TEST_MODEL, http_client=http_client)


def test_unknown_model_is_refused_at_startup() -> None:
    server = FakeOllamaServer(tags=tags_json(model="some-other-model"))
    with pytest.raises(OllamaModelNotFound, match="fetch_local_model"):
        make_client(server)


def test_expected_digest_is_enforced() -> None:
    with pytest.raises(OllamaDigestMismatch):
        make_client(FakeOllamaServer(), OllamaOptions(expected_digest=OTHER_DIGEST))
    client = make_client(FakeOllamaServer(), OllamaOptions(expected_digest=f"sha256:{TEST_DIGEST}"))
    assert client.digest == TEST_DIGEST


def test_context_guard_uses_ceil_of_half_the_characters() -> None:
    system = "x" * 1001
    messages = [LLMMessage(role="user", content="y" * 1000)]
    est_upper = math.ceil(2001 / 2)
    assert estimate_prompt_tokens(system, messages) == est_upper == 1001

    server = FakeOllamaServer()
    client = make_client(server, OllamaOptions(num_ctx=1100))
    with pytest.raises(ContextBudgetExceeded) as caught:
        client.complete(system, messages, max_tokens=100)
    assert (caught.value.est_upper_tokens, caught.value.num_predict, caught.value.num_ctx) == (1001, 100, 1100)
    assert server.chat_bodies() == []

    response = client.complete(system, messages, max_tokens=99)
    assert response.metadata["est_upper_prompt_tokens"] == 1001
    assert len(server.chat_bodies()) == 1


# The body Ollama 0.40.3 sent on 2026-10-12 for a 700-digit prompt with num_ctx 512 (an error object as JSON text).
OVERFLOW_ERROR_TEXT = json.dumps(
    {
        "error": {
            "code": 400,
            "message": "request (729 tokens) exceeds the available context size (512 tokens), try increasing it",
            "type": "exceed_context_size_error",
            "n_prompt_tokens": 729,
            "n_ctx": 512,
        }
    }
)


def test_server_context_rejection_is_a_context_error_with_the_server_count() -> None:
    server = FakeOllamaServer(chat={"error": OVERFLOW_ERROR_TEXT}, chat_status=400)
    client = make_client(server, OllamaOptions(num_ctx=512))
    digits = [LLMMessage(role="user", content="0123456789" * 66)]
    with pytest.raises(ContextRejectedByServer) as caught:
        client.complete("", digits, max_tokens=8)
    error = caught.value
    assert isinstance(error, ContextBudgetExceeded)
    assert not isinstance(error, OllamaError)
    assert (error.n_prompt_tokens, error.num_ctx, error.num_predict, error.est_upper_tokens) == (729, 512, 8, 330)
    assert "729 prompt tokens" in str(error)


def test_other_http_400_replies_stay_ollama_errors() -> None:
    server = FakeOllamaServer(chat={"error": "invalid options"}, chat_status=400)
    with pytest.raises(OllamaError, match="HTTP 400: invalid options"):
        make_client(server).complete("sys", USER, max_tokens=10)


def test_non_json_reply_at_startup_is_an_ollama_error() -> None:
    def plain_text(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="Ollama is running")

    http_client = httpx.Client(transport=httpx.MockTransport(plain_text))
    with pytest.raises(OllamaError, match="did not return JSON"):
        OllamaClient(BASE_URL, TEST_MODEL, http_client=http_client)


def test_possible_truncation_flag_when_tokens_reach_num_ctx() -> None:
    at_limit = FakeOllamaServer(chat=chat_json(prompt_eval_count=4000, eval_count=96))
    assert make_client(at_limit).complete("s", USER, max_tokens=10).metadata["possible_truncation"] is True
    below = FakeOllamaServer(chat=chat_json(prompt_eval_count=4000, eval_count=95))
    assert make_client(below).complete("s", USER, max_tokens=10).metadata["possible_truncation"] is False


def test_base_url_must_be_https_unless_loopback() -> None:
    with pytest.raises(SettingsError):
        OllamaClient("http://10.0.0.5:11434", TEST_MODEL, http_client=FakeOllamaServer().client())
    client = OllamaClient("https://gpu-box.example:11434/", TEST_MODEL, http_client=FakeOllamaServer().client())
    assert client.base_url == "https://gpu-box.example:11434"


def test_fingerprint_holds_digest_and_options() -> None:
    client = make_client(FakeOllamaServer(), OllamaOptions(num_ctx=2048, seed=7))
    assert client.request_fingerprint == {
        "base_url": BASE_URL,
        "ollama_version": "0.40.3",
        "digest": TEST_DIGEST,
        "num_ctx": "2048",
        "seed": "7",
        "format_sha256": "",
    }


def test_model_record_matches_the_provenance_shape() -> None:
    record = make_client(FakeOllamaServer()).model_record("Apache-2.0")
    assert record == {
        "provider": "ollama",
        "model": TEST_MODEL,
        "digest": TEST_DIGEST,
        "license": "Apache-2.0",
        "runtime_version": "ollama 0.40.3",
    }


def ollama_settings(tmp_path: Path) -> LLMSettings:
    """Settings for the ollama provider with a temporary cache and the repo price list."""
    return LLMSettings(
        provider="ollama", cache_dir=tmp_path / "llm_cache", pricing_file=CONFIG_DIR / "llm_pricing.yaml"
    )


def build_cached(tmp_path: Path, server: FakeOllamaServer, options: OllamaOptions | None = None) -> CachedLLMClient:
    """Cached(Budgeted(Ollama)) from the factory, for a model that has a 0.0 price entry."""
    client = get_llm_client(
        "ollama",
        settings=ollama_settings(tmp_path),
        model="qwen2.5-3b-instruct-q4km",
        http_client=server.client(),
        ollama_options=options,
    )
    assert isinstance(client, CachedLLMClient)
    return client


def test_digest_and_options_are_in_the_cache_key(tmp_path: Path) -> None:
    tags = tags_json(model="qwen2.5-3b-instruct-q4km", digest=TEST_DIGEST)
    first_server = FakeOllamaServer(tags=tags)
    first = build_cached(tmp_path, first_server)
    first.complete("sys", USER, max_tokens=10)
    repeat = first.complete("sys", USER, max_tokens=10)
    assert repeat.cached is True
    assert len(first_server.chat_bodies()) == 1

    reimported = FakeOllamaServer(tags=tags_json(model="qwen2.5-3b-instruct-q4km", digest=OTHER_DIGEST))
    build_cached(tmp_path, reimported).complete("sys", USER, max_tokens=10)
    assert len(reimported.chat_bodies()) == 1

    other_seed = FakeOllamaServer(tags=tags)
    build_cached(tmp_path, other_seed, OllamaOptions(seed=7)).complete("sys", USER, max_tokens=10)
    assert len(other_seed.chat_bodies()) == 1

    other_ctx = FakeOllamaServer(tags=tags)
    build_cached(tmp_path, other_ctx, OllamaOptions(num_ctx=8192)).complete("sys", USER, max_tokens=10)
    assert len(other_ctx.chat_bodies()) == 1

    same_again = FakeOllamaServer(tags=tags)
    cached = build_cached(tmp_path, same_again).complete("sys", USER, max_tokens=10)
    assert cached.cached is True
    assert cached.metadata["digest"] == TEST_DIGEST
    assert same_again.chat_bodies() == []

    upgraded = FakeOllamaServer(tags=tags, version="0.41.0")
    build_cached(tmp_path, upgraded).complete("sys", USER, max_tokens=10)
    assert len(upgraded.chat_bodies()) == 1


def test_length_stopped_answer_is_cached_and_raised_again(tmp_path: Path) -> None:
    tags = tags_json(model="qwen2.5-3b-instruct-q4km")
    server = FakeOllamaServer(chat=chat_json(prompt_eval_count=900, eval_count=200, done_reason="length"), tags=tags)
    for run in range(2):
        with pytest.raises(IncompleteLLMResponse) as caught:
            build_cached(tmp_path, server).complete("sys", USER, max_tokens=200)
        assert caught.value.response.cached is (run == 1)
        assert caught.value.response.stop_reason == "length"
    assert len(server.chat_bodies()) == 1


@pytest.mark.integration
@pytest.mark.skipif(not LIVE, reason="set UWC_OLLAMA_LIVE=1 to call the real local Ollama server")
def test_live_local_ollama_smoke() -> None:
    try:
        with ollama_queue_lock("tests/llm/test_ollama_client.py live smoke", timeout_s=600):
            client = OllamaClient(LIVE_BASE_URL, LIVE_MODEL)
            response = client.complete(
                "Answer with one word.",
                [LLMMessage(role="user", content="What is the capital of France?")],
                max_tokens=10,
                temperature=0,
            )
    except OllamaQueueBusy as busy:
        pytest.skip(f"the Ollama queue stayed busy: {busy}")
    print(
        f"\nlive ollama: model={LIVE_MODEL} digest={client.digest[:12]} version={client.runtime_version} "
        f"latency_s={response.latency_s:.2f} tokens_in={response.input_tokens} tokens_out={response.output_tokens} "
        f"load_duration_ns={response.metadata['load_duration_ns']} text={response.text!r}"
    )
    assert response.provider == "ollama"
    assert response.text.strip()
    assert response.output_tokens > 0
    assert len(client.digest) == 64


@pytest.mark.integration
@pytest.mark.skipif(not LIVE, reason="set UWC_OLLAMA_LIVE=1 to call the real local Ollama server")
def test_live_local_ollama_json_schema_format() -> None:
    """Checks that the installed Ollama version honours a JSON-schema `format` (the spec asks to verify support)."""
    schema = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
    try:
        with ollama_queue_lock("tests/llm/test_ollama_client.py live format", timeout_s=600):
            client = OllamaClient(LIVE_BASE_URL, LIVE_MODEL, options=OllamaOptions(format_schema=schema))
            response = client.complete(
                "Reply in JSON.",
                [LLMMessage(role="user", content="Which city is the capital of France?")],
                max_tokens=30,
                temperature=0,
            )
    except OllamaQueueBusy as busy:
        pytest.skip(f"the Ollama queue stayed busy: {busy}")
    print(
        f"\nlive ollama format: version={client.runtime_version} latency_s={response.latency_s:.2f} "
        f"tokens_in={response.input_tokens} tokens_out={response.output_tokens} text={response.text!r}"
    )
    parsed = json.loads(response.text)
    assert isinstance(parsed, dict)
    assert isinstance(parsed.get("city"), str)
