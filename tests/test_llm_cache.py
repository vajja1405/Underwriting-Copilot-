"""Tests for uwcopilot.llm.cache: hits cost nothing, any change in the request is a miss."""

import json
from pathlib import Path

import pytest

from llm_test_helpers import PretendPaidClient
from uwcopilot.llm.base import IncompleteLLMResponse, LLMMessage
from uwcopilot.llm.cache import CachedLLMClient, LLMCache, cache_key
from uwcopilot.llm.fake_client import FakeLLM

USER = [LLMMessage(role="user", content="Summarize section NBC-3.2.")]
PARAMS = {"max_tokens": 100, "temperature": 0}


def test_cache_key_is_stable_and_sensitive_to_every_field() -> None:
    base = cache_key("anthropic", "m", "sys", USER, PARAMS)
    assert base == cache_key("anthropic", "m", "sys", USER, dict(PARAMS))
    assert len(base) == 64
    other_message = [LLMMessage(role="user", content="Summarize section NBC-3.3.")]
    variants = [
        cache_key("openai", "m", "sys", USER, PARAMS),
        cache_key("anthropic", "m2", "sys", USER, PARAMS),
        cache_key("anthropic", "m", "sys!", USER, PARAMS),
        cache_key("anthropic", "m", "sys", other_message, PARAMS),
        cache_key("anthropic", "m", "sys", USER, {"max_tokens": 101, "temperature": 0}),
    ]
    assert base not in variants
    assert len(set(variants)) == len(variants)


def test_miss_then_hit_calls_the_inner_client_once(tmp_path: Path) -> None:
    fake = FakeLLM(["only one scripted answer"])
    client = CachedLLMClient(fake, LLMCache(tmp_path))
    first = client.complete("sys", USER, max_tokens=100)
    second = client.complete("sys", USER, max_tokens=100)
    assert len(fake.calls) == 1
    assert first.cached is False
    assert second.cached is True
    assert second.cost_usd == 0.0
    assert second.text == first.text
    assert (client.hits, client.misses) == (1, 1)


def test_different_parameters_are_a_miss(tmp_path: Path) -> None:
    fake = FakeLLM(["a", "b"])
    client = CachedLLMClient(fake, LLMCache(tmp_path))
    client.complete("sys", USER, max_tokens=100, temperature=0)
    second = client.complete("sys", USER, max_tokens=100, temperature=0.7)
    assert second.cached is False
    assert len(fake.calls) == 2


def test_cache_file_holds_the_request_and_the_response(tmp_path: Path) -> None:
    client = CachedLLMClient(FakeLLM(["stored text"]), LLMCache(tmp_path))
    client.complete("sys", USER, max_tokens=100)
    files = list(tmp_path.rglob("*.json"))
    assert len(files) == 1
    entry = json.loads(files[0].read_text(encoding="utf-8"))
    assert entry["request"]["system"] == "sys"
    assert entry["request"]["messages"][0]["content"] == USER[0].content
    assert entry["response"]["text"] == "stored text"
    assert entry["key"] == files[0].stem


def test_endpoint_or_effort_change_is_a_miss(tmp_path: Path) -> None:
    """Pointing the client at another base URL (or effort) must not serve the old endpoint's answers."""
    fake = FakeLLM(["from gateway A", "from gateway B", "gateway B, high effort"])
    cache = LLMCache(tmp_path)
    fake.request_fingerprint = {"base_url": "https://gateway-a.example", "effort": "low"}  # type: ignore[attr-defined]
    assert CachedLLMClient(fake, cache).complete("sys", USER, max_tokens=100).text == "from gateway A"
    fake.request_fingerprint = {"base_url": "https://gateway-b.example", "effort": "low"}  # type: ignore[attr-defined]
    assert CachedLLMClient(fake, cache).complete("sys", USER, max_tokens=100).text == "from gateway B"
    fake.request_fingerprint = {"base_url": "https://gateway-b.example", "effort": "high"}  # type: ignore[attr-defined]
    assert CachedLLMClient(fake, cache).complete("sys", USER, max_tokens=100).text == "gateway B, high effort"
    assert len(fake.calls) == 3


def test_incomplete_answers_are_cached_and_raised_again(tmp_path: Path) -> None:
    """A cut-off answer is stored once; a rerun raises the same failure from the cache without a new call."""
    inner = PretendPaidClient(stop_reason="max_tokens")
    raised = []
    for _ in range(2):
        client = CachedLLMClient(inner, LLMCache(tmp_path))
        with pytest.raises(IncompleteLLMResponse) as caught:
            client.complete("sys", USER, max_tokens=100)
        raised.append(caught.value.response)
    assert inner.calls == 1
    assert len(list(tmp_path.rglob("*.json"))) == 1
    assert [response.cached for response in raised] == [False, True]
    assert raised[1].stop_reason == "max_tokens"
    assert raised[1].cost_usd == 0.0


def test_a_new_cache_object_reads_existing_entries(tmp_path: Path) -> None:
    CachedLLMClient(FakeLLM(["persisted"]), LLMCache(tmp_path)).complete("sys", USER, max_tokens=100)
    rerun = CachedLLMClient(FakeLLM([]), LLMCache(tmp_path))
    assert rerun.complete("sys", USER, max_tokens=100).text == "persisted"


def test_entries_written_before_the_metadata_field_still_load(tmp_path: Path) -> None:
    cache = LLMCache(tmp_path)
    key = "ab" + "0" * 62
    old_response = {
        "text": "old answer",
        "model": "claude-haiku-5-5",
        "input_tokens": 10,
        "output_tokens": 2,
        "latency_s": 0.5,
        "cost_usd": 0.0001,
        "cached": False,
        "provider": "anthropic",
        "stop_reason": "end_turn",
    }
    path = cache.path_for(key)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"format_version": 2, "key": key, "request": {}, "response": old_response}))
    loaded = cache.get(key)
    assert loaded is not None
    assert loaded.text == "old answer"
    assert loaded.metadata == {}
