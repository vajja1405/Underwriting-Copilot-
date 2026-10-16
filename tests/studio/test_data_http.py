"""PoliteClient and ResponseCache: caching with checksums, retries, rate limits, and secrets that never leak."""

import json

import httpx
import pytest
from studio_helpers import FakeNetwork, fast_policies, make_client

from uwcopilot.studio.data.cache import CachedResponse, ResponseCache, request_key, sha256_bytes
from uwcopilot.studio.data.errors import CacheCorrupted, HostUnreachable, UpstreamError
from uwcopilot.studio.data.http import HostPolicy, backoff_seconds, redact

URL = "https://api.example.test/data"


def ok(body: str = '{"ok": 1}') -> httpx.Response:
    return httpx.Response(200, content=body.encode(), headers={"content-type": "application/json"})


def test_second_identical_request_comes_from_the_cache(tmp_path) -> None:
    network = FakeNetwork()
    network.add(URL, lambda request: ok())
    client, _ = make_client(tmp_path, network)
    first = client.get(URL, {"a": "1"})
    second = client.get(URL, {"a": "1"})
    assert not first.from_cache and second.from_cache
    assert len(network.requests) == 1
    assert second.sha256 == sha256_bytes(b'{"ok": 1}') == first.sha256
    assert second.retrieved_at == first.retrieved_at  # the original request date is kept


def test_secret_params_never_reach_cache_key_disk_or_result(tmp_path) -> None:
    network = FakeNetwork()
    network.add(URL, lambda request: ok())
    client, _ = make_client(tmp_path, network)
    result = client.get(URL, {"q": "x", "key": "SECRET123"}, secret_params=["key"])
    assert network.requests[0].url.params["key"] == "SECRET123"  # the key is sent ...
    assert "key" not in result.params  # ... but never stored
    for path in (tmp_path / "cache").rglob("*"):
        if path.is_file():
            assert b"SECRET123" not in path.read_bytes()
    # A different key value hits the same cache entry: the key is not part of the cache key.
    again = client.get(URL, {"q": "x", "key": "OTHER"}, secret_params=["key"])
    assert again.from_cache


def test_secrets_are_redacted_from_error_messages(tmp_path) -> None:
    network = FakeNetwork()
    network.add(URL, lambda request: httpx.Response(400, content=b"bad key SECRET123"))
    client, _ = make_client(tmp_path, network)
    with pytest.raises(UpstreamError) as caught:
        client.get(URL, {"key": "SECRET123"}, secret_params=["key"])
    assert "SECRET123" not in str(caught.value) and "***" in str(caught.value)


def test_secret_header_token_is_redacted(tmp_path) -> None:
    network = FakeNetwork()
    network.add(URL, lambda request: httpx.Response(401, content=b"token TOKEN9 rejected"))
    client, _ = make_client(tmp_path, network)
    with pytest.raises(UpstreamError) as caught:
        client.get(URL, headers={"Authorization": "Bearer TOKEN9"}, secret_headers=["Authorization"])
    assert "TOKEN9" not in str(caught.value)


def test_retries_transient_status_then_succeeds(tmp_path) -> None:
    answers = iter([httpx.Response(503), httpx.Response(429, headers={"retry-after": "7"}), ok()])
    network = FakeNetwork()
    network.add(URL, lambda request: next(answers))
    client, sleeps = make_client(tmp_path, network, policies={"api.example.test": HostPolicy(min_interval_s=0)})
    assert client.get(URL).status_code == 200
    assert len(network.requests) == 3
    assert sleeps == [2.0, 7.0]  # backoff base x 2^0, then Retry-After


def test_client_error_is_not_retried(tmp_path) -> None:
    network = FakeNetwork()
    network.add(URL, lambda request: httpx.Response(404, content=b"nope"))
    client, _ = make_client(tmp_path, network)
    with pytest.raises(UpstreamError) as caught:
        client.get(URL)
    assert caught.value.status_code == 404 and len(network.requests) == 1


def test_unreachable_host_names_the_host(tmp_path) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection reset by peer", request=request)

    network = FakeNetwork()
    network.add(URL, refuse)
    client, sleeps = make_client(tmp_path, network, policies=fast_policies("api.example.test"))
    with pytest.raises(HostUnreachable, match="api.example.test") as caught:
        client.get(URL)
    assert caught.value.host == "api.example.test"
    assert len(network.requests) == 3 and len(sleeps) == 2


def test_offline_mode_uses_only_the_cache(tmp_path) -> None:
    network = FakeNetwork()
    network.add(URL, lambda request: ok())
    client, _ = make_client(tmp_path, network)
    client.get(URL)
    offline, _ = make_client(tmp_path, network, offline=True)
    assert offline.get(URL).from_cache
    with pytest.raises(HostUnreachable, match="offline"):
        offline.get(URL, {"other": "1"})
    assert len(network.requests) == 1


def test_rate_limit_waits_between_requests_to_one_host(tmp_path) -> None:
    network = FakeNetwork()
    network.add(URL, lambda request: ok())
    times = iter([100.0, 100.0, 100.2, 101.0])
    client, sleeps = make_client(
        tmp_path, network, policies={"api.example.test": HostPolicy(min_interval_s=1.0)}, clock=lambda: next(times)
    )
    client.get(URL, {"n": "1"})
    client.get(URL, {"n": "2"})
    assert sleeps == [pytest.approx(0.8)]


def test_post_json_drops_secret_fields_from_cache(tmp_path) -> None:
    network = FakeNetwork()
    network.add(URL, lambda request: ok())
    client, _ = make_client(tmp_path, network)
    result = client.post_json(URL, {"seriesid": ["A"], "registrationkey": "SECRETX"}, secret_fields=["registrationkey"])
    assert json.loads(network.requests[0].content)["registrationkey"] == "SECRETX"
    assert "SECRETX" not in json.dumps(result.params)
    assert client.post_json(
        URL, {"seriesid": ["A"], "registrationkey": "Y"}, secret_fields=["registrationkey"]
    ).from_cache


def test_cache_detects_a_corrupted_body(tmp_path) -> None:
    cache = ResponseCache(tmp_path)
    key = request_key("GET", URL, {})
    from datetime import UTC, datetime

    cache.put(key, CachedResponse(b"abc", sha256_bytes(b"abc"), datetime.now(UTC), URL, {}, 200, "text/plain"))
    body_path, _ = cache.paths_for(key)
    body_path.write_bytes(b"tampered")
    with pytest.raises(CacheCorrupted):
        cache.get(key)


def test_request_key_ignores_param_order_but_not_values() -> None:
    assert request_key("GET", URL, {"a": "1", "b": "2"}) == request_key("get", URL, {"b": "2", "a": "1"})
    assert request_key("GET", URL, {"a": "1"}) != request_key("GET", URL, {"a": "2"})


def test_backoff_and_redact_helpers() -> None:
    policy = HostPolicy(backoff_base_s=2.0)
    assert [backoff_seconds(policy, n, None) for n in range(3)] == [2.0, 4.0, 8.0]
    assert backoff_seconds(policy, 0, "1000") == 60.0
    assert redact("a SECRET b", ["SECRET", ""]) == "a *** b"
